"""
Pi Bridge Client for Nimbus

Python 客户端，通过 JSON-RPC 调用 Node.js 的 pi-bridge
封装 pi-ai 和 pi-tui 的能力

Usage:
    from nimbus.bridge import PiClient, PiAI, PiTUI
    
    async with PiClient() as client:
        # 使用 AI
        async for event in client.ai.stream(messages):
            print(event)
        
        # 使用 TUI
        client.tui.render_markdown("# Hello")
"""

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator


# ============================================================================
# JSON-RPC Types
# ============================================================================

@dataclass
class JsonRpcRequest:
    method: str
    params: dict | None = None
    id: int = 0
    jsonrpc: str = "2.0"
    
    def to_json(self) -> str:
        return json.dumps({
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": self.params,
        })


@dataclass
class JsonRpcResponse:
    id: int
    result: Any = None
    error: dict | None = None
    jsonrpc: str = "2.0"


# ============================================================================
# AI Types
# ============================================================================

@dataclass
class Message:
    role: str  # "user" | "assistant" | "system" | "toolResult"
    content: str | list[dict]
    tool_call_id: str | None = None  # For toolResult messages
    tool_name: str | None = None  # For toolResult messages


@dataclass
class StreamEvent:
    type: str  # "start" | "text" | "tool_call" | "thinking" | "usage" | "stop" | "error"
    text: str | None = None
    tool_call: dict | None = None
    reason: str | None = None
    usage: dict | None = None
    error: str | None = None


@dataclass
class CompletionResult:
    content: list[dict]
    usage: dict


# ============================================================================
# AI Wrapper
# ============================================================================

