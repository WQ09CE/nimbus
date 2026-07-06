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
import socket
import ssl
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import httpx
import litellm
from litellm import acompletion
from litellm.utils import ModelResponse

from nimbus.adapters.types import LLMConfig, LLMStreamEvent, TokenUsage, VcpuLLMResponse
from nimbus.config import get_config
from nimbus.core.models.registry import ModelRegistry
from nimbus.core.protocol import Fault

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


def _try_parse_tool_call(d: dict) -> dict | None:
    """Try to parse a dict as a tool call, normalizing various key formats.

    Handles:
      - "name"/"tool_name"/"tool" as tool name
      - "arguments"/"parameters"/"args" as arguments (dict or JSON string)
      - "function" key -> recursive parse
      - "result" key -> SubmitResult
    """
    # Normalize: accept "name", "tool_name", or "tool" as the tool name key
    tool_name = d.get("name") or d.get("tool_name") or d.get("tool")
    if tool_name and isinstance(tool_name, str):
        args = d.get("arguments") or d.get("parameters") or d.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                return None
        if isinstance(args, dict):
            return {"name": tool_name, "arguments": args}
    if "function" in d and isinstance(d["function"], dict):
        return _try_parse_tool_call(d["function"])
    if not tool_name and "result" in d:
        return {"name": "SubmitResult", "arguments": {"result": d["result"]}}
    return None


def _extract_tool_calls_from_json(json_str: str) -> list[dict] | None:
    """Extract tool calls from a JSON string, handling various small-model formats.

    Returns a list of normalized tool call dicts [{"name": ..., "arguments": {...}}]
    or None if the string is not a valid tool call.
    """
    try:
        parsed = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        # Fallback: model might have generated implicit python code blocks like `ls(".")`
        # Or implicit agent OS tools like `call:ls{"path":"."}`
        results = []
        
        # 1. Match `call:tool_name{...}`
        call_matches = re.finditer(r'call:\s*([a-zA-Z0-9_-]+)\s*(\{.*?\})', json_str, re.DOTALL)
        for m in call_matches:
            name, args_str = m.group(1), m.group(2)
            try:
                args = json.loads(args_str)
                results.append({"name": name, "arguments": args})
            except:
                pass
                
        # 2. Match python style `tool_name(k="v", k2=v2)` 
        # Very simple heuristic: word(string)
        py_matches = re.finditer(r'([a-zA-Z0-9_-]+)\s*\((.*?)\)', json_str, re.DOTALL)
        for m in py_matches:
            name, args_str = m.group(1), m.group(2)
            # If we already found call: matches, skip this to avoid double counting
            if results: continue
            
            # Simple heuristic for argument string e.g., `"."` or `path="."`
            args = {}
            if args_str.strip():
                # Attempt to parse as json string if it's just a single string argument like `"."`
                try:
                    val = json.loads(args_str)
                    if isinstance(val, str):
                        args["path"] = val
                except:
                    # Generic fallback: just dump the raw string
                    args["raw_args"] = args_str.strip()
            results.append({"name": name, "arguments": args})
            
        return results if results else None

    # Unwrap {"tool_calls": [...]} wrapper (qwen/ollama format)
    if isinstance(parsed, dict) and "tool_calls" in parsed and isinstance(parsed["tool_calls"], list):
        parsed = parsed["tool_calls"]

    results = []
    if isinstance(parsed, dict):
        tc = _try_parse_tool_call(parsed)
        if tc: results.append(tc)
    elif isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                tc = _try_parse_tool_call(item)
                if tc: results.append(tc)
    return results if results else None


def _extract_tool_calls_from_text(text: str) -> list[dict] | None:
    """Extract JSON tool calls embedded in free-form model text.

    Gemma4/Ollama can emit a short explanation followed by a fenced JSON
    function-call object. That content may already have streamed as text, but
    the adapter still needs to recover the structured call before VCPU handling.
    """
    content = text.strip()
    if not content:
        return None

    candidates: list[str] = []
    for match in re.finditer(
        r"```(?:json)?\s*\n(.*?)\n\s*```",
        content,
        re.DOTALL | re.IGNORECASE,
    ):
        candidates.append(match.group(1).strip())

    if not candidates:
        if content.startswith(("{", "[")):
            candidates.append(content)
        else:
            raw = re.search(r"(\{.*\}|\[.*\])", content, re.DOTALL)
            if raw:
                candidates.append(raw.group(1).strip())

    for candidate in candidates:
        extracted = _extract_tool_calls_from_json(candidate)
        if extracted:
            return extracted
    return None


