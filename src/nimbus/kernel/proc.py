"""
Process Control Block for Agent OS.

Architecture Layer: 1 (Agent OS - Kernel)
Von Neumann Role: PCB (Process Control Block)

This module defines the core process abstraction for the Agent OS.
Each AgentProcess represents an isolated execution context with its own
memory space, resource quotas, and permission boundaries.
"""

__layer__ = 1
__role__ = "PCB"

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class ProcessState(str, Enum):
    """Process lifecycle states following Unix process model."""

    CREATED = "created"  # Process created but not yet scheduled
    READY = "ready"  # Ready to run, waiting for CPU
    RUNNING = "running"  # Currently executing
    BLOCKED = "blocked"  # Waiting for I/O or child process
    COMPLETED = "completed"  # Successfully finished (exit_code = 0)
    FAILED = "failed"  # Finished with error (exit_code != 0)
    ZOMBIE = "zombie"  # Finished but parent hasn't called wait()
    CANCELLED = "cancelled"  # Killed by signal


@dataclass
class AgentProcess:
    """
    Process Control Block for Agent Process.

    This is the fundamental unit of execution in Agent OS. Each process has:
    - Identity: pid, parent_pid, role
    - Context Isolation: independent memory space, system prompt, task
    - Resource Quotas: token budget, max turns
    - State: lifecycle state, exit code, result
    - Permissions: allowed tools, filesystem access
    - Relationships: parent-child hierarchy
    """

    # ========== Identity ==========
    pid: str
    parent_pid: Optional[str]
    role: str  # e.g., "Brain", "Coder", "Reviewer"

    # ========== Context Isolation (Memory Space) ==========
    memory: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    task_instruction: str = ""

    # ========== Resource Quotas ==========
    token_usage: int = 0
    max_token_budget: int = 50000
    max_turns: int = 50
    current_turn: int = 0

    # ========== State ==========
    state: ProcessState = ProcessState.CREATED
    exit_code: int = 0
    result: Any = None
    error: Optional[str] = None

    # ========== Permissions ==========
    allowed_tools: Set[str] = field(default_factory=set)
    fs_mode: str = "rw"  # "ro" | "rw" | "none"
    allowed_paths: List[str] = field(default_factory=list)

    # ========== Scheduling ==========
    priority: int = 0  # Higher = more priority
    depth: int = 0  # Hierarchy depth (init = 0)
    vcpu_affinity: Optional[str] = None  # Optional vCPU binding (None = use default)

    # ========== Timing ==========
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    # ========== Relationships ==========
    children: List[str] = field(default_factory=list)

    # ========== Runtime (Internal) ==========
    _async_task: Optional[Any] = field(default=None, repr=False)
    _completion_event: Optional[Any] = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        role: str,
        parent_pid: Optional[str] = None,
        depth: int = 0,
        **kwargs: Any,
    ) -> "AgentProcess":
        """
        Factory method to create a new process.

        Args:
            role: Process role (e.g., "Brain", "Coder")
            parent_pid: Parent process ID (None for init)
            depth: Hierarchy depth
            **kwargs: Additional process attributes

        Returns:
            New AgentProcess instance with unique PID
        """
        return cls(
            pid=f"proc_{uuid.uuid4().hex[:8]}",
            parent_pid=parent_pid,
            role=role,
            depth=depth,
            **kwargs,
        )

    def is_terminal(self) -> bool:
        """Check if process is in terminal state (cannot transition further)."""
        return self.state in (
            ProcessState.COMPLETED,
            ProcessState.FAILED,
            ProcessState.ZOMBIE,
            ProcessState.CANCELLED,
        )

    def is_runnable(self) -> bool:
        """Check if process can be scheduled for execution."""
        return self.state in (ProcessState.CREATED, ProcessState.READY)

    def can_fork(self) -> bool:
        """Check if process can create children."""
        return self.state == ProcessState.RUNNING

    def has_budget(self) -> bool:
        """Check if process has remaining token budget."""
        return self.token_usage < self.max_token_budget

    def has_turns(self) -> bool:
        """Check if process has remaining turns."""
        return self.current_turn < self.max_turns

    def consume_tokens(self, tokens: int) -> bool:
        """
        Consume tokens from budget.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if consumption succeeded, False if over budget
        """
        if self.token_usage + tokens > self.max_token_budget:
            return False
        self.token_usage += tokens
        return True

    def increment_turn(self) -> bool:
        """
        Increment turn counter.

        Returns:
            True if increment succeeded, False if max turns reached
        """
        if self.current_turn >= self.max_turns:
            return False
        self.current_turn += 1
        return True

    def complete(self, result: Any = None) -> None:
        """Mark process as successfully completed."""
        self.state = ProcessState.COMPLETED
        self.exit_code = 0
        self.result = result
        self.finished_at = datetime.now()

    def fail(self, error: str, exit_code: int = 1) -> None:
        """Mark process as failed."""
        self.state = ProcessState.FAILED
        self.exit_code = exit_code
        self.error = error
        self.finished_at = datetime.now()

    def cancel(self) -> None:
        """Mark process as cancelled."""
        self.state = ProcessState.CANCELLED
        self.exit_code = -1
        self.finished_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize process to dictionary for IPC/logging."""
        return {
            "pid": self.pid,
            "parent_pid": self.parent_pid,
            "role": self.role,
            "state": self.state.value,
            "depth": self.depth,
            "token_usage": self.token_usage,
            "max_token_budget": self.max_token_budget,
            "current_turn": self.current_turn,
            "max_turns": self.max_turns,
            "exit_code": self.exit_code,
            "result": self.result,
            "error": self.error,
            "children": self.children,
            "vcpu_affinity": self.vcpu_affinity,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"AgentProcess(pid={self.pid!r}, role={self.role!r}, "
            f"state={self.state.value!r}, depth={self.depth})"
        )