class PiAI:
    """封装 pi-ai 的 Python 接口"""
    
    def __init__(self, client: "PiClient"):
        self._client = client
        self.provider: str = "anthropic"
        self.model_id: str = "claude-sonnet-4-20250514"
    
    def set_model(self, provider: str, model_id: str):
        """设置使用的模型"""
        self.provider = provider
        self.model_id = model_id
    
    async def stream(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 8192,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式调用 LLM"""
        # Convert messages to RPC format
        rpc_messages = []
        for m in messages:
            msg_dict = {"role": m.role, "content": m.content}
            # Include tool result fields if present
            if m.tool_call_id:
                msg_dict["toolCallId"] = m.tool_call_id
            if m.tool_name:
                msg_dict["toolName"] = m.tool_name
            rpc_messages.append(msg_dict)
        
        # 发送 stream 请求
        payload = {
            "provider": self.provider,
            "modelId": self.model_id,
            "messages": rpc_messages,
            "options": {"maxTokens": max_tokens},
        }
        if tools:
            payload["tools"] = tools

        self._client._send_request("ai.stream", payload)
        
        # 读取 streaming 事件
        async for data in self._client._read_messages():
            # 跳过响应（只要通知）
            if "id" in data:
                continue
            
            if data.get("method") == "ai.streamEvent":
                params = data.get("params", {})
                event = StreamEvent(
                    type=params.get("type", "unknown"),
                    text=params.get("text"),
                    tool_call=params.get("toolCall"),
                    reason=params.get("reason"),
                    usage=params.get("usage"),
                    error=params.get("error"),
                )
                yield event
                
                if event.type in ("stop", "error"):
                    break
    
    async def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 8192,
        tools: list[dict] | None = None,
    ) -> CompletionResult:
        """非流式调用 LLM"""
        # Convert messages to RPC format
        rpc_messages = []
        for m in messages:
            msg_dict = {"role": m.role, "content": m.content}
            # Include tool result fields if present
            if m.tool_call_id:
                msg_dict["toolCallId"] = m.tool_call_id
            if m.tool_name:
                msg_dict["toolName"] = m.tool_name
            rpc_messages.append(msg_dict)
        
        payload = {
            "provider": self.provider,
            "modelId": self.model_id,
            "messages": rpc_messages,
            "options": {"maxTokens": max_tokens},
        }
        if tools:
            payload["tools"] = tools

        result = await self._client._call("ai.complete", payload)
        return CompletionResult(
            content=result.get("content", []),
            usage=result.get("usage", {}),
        )
    
    async def get_models(self) -> list[dict]:
        """获取可用模型列表"""
        return await self._client._call("ai.getModels")


# ============================================================================
# TUI Wrapper
# ============================================================================

class PiTUI:
    """封装 pi-tui 的 Python 接口"""
    
    def __init__(self, client: "PiClient"):
        self._client = client
    
    async def render_markdown(self, content: str):
        """渲染 Markdown"""
        await self._client._call("tui.render", {"type": "markdown", "content": content})
    
    async def render_text(self, content: str):
        """渲染纯文本"""
        await self._client._call("tui.render", {"type": "text", "content": content})
    
    async def render_streaming(self, content: str):
        """渲染流式内容（不换行）"""
        await self._client._call("tui.render", {"type": "streaming", "content": content})
    
    async def notify(self, message: str, type: str = "info"):
        """显示通知"""
        await self._client._call("tui.notify", {"message": message, "type": type})
    
    async def get_input(self, prompt: str = "") -> str:
        """获取用户输入"""
        import sys
        if prompt:
            print(prompt, end="", flush=True)
        # 在线程池中运行阻塞的 input()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, input)


# ============================================================================
# Main Client
# ============================================================================

class PiClient:
    """Pi Bridge 客户端"""
    
    def __init__(
        self,
        bridge_path: str | None = None,
        node_path: str = "node",
    ):
        self._bridge_path = bridge_path or self._find_bridge()
        self._node_path = node_path
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        
        # 子模块
        self.ai = PiAI(self)
        self.tui = PiTUI(self)
    
    def _find_bridge(self) -> str:
        """查找 pi-bridge 文件"""
        candidates = [
            Path(__file__).parent.parent.parent.parent.parent / "bridge" / "pi-bridge.ts",
            Path(__file__).parent.parent.parent.parent.parent / "bridge" / "dist" / "pi-bridge.js",
        ]
        for path in candidates:
            if path.exists():
                return str(path)
        raise FileNotFoundError("Could not find pi-bridge.ts or pi-bridge.js")
    
    async def __aenter__(self) -> "PiClient":
        await self.start()
        return self
    
    async def __aexit__(self, *args):
        await self.stop()
    
    async def start(self):
        """启动 Node.js 子进程"""
        if self._bridge_path.endswith(".ts"):
            cmd = ["npx", "tsx", self._bridge_path]
        else:
            cmd = [self._node_path, self._bridge_path]
        
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        
        # 等待启动消息
        await asyncio.sleep(0.5)
        
        # 验证连接
        result = await self._call("ping")
        if not result.get("pong"):
            raise RuntimeError("Failed to connect to pi-bridge")
    
    async def stop(self):
        """停止子进程"""
        if self._process:
            try:
                await self._call("shutdown")
            except Exception:
                pass
            self._process.terminate()
            self._process = None
    
    def _send_request(self, method: str, params: dict | None = None) -> None:
        """发送请求（不等待响应）"""
        if not self._process:
            raise RuntimeError("PiClient not started")
        
        self._request_id += 1
        request = JsonRpcRequest(method=method, params=params, id=self._request_id)
        self._process.stdin.write(request.to_json() + "\n")
        self._process.stdin.flush()
    
    async def _read_line(self) -> str | None:
        """异步读取一行"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._process.stdout.readline)
    
    async def _read_messages(self) -> AsyncIterator[dict]:
        """读取消息流"""
        while self._process:
            line = await self._read_line()
            if not line:
                break
            try:
                data = json.loads(line)
                yield data
            except json.JSONDecodeError:
                continue
    
    async def _call(self, method: str, params: dict | None = None) -> Any:
        """发送请求并等待响应"""
        self._send_request(method, params)
        
        # 读取响应
        async for data in self._read_messages():
            if "id" in data:
                if data.get("error"):
                    raise RuntimeError(f"RPC Error: {data['error']}")
                return data.get("result")
        
        raise RuntimeError("No response received")


# ============================================================================
# 便捷函数
# ============================================================================

async def create_pi_client(**kwargs) -> PiClient:
    """创建并启动 PiClient"""
    client = PiClient(**kwargs)
    await client.start()
    return client
