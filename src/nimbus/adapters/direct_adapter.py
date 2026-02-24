"""
Direct Adapter for Nimbus — Three-Channel Streaming

Three channels:
  1. Anthropic Native (OAuth) — used when the model is Claude and OAuth
     credentials are available.  Calls the Anthropic SDK directly with
     stealth headers for Claude-Code identity.
  2. OpenAI Codex (OAuth) — used when the model is an openai-codex model
     and Codex OAuth credentials are available.  Calls the OpenAI SDK
     directly with ChatGPT subscription credentials.
  3. LiteLLM (default) — used for all other providers (Gemini, OpenAI, etc.)
     and as a fallback when no OAuth token is present.
"""

import asyncio
import base64
import json
import logging
import os
import platform
import re
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import httpx

import litellm
from litellm import acompletion
from litellm.utils import ModelResponse

from nimbus.config import get_config
from nimbus.adapters.types import LLMConfig, VcpuLLMResponse, LLMStreamEvent
from nimbus.core.models.registry import ModelRegistry

logger = logging.getLogger(__name__)

# Configure LiteLLM
litellm.drop_params = True

# Codex Responses API constants
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_MAX_RETRIES = 3
CODEX_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
CODEX_RETRY_BASE_DELAY = 1.0


def _sanitize_tool_id(id_str: str) -> str:
    """Sanitize tool ID to match Anthropic's pattern: ^[a-zA-Z0-9_-]+$"""
    if not id_str:
        return "tool_0"
    return re.sub(r'[^a-zA-Z0-9_-]', '_', id_str)


def _strip_gemini_thought_id(id_str: str) -> str:
    """Strip Gemini's embedded thinking from tool_call ID.
    Gemini encodes thinking as: call_{hex}__thought__{base64}
    """
    if not id_str or "__thought__" not in id_str:
        return id_str
    clean_id = id_str.split("__thought__")[0]
    logger.debug("Stripped Gemini thinking from tool_call ID: %d -> %d chars", len(id_str), len(clean_id))
    return clean_id or id_str


