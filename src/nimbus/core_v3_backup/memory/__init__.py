"""
Nimbus v2 Memory Management Unit (MMU)

This module manages the context window for LLM interactions.
It implements a stack-based memory model with pinned context.

Key Components:
- PinnedContext: Immutable system anchors (always at the top)
- StackFrame: Call stack frames for subprocess management
- MMU: Memory Management Unit (context assembly)
"""

from nimbus.core.memory.context import Message, PinnedContext, StackFrame
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.memory.state_manager import StateManager

__all__ = [
    "PinnedContext",
    "StackFrame",
    "Message",
    "MMU",
    "MMUConfig",
    "StateManager",
]
