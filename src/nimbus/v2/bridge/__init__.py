"""
Pi Bridge - 让 Nimbus 复用 pi-ai 和 pi-tui

Usage:
    from nimbus.v2.bridge import PiClient, Message

    async with PiClient() as pi:
        # 设置模型
        pi.ai.set_model("anthropic", "claude-sonnet-4-20250514")
        
        # 流式调用
        async for event in pi.ai.stream([Message("user", "Hello")]):
            if event.type == "text":
                print(event.text, end="")
        
        # TUI 交互
        await pi.tui.render_markdown("# Done!")
"""

from .pi_client import (
    PiClient,
    PiAI,
    PiTUI,
    Message,
    StreamEvent,
    CompletionResult,
    create_pi_client,
)

__all__ = [
    "PiClient",
    "PiAI",
    "PiTUI",
    "Message",
    "StreamEvent",
    "CompletionResult",
    "create_pi_client",
]