class DirectAdapter:
    """
    Direct Adapter with three-channel streaming.

    Channel 1 — Anthropic Native (OAuth):
        When the current model is a Claude model *and* valid OAuth credentials
        exist, requests go directly through the ``anthropic`` Python SDK with
        Claude-Code stealth headers.

    Channel 2 — OpenAI Codex (OAuth):
        When the current model is an openai-codex model *and* valid Codex OAuth
        credentials exist, requests go directly through the ``openai`` Python
        SDK with ChatGPT subscription credentials.

    Channel 3 — LiteLLM (default):
        For every other case (Gemini, OpenAI, Claude without OAuth, ...) the
        request is routed through LiteLLM.
    """

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig()
        self._model = self.config.get_model()
        self._ensure_api_keys()
        self._init_anthropic_oauth()
        self._init_openai_codex_oauth()
        # Cached Anthropic SDK client (reused across calls, rebuilt on token change)
        self._anthropic_client: Any = None
        self._anthropic_client_token: str | None = None
        # Cached httpx client for Codex channel (reused across calls, rebuilt on token change)
        self._codex_client: Optional[httpx.AsyncClient] = None
        self._codex_client_token: Optional[str] = None

    def _ensure_api_keys(self):
        """Ensure API keys are loaded from NimbusConfig."""
        cfg = get_config()
        if cfg.gemini_api_key and "GEMINI_API_KEY" not in os.environ:
            os.environ["GEMINI_API_KEY"] = cfg.gemini_api_key
        if cfg.gemini_api_key and "GOOGLE_API_KEY" not in os.environ:
             os.environ["GOOGLE_API_KEY"] = cfg.gemini_api_key

    def _init_anthropic_oauth(self):
        """Initialize Anthropic OAuth state from NimbusConfig."""
        from nimbus.adapters.anthropic_oauth import load_oauth_token

        cfg = get_config()
        self._anthropic_auth: dict | None = None
        self._anthropic_oauth_path = Path(cfg.anthropic_oauth_path).expanduser()

        if cfg.anthropic_use_oauth:
            auth = load_oauth_token(self._anthropic_oauth_path)
            if auth is not None:
                self._anthropic_auth = auth
                logger.info(
                    "Anthropic OAuth loaded from %s", self._anthropic_oauth_path
                )
            else:
                logger.debug(
                    "Anthropic OAuth enabled but no auth.json found at %s",
                    self._anthropic_oauth_path,
                )

    def _init_openai_codex_oauth(self):
        """Initialize OpenAI Codex OAuth state from NimbusConfig."""
        from nimbus.adapters.openai_codex_oauth import load_oauth_token

        cfg = get_config()
        self._codex_auth: dict | None = None

        if cfg.codex_use_oauth:
            # Codex tokens also live in the same auth.json
            auth_path = Path(cfg.anthropic_oauth_path).expanduser()
            auth = load_oauth_token(auth_path)
            if auth is not None:
                self._codex_auth = auth
                logger.info("OpenAI Codex OAuth loaded from %s", auth_path)
            else:
                logger.debug(
                    "Codex OAuth enabled but no openai-codex token in %s",
                    auth_path,
                )

    def _get_codex_client(self, token: str) -> httpx.AsyncClient:
        """Return a persistent httpx client for Codex, rebuilding on token change."""
        if self._codex_client is None or self._codex_client_token != token:
            if self._codex_client is not None:
                # Schedule close of the old client (fire-and-forget)
                try:
                    asyncio.get_event_loop().create_task(self._codex_client.aclose())
                except RuntimeError:
                    pass  # no running loop — will be GC'd
            self._codex_client = httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=30.0)
            )
            self._codex_client_token = token
        return self._codex_client

    def _is_anthropic_model(self) -> bool:
        """Check if current model is an Anthropic model."""
        info = ModelRegistry.get(self._model)
        if info:
            return info.provider == "anthropic"
        return "claude" in self._model.lower()

    def _is_openai_codex_model(self) -> bool:
        """Check if current model uses OpenAI Codex (ChatGPT subscription)."""
        info = ModelRegistry.get(self._model)
        if info:
            return info.provider == "openai-codex"
        return "openai-codex" in self._model.lower()

    async def __aenter__(self) -> "DirectAdapter":
        return self

    async def __aexit__(self, *args):
        pass

    async def start(self):
        """No-op for direct adapter."""
        pass

    async def stop(self):
        """Close persistent HTTP clients."""
        if self._codex_client is not None:
            try:
                await self._codex_client.aclose()
            except Exception:
                pass
            self._codex_client = None
            self._codex_client_token = None

    async def health_check(self) -> bool:
        """Check if we have credentials for at least one provider."""
        has_gemini = bool(
            os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        )
        has_anthropic = self._anthropic_auth is not None
        has_codex = self._codex_auth is not None
        return has_gemini or has_anthropic or has_codex

    @staticmethod
    def _convert_image_block(block: dict, target: str) -> dict:
        """
        Convert a single Nimbus image block to a target LLM format.

        Nimbus internal format:
            {"type": "image", "data": "<base64>", "mimeType": "image/png"}

        *target* is one of ``"anthropic"``, ``"openai"``, ``"responses"``.
        Text blocks are returned unchanged (for anthropic/openai) or
        remapped to the appropriate type (for responses).
        """
        btype = block.get("type", "")

        if btype == "image":
            mime = block.get("mimeType", "image/png")
            data = block.get("data", "")
            if target == "anthropic":
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": data,
                    },
                }
            elif target == "openai":
                return {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{data}",
                    },
                }
            elif target == "responses":
                return {
                    "type": "input_image",
                    "image_url": f"data:{mime};base64,{data}",
                }

        # text block
        if target == "responses":
            return {"type": "input_text", "text": block.get("text", "")}

        # anthropic / openai: text blocks pass through unchanged
        return block

    @staticmethod
    def _convert_content_blocks(content, target: str):
        """
        If *content* is a list, convert every block to *target* format.
        If it is a plain string, return it unchanged.

        Returns str | list.
        """
        if isinstance(content, list):
            return [
                DirectAdapter._convert_image_block(b, target) for b in content
            ]
        return content

    def _convert_tools(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        """Convert tools to OpenAI format (LiteLLM expects this)."""
        if not tools:
            return None

        result = []
        for tool in tools:
            t = tool.copy()
            if t.get("type") == "function":
                result.append(t)
            elif "function" in t:
                 if "type" not in t:
                     t["type"] = "function"
                 result.append(t)
            else:
                 # Simplified format -> OpenAI format
                result.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name"),
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    }
                })
        return result

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> VcpuLLMResponse:
        """
        Non-streaming chat (simulated via stream to support on_chunk).
        """
        full_content = []
        collected_tool_calls = []

        try:
            async for event in self.stream(messages, tools):
                if event.type == "text":
                    text = event.text
                    full_content.append(text)
                    if on_chunk:
                        on_chunk(text)
                elif event.type == "tool_call" and event.tool_call:
                    collected_tool_calls.append(event.tool_call)
                elif event.type == "error":
                     logger.error(f"Stream error: {event.error}")
                     raise RuntimeError(f"LLM Stream Error: {event.error}")

        except Exception as e:
            logger.error(f"DirectAdapter chat failed: {e}")
            raise RuntimeError(f"LLM call failed: {e}")

        content = "".join(full_content)

        # Format tool calls for VcpuLLMResponse
        tool_calls = []
        for tc in collected_tool_calls:
             tool_calls.append({
                 "id": tc.get("id"),
                 "type": "function",
                 "function": {
                     "name": tc.get("name"),
                     "arguments": json.dumps(tc.get("arguments"))
                                  if isinstance(tc.get("arguments"), dict)
                                  else tc.get("arguments")
                 }
             })

        return VcpuLLMResponse(
            content=content if content else None,
            tool_calls=tool_calls if tool_calls else None,
        )

    # ------------------------------------------------------------------
    # Stream router
    # ------------------------------------------------------------------

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """
        Stream response — routes to Anthropic native or LiteLLM.
        """
        if self._is_anthropic_model() and self._anthropic_auth is not None:
            async for event in self._stream_anthropic_native(messages, tools):
                yield event
        elif self._is_openai_codex_model() and self._codex_auth is not None:
            async for event in self._stream_openai_native(messages, tools):
                yield event
        else:
            async for event in self._stream_litellm(messages, tools):
                yield event

    # ------------------------------------------------------------------
    # Channel 1: Anthropic Native (OAuth)
    # ------------------------------------------------------------------

    def _convert_messages_to_anthropic(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[str, List[Dict[str, Any]]]:
        """
        Convert OpenAI-format messages to Anthropic format.

        Returns (system_text, anthropic_messages).
        """
        system_parts: list[str] = []
        raw_messages: list[dict] = []

        for msg in messages:
            role = msg.get("role", "")

            # ---- system messages -> collected into system_text ----
            if role == "system":
                content = msg.get("content", "")
                if content:
                    system_parts.append(content)
                continue

            # ---- assistant with tool_calls ----
            if role == "assistant" and msg.get("tool_calls"):
                content_blocks: list[dict] = []
                # Preserve text content if present
                text = msg.get("content")
                if text:
                    content_blocks.append({"type": "text", "text": text})
                # Convert tool_calls to tool_use blocks
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    args_raw = func.get("arguments", "{}")
                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    else:
                        args = args_raw
                    content_blocks.append({
                        "type": "tool_use",
                        "id": _sanitize_tool_id(tc.get("id", "")),
                        "name": func.get("name", ""),
                        "input": args,
                    })
                raw_messages.append({"role": "assistant", "content": content_blocks})
                continue

            # ---- tool result ----
            if role == "tool":
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": _sanitize_tool_id(msg.get("tool_call_id", "")),
                    "content": msg.get("content", ""),
                }
                raw_messages.append({
                    "role": "user",
                    "content": [tool_result_block],
                })
                continue

            # ---- user / plain assistant ----
            raw_content = msg.get("content", "") or ""
            raw_messages.append({
                "role": role,
                "content": self._convert_content_blocks(raw_content, "anthropic"),
            })

        # Merge consecutive same-role messages (Anthropic API requirement)
        merged: list[dict] = []
        for m in raw_messages:
            if merged and merged[-1]["role"] == m["role"]:
                prev_content = merged[-1]["content"]
                cur_content = m["content"]
                # Normalize both to list form
                if isinstance(prev_content, str):
                    prev_content = [{"type": "text", "text": prev_content}]
                if isinstance(cur_content, str):
                    cur_content = [{"type": "text", "text": cur_content}]
                merged[-1]["content"] = prev_content + cur_content
            else:
                merged.append(m)

        system_text = "\n\n".join(system_parts)
        return system_text, merged

    def _convert_tools_to_anthropic(
        self, tools: Optional[List[Dict[str, Any]]]
    ) -> Optional[List[Dict[str, Any]]]:
        """Convert tools to Anthropic native format."""
        if not tools:
            return None

        result = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                result.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                })
            elif "name" in tool:
                # Nimbus simplified format
                result.append({
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                })
        return result if result else None

    def _validate_anthropic_context(self, messages: list) -> list:
        """
        Validate and auto-repair context for Anthropic API constraints.

        Rules:
        1. Conversation must not end with assistant message
        2. Every tool_use must have a corresponding tool_result
        3. No orphan tool_use blocks without matching tool_result

        This is a defensive last-resort layer. Ideally the MMU and VCPU
        already produce well-formed context, but truncation / early-return
        edge cases can slip through.
        """
        if not messages:
            return messages

        # Rule 1: Must not end with assistant message
        # (It's likely an orphan from truncation or discard.)
        while messages and messages[-1].get("role") == "assistant":
            logger.warning(
                "Context validation: Removing trailing assistant message"
            )
            messages.pop()

        if not messages:
            return messages

        # Rule 2: Check tool_use / tool_result pairing
        tool_use_ids: set[str] = set()
        tool_result_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tid = block.get("id")
                            if tid:
                                tool_use_ids.add(tid)
            elif msg.get("role") == "user":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            tid = block.get("tool_use_id")
                            if tid:
                                tool_result_ids.add(tid)

        orphan_ids = tool_use_ids - tool_result_ids
        if orphan_ids:
            logger.warning(
                "Context validation: Found %d orphan tool_use IDs, removing orphan blocks: %s",
                len(orphan_ids),
                orphan_ids,
            )
            # Remove orphan tool_use blocks from assistant messages
            for msg in messages:
                if msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        msg["content"] = [
                            block
                            for block in content
                            if not (
                                isinstance(block, dict)
                                and block.get("type") == "tool_use"
                                and block.get("id") in orphan_ids
                            )
                        ]
                        # If no content blocks remain, mark for removal
                        if not msg["content"]:
                            msg["_remove"] = True

            messages = [m for m in messages if not m.get("_remove")]

        return messages

    async def _stream_anthropic_native(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """
        Stream via the Anthropic Python SDK with OAuth credentials.
        """
        try:
            import anthropic
        except ImportError:
            logger.error("anthropic package not installed, falling back to LiteLLM")
            async for event in self._stream_litellm(messages, tools):
                yield event
            return

        from nimbus.adapters.anthropic_oauth import (
            check_and_refresh,
            STEALTH_HEADERS,
            CLAUDE_CODE_SYSTEM_PREFIX,
        )

        # Refresh / validate OAuth token
        try:
            access_token = check_and_refresh(
                self._anthropic_auth, self._anthropic_oauth_path  # type: ignore[arg-type]
            )
        except RuntimeError as exc:
            logger.error("OAuth token refresh failed: %s", exc)
            yield LLMStreamEvent(type="error", error=str(exc))
            return

        # Build model id (strip provider prefix)
        model_id = self._model
        if model_id.startswith("anthropic/"):
            model_id = model_id[len("anthropic/"):]

        # Convert messages & tools
        system_text, anthropic_messages = self._convert_messages_to_anthropic(messages)
        # Validate and auto-repair context before sending to API
        anthropic_messages = self._validate_anthropic_context(anthropic_messages)
        anthropic_tools = self._convert_tools_to_anthropic(tools)

        # Prepend Claude Code system prefix
        if system_text:
            full_system = CLAUDE_CODE_SYSTEM_PREFIX + "\n\n" + system_text
        else:
            full_system = CLAUDE_CODE_SYSTEM_PREFIX

        # Build request kwargs
        kwargs: dict[str, Any] = {
            "model": model_id,
            "max_tokens": self.config.max_tokens,
            "system": full_system,
            "messages": anthropic_messages,
        }
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature

        # Reuse client if token hasn't changed
        if self._anthropic_client is None or self._anthropic_client_token != access_token:
            self._anthropic_client = anthropic.AsyncAnthropic(
                auth_token=access_token,
                default_headers=STEALTH_HEADERS,
                timeout=httpx.Timeout(timeout=120.0, connect=10.0),
            )
            self._anthropic_client_token = access_token
        client = self._anthropic_client

        try:
            current_tool: dict | None = None
            t_start = time.monotonic()
            ttfb_logged = False
            chunk_count = 0

            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    chunk_count += 1
                    if not ttfb_logged:
                        ttfb = time.monotonic() - t_start
                        logger.info(
                            "[Anthropic] model=%s TTFB=%.1fs",
                            model_id, ttfb,
                        )
                        ttfb_logged = True

                    if event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            current_tool = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "arguments": "",
                            }
                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield LLMStreamEvent(
                                type="text", text=event.delta.text
                            )
                        elif event.delta.type == "input_json_delta":
                            if current_tool is not None:
                                current_tool["arguments"] += event.delta.partial_json
                    elif event.type == "content_block_stop":
                        if current_tool is not None:
                            try:
                                args = (
                                    json.loads(current_tool["arguments"])
                                    if current_tool["arguments"]
                                    else {}
                                )
                            except (json.JSONDecodeError, TypeError):
                                args = current_tool["arguments"]
                            yield LLMStreamEvent(
                                type="tool_call",
                                tool_call={
                                    "id": current_tool["id"],
                                    "name": current_tool["name"],
                                    "arguments": args,
                                },
                            )
                            current_tool = None
                    elif event.type == "message_stop":
                        yield LLMStreamEvent(type="stop", reason="stop")

            total = time.monotonic() - t_start
            logger.info(
                "[Anthropic] model=%s TTFB=%.1fs total=%.1fs chunks=%d",
                model_id, ttfb if ttfb_logged else total, total, chunk_count,
            )

        except Exception as e:
            logger.error(f"Anthropic native streaming error: {type(e).__name__}: {e!r}")
            yield LLMStreamEvent(type="error", error=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)

    # ------------------------------------------------------------------
    # Channel 2: OpenAI Codex — Responses API (OAuth)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_account_id_from_jwt(token: str) -> str:
        """Extract chatgpt_account_id from a JWT access token."""
        try:
            payload_b64 = token.split(".")[1]
            # Add padding if needed
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            return payload["https://api.openai.com/auth"]["chatgpt_account_id"]
        except Exception:
            return ""

    def _build_codex_headers(self, access_token: str) -> dict:
        """Build HTTP headers for the Codex Responses API."""
        return {
            "Authorization": f"Bearer {access_token}",
            "chatgpt-account-id": self._extract_account_id_from_jwt(access_token),
            "OpenAI-Beta": "responses=experimental",
            "originator": "pi",
            "User-Agent": f"nimbus/0.2 ({platform.system()})",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }

    def _convert_messages_to_responses_api(
        self, messages: list
    ) -> tuple[str, list]:
        """
        Convert OpenAI-format messages to Responses API format.

        Returns (instructions_str, input_list).
        """
        system_parts: list[str] = []
        input_items: list[dict] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""

            if role == "system":
                if content:
                    system_parts.append(content)
                continue

            if role == "user":
                raw_content = msg.get("content", "") or ""
                if isinstance(raw_content, list):
                    converted = self._convert_content_blocks(raw_content, "responses")
                    input_items.append({
                        "role": "user",
                        "content": converted,
                    })
                else:
                    input_items.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": raw_content}],
                    })
                continue

            if role == "assistant":
                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    # Plain assistant message
                    input_items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content}],
                    })
                else:
                    # Assistant message with tool calls
                    if content:
                        input_items.append({
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": content}],
                        })
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        tc_id = tc.get("id", "")
                        # Responses API requires id to start with "fc"
                        fc_id = tc_id if tc_id.startswith("fc") else f"fc_{tc_id}"
                        arguments = func.get("arguments", "")
                        if isinstance(arguments, dict):
                            arguments = json.dumps(arguments)
                        input_items.append({
                            "type": "function_call",
                            "id": fc_id,
                            "call_id": tc_id,
                            "name": func.get("name", ""),
                            "arguments": arguments,
                        })
                continue

            if role == "tool":
                input_items.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": content,
                })
                continue

        instructions = "\n\n".join(system_parts)
        return instructions, input_items

    def _convert_tools_to_responses_api(
        self, tools: list | None
    ) -> list | None:
        """
        Convert OpenAI nested tool format to Responses API flat format.

        Input:  {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        Output: {"type": "function", "name": ..., "description": ..., "parameters": ...}
        """
        if not tools:
            return None

        result = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                result.append({
                    "type": "function",
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {}),
                })
            elif "name" in tool:
                # Nimbus simplified format
                result.append({
                    "type": "function",
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {}),
                })
        return result if result else None

    async def _parse_sse_lines(
        self, aiter_bytes
    ) -> AsyncIterator[tuple[str, str]]:
        """
        Parse raw bytes from an httpx streaming response into SSE events.

        Yields (event_type, data_string) tuples.
        """
        buffer = ""
        event_type = "message"
        data_lines: list[str] = []

        async for raw_bytes in aiter_bytes:
            buffer += raw_bytes.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")

                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        continue
                    data_lines.append(data_str)
                elif line == "":
                    # Blank line = event boundary
                    if data_lines:
                        yield event_type, "\n".join(data_lines)
                    event_type = "message"
                    data_lines = []

        # Flush remaining data if buffer ends without trailing newline
        if data_lines:
            yield event_type, "\n".join(data_lines)

    async def _stream_openai_native(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """
        Stream via the ChatGPT Codex Responses API with OAuth credentials.
        """
        from nimbus.adapters.openai_codex_oauth import check_and_refresh

        # Refresh / validate OAuth token
        try:
            access_token = check_and_refresh(
                self._codex_auth,
                Path(get_config().anthropic_oauth_path).expanduser(),
            )
        except RuntimeError as exc:
            logger.error("Codex OAuth token refresh failed: %s", exc)
            yield LLMStreamEvent(type="error", error=str(exc))
            return

        # Build model id (strip provider prefix)
        model_id = self._model
        if model_id.startswith("openai-codex/"):
            model_id = model_id[len("openai-codex/"):]

        # Build headers
        headers = self._build_codex_headers(access_token)

        # Convert messages and tools to Responses API format
        instructions, input_items = self._convert_messages_to_responses_api(messages)
        codex_tools = self._convert_tools_to_responses_api(self._convert_tools(tools))

        # Build request body
        body: dict[str, Any] = {
            "model": model_id,
            "store": False,
            "stream": True,
            "input": input_items,
            "tools": codex_tools or [],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "text": {"verbosity": "medium"},
            "include": [
                "reasoning.encrypted_content",
            ],
        }
        body["instructions"] = instructions if instructions else "You are a helpful assistant."
        # Note: Codex Responses API does not support temperature parameter

        # Get or reuse persistent httpx client
        client = self._get_codex_client(access_token)

        # Retry loop
        for attempt in range(CODEX_MAX_RETRIES):
            try:
                t_start = time.monotonic()
                ttfb_logged = False
                chunk_count = 0

                async with client.stream(
                    "POST", CODEX_RESPONSES_URL, headers=headers, json=body
                ) as resp:
                    if resp.status_code >= 400:
                        resp_body = (await resp.aread()).decode("utf-8", errors="replace")
                        logger.error(
                            "Codex API %d response: %s\nRequest body: %s",
                            resp.status_code, resp_body[:2000],
                            json.dumps(body, ensure_ascii=False, default=str)[:2000],
                        )
                        if resp.status_code in CODEX_RETRY_STATUS_CODES and attempt < CODEX_MAX_RETRIES - 1:
                            delay = CODEX_RETRY_BASE_DELAY * (2 ** attempt)
                            logger.warning(
                                "Codex API %d, retrying in %.1fs (attempt %d/%d)",
                                resp.status_code, delay,
                                attempt + 1, CODEX_MAX_RETRIES,
                            )
                            await asyncio.sleep(delay)
                            continue
                    resp.raise_for_status()

                    # SSE state machine
                    pending_calls: dict[str, dict] = {}

                    async for event_type, data_str in self._parse_sse_lines(
                        resp.aiter_bytes()
                    ):
                        if not data_str:
                            continue
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        chunk_count += 1
                        if not ttfb_logged:
                            ttfb = time.monotonic() - t_start
                            logger.info("[Codex] TTFB=%.1fs", ttfb)
                            ttfb_logged = True

                        logger.debug(
                            "SSE event=%s data=%s",
                            event_type, data_str[:500],
                        )

                        if event_type == "response.output_text.delta":
                            yield LLMStreamEvent(
                                type="text",
                                text=data.get("delta", ""),
                            )

                        elif event_type == "response.output_item.added":
                            item = data.get("item", {})
                            if item.get("type") == "function_call":
                                item_id = item.get("id", "")
                                call_id = item.get("call_id", item_id)
                                pending_calls[item_id] = {
                                    "id": call_id,
                                    "name": item.get("name", ""),
                                    "arguments": "",
                                }

                        elif event_type == "response.function_call_arguments.delta":
                            item_id = data.get("item_id", data.get("call_id", ""))
                            if item_id in pending_calls:
                                pending_calls[item_id]["arguments"] += data.get(
                                    "delta", ""
                                )

                        elif event_type == "response.output_item.done":
                            item = data.get("item", {})
                            if item.get("type") == "function_call":
                                item_id = item.get("id", "")
                                tc = pending_calls.pop(item_id, None)
                                if tc:
                                    try:
                                        args = (
                                            json.loads(tc["arguments"])
                                            if tc["arguments"]
                                            else {}
                                        )
                                    except (json.JSONDecodeError, TypeError):
                                        args = tc["arguments"]
                                    yield LLMStreamEvent(
                                        type="tool_call",
                                        tool_call={
                                            "id": tc["id"],
                                            "name": tc["name"],
                                            "arguments": args,
                                        },
                                    )

                        elif event_type == "response.completed":
                            yield LLMStreamEvent(type="stop", reason="stop")

                        elif event_type in ("error", "response.failed"):
                            err = data.get("error")
                            if isinstance(err, dict):
                                err_msg = err.get("message", str(data))
                            else:
                                err_msg = str(data)
                            yield LLMStreamEvent(type="error", error=err_msg)

                total = time.monotonic() - t_start
                logger.info(
                    "[Codex] TTFB=%.1fs total=%.1fs chunks=%d",
                    ttfb if ttfb_logged else total, total, chunk_count,
                )
                return  # Success, exit retry loop

            except httpx.HTTPStatusError as e:
                if (
                    e.response.status_code in CODEX_RETRY_STATUS_CODES
                    and attempt < CODEX_MAX_RETRIES - 1
                ):
                    delay = CODEX_RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "Codex API %d, retrying in %.1fs (attempt %d/%d)",
                        e.response.status_code, delay,
                        attempt + 1, CODEX_MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("Codex API error: %s", e)
                yield LLMStreamEvent(type="error", error=str(e))
                return
            except Exception as e:
                logger.error("Codex streaming error: %s", e)
                yield LLMStreamEvent(type="error", error=str(e))
                return

    # ------------------------------------------------------------------
    # Channel 3: LiteLLM (default)
    # ------------------------------------------------------------------

    async def _stream_litellm(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """
        Stream response from LiteLLM (original stream logic).
        """
        openai_tools = self._convert_tools(tools)

        # Adjust model name for LiteLLM using ModelRegistry
        model = self._model
        info = ModelRegistry.get(model)
        if info:
            if info.provider == "google":
                # LiteLLM uses gemini/ prefix for Google AI Studio
                model = f"gemini/{info.model_id}"
            elif info.provider == "anthropic":
                model = f"anthropic/{info.model_id}"
            else:
                model = info.model_id
        else:
            # Fallback for unregistered models
            if "gemini" in model:
                if "google/" in model:
                    model = model.replace("google/", "gemini/")
                elif "gemini/" not in model:
                    model = f"gemini/{model}"
            elif "claude" in model and "anthropic/" not in model:
                model = f"anthropic/{model}"

        # Clean messages and convert image blocks to OpenAI format
        clean_messages = []
        for m in messages:
            msg = m.copy()
            if msg.get("content") is None:
                msg["content"] = ""
            elif isinstance(msg.get("content"), list):
                msg["content"] = self._convert_content_blocks(
                    msg["content"], "openai"
                )
            clean_messages.append(msg)

        try:
            t_start = time.monotonic()
            ttfb_logged = False
            chunk_count = 0

            response = await acompletion(
                model=model,
                messages=clean_messages,
                tools=openai_tools,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                stream=True,
            )

            tool_call_chunks = {}

            async for chunk in response:
                chunk_count += 1
                if not ttfb_logged:
                    ttfb = time.monotonic() - t_start
                    logger.info("[LiteLLM] model=%s TTFB=%.1fs", model, ttfb)
                    ttfb_logged = True

                delta = chunk.choices[0].delta

                if delta.content:
                    yield LLMStreamEvent(type="text", text=delta.content)

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_call_chunks:
                            tool_call_chunks[idx] = {
                                "id": tc.id or "",
                                "name": tc.function.name or "",
                                "arguments": tc.function.arguments or ""
                            }
                        else:
                            if tc.id: tool_call_chunks[idx]["id"] += tc.id
                            if tc.function.name: tool_call_chunks[idx]["name"] += tc.function.name
                            if tc.function.arguments: tool_call_chunks[idx]["arguments"] += tc.function.arguments

            total = time.monotonic() - t_start
            logger.info(
                "[LiteLLM] model=%s TTFB=%.1fs total=%.1fs chunks=%d",
                model, ttfb if ttfb_logged else total, total, chunk_count,
            )

            for idx, tc_data in tool_call_chunks.items():
                try:
                    args = json.loads(tc_data["arguments"])
                except:
                    args = tc_data["arguments"]

                yield LLMStreamEvent(
                    type="tool_call",
                    tool_call={
                        "id": _strip_gemini_thought_id(tc_data["id"]),
                        "name": tc_data["name"],
                        "arguments": args
                    }
                )

            yield LLMStreamEvent(type="stop", reason="stop")

        except Exception as e:
            logger.error(f"LiteLLM error: {e}")
            yield LLMStreamEvent(type="error", error=str(e))

    async def list_models(self) -> List[Dict[str, str]]:
        """List available models from configured providers."""
        models = []

        # Google Gemini models
        models.extend([
            {"id": "google/gemini-3-flash-preview", "object": "model", "owned_by": "google"},
            {"id": "google/gemini-3-pro-preview", "object": "model", "owned_by": "google"},
            {"id": "google/gemini-3.1-pro-preview", "object": "model", "owned_by": "google"},
            {"id": "google/gemini-3.1-pro-preview-customtools", "object": "model", "owned_by": "google"},
        ])

        # Anthropic Claude models
        models.extend([
            {"id": "anthropic/claude-opus-4-6", "object": "model", "owned_by": "anthropic"},
            {"id": "anthropic/claude-sonnet-4-6", "object": "model", "owned_by": "anthropic"},
        ])

        # OpenAI Codex models
        models.extend([
            {"id": "openai-codex/gpt-5.2-codex", "object": "model", "owned_by": "openai-codex"},
            {"id": "openai-codex/gpt-5.3-codex", "object": "model", "owned_by": "openai-codex"},
        ])

        return models
