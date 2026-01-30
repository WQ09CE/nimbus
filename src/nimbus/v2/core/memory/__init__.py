"""
Nimbus v2 Memory Management Unit (MMU)

This module manages the context window for LLM interactions.
It implements a stack-based memory model with pinned context.

Key Components:
- PinnedContext: Immutable system anchors (always at the top)
- StackFrame: Call stack frames for subprocess management
- MMU: Memory Management Unit (context assembly)
- Context Stack 提炼: Tool call 价值标记和智能过滤
"""

from nimbus.v2.core.memory.context import PinnedContext, StackFrame, Message
from nimbus.v2.core.memory.mmu import MMU, MMUConfig, ToolCallMarker, ToolCallValue

__all__ = [
    "PinnedContext",
    "StackFrame",
    "Message",
    "MMU",
    "MMUConfig",
    "ToolCallMarker",
    "ToolCallValue",
]
