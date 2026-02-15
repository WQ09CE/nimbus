"""
Direct Adapter for Nimbus (LiteLLM)

Replaces Pi-AI Bridge with direct Python calls to LiteLLM.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import litellm
from litellm import acompletion
from litellm.utils import ModelResponse

from nimbus.config import get_config
from nimbus.adapters.types import LLMConfig, VcpuLLMResponse, LLMStreamEvent

logger = logging.getLogger(__name__)

# Configure LiteLLM
litellm.drop_params = True


class DirectAdapter:
    """
    Direct Adapter using LiteLLM to call LLM providers directly.
    Replaces the Node.js bridge.
    """

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig()
        self._model = self.config.get_model()
        self._ensure_api_keys()

    def _ensure_api_keys(self):
        """Ensure API keys are loaded from NimbusConfig."""
        cfg = get_config()
        if cfg.gemini_api_key and "GEMINI_API_KEY" not in os.environ:
            os.environ["GEMINI_API_KEY"] = cfg.gemini_api_key
        if cfg.gemini_api_key and "GOOGLE_API_KEY" not in os.environ:
             os.environ["GOOGLE_API_KEY"] = cfg.gemini_api_key

    async def __aenter__(self) -> "DirectAdapter":
        return self

    async def __aexit__(self, *args):
        pass

    async def start(self):
        """No-op for direct adapter."""
        pass

    async def stop(self):
        """No-op for direct adapter."""
        pass

    async def health_check(self) -> bool:
        """Simple check if we have API keys."""
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

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
        # VcpuLLMResponse expects: [{"id":..., "type": "function", "function": {"name":..., "arguments": str/dict}}]
        tool_calls = []
        for tc in collected_tool_calls:
             # Arguments from stream are dicts (parsed JSON)
             # We should probably serialize them back to string for consistency with OpenAI format
             # unless Vcpu handles dict arguments.
             # Serialize to string for OpenAI format consistency.
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

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """
        Stream response from LiteLLM.
        """
        openai_tools = self._convert_tools(tools)
        
        # Adjust model name for LiteLLM
        model = self._model
        if "gemini" in model:
             if "google/" in model:
                 # LiteLLM uses 'gemini/' prefix for Google AI Studio
                 model = model.replace("google/", "gemini/")
             elif "gemini/" not in model:
                 model = f"gemini/{model}"
        elif "claude" in model and "anthropic/" not in model:
             model = f"anthropic/{model}"

        # Clean messages
        clean_messages = []
        for m in messages:
            msg = m.copy()
            # Remove None content
            if msg.get("content") is None:
                msg["content"] = "" 
            clean_messages.append(msg)

        try:
            response = await acompletion(
                model=model,
                messages=clean_messages,
                tools=openai_tools,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                stream=True,
            )

            tool_call_chunks = {} # index -> {id, name, arguments_parts}

            async for chunk in response:
                delta = chunk.choices[0].delta
                
                # Text content
                if delta.content:
                    yield LLMStreamEvent(type="text", text=delta.content)

                # Tool calls accumulation
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
            
            # Emit accumulated tool calls
            for idx, tc_data in tool_call_chunks.items():
                # Try to parse arguments
                try:
                    args = json.loads(tc_data["arguments"])
                except:
                    args = tc_data["arguments"]

                yield LLMStreamEvent(
                    type="tool_call",
                    tool_call={
                        "id": tc_data["id"],
                        "name": tc_data["name"],
                        "arguments": args
                    }
                )

            # Done
            yield LLMStreamEvent(type="stop", reason="stop")

        except Exception as e:
            logger.error(f"LiteLLM error: {e}")
            yield LLMStreamEvent(type="error", error=str(e))

    async def list_models(self) -> List[Dict[str, str]]:
        """List available models from configured providers."""
        models = []

        # Gemini models (Google AI Studio)
        models.extend([
            {"id": "google/gemini-3-flash-preview", "object": "model", "owned_by": "google"},
            {"id": "google/gemini-3-pro-preview", "object": "model", "owned_by": "google"},
            {"id": "google/gemini-2.5-flash", "object": "model", "owned_by": "google"},
            {"id": "google/gemini-2.5-pro", "object": "model", "owned_by": "google"},
            {"id": "google/gemini-2.0-flash-exp", "object": "model", "owned_by": "google"},
            {"id": "google/gemini-1.5-pro", "object": "model", "owned_by": "google"},
            {"id": "google/gemini-1.5-flash", "object": "model", "owned_by": "google"},
        ])

        # Claude models (Anthropic)
        models.extend([
            {"id": "anthropic/claude-opus-4-6", "object": "model", "owned_by": "anthropic"},
            {"id": "anthropic/claude-sonnet-4-20250514", "object": "model", "owned_by": "anthropic"},
            {"id": "anthropic/claude-3-5-sonnet-20241022", "object": "model", "owned_by": "anthropic"},
            {"id": "anthropic/claude-3-opus-20240229", "object": "model", "owned_by": "anthropic"},
        ])

        # OpenAI models
        models.extend([
            {"id": "openai/gpt-4o", "object": "model", "owned_by": "openai"},
            {"id": "openai/gpt-4-turbo", "object": "model", "owned_by": "openai"},
            {"id": "openai/gpt-3.5-turbo", "object": "model", "owned_by": "openai"},
        ])

        return models
