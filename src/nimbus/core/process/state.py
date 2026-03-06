"""
Definition of a single AgentOS Process and its state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from nimbus.core.memory.mmu import MMU
    from nimbus.core.protocol import ToolResult
    from nimbus.core.runtime.vcpu import VCPU
    from nimbus.os.gate import KernelGate
    from nimbus.core.ipc.mailbox import Mailbox

ProcessState = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]

@dataclass
class Process:
    """A process managed by the ProcessManager."""

    pid: str
    goal: str
    role: str = ""              # Kept as pure label (logging/UI)
    is_interactive: bool = False  # Interactive session
    text_is_final: bool = True    # Pure text = final reply
    state: ProcessState = "PENDING"
    vcpu: Optional[VCPU] = None
    mmu: Optional[MMU] = None
    gate: Optional[KernelGate] = None
    result: Optional[ToolResult] = None
    task: Optional[asyncio.Task] = None
    inbox: Mailbox = field(default_factory=list)
    outbox: Mailbox = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
