"""
Pi-AI HTTP Client

通过 HTTP 调用 pi-ai-server，替代之前的 subprocess + JSON-RPC 方式。

优势：
- 无需管理子进程
- HTTP 超时可控
- 更稳定的连接
- pi-ai 自动处理 OAuth token 刷新

Usage:
    client = PiAiHttpClient()
    
    # 非流式调用
    result = await client.complete(messages, model="anthropic/claude-sonnet-4-20250514")
    
    # 流式调用
    async for event in client.stream(messages):
        print(event)
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_BASE_URL = "http://localhost:3031"
DEFAULT_TIMEOUT = 120.0  # 秒
DEFAULT_MODEL = "anthropic/claude-sonnet-4-20250514"


@dataclass
class Message:
    """消息"""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | List[Dict[str, Any]]
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class CompletionResult:
    """完成结果"""
    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    model: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"


@dataclass
class StreamEvent:
    """流式事件"""
    type: str  # "delta" | "tool_call" | "done" | "error"
    content: Optional[str] = None
    tool_call: Optional[ToolCall] = None
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    usage: Optional[Dict[str, int]] = None


class PiAiHttpClient:
    """Pi-AI HTTP 客户端"""
    
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        default_model: str = DEFAULT_MODEL,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.default_model = default_model
        self._client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self) -> "PiAiHttpClient":
        await self.start()
        return self
    
    async def __aexit__(self, *args):
        await self.stop()
    
    async def start(self):
        """启动客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
    
    async def stop(self):
        """停止客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            if self._client is None:
                await self.start()
            resp = await self._client.get(f"{self.base_url}/health", timeout=5.0)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False
    
    async def list_models(self) -> List[Dict[str, str]]:
        """列出可用模型"""
        if self._client is None:
            await self.start()
        resp = await self._client.get(f"{self.base_url}/v1/models")
        resp.raise_for_status()
        return resp.json().get("data", [])
    
    def _build_request(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """构建请求"""
        # 转换消息格式
        msg_list = []
        for msg in messages:
            m = {"role": msg.role, "content": msg.content}
            if msg.tool_call_id:
                m["tool_call_id"] = msg.tool_call_id
            if msg.name:
                m["name"] = msg.name
            msg_list.append(m)
        
        req = {
            "model": model or self.default_model,
            "messages": msg_list,
            "stream": stream,
        }
        
        if tools:
            req["tools"] = tools
        
        return req
    
    async def complete(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> CompletionResult:
        """非流式完成"""
        if self._client is None:
            await self.start()
        
        req = self._build_request(messages, model, tools, stream=False)
        
        resp = await self._client.post(
            f"{self.base_url}/v1/chat/completions",
            json=req,
        )
        resp.raise_for_status()
        data = resp.json()
        
        # 解析响应
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        
        # 提取文本内容
        content = message.get("content", "")
        
        # 提取工具调用
        tool_calls = []
        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            args = func.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=func.get("name", ""),
                arguments=args,
            ))
        
        return CompletionResult(
            content=content,
            tool_calls=tool_calls,
            model=data.get("model", ""),
            usage=data.get("usage", {}),
            finish_reason=choice.get("finish_reason", "stop"),
        )
    
    async def stream(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式完成"""
        if self._client is None:
            await self.start()
        
        req = self._build_request(messages, model, tools, stream=True)
        
        async with self._client.stream(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            json=req,
        ) as resp:
            resp.raise_for_status()
            
            async for line in resp.aiter_lines():
                if not line:
                    continue
                
                # 解析 SSE
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    
                    # 根据事件类型处理
                    if event_type == "delta":
                        # 文本增量
                        choices = data.get("choices", [{}])
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield StreamEvent(type="delta", content=content)
                    
                    elif event_type == "tool_call":
                        # 工具调用
                        tc = data.get("tool_call", {})
                        func = tc.get("function", {})
                        args = func.get("arguments", "{}")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        yield StreamEvent(
                            type="tool_call",
                            tool_call=ToolCall(
                                id=tc.get("id", ""),
                                name=func.get("name", ""),
                                arguments=args,
                            ),
                        )
                    
                    elif event_type == "done":
                        yield StreamEvent(
                            type="done",
                            finish_reason=data.get("finish_reason", "stop"),
                        )
                    
                    elif event_type == "result":
                        yield StreamEvent(
                            type="result",
                            usage=data.get("usage", {}),
                        )
                    
                    elif event_type == "error":
                        yield StreamEvent(
                            type="error",
                            error=data.get("error", "Unknown error"),
                        )


# ============================================================================
# 便捷函数
# ============================================================================

_default_client: Optional[PiAiHttpClient] = None


async def get_client() -> PiAiHttpClient:
    """获取默认客户端（单例）"""
    global _default_client
    if _default_client is None:
        _default_client = PiAiHttpClient()
        await _default_client.start()
    return _default_client


async def complete(
    messages: List[Message],
    model: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
) -> CompletionResult:
    """便捷的非流式完成函数"""
    client = await get_client()
    return await client.complete(messages, model, tools)


async def stream(
    messages: List[Message],
    model: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
) -> AsyncIterator[StreamEvent]:
    """便捷的流式完成函数"""
    client = await get_client()
    async for event in client.stream(messages, model, tools):
        yield event
