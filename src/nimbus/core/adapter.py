"""
LLM Adapter — The ALU (Arithmetic Logic Unit) of the vCPU.

Translates between the VCPU's simple chat(messages, tools) interface
and the actual LLM API. Handles:
- OpenAI-compatible API calls (covers OpenAI, Anthropic via proxy, local models)
- Response normalization into a simple dataclass
- Token usage tracking
- Retry on transient errors

Why not just call the API directly in the VCPU?
Because the VCPU shouldn't know or care which LLM provider it's talking to.
The Adapter is a clean abstraction boundary.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from .protocol import Fault


# =============================================================================
# Response Model
# =============================================================================


@dataclass
class LLMResponse:
    """Normalized LLM response for the VCPU."""
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Dict[str, int] = field(default_factory=dict)  # prompt_tokens, completion_tokens


# =============================================================================
# Adapter for OpenAI-compatible APIs
# =============================================================================


@dataclass
class AdapterConfig:
    model: str = "gpt-4o"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 4096


class OpenAIAdapter:
    """LLM adapter for any OpenAI-compatible API.

    Works with: OpenAI, Azure OpenAI, Ollama, LM Studio, vLLM,
    and any other service that implements the chat completions API.

    Usage:
        adapter = OpenAIAdapter(AdapterConfig(model="gpt-4o"))
        response = await adapter.chat(messages, tools)
    """

    def __init__(self, config: Optional[AdapterConfig] = None):
        self.config = config or AdapterConfig()
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError:
                raise ImportError("openai package required: pip install openai")

            kwargs = {}
            api_key = self.config.api_key or os.environ.get("OPENAI_API_KEY")
            if api_key:
                kwargs["api_key"] = api_key
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url

            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    async def chat(self, messages: List[Dict], tools: List[Dict]) -> LLMResponse:
        """Send messages to the LLM and return a normalized response.

        Args:
            messages: List of message dicts (role, content, tool_calls, etc.)
            tools: List of tool schemas in OpenAI format.

        Returns:
            LLMResponse with content and/or tool_calls.
        """
        client = self._get_client()

        # Build request kwargs
        kwargs: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:
            error_str = str(e).lower()
            if "rate_limit" in error_str or "429" in error_str:
                raise Fault(domain="LLM", code="RATE_LIMIT", message=str(e), retryable=True)
            if "context_length" in error_str or "maximum context" in error_str:
                raise Fault(domain="LLM", code="CTX_OVERFLOW", message=str(e), retryable=False)
            raise Fault(domain="LLM", code="SYSTEM_ERROR", message=str(e), retryable=True)

        # Extract the first choice
        choice = response.choices[0]
        msg = choice.message

        # Normalize tool calls
        tool_calls = None
        if msg.tool_calls:
            tool_calls = []
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })

        # Extract usage
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            usage=usage,
        )


# =============================================================================
# Anthropic Native Adapter
# =============================================================================


class AnthropicAdapter:
    """LLM adapter for Anthropic's native API.

    Uses the anthropic Python SDK directly. Handles the Anthropic-specific
    message format (system as separate param, tool_use blocks, etc.).
    """

    def __init__(self, config: Optional[AdapterConfig] = None):
        self.config = config or AdapterConfig(model="claude-sonnet-4-20250514")
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError("anthropic package required: pip install anthropic")

            api_key = self.config.api_key or os.environ.get("ANTHROPIC_API_KEY")
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        return self._client

    async def chat(self, messages: List[Dict], tools: List[Dict]) -> LLMResponse:
        """Send messages using Anthropic's native format."""
        client = self._get_client()

        # Anthropic requires system message as separate parameter
        system_text = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text += (msg.get("content") or "") + "\n"
            else:
                chat_messages.append(self._convert_message(msg))

        # Convert tools to Anthropic format
        anthropic_tools = []
        for t in tools:
            func = t.get("function", t)
            anthropic_tools.append({
                "name": func.get("name", t.get("name")),
                "description": func.get("description", t.get("description", "")),
                "input_schema": func.get("parameters", t.get("input_schema", {"type": "object", "properties": {}})),
            })

        kwargs: Dict[str, Any] = {
            "model": self.config.model,
            "messages": chat_messages,
            "max_tokens": self.config.max_tokens,
        }
        if system_text.strip():
            kwargs["system"] = system_text.strip()
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        try:
            response = await client.messages.create(**kwargs)
        except Exception as e:
            error_str = str(e).lower()
            if "rate_limit" in error_str or "429" in error_str:
                raise Fault(domain="LLM", code="RATE_LIMIT", message=str(e), retryable=True)
            if "context_length" in error_str or "too long" in error_str:
                raise Fault(domain="LLM", code="CTX_OVERFLOW", message=str(e), retryable=False)
            raise Fault(domain="LLM", code="SYSTEM_ERROR", message=str(e), retryable=True)

        # Parse Anthropic response format
        content_text = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input) if isinstance(block.input, dict) else block.input,
                    },
                })

        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            }

        return LLMResponse(
            content=content_text or None,
            tool_calls=tool_calls or None,
            usage=usage,
        )

    def _convert_message(self, msg: Dict) -> Dict:
        """Convert from OpenAI message format to Anthropic format."""
        role = msg["role"]

        if role == "tool":
            # Anthropic uses tool_result blocks inside user messages
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }],
            }

        if role == "assistant" and msg.get("tool_calls"):
            # Convert tool_calls to Anthropic tool_use blocks
            content = []
            if msg.get("content"):
                content.append({"type": "text", "text": msg["content"]})
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                args = func.get("arguments", "{}")
                content.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": func.get("name", ""),
                    "input": json.loads(args) if isinstance(args, str) else args,
                })
            return {"role": "assistant", "content": content}

        return {"role": role, "content": msg.get("content", "")}
