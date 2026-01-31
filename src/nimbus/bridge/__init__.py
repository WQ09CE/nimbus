"""
Pi Bridge - 让 Nimbus 复用 pi-ai

推荐使用 HTTP 客户端（新方式）：
    from nimbus.bridge import PiAiHttpClient
    
    client = PiAiHttpClient()
    await client.start()
    result = await client.complete(messages, model="anthropic/claude-sonnet-4-20250514")

旧方式（subprocess JSON-RPC，已废弃）：
    from nimbus.bridge import PiClient
"""

# 新的 HTTP 客户端（推荐）
from .pi_ai_http import (
    PiAiHttpClient,
    Message,
    ToolCall,
    CompletionResult,
    StreamEvent,
    get_client,
    complete,
    stream,
)

# 旧的 subprocess 客户端（向后兼容，但已废弃）
try:
    from .pi_client import (
        PiClient,
        PiAI,
        PiTUI,
        Message as PiMessage,
        StreamEvent as PiStreamEvent,
        CompletionResult as PiCompletionResult,
        create_pi_client,
    )
except ImportError:
    # 如果旧客户端被删除，提供空实现
    PiClient = None
    PiAI = None
    PiTUI = None
    PiMessage = None
    PiStreamEvent = None
    PiCompletionResult = None
    create_pi_client = None

__all__ = [
    # 新的 HTTP 客户端
    "PiAiHttpClient",
    "Message",
    "ToolCall",
    "CompletionResult",
    "StreamEvent",
    "get_client",
    "complete",
    "stream",
    # 旧的（向后兼容）
    "PiClient",
    "PiAI",
    "PiTUI",
    "PiMessage",
    "PiStreamEvent",
    "PiCompletionResult",
    "create_pi_client",
]