def _find_json_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) of top-level balanced {...}/[...] spans, ignoring
    braces inside strings. Used to locate embedded JSON to strip from prose."""
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "{[":
            depth = 0
            in_str = False
            esc = False
            j = i
            while j < n:
                c = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                else:
                    if c == '"':
                        in_str = True
                    elif c in "{[":
                        depth += 1
                    elif c in "}]":
                        depth -= 1
                        if depth == 0:
                            spans.append((i, j + 1))
                            break
                j += 1
            i = (j + 1) if j < n else n
        else:
            i += 1
    return spans


def _strip_tool_call_blocks(text: str) -> str:
    """Remove tool-call JSON that a model inlined into its prose, so the JSON
    doesn't leak into the stored/displayed assistant message. Only removes
    blocks that actually parse as a tool call — ordinary JSON the user is
    discussing is left untouched."""
    if not text:
        return text
    cleaned = text

    # 1. Fenced ```json / ```tool_code blocks that are tool calls.
    def _fenced_repl(m: "re.Match[str]") -> str:
        inner = m.group(1).strip()
        return "" if _extract_tool_calls_from_json(inner) else m.group(0)

    cleaned = re.sub(
        r"```(?:json|tool_code|tool)?\s*\n(.*?)\n\s*```",
        _fenced_repl,
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 2. Bare top-level JSON spans that are tool calls (remove from the end so
    #    earlier indices stay valid).
    for start, end in reversed(_find_json_spans(cleaned)):
        seg = cleaned[start:end].strip()
        if _extract_tool_calls_from_json(seg):
            cleaned = cleaned[:start] + cleaned[end:]

    # Tidy leftover whitespace.
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _tool_names_from_openai_tools(tools: Optional[List[Dict[str, Any]]]) -> set[str]:
    names: set[str] = set()
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str) and name:
            names.add(name)
    return names


def _filter_tool_calls_for_schemas(
    tool_calls: list[dict] | None,
    tool_names: set[str],
) -> list[dict] | None:
    if not tool_calls:
        return None
    if not tool_names:
        return tool_calls
    filtered = [tc for tc in tool_calls if tc.get("name") in tool_names]
    return filtered or None


def _find_closing_fence(text: str) -> int | None:
    if not text.startswith("```"):
        return None
    idx = text.find("```", 3)
    return idx if idx >= 0 else None


def _find_closing_brace(text: str) -> int | None:
    """Index of the brace that closes the JSON object/array at text[0], or None
    if not yet balanced. String-aware (ignores braces inside JSON strings)."""
    if not text or text[0] not in "{[":
        return None
    depth = 0
    in_str = False
    esc = False
    for i, c in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return i
    return None


# Start of an inlined JSON tool call: `{"` or `[{` (allowing whitespace).
_BRACE_TOOL_START_RE = re.compile(r'\{\s*"|\[\s*\{')


_TOOL_CALL_TEXT_MARKERS = ("tool calls:", "tool call:")


def _find_tool_call_text_marker(text: str) -> int | None:
    lower = text.lower()
    hits = [idx for marker in _TOOL_CALL_TEXT_MARKERS if (idx := lower.find(marker)) >= 0]
    return min(hits) if hits else None


def _tool_call_marker_suffix_len(text: str) -> int:
    lower = text.lower()
    best = 0
    for marker in _TOOL_CALL_TEXT_MARKERS:
        max_len = min(len(marker) - 1, len(lower))
        for length in range(1, max_len + 1):
            if lower.endswith(marker[:length]):
                best = max(best, length)
    return best


def _parse_maybe_json(value: Any) -> Any:
    """Best-effort parse of JSON-ish function-call arguments."""
    if value in (None, ""):
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return value
    return value


def _merge_codex_function_call_item(
    pending: Optional[dict[str, Any]],
    item: dict[str, Any],
) -> dict[str, Any]:
    """Merge streaming + final Responses API function_call payloads.

    GPT-5.4 sometimes sends full arguments on output_item.added/done instead of only
    streaming them via response.function_call_arguments.delta. Keep compatibility
    with both shapes.
    """
    item_id = item.get("id", "")
    call_id = item.get("call_id") or item_id
    name = item.get("name") or (pending or {}).get("name", "")

    pending_args = (pending or {}).get("arguments", "")
    item_args = item.get("arguments")
    if item_args in (None, ""):
        arguments = pending_args
    elif pending_args:
        arguments = pending_args
    else:
        arguments = item_args

    return {
        "id": (pending or {}).get("id") or call_id,
        "name": name,
        "arguments": _parse_maybe_json(arguments),
    }


_BUILTIN_OPENAI_TOOL_CACHE: Optional[dict[str, dict[str, Any]]] = None


def _get_builtin_openai_tool_schema(name: str) -> Optional[dict[str, Any]]:
    """Best-effort lookup for Nimbus builtin tool schema by name."""
    global _BUILTIN_OPENAI_TOOL_CACHE
    if _BUILTIN_OPENAI_TOOL_CACHE is None:
        try:
            from nimbus.core.agent import _register_default_tools
            from nimbus.core.tools.registry import ToolRegistry

            registry = ToolRegistry()
            _register_default_tools(registry)
            cache: dict[str, dict[str, Any]] = {}
            for schema in registry.get_schemas(format="openai"):
                func = schema.get("function", {})
                name_key = func.get("name")
                if name_key:
                    cache[name_key] = schema
            _BUILTIN_OPENAI_TOOL_CACHE = cache
        except Exception:
            _BUILTIN_OPENAI_TOOL_CACHE = {}
    schema = _BUILTIN_OPENAI_TOOL_CACHE.get(name) if _BUILTIN_OPENAI_TOOL_CACHE else None
    return schema.copy() if schema else None


def _codex_event_summary(event_type: str, data: dict[str, Any]) -> str:
    """Compact info-level summary for Codex SSE debugging."""
    item = data.get("item", {}) if isinstance(data, dict) else {}
    if isinstance(item, dict) and item.get("type") == "function_call":
        args = item.get("arguments")
        if isinstance(args, str):
            args_desc = f"str:{len(args)}"
        elif isinstance(args, dict):
            args_desc = f"dict:{','.join(sorted(args.keys()))}"
        elif args is None:
            args_desc = "none"
        else:
            args_desc = type(args).__name__
        return (
            f"event={event_type} item.type=function_call id={item.get('id','')} "
            f"call_id={item.get('call_id','')} name={item.get('name','')} args={args_desc}"
        )
    if event_type.startswith("response.function_call"):
        args = data.get("arguments") if isinstance(data, dict) else None
        delta = data.get("delta") if isinstance(data, dict) else None
        return (
            f"event={event_type} item_id={data.get('item_id','')} call_id={data.get('call_id','')} "
            f"has_args={args not in (None, '')} has_delta={delta not in (None, '')}"
        )
    return f"event={event_type}"


def _classify_llm_exception(exc: Exception) -> Fault:
    """Map transient network/LLM upstream errors to retryable Fault."""
    msg = str(exc)
    root = exc.__cause__ or exc
    root_type = type(root).__name__

    network_types = (
        asyncio.TimeoutError,
        TimeoutError,
        socket.timeout,
        ConnectionError,
        ssl.SSLError,
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.NetworkError,
        httpx.ProtocolError,
        httpx.RemoteProtocolError,
        httpx.ProxyError,
    )

    retryable = isinstance(exc, network_types) or isinstance(root, network_types)
    domain = "NETWORK" if retryable else "LLM"
    code = "TIMEOUT" if isinstance(exc, (asyncio.TimeoutError, TimeoutError, socket.timeout, httpx.TimeoutException)) else "SYSTEM_ERROR"

    # HTTP status based transient classification (5xx / 429)
    response = getattr(exc, "response", None) or getattr(root, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        if status_code >= 500 or status_code == 429:
            retryable = True
            domain = "NETWORK"
        else:
            domain = "LLM"

    # LiteLLM-specific transient errors (server disconnect, mid-stream fallback, etc.)
    # These are NOT subclasses of standard network exceptions but are still retryable.
    _LITELLM_RETRYABLE_RE = re.compile(
        r"server.?disconnect|MidStreamFallback|ServiceUnavailable|"
        r"APIConnectionError|overloaded|rate.?limit|too many requests|"
        r"429|500|502|503|504|service.?unavailable|connection.?error|"
        r"connection.?refused|other side closed|fetch failed|upstream|"
        r"reset before headers|terminated|retry delay",
        re.IGNORECASE,
    )
    if not retryable and _LITELLM_RETRYABLE_RE.search(msg):
        retryable = True
        domain = "NETWORK"

    return Fault(
        domain=domain,
        code=code,
        message=f"LLM call failed: {msg}",
        retryable=retryable,
        context={
            "exception_type": type(exc).__name__,
            "root_exception_type": root_type,
            "status_code": status_code,
            "error": msg,
        },
    )


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
            
            # Remove data URL prefix if it exists in the raw base64 data
            if data.startswith("data:"):
                # format is usually data:image/jpeg;base64,/9j/4AA...
                parts = data.split(",", 1)
                if len(parts) == 2:
                    data = parts[1]
            
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
            if t.get("type") == "function" and "function" in t:
                result.append(t)
            elif "function" in t:
                if "type" not in t:
                    t["type"] = "function"
                result.append(t)
            else:
                # Simplified format -> OpenAI format. Accept input_schema as an
                # alias for parameters (anthropic-shaped tools) so a format
                # mismatch never silently ships an empty schema.
                result.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name"),
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters") or t.get("input_schema") or {},
                        "strict": t.get("strict", True),
                    }
                })
        return result

    async def chat(
        self,
        mmu: Any,
        tools: Optional[List[Dict[str, Any]]] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> VcpuLLMResponse:
        """
        Non-streaming chat (simulated via stream to support on_chunk).
        """
        full_content = []
        collected_tool_calls = []
        collected_usage = None  # TokenUsage from stream

        try:
            async for event in self.stream(mmu, tools):
                if event.type == "text":
                    text = event.text
                    full_content.append(text)
                    if on_chunk:
                        on_chunk(text)
                elif event.type == "tool_call" and event.tool_call:
                    collected_tool_calls.append(event.tool_call)
                elif event.type == "usage" and event.usage:
                    logger.info("[chat] Received usage event: %s", event.usage)
                    # Build TokenUsage from stream usage data (pi-style)
                    u = event.usage
                    collected_usage = TokenUsage(
                        input=u.get("input", 0),
                        output=u.get("output", 0),
                        cache_read=u.get("cache_read", 0),
                        cache_write=u.get("cache_write", 0),
                        total=u.get("total", 0),
                    )
                    # Compute cost if model pricing is available
                    model_key = self._model
                    if '/' in model_key:
                        model_key = model_key.split('/', 1)[1]
                    info = ModelRegistry.get(model_key)
                    if info and hasattr(info, 'cost_per_million'):
                        collected_usage.compute_cost(info.cost_per_million)
                elif event.type == "error":
                     logger.error(f"Stream error: {event.error}")
                     # Classify it properly so VCPU can catch and retry it via StateErrorRecovery
                     raise _classify_llm_exception(RuntimeError(str(event.error)))

        except Exception as e:
            logger.error(f"DirectAdapter chat failed: {e}")
            if isinstance(e, Fault):
                raise
            raise _classify_llm_exception(e)

        content = "".join(full_content)

        # If the model inlined tool-call JSON into its prose (gemma/ollama text
        # protocol), strip it so the JSON doesn't leak into the stored message
        # and the model's own next-turn context. No-op for providers whose tool
        # calls arrive structured (Anthropic/Codex) — their prose has no JSON.
        if collected_tool_calls and content:
            content = _strip_tool_call_blocks(content)

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

        logger.debug("chat() returning: content_len=%d tool_calls=%d usage=%s",
                     len(content), len(tool_calls),
                     collected_usage.to_dict() if collected_usage else None)

        return VcpuLLMResponse(
            content=content if content else None,
            tool_calls=tool_calls if tool_calls else None,
            usage=collected_usage,
        )

    # ------------------------------------------------------------------
    # Stream router
    # ------------------------------------------------------------------

    async def stream(
        self,
        mmu: Any,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """
        Stream response — routes to Anthropic native or LiteLLM.
        Assembles context Just-In-Time from the MMU, or accepts raw messages list.
        """
        # Just-In-Time Context Assembly & Compaction
        if isinstance(mmu, list):
            messages = mmu
        else:
            compact_on_limit = getattr(mmu.config, "compact_on_limit", False) if hasattr(mmu, "config") else False
            messages = mmu.assemble_context(compact_on_limit=compact_on_limit)
        
        # Stream Routing
        # Sidecar-served models must use LiteLLM with base_url — the routed
        # model name ("anthropic/...", "openai-codex/...") would otherwise
        # string-match the native channel checks below.
        if self.config.via_sidecar:
            streamer = self._stream_litellm(messages, tools)
        elif self._is_anthropic_model() and self._anthropic_auth is not None:
            streamer = self._stream_anthropic_native(messages, tools)
        elif self._is_openai_codex_model() and self._codex_auth is not None:
            streamer = self._stream_openai_native(messages, tools)
        else:
            streamer = self._stream_litellm(messages, tools)

        # Intercept and sanitize stream
        buffer = ""
        suppressed = False
        
        # We need the hallucination patterns. We can get them from decoder or hardcode for now.
        # Often models hallucinate `[Calling` or `[Tool:` etc.
        patterns = [
            "[Called", "[Calling", "[Tool:", "[Execute:", 
            "```tool", "<tool_call>", "<function_call>"
        ]

        async for event in streamer:
            if event.type == "text" and event.text:
                if not suppressed:
                    buffer += event.text
                    # Check if buffer contains any hallucination pattern
                    for pattern in patterns:
                        if pattern in buffer:
                            suppressed = True
                            logger.warning(
                                f"🛡️ Adapter hallucination firewall: Suppressing output containing '{pattern}'"
                            )
                            break
                    
                    if not suppressed:
                        yield event
                else:
                    # Output is suppressed, filter this text chunk
                    continue
            else:
                # Yield non-text events directly
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
                        "input_schema", tool.get("parameters", {"type": "object", "properties": {}})
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

        # Rule 2a: Remove orphan tool_use (no matching tool_result)
        orphan_use_ids = tool_use_ids - tool_result_ids
        if orphan_use_ids:
            logger.warning(
                "Context validation: Found %d orphan tool_use IDs, removing: %s",
                len(orphan_use_ids),
                orphan_use_ids,
            )
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
                                and block.get("id") in orphan_use_ids
                            )
                        ]
                        if not msg["content"]:
                            msg["_remove"] = True

            messages = [m for m in messages if not m.get("_remove")]

        # Rule 2b: Remove orphan tool_result (no matching tool_use)
        # This catches cases where error recovery or compaction left behind
        # a tool_result whose corresponding assistant tool_use was removed.
        orphan_result_ids = tool_result_ids - tool_use_ids
        if orphan_result_ids:
            logger.warning(
                "Context validation: Found %d orphan tool_result IDs, removing: %s",
                len(orphan_result_ids),
                orphan_result_ids,
            )
            for msg in messages:
                if msg.get("role") == "user":
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        msg["content"] = [
                            block
                            for block in content
                            if not (
                                isinstance(block, dict)
                                and block.get("type") == "tool_result"
                                and block.get("tool_use_id") in orphan_result_ids
                            )
                        ]
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
            CLAUDE_CODE_SYSTEM_PREFIX,
            STEALTH_HEADERS,
            check_and_refresh,
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
            # Usage accumulator (pi-style: message_start + message_delta)
            usage_data: dict = {
                "input": 0, "output": 0,
                "cache_read": 0, "cache_write": 0, "total": 0,
            }

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

                    # Pi-style usage parsing from message_start
                    if event.type == "message_start":
                        msg_usage = getattr(event, 'message', None)
                        if msg_usage:
                            msg_usage = getattr(msg_usage, 'usage', None)
                        if msg_usage:
                            usage_data["input"] = getattr(msg_usage, 'input_tokens', 0) or 0
                            usage_data["output"] = getattr(msg_usage, 'output_tokens', 0) or 0
                            usage_data["cache_read"] = getattr(msg_usage, 'cache_read_input_tokens', 0) or 0
                            usage_data["cache_write"] = getattr(msg_usage, 'cache_creation_input_tokens', 0) or 0
                    elif event.type == "content_block_start":
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
                    # Pi-style usage parsing from message_delta (final output count)
                    elif event.type == "message_delta":
                        delta_usage = getattr(event, 'usage', None)
                        if delta_usage:
                            out = getattr(delta_usage, 'output_tokens', None)
                            if out is not None:
                                usage_data["output"] = out
                            inp = getattr(delta_usage, 'input_tokens', None)
                            if inp is not None:
                                usage_data["input"] = inp
                            cr = getattr(delta_usage, 'cache_read_input_tokens', None)
                            if cr is not None:
                                usage_data["cache_read"] = cr
                            cw = getattr(delta_usage, 'cache_creation_input_tokens', None)
                            if cw is not None:
                                usage_data["cache_write"] = cw
                    elif event.type == "message_stop":
                        # Emit usage event before stop (pi-style)
                        usage_data["total"] = (
                            usage_data["input"] + usage_data["output"]
                            + usage_data["cache_read"] + usage_data["cache_write"]
                        )
                        if usage_data["total"] > 0:
                            yield LLMStreamEvent(type="usage", usage=usage_data)
                        yield LLMStreamEvent(type="stop", reason="stop")

            total = time.monotonic() - t_start
            logger.info(
                "[Anthropic] model=%s TTFB=%.1fs total=%.1fs chunks=%d usage=%s",
                model_id, ttfb if ttfb_logged else total, total, chunk_count,
                usage_data if usage_data["total"] > 0 else "N/A",
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
                parameters = func.get("parameters", {})
                if not parameters and func.get("name"):
                    fallback = _get_builtin_openai_tool_schema(func.get("name", ""))
                    if fallback:
                        fallback_func = fallback.get("function", {})
                        parameters = fallback_func.get("parameters", parameters)
                        func = {
                            **fallback_func,
                            **func,
                            "parameters": parameters,
                        }
                result.append({
                    "type": "function",
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": parameters,
                })
            elif "name" in tool:
                # Nimbus simplified format
                parameters = tool.get("parameters", {})
                if not parameters and tool.get("name"):
                    fallback = _get_builtin_openai_tool_schema(tool.get("name", ""))
                    if fallback:
                        fallback_func = fallback.get("function", {})
                        parameters = fallback_func.get("parameters", parameters)
                        tool = {
                            **fallback_func,
                            **tool,
                            "parameters": parameters,
                        }
                result.append({
                    "type": "function",
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": parameters,
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
                        if event_type.startswith("response.output_item") or event_type.startswith("response.function_call"):
                            logger.info("[CodexSSE] %s", _codex_event_summary(event_type, data))

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
                                    "arguments": item.get("arguments", ""),
                                }
                                if item.get("arguments") not in (None, ""):
                                    logger.info(
                                        "[Codex] function_call added with inline arguments: id=%s name=%s",
                                        call_id,
                                        item.get("name", ""),
                                    )

                        elif event_type == "response.function_call_arguments.delta":
                            item_id = data.get("item_id", data.get("call_id", ""))
                            if item_id in pending_calls:
                                pending_calls[item_id]["arguments"] += data.get(
                                    "delta", ""
                                )

                        elif event_type == "response.function_call_arguments.done":
                            item_id = data.get("item_id", data.get("call_id", ""))
                            if item_id in pending_calls and data.get("arguments") not in (None, ""):
                                pending_calls[item_id]["arguments"] = data.get("arguments", "")

                        elif event_type == "response.output_item.done":
                            item = data.get("item", {})
                            if item.get("type") == "function_call":
                                item_id = item.get("id", "")
                                merged = _merge_codex_function_call_item(
                                    pending_calls.pop(item_id, None),
                                    item,
                                )
                                yield LLMStreamEvent(
                                    type="tool_call",
                                    tool_call=merged,
                                )

                        elif event_type.startswith("response.function_call"):
                            logger.info(
                                "[Codex] Unhandled function-call event=%s payload=%s",
                                event_type,
                                data_str[:500],
                            )

                        elif event_type == "response.completed":
                            # Pi-style usage parsing from response.completed
                            resp_data = data.get("response", data)
                            resp_usage = resp_data.get("usage", {})
                            if resp_usage:
                                cached = 0
                                details = resp_usage.get("input_tokens_details", {})
                                if details:
                                    cached = details.get("cached_tokens", 0) or 0
                                usage_dict = {
                                    "input": (resp_usage.get("input_tokens", 0) or 0) - cached,
                                    "output": resp_usage.get("output_tokens", 0) or 0,
                                    "cache_read": cached,
                                    "cache_write": 0,
                                    "total": resp_usage.get("total_tokens", 0) or 0,
                                }
                                if usage_dict["total"] > 0:
                                    yield LLMStreamEvent(type="usage", usage=usage_dict)
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
        import re

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
            elif info.provider == "ollama":
                model = f"ollama/{info.model_id}"
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

        # Ollama tool-calling must go through the /api/chat endpoint. LiteLLM's
        # `ollama/` provider hits /api/generate, which drops native tool_calls in
        # streaming mode — they leak as plain text and then collapse to an empty
        # decode. `ollama_chat/` streams native tool_calls correctly, the same
        # path every other provider uses, so the gemma4 text-extraction
        # workaround below stays inert.
        if model.startswith("ollama/"):
            model = "ollama_chat/" + model[len("ollama/"):]

        # Clean messages and convert image blocks to OpenAI format
        clean_messages = []
        for m in messages:
            msg = m.copy()
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # LiteLLM's Gemini/other transformers expect tool_call arguments as JSON strings, not dicts
                tool_calls = []
                for tc in msg["tool_calls"]:
                    tc_copy = tc.copy()
                    if "function" in tc_copy:
                        func = tc_copy["function"].copy()
                        if isinstance(func.get("arguments"), dict):
                            func["arguments"] = json.dumps(func["arguments"])
                        tc_copy["function"] = func
                    tool_calls.append(tc_copy)
                msg["tool_calls"] = tool_calls

            if msg.get("content") is None:
                msg["content"] = ""
            elif isinstance(msg.get("content"), list):
                msg["content"] = self._convert_content_blocks(
                    msg["content"], "openai"
                )
            clean_messages.append(msg)

        def _request_payload_for_model(
            current_model: str,
        ) -> tuple[list[dict], Optional[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]]]:
            # Gemma4 (and other ollama models) now stream native tool_calls via
            # the ollama_chat/ endpoint, so provider tools are passed like any
            # other model. Any JSON-as-text a weak model still leaks is caught by
            # the text-extraction fallback below.
            return clean_messages, openai_tools, openai_tools

        try:
            t_start = time.monotonic()
            ttfb_logged = False
            chunk_count = 0
            active_tools_for_response = None
            
            # Rate limit retry logic
            current_model = model
            try:
                # Pass api_base for Ollama/local providers
                request_messages, request_tools, logical_tools = _request_payload_for_model(current_model)
                active_tools_for_response = logical_tools
                acompletion_kwargs = dict(
                    model=current_model,
                    messages=request_messages,
                    tools=request_tools,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                if self.config.base_url:
                    acompletion_kwargs["api_base"] = self.config.base_url
                # Disable thinking mode for ollama models (e.g. qwen3.5)
                # to avoid empty responses where all content goes to reasoning_content
                # Use reasoning_effort="none" instead of think=False because litellm's
                # supported params list includes "reasoning_effort" but not "think".
                # litellm maps reasoning_effort="none" to think=False for ollama models.
                if current_model.startswith(("ollama/", "ollama_chat/")):
                    acompletion_kwargs["reasoning_effort"] = "none"
                response = await acompletion(**acompletion_kwargs)
            except Exception as e:
                # Check for rate limit (429) or resource exhausted
                err_str = str(e).lower()
                is_rate_limit = "429" in err_str or "resource exhausted" in err_str or "rate limit" in err_str
                
                if is_rate_limit:
                    fallback = ModelRegistry.get_same_provider_fallback(self._model)
                    if fallback:
                        # Normalize fallback model name for LiteLLM
                        fallback_info = ModelRegistry.get(fallback)
                        if fallback_info:
                            if fallback_info.provider == "google":
                                fallback_model = f"gemini/{fallback_info.model_id}"
                            elif fallback_info.provider == "anthropic":
                                fallback_model = f"anthropic/{fallback_info.model_id}"
                            else:
                                fallback_model = fallback_info.model_id
                        else:
                            fallback_model = fallback

                        logger.warning(
                            "Rate limit hit on %s. Falling back to %s", 
                            current_model, fallback_model
                        )
                        
                        # Retry with fallback
                        current_model = fallback_model
                        request_messages, request_tools, logical_tools = _request_payload_for_model(current_model)
                        active_tools_for_response = logical_tools
                        acompletion_kwargs = dict(
                            model=current_model,
                            messages=request_messages,
                            tools=request_tools,
                            temperature=self.config.temperature,
                            max_tokens=self.config.max_tokens,
                            stream=True,
                            stream_options={"include_usage": True},
                        )
                        if self.config.base_url:
                            acompletion_kwargs["api_base"] = self.config.base_url
                        # Disable thinking mode for ollama models (e.g. qwen3.5)
                        # Use reasoning_effort="none" instead of think=False because litellm's
                        # supported params list includes "reasoning_effort" but not "think".
                        if current_model.startswith(("ollama/", "ollama_chat/")):
                            acompletion_kwargs["reasoning_effort"] = "none"
                        response = await acompletion(**acompletion_kwargs)
                    else:
                        raise e
                else:
                    raise e

            tool_call_chunks = {}
            reasoning_chunks = []
            text_buffer = []
            text_streamed = False  # Track if we've yielded text chunks in real-time
            buffering_json_text = False
            embedded_text_tool_calls: list[dict] = []
            buffering_fenced_tool_text = False
            fenced_tool_buffer = ""
            buffering_labeled_tool_text = False
            labeled_tool_buffer = ""
            buffering_brace_tool_text = False
            brace_tool_buffer = ""
            fence_probe = ""
            tool_schema_names = _tool_names_from_openai_tools(active_tools_for_response)
            # ollama models are rewritten to the ollama_chat/ endpoint above, so
            # match both prefixes: the fallback stays live even though the model
            # string no longer starts with plain "ollama/".
            gemma_text_tool_extract = (
                current_model.startswith(("ollama/gemma4", "ollama_chat/gemma4"))
                and active_tools_for_response is not None
            )
            last_usage = None  # Last chunk's usage data (LiteLLM/OpenAI)

            async for chunk in response:
                chunk_count += 1
                if not ttfb_logged:
                    ttfb = time.monotonic() - t_start
                    logger.info("[LiteLLM] model=%s TTFB=%.1fs", current_model, ttfb)
                    ttfb_logged = True

                delta = chunk.choices[0].delta

                # Track usage from streaming chunks
                # LiteLLM with stream_options={"include_usage": True} sends
                # a final chunk with usage data; also check model_extra (pydantic v2)
                chunk_usage = getattr(chunk, 'usage', None)
                if not chunk_usage and hasattr(chunk, 'model_extra'):
                    chunk_usage = (chunk.model_extra or {}).get('usage', None)
                if chunk_usage:
                    last_usage = chunk_usage
                    logger.info("[LiteLLM] chunk#%d has usage: %s", chunk_count, chunk_usage)

                if delta.content:
                    text_buffer.append(delta.content)
                    if gemma_text_tool_extract and not buffering_json_text:
                        candidate = "".join(text_buffer).lstrip()
                        if not text_streamed and candidate.startswith(("{", "[")):
                            buffering_json_text = True
                            continue

                        pending = delta.content
                        while True:
                            if buffering_fenced_tool_text:
                                fenced_tool_buffer += pending
                                pending = ""
                                close_idx = _find_closing_fence(fenced_tool_buffer)
                                if close_idx is None:
                                    break

                                block = fenced_tool_buffer[:close_idx + 3]
                                rest = fenced_tool_buffer[close_idx + 3:]
                                extracted = _filter_tool_calls_for_schemas(
                                    _extract_tool_calls_from_text(block),
                                    tool_schema_names,
                                )
                                if extracted:
                                    embedded_text_tool_calls.extend(extracted)
                                else:
                                    text_streamed = True
                                    yield LLMStreamEvent(type="text", text=block)

                                buffering_fenced_tool_text = False
                                fenced_tool_buffer = ""
                                pending = rest
                                if not pending:
                                    break
                                continue

                            if buffering_brace_tool_text:
                                brace_tool_buffer += pending
                                pending = ""
                                close_idx = _find_closing_brace(brace_tool_buffer)
                                if close_idx is None:
                                    break
                                block = brace_tool_buffer[:close_idx + 1]
                                rest = brace_tool_buffer[close_idx + 1:]
                                extracted = _filter_tool_calls_for_schemas(
                                    _extract_tool_calls_from_text(block),
                                    tool_schema_names,
                                )
                                if extracted:
                                    embedded_text_tool_calls.extend(extracted)
                                else:
                                    # Not a tool call — it was just prose with braces.
                                    text_streamed = True
                                    yield LLMStreamEvent(type="text", text=block)
                                buffering_brace_tool_text = False
                                brace_tool_buffer = ""
                                pending = rest
                                if not pending:
                                    break
                                continue

                            if buffering_labeled_tool_text:
                                labeled_tool_buffer += pending
                                break

                            if fence_probe:
                                pending = fence_probe + pending
                                fence_probe = ""

                            marker_idx = _find_tool_call_text_marker(pending)
                            fence_idx = pending.find("```")
                            if marker_idx is not None and (fence_idx < 0 or marker_idx < fence_idx):
                                prefix = pending[:marker_idx]
                                if prefix:
                                    text_streamed = True
                                    yield LLMStreamEvent(type="text", text=prefix)
                                buffering_labeled_tool_text = True
                                labeled_tool_buffer = pending[marker_idx:]
                                break

                            if fence_idx >= 0:
                                prefix = pending[:fence_idx]
                                if prefix:
                                    text_streamed = True
                                    yield LLMStreamEvent(type="text", text=prefix)
                                buffering_fenced_tool_text = True
                                fenced_tool_buffer = pending[fence_idx:]
                                pending = ""
                                continue

                            # Bare inlined JSON tool call after prose ("…path.\n\n{\"name\":…").
                            # Buffer from the brace so the JSON never streams as text.
                            brace_match = _BRACE_TOOL_START_RE.search(pending)
                            if brace_match:
                                bidx = brace_match.start()
                                prefix = pending[:bidx]
                                if prefix:
                                    text_streamed = True
                                    yield LLMStreamEvent(type="text", text=prefix)
                                buffering_brace_tool_text = True
                                brace_tool_buffer = pending[bidx:]
                                pending = ""
                                continue

                            backtick_tail_len = 2 if pending.endswith("``") else 1 if pending.endswith("`") else 0
                            marker_tail_len = _tool_call_marker_suffix_len(pending)
                            # Hold a trailing bare '{'/'[' so a cross-delta '{"' is caught next chunk.
                            brace_tail_len = 1 if pending.endswith(("{", "[")) else 0
                            tail_len = max(backtick_tail_len, marker_tail_len, brace_tail_len)
                            if tail_len:
                                emit_text = pending[:-tail_len]
                                fence_probe = pending[-tail_len:]
                            else:
                                emit_text = pending
                            if emit_text:
                                text_streamed = True
                                yield LLMStreamEvent(type="text", text=emit_text)
                            break
                        continue

                    # Yield text chunks immediately for real-time streaming.
                    # Heuristic: if the accumulated text starts like JSON, buffer
                    # the whole response for tool-call extraction. Gemma4/Ollama
                    # can stream JSON tool calls as content split like '{"',
                    # 'name', ...; checking only the current delta drops the
                    # opening brace and prevents extraction.
                    if buffering_json_text:
                        pass
                    elif text_streamed:
                        yield LLMStreamEvent(type="text", text=delta.content)
                    else:
                        candidate = "".join(text_buffer).lstrip()
                        if candidate.startswith(("{", "[")):
                            buffering_json_text = True
                        elif candidate:
                            text_streamed = True
                            yield LLMStreamEvent(type="text", text="".join(text_buffer))

                # Capture reasoning_content as fallback (qwen3.5/deepseek thinking mode)
                reasoning = getattr(delta, 'reasoning_content', None)
                if reasoning:
                    reasoning_chunks.append(reasoning)

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

                reason = chunk.choices[0].finish_reason
                if reason == "malformed_function_call":
                    # The LLM tried to call a non-existent tool or bad schema natively. 
                    # Instead of returning empty (which causes infinite retry loops), yield a fake text response.
                    error_msg = getattr(chunk.choices[0], 'finishMessage', 'malformed_function_call')
                    logger.warning(f"[LiteLLM] Intercepted malformed function call: {error_msg}")
                    yield LLMStreamEvent(type="text", text=f"\n\n[System Error: Native tool call failed ({error_msg}). Please try again using only the exact tool names provided in the schema, such as 'Bash' or 'Read'.]\n")
                    yield LLMStreamEvent(type="stop", reason="stop")
                    return
                    
                if reason and reason not in ("stop", "length", "tool_calls", "function_call", "max_tokens", "content_filter"):
                    logger.error(f"[LiteLLM] Abnormal finish reason: {reason} (model={current_model})")
                    yield LLMStreamEvent(type="error", error=f"LLM stopped abruptly with reason: {reason}")
                    return

            if buffering_fenced_tool_text:
                extracted = _filter_tool_calls_for_schemas(
                    _extract_tool_calls_from_text(fenced_tool_buffer),
                    tool_schema_names,
                )
                if extracted:
                    embedded_text_tool_calls.extend(extracted)
                else:
                    text_streamed = True
                    yield LLMStreamEvent(type="text", text=fenced_tool_buffer)
                buffering_fenced_tool_text = False
                fenced_tool_buffer = ""
            elif buffering_labeled_tool_text:
                extracted = _filter_tool_calls_for_schemas(
                    _extract_tool_calls_from_text(labeled_tool_buffer),
                    tool_schema_names,
                )
                if extracted:
                    embedded_text_tool_calls.extend(extracted)
                else:
                    text_streamed = True
                    yield LLMStreamEvent(type="text", text=labeled_tool_buffer)
                buffering_labeled_tool_text = False
                labeled_tool_buffer = ""
            elif fence_probe:
                text_streamed = True
                yield LLMStreamEvent(type="text", text=fence_probe)
                fence_probe = ""

            total = time.monotonic() - t_start
            logger.info(
                "[LiteLLM] model=%s TTFB=%.1fs total=%.1fs chunks=%d",
                current_model, ttfb if ttfb_logged else total, total, chunk_count,
            )

            # 1. Yield extracted JSON tool calls or the raw text
            full_text = "".join(text_buffer)
            # Diagnostic (debug — fires on every text-protocol turn): the raw
            # text when no native tool call was captured. Confirmed empty-decode
            # turns show raw_text='' (genuine empty generation, nothing to extract).
            if not tool_call_chunks and not embedded_text_tool_calls:
                logger.debug(
                    "[LiteLLM] no native tool_call. raw_text=%r reasoning_len=%d text_streamed=%s",
                    full_text[:600], sum(len(r) for r in reasoning_chunks), text_streamed,
                )
            if full_text.strip():
                content_to_check = full_text.strip()
                gemma_post_tool_without_schemas = (
                    current_model.startswith(("ollama/gemma4", "ollama_chat/gemma4"))
                    and active_tools_for_response is None
                    and any(msg.get("role") == "tool" for msg in clean_messages)
                )
                should_extract_text_tools = (
                    not gemma_post_tool_without_schemas
                    and (
                        not text_streamed
                        or (
                            current_model.startswith(("ollama/", "ollama_chat/"))
                            and active_tools_for_response is not None
                        )
                    )
                )
                extracted_tcs = None
                if embedded_text_tool_calls:
                    extracted_tcs = embedded_text_tool_calls
                elif should_extract_text_tools:
                    extracted_tcs = _extract_tool_calls_from_text(content_to_check)
                    if (
                        extracted_tcs
                        and current_model.startswith(("ollama/gemma4", "ollama_chat/gemma4"))
                        and active_tools_for_response is not None
                    ):
                        extracted_tcs = _filter_tool_calls_for_schemas(
                            extracted_tcs,
                            tool_schema_names,
                        )

                if extracted_tcs:
                    logger.info("[LiteLLM] Intercepted %d JSON tool call(s) from text stream.", len(extracted_tcs))
                    for i, tc in enumerate(extracted_tcs):
                        yield LLMStreamEvent(
                            type="tool_call",
                            tool_call={
                                # Unique per call — `i` resets to 0 each step, so a
                                # bare index collides across steps in a turn and makes
                                # the UI merge separate tool cards (lost ordering).
                                "id": f"json_extract_txt_{i}_{uuid.uuid4().hex[:8]}",
                                "name": tc["name"],
                                "arguments": tc["arguments"]
                            }
                        )
                elif not text_streamed:
                    # Guard: small models sometimes output {"error": "..."} as text
                    handled_error = False
                    if content_to_check.startswith('{') or content_to_check.startswith('['):
                        try:
                            parsed_obj = json.loads(content_to_check)
                            if isinstance(parsed_obj, dict) and "error" in parsed_obj and len(parsed_obj) <= 3:
                                logger.warning(
                                    "[LiteLLM] Suppressed JSON error text (small model retry signal): %s",
                                    content_to_check[:200],
                                )
                                handled_error = True
                            elif (
                                isinstance(parsed_obj, dict)
                                and str(parsed_obj.get("name", "")).lower() in {"none", "null", "no_tool"}
                            ):
                                logger.warning(
                                    "[LiteLLM] Suppressed JSON no-op tool text: %s",
                                    content_to_check[:200],
                                )
                                handled_error = True
                        except (json.JSONDecodeError, ValueError):
                            pass

                    if not handled_error:
                        yield LLMStreamEvent(type="text", text=full_text)

            # 2. Fallback: if no text or tool_calls, but we got reasoning_content
            if not full_text and not tool_call_chunks and reasoning_chunks:
                full_reasoning = "".join(reasoning_chunks)
                logger.warning(
                    "[LiteLLM] model=%s produced only reasoning_content (%d chars), "
                    "no content/tool_calls. Using reasoning as fallback content. "
                    "Consider disabling thinking mode.",
                    current_model, len(full_reasoning)
                )
                
                # Check if the fallback reasoning content is actually a JSON tool call (Ollama specific leak fix)
                content_to_check = full_reasoning.strip()
                extracted_tcs = _extract_tool_calls_from_text(content_to_check)
                if extracted_tcs:
                    logger.info("[LiteLLM] Extracted %d JSON tool call(s) from Ollama reasoning fallback text.", len(extracted_tcs))
                    # Yield proper tool call events instead of text
                    for i, tc in enumerate(extracted_tcs):
                        yield LLMStreamEvent(
                            type="tool_call",
                            tool_call={
                                "id": f"json_extract_reas_{i}_{uuid.uuid4().hex[:8]}",
                                "name": tc["name"],
                                "arguments": tc["arguments"]
                            }
                        )
                    yield LLMStreamEvent(type="stop", reason="stop")
                    return

                yield LLMStreamEvent(type="text", text=full_reasoning)

            # 3. Yield any natively captured tool calls
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

            # Emit usage event BEFORE stop (pi-style, must be before stop
            # so chat() consumer sees it before ending iteration)
            if last_usage:
                logger.info("[LiteLLM] last_usage found: %s", last_usage)
                cached = 0
                prompt_details = getattr(last_usage, 'prompt_tokens_details', None)
                if prompt_details:
                    cached = getattr(prompt_details, 'cached_tokens', 0) or 0
                usage_dict = {
                    "input": (getattr(last_usage, 'prompt_tokens', 0) or 0) - cached,
                    "output": getattr(last_usage, 'completion_tokens', 0) or 0,
                    "cache_read": cached,
                    "cache_write": 0,
                    "total": getattr(last_usage, 'total_tokens', 0) or 0,
                }
                if usage_dict["total"] > 0:
                    yield LLMStreamEvent(type="usage", usage=usage_dict)

            yield LLMStreamEvent(type="stop", reason="stop")

        except asyncio.CancelledError:
            logger.info("LiteLLM streaming task cancelled by user")
            # Force close the underlying aiohttp/httpx response to stop downloading
            if response is not None:
                if hasattr(response, "close"):
                    if asyncio.iscoroutinefunction(response.close):
                        await response.close()
                    else:
                        response.close()
                elif hasattr(response, "response") and hasattr(response.response, "aclose"):
                    await response.response.aclose()
            raise
        except Exception as e:
            logger.error(f"LiteLLM error: {e}")
            yield LLMStreamEvent(type="error", error=str(e))

    async def list_models(self) -> List[Dict[str, str]]:
        """List available models from ModelRegistry (single source of truth)."""
        from nimbus.core.models.registry import ModelRegistry

        return [
            {"id": info.full_name, "object": "model", "owned_by": info.provider}
            for info in ModelRegistry.list_models()
        ]
