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

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


def _format_timestamp() -> str:
    """Generate ISO timestamp for logs."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _truncate(s: str, max_len: int = 200) -> str:
    """Truncate string for logging."""
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


# 默认配置
DEFAULT_BASE_URL = "http://localhost:3031"
DEFAULT_TIMEOUT = 120.0  # 秒
DEFAULT_MODEL = "anthropic/claude-opus-4-5"


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
    thought_signature: Optional[str] = None  # Gemini 3 thought signature for round-trip


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

    # Class-level request counter for logging
    _request_counter: int = 0

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        default_model: str = DEFAULT_MODEL,
        session_id: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.default_model = default_model
        self._client: Optional[httpx.AsyncClient] = None
        # Session ID for logging - generate one if not provided
        self.session_id = session_id or f"sess_{uuid.uuid4().hex[:8]}"

    async def __aenter__(self) -> "PiAiHttpClient":
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.stop()

    async def start(self):
        """启动客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
            from loguru import logger

            ts = _format_timestamp()
            logger.info(
                f"[{ts}] [{self.session_id}] Pi-AI client started | "
                f"url={self.base_url}, model={self.default_model}"
            )

    async def stop(self):
        """停止客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
            from loguru import logger

            ts = _format_timestamp()
            logger.info(f"[{ts}] [{self.session_id}] Pi-AI client stopped")

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
        temperature: Optional[float] = None,
        thinking: Optional[bool] = None,
        stop: Optional[List[str]] = None,
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

        if temperature is not None:
            req["temperature"] = temperature

        if thinking is not None:
            req["thinking"] = thinking

        if stop is not None:
            req["stop"] = stop

        return req

    async def complete(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        thinking: Optional[bool] = None,
        stop: Optional[List[str]] = None,
    ) -> CompletionResult:
        """非流式完成"""
        if self._client is None:
            await self.start()

        # Increment request counter
        PiAiHttpClient._request_counter += 1
        req_id = PiAiHttpClient._request_counter

        req = self._build_request(
            messages,
            model,
            tools,
            stream=False,
            temperature=temperature,
            thinking=thinking,
            stop=stop,
        )

        # Log request info
        from loguru import logger

        ts = _format_timestamp()
        msg_count = len(messages)
        tool_count = len(tools) if tools else 0
        last_role = messages[-1].role if messages else "none"
        logger.debug(
            f"[{ts}] [{self.session_id}] req#{req_id} → pi-ai | "
            f"messages={msg_count}, tools={tool_count}, last_role={last_role}"
        )

        resp = await self._client.post(
            f"{self.base_url}/v1/chat/completions",
            json=req,
        )
        resp.raise_for_status()
        data = resp.json()

        # Parse response
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")

        # Enhanced logging
        ts = _format_timestamp()
        content_preview = message.get("content", "")
        if content_preview:
            content_preview = _truncate(content_preview, 100)
        tool_calls_raw = message.get("tool_calls", [])
        tool_names = [tc.get("function", {}).get("name", "?") for tc in tool_calls_raw]

        # Detect if this is a return_result call
        is_final = "return_result" in tool_names

        logger.info(
            f"[{ts}] [{self.session_id}] req#{req_id} ← pi-ai | "
            f"finish={finish_reason}, tools={tool_names or 'none'}, "
            f"content={repr(content_preview) if content_preview else 'none'}"
            f"{' [FINAL]' if is_final else ''}"
        )

        # 解析响应

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
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    arguments=args,
                )
            )

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
        temperature: Optional[float] = None,
        thinking: Optional[bool] = None,
        stop: Optional[List[str]] = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式完成"""
        if self._client is None:
            await self.start()

        # Increment request counter
        PiAiHttpClient._request_counter += 1
        req_id = PiAiHttpClient._request_counter

        req = self._build_request(
            messages,
            model,
            tools,
            stream=True,
            temperature=temperature,
            thinking=thinking,
            stop=stop,
        )

        # Log request info
        from loguru import logger

        ts = _format_timestamp()
        msg_count = len(messages)
        tool_count = len(tools) if tools else 0
        logger.debug(
            f"[{ts}] [{self.session_id}] req#{req_id} → pi-ai [STREAM] | "
            f"messages={msg_count}, tools={tool_count}"
        )

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
                                thought_signature=tc.get("thoughtSignature"),
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
