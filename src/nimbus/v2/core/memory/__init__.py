"""
Nimbus v2 Memory Management Unit (MMU)

This module manages the context window for LLM interactions.
It implements a stack-based memory model with pinned context.

Key Components:
- PinnedContext: Immutable system anchors (always at the top)
- StackFrame: Call stack frames for subprocess management
- MMU: Memory Management Unit (context assembly)
"""

from nimbus.v2.core.memory.context import PinnedContext, StackFrame, Message
from nimbus.v2.core.memory.mmu import MMU, MMUConfig

__all__ = [
    "PinnedContext",
    "StackFrame",
    "Message",
    "MMU",
    "MMUConfig",
]
