"""
Pi Adapter for Nimbus vCPU

将 pi-ai HTTP 服务适配为 Nimbus 的 LLM 接口

Usage:
    from nimbus.adapters.pi_adapter import PiLLMAdapter

    llm = PiLLMAdapter()
    response = await llm.chat(messages, tools=tools)
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from nimbus.bridge.pi_ai_http import (
    Message as HttpMessage,
)
from nimbus.bridge.pi_ai_http import (
    PiAiHttpClient,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Types
# ============================================================================


@dataclass
class LLMStreamEvent:
    """LLM 流式事件"""

    type: str  # "start" | "text" | "tool_call" | "thinking" | "usage" | "stop" | "error"
    text: str = ""
    tool_call: dict | None = None
    usage: dict | None = None
    reason: str = ""
    error: str = ""


class VcpuLLMResponse:
    """Adapter response for vCPU"""

    def __init__(self, content: str | None = None, tool_calls: list | None = None):
        self._content = content
        self._tool_calls = tool_calls

    @property
    def content(self) -> str | None:
        return self._content

    @property
    def tool_calls(self) -> list | None:
        return self._tool_calls


# ============================================================================
# LLM Adapter
# ============================================================================


@dataclass
class PiLLMConfig:
    """Pi LLM 适配器配置

    可以用两种方式指定模型:
    1. model: "anthropic/claude-sonnet-4-20250514" (pi-ai 格式)
    2. provider + model_id: 分开指定 (会自动合并)
    """

    base_url: str = "http://localhost:3031"
    model: str = ""  # 完整模型名 (provider/model_id)
    provider: str = "anthropic"  # 兼容旧接口
    model_id: str = "claude-sonnet-4-20250514"  # 兼容旧接口
    max_tokens: int = 8192
    timeout: float = 300.0  # 5min, for deep-thinking models (Opus)
    temperature: Optional[float] = None
    thinking: Optional[bool] = None
    stop: Optional[List[str]] = None

    def get_model(self) -> str:
        """获取完整的模型名"""
        if self.model:
            return self.model
        return f"{self.provider}/{self.model_id}"


class PiLLMAdapter:
    """
    将 pi-ai HTTP 服务适配为 Nimbus 的 LLM 接口

    通过 HTTP 调用 pi-ai-server，获得：
    - 统一的多 provider 支持
    - 自动 OAuth token 刷新
    - 稳定的连接（无需管理子进程）
    """

    def __init__(self, config: PiLLMConfig | None = None):
        self.config = config or PiLLMConfig()
        self._model = self.config.get_model()
        self._client = PiAiHttpClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            default_model=self._model,
        )
        self._started = False

    async def __aenter__(self) -> "PiLLMAdapter":
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.stop()

    async def start(self):
        """启动适配器"""
        if not self._started:
            await self._client.start()
            self._started = True
            logger.info(f"PiLLMAdapter started, base_url={self.config.base_url}")

    async def stop(self):
        """停止适配器"""
        if self._started:
            await self._client.stop()
            self._started = False
            logger.info("PiLLMAdapter stopped")

    async def health_check(self) -> bool:
        """健康检查"""
        return await self._client.health_check()

    def _convert_messages_to_http(self, messages: List[Dict[str, Any]]) -> List[HttpMessage]:
        """将 vCPU 消息转换为 HTTP 客户端消息格式"""
        result = []

        # Anthropic API requires conversation to end with a user message.
        # If the last message is assistant (e.g. multi-thought mode), append
        # a minimal user turn. This only affects the API payload, never stored.
        if messages and messages[-1].get("role") == "assistant":
            messages = list(messages)  # shallow copy to avoid mutating caller
            messages.append({"role": "user", "content": "go on"})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")

            # Handle tool result message
            if role == "tool":
                result.append(
                    HttpMessage(
                        role="tool",
                        content=content if isinstance(content, str) else json.dumps(content),
                        tool_call_id=msg.get("tool_call_id"),
                        name=msg.get("name"),
                    )
                )
                continue

            # Handle assistant message with tool_calls
            if role == "assistant" and tool_calls:
                # 构建包含 tool_use 的内容
                assistant_content = []

                # 添加文本内容
                if content:
                    if isinstance(content, str):
                        assistant_content.append({"type": "text", "text": content})
                    elif isinstance(content, list):
                        assistant_content.extend(content)

                # 添加 tool_use blocks
                for tc in tool_calls:
                    func = tc.get("function", {})
                    args = func.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}

                    tool_block = {
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": func.get("name"),
                            "input": args,
                    }
                    # Preserve Gemini 3 thought signature for round-trip
                    if tc.get("thought_signature"):
                        tool_block["thought_signature"] = tc["thought_signature"]
                    assistant_content.append(tool_block)

                result.append(HttpMessage(role="assistant", content=assistant_content))
                continue

            # Handle regular messages
            if isinstance(content, list):
                result.append(HttpMessage(role=role, content=content))
            else:
                result.append(HttpMessage(role=role, content=content or ""))

        return result

    def _convert_tools_to_http(
        self, tools: Optional[List[Dict[str, Any]]]
    ) -> Optional[List[Dict[str, Any]]]:
        """将 vCPU 工具定义转换为 HTTP 格式（OpenAI 风格）"""
        if not tools:
            return None

        result = []
        for tool in tools:
            # 支持 OpenAI 格式和简化格式
            if tool.get("type") == "function":
                result.append(tool)
            elif "function" in tool:
                result.append(tool)
            else:
                # 简化格式，需要转换
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.get("name"),
                            "description": tool.get("description", ""),
                            "parameters": tool.get("parameters", {}),
                        },
                    }
                )

        return result

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> VcpuLLMResponse:
        """
        实现 LLMClient 协议，供 vCPU 调用

        Args:
            messages: vCPU 消息列表
            tools: 工具定义列表
            on_chunk: 流式回调函数，接收文本增量

        Returns:
            VcpuLLMResponse 包含内容和工具调用
        """
        if not self._started:
            await self.start()

        full_content = []
        collected_tool_calls = []

        # Use stream() to support real-time feedback
        try:
            async for event in self.stream(messages, tools):
                if event.type == "text":
                    text = event.text
                    full_content.append(text)
                    if on_chunk:
                        # Call sync callback (vCPU event emission is sync)
                        on_chunk(text)
                elif event.type == "tool_call" and event.tool_call:
                    collected_tool_calls.append(event.tool_call)
                elif event.type == "error":
                    error_msg = f"Stream error: {event.error}"
                    logger.error(error_msg)
                    # If it's a context length error, we must fail hard so vCPU can handle it (e.g. compact)
                    if "context_length_exceeded" in str(event.error) or "prompt is too long" in str(event.error):
                        raise RuntimeError(error_msg)
                    
                    # For other errors, we might want to return partial content if available
                    if not full_content and not collected_tool_calls:
                         raise RuntimeError(error_msg)

        except Exception as e:
            logger.error(f"HTTP API call failed: {e}")
            raise RuntimeError(f"LLM call failed: {e}")

        content = "".join(full_content)

        # Format tool calls
        tool_calls = []
        for tc in collected_tool_calls:
            # Need to convert to proper format if needed
            # stream() returns dicts: {id, name, arguments}
            # VcpuLLMResponse expects list of dicts with type='function' etc.
            # But wait, previous chat() implementation did:
            # { "id": tc.id, "type": "function", "function": { name, arguments } }
            # Here tc is already a dict from LLMStreamEvent

            tc_entry = {
                    "id": tc.get("id"),
                    "type": "function",
                    "function": {
                        "name": tc.get("name"),
                        # arguments in collected_tool_calls are dicts (parsed by pi_ai_http)
                        # We need to dump them back to string because vCPU expects string arguments?
                        # Or vCPU handles dict?
                        # vCPU decoder expects string OR dict.
                        # Previous implementation used json.dumps.
                        "arguments": json.dumps(tc.get("arguments"))
                        if isinstance(tc.get("arguments"), dict)
                        else tc.get("arguments"),
                    },
                }
            # Preserve Gemini 3 thought signature for round-trip
            if tc.get("thought_signature"):
                tc_entry["thought_signature"] = tc["thought_signature"]
            tool_calls.append(tc_entry)

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
        流式调用 LLM

        Args:
            messages: 消息列表
            tools: 工具定义列表

        Yields:
            LLMStreamEvent 流式事件
        """
        if not self._started:
            await self.start()

        http_messages = self._convert_messages_to_http(messages)
        http_tools = self._convert_tools_to_http(tools)

        async for event in self._client.stream(
            messages=http_messages,
            model=self._model,
            tools=http_tools,
            temperature=self.config.temperature,
            thinking=self.config.thinking,
            stop=self.config.stop,
        ):
            if event.type == "delta":
                yield LLMStreamEvent(type="text", text=event.content or "")
            elif event.type == "tool_call" and event.tool_call:
                tc_data = {
                    "id": event.tool_call.id,
                    "name": event.tool_call.name,
                    "arguments": event.tool_call.arguments,
                }
                # Preserve Gemini 3 thought signature for round-trip
                if event.tool_call.thought_signature:
                    tc_data["thought_signature"] = event.tool_call.thought_signature
                yield LLMStreamEvent(
                    type="tool_call",
                    tool_call=tc_data,
                )
            elif event.type == "thinking":
                # Thinking events act as keepalive (httpx timeout already reset
                # by the stream iteration). No need to forward to caller.
                pass
            elif event.type == "done":
                yield LLMStreamEvent(type="stop", reason=event.finish_reason or "stop")
            elif event.type == "error":
                yield LLMStreamEvent(type="error", error=event.error or "Unknown error")
            elif event.type == "result":
                yield LLMStreamEvent(type="usage", usage=event.usage)

    async def list_models(self) -> List[Dict[str, str]]:
        """列出可用模型"""
        if not self._started:
            await self.start()
        models = await self._client.list_models()
        if not models:
            # Fallback to defaults if empty
            return [
                {"id": "anthropic/claude-sonnet-4-20250514", "object": "model"},
                {"id": "anthropic/claude-opus-4-5", "object": "model"},
                {"id": "openai/gpt-4o", "object": "model"},
                {"id": "openai/gpt-4-turbo", "object": "model"},
            ]
        return models


# ============================================================================
# Convenience
# ============================================================================


async def create_pi_adapter(config: PiLLMConfig | None = None) -> PiLLMAdapter:
    """创建并启动 Pi LLM 适配器"""
    adapter = PiLLMAdapter(config)
    await adapter.start()
    return adapter
