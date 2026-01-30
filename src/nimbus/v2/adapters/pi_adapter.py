"""
Pi Adapter for Nimbus vCPU

将 pi-ai 适配为 Nimbus 的 LLM 接口

Usage:
    from nimbus.v2.adapters.pi_adapter import PiLLMAdapter, PiIOAdapter
    
    async with PiLLMAdapter() as llm:
        async for event in llm.stream(messages):
            print(event)
"""

from dataclasses import dataclass, field
from typing import AsyncIterator, Any, Protocol

from nimbus.v2.bridge import PiClient, Message as PiMessage, StreamEvent
from nimbus.v2.core.memory.context import Message


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


@dataclass
class LLMResponse:
    """LLM 完整响应"""
    content: list[dict]
    usage: dict


# ============================================================================
# LLM Adapter
# ============================================================================

@dataclass
class PiLLMConfig:
    """Pi LLM 适配器配置"""
    provider: str = "anthropic"
    model_id: str = "claude-sonnet-4-20250514"
    max_tokens: int = 8192
    temperature: float = 0.7


class PiLLMAdapter:
    """
    将 pi-ai 适配为 Nimbus 的 LLM 接口
    
    这样 Nimbus 的 vCPU 就可以使用 pi-ai 的所有模型和功能
    """
    
    def __init__(self, config: PiLLMConfig | None = None):
        self.config = config or PiLLMConfig()
        self._client: PiClient | None = None
    
    async def __aenter__(self) -> "PiLLMAdapter":
        self._client = PiClient()
        await self._client.start()
        self._client.ai.set_model(self.config.provider, self.config.model_id)
        return self
    
    async def __aexit__(self, *args):
        if self._client:
            await self._client.stop()
            self._client = None
    
    def _convert_messages(self, messages: list) -> list[PiMessage]:
        """将 Nimbus Message 转换为 Pi Message"""
        result = []
        for msg in messages:
            # 支持 Message 对象和 dict
            if hasattr(msg, 'role'):
                role = msg.role
                content = msg.content
            else:
                role = msg.get('role', 'user')
                content = msg.get('content', '')
            
            if isinstance(content, list):
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                content = "\n".join(texts)
            
            result.append(PiMessage(role=role, content=content or ""))
        
        return result
    
    async def stream(
        self,
        messages: list[Message],
        **kwargs,
    ) -> AsyncIterator[LLMStreamEvent]:
        """流式调用 LLM"""
        if not self._client:
            raise RuntimeError("Adapter not started")
        
        pi_messages = self._convert_messages(messages)
        
        async for event in self._client.ai.stream(
            pi_messages,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
        ):
            yield self._convert_event(event)
    
    def _convert_event(self, event: StreamEvent) -> LLMStreamEvent:
        """将 Pi StreamEvent 转换为 LLMStreamEvent"""
        if event.type == "start":
            return LLMStreamEvent(type="start")
        elif event.type == "text":
            return LLMStreamEvent(type="text", text=event.text or "")
        elif event.type == "tool_call":
            return LLMStreamEvent(type="tool_call", tool_call=event.tool_call)
        elif event.type == "thinking":
            return LLMStreamEvent(type="thinking", text=event.text or "")
        elif event.type == "usage":
            return LLMStreamEvent(type="usage", usage=event.usage)
        elif event.type == "stop":
            return LLMStreamEvent(type="stop", reason=event.reason or "end")
        elif event.type == "error":
            return LLMStreamEvent(type="error", error=event.error or "")
        else:
            return LLMStreamEvent(type="unknown")
    
    async def complete(
        self,
        messages: list[Message],
        **kwargs,
    ) -> LLMResponse:
        """非流式调用 LLM"""
        if not self._client:
            raise RuntimeError("Adapter not started")
        
        pi_messages = self._convert_messages(messages)
        result = await self._client.ai.complete(
            pi_messages,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
        )
        
        return LLMResponse(
            content=result.content,
            usage=result.usage,
        )
    
    async def get_models(self) -> list[dict]:
        """获取可用模型"""
        if not self._client:
            raise RuntimeError("Adapter not started")
        return await self._client.ai.get_models()


# ============================================================================
# IO Adapter
# ============================================================================

class PiIOAdapter:
    """
    将 pi-tui 适配为 Nimbus 的 IO 处理器
    """
    
    def __init__(self, client: PiClient):
        self._client = client
    
    async def print(self, content: str, *, markdown: bool = True):
        """输出内容"""
        if markdown:
            await self._client.tui.render_markdown(content)
        else:
            await self._client.tui.render_text(content)
    
    async def print_streaming(self, chunk: str):
        """输出流式内容"""
        await self._client.tui.render_streaming(chunk)
    
    async def input(self, prompt: str = "") -> str:
        """获取用户输入"""
        if prompt:
            await self._client.tui.render_text(prompt)
        return await self._client.tui.get_input()
    
    async def notify(self, message: str, type: str = "info"):
        """显示通知"""
        await self._client.tui.notify(message, type)
    
    async def select(self, title: str, options: list[str]) -> str | None:
        """显示选择器"""
        return await self._client.tui.select(title, options)
    
    async def confirm(self, title: str, message: str) -> bool:
        """显示确认对话框"""
        return await self._client.tui.confirm(title, message)


# ============================================================================
# Convenience
# ============================================================================

async def create_pi_adapters(config: PiLLMConfig | None = None) -> tuple[PiLLMAdapter, PiIOAdapter]:
    """创建 LLM 和 IO 适配器"""
    llm = PiLLMAdapter(config)
    await llm.__aenter__()
    io = PiIOAdapter(llm._client)
    return llm, io
