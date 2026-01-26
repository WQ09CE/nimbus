"""
Inter-Process Communication for Agent OS.

Architecture Layer: 1 (Agent OS - Kernel)
Von Neumann Role: IPC (Message Passing)

This module provides message passing between agent processes,
enabling coordination and result propagation in the process hierarchy.
"""

__layer__ = 1
__role__ = "IPC"

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class MessageType(str, Enum):
    """Types of IPC messages."""

    # Control messages
    SPAWN = "spawn"  # Request to spawn child process
    RESULT = "result"  # Child returning result to parent
    SIGNAL = "signal"  # Signal (kill, pause, resume)

    # Data messages
    CONTEXT = "context"  # Context/memory transfer
    STREAM = "stream"  # Streaming output chunk

    # Status messages
    STATUS = "status"  # Status update
    ERROR = "error"  # Error notification


class Signal(str, Enum):
    """Process signals (Unix-like)."""

    SIGTERM = "SIGTERM"  # Graceful termination
    SIGKILL = "SIGKILL"  # Immediate termination
    SIGSTOP = "SIGSTOP"  # Pause execution
    SIGCONT = "SIGCONT"  # Resume execution


@dataclass
class IPCMessage:
    """
    IPC Message for inter-process communication.

    Messages are the fundamental unit of communication between processes.
    They enable context passing, result propagation, and coordination.
    """

    # Identity
    msg_id: str
    msg_type: MessageType

    # Routing
    from_pid: str
    to_pid: str

    # Payload
    payload: Dict[str, Any] = field(default_factory=dict)

    # Metadata
    timestamp: datetime = field(default_factory=datetime.now)
    correlation_id: Optional[str] = None  # For request-response pairing

    @classmethod
    def create(
        cls,
        msg_type: MessageType,
        from_pid: str,
        to_pid: str,
        payload: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> "IPCMessage":
        """Factory method to create a new message."""
        return cls(
            msg_id=f"msg_{uuid.uuid4().hex[:8]}",
            msg_type=msg_type,
            from_pid=from_pid,
            to_pid=to_pid,
            payload=payload or {},
            correlation_id=correlation_id,
        )

    @classmethod
    def spawn_request(
        cls,
        from_pid: str,
        to_pid: str,
        role: str,
        task: str,
        **kwargs: Any,
    ) -> "IPCMessage":
        """Create a spawn request message."""
        return cls.create(
            msg_type=MessageType.SPAWN,
            from_pid=from_pid,
            to_pid=to_pid,
            payload={
                "role": role,
                "task": task,
                **kwargs,
            },
        )

    @classmethod
    def result_message(
        cls,
        from_pid: str,
        to_pid: str,
        result: Any,
        exit_code: int = 0,
        correlation_id: Optional[str] = None,
    ) -> "IPCMessage":
        """Create a result message."""
        return cls.create(
            msg_type=MessageType.RESULT,
            from_pid=from_pid,
            to_pid=to_pid,
            payload={
                "result": result,
                "exit_code": exit_code,
            },
            correlation_id=correlation_id,
        )

    @classmethod
    def signal_message(
        cls,
        from_pid: str,
        to_pid: str,
        signal: Signal,
    ) -> "IPCMessage":
        """Create a signal message."""
        return cls.create(
            msg_type=MessageType.SIGNAL,
            from_pid=from_pid,
            to_pid=to_pid,
            payload={"signal": signal.value},
        )

    @classmethod
    def error_message(
        cls,
        from_pid: str,
        to_pid: str,
        error: str,
        error_type: str = "RuntimeError",
    ) -> "IPCMessage":
        """Create an error message."""
        return cls.create(
            msg_type=MessageType.ERROR,
            from_pid=from_pid,
            to_pid=to_pid,
            payload={
                "error": error,
                "error_type": error_type,
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize message to dictionary."""
        return {
            "msg_id": self.msg_id,
            "msg_type": self.msg_type.value,
            "from_pid": self.from_pid,
            "to_pid": self.to_pid,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
        }

    def __repr__(self) -> str:
        return (
            f"IPCMessage(id={self.msg_id!r}, type={self.msg_type.value!r}, "
            f"from={self.from_pid!r}, to={self.to_pid!r})"
        )
