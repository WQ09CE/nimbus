from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Literal
import uuid
from datetime import datetime, timezone

MessageRole = Literal["system", "user", "assistant", "tool"]
MessageType = Literal["request", "response", "event", "error"]


@dataclass
class IPCMessage:
    """
    Standard Inter-Process Communication (IPC) Message used by AgentOS.
    Sub-Agents use this to exchange strict JSON contracts instead of unstructured text.
    """
    id: str = field(default_factory=lambda: f"msg-{uuid.uuid4().hex[:8]}")
    sender_pid: str = ""
    target_pid: str = ""
    type: MessageType = "request"
    
    # Contract Schema (JSON-serializable)
    payload: Dict[str, Any] = field(default_factory=dict)
    
    # Optional metadata (correlation IDs, routing tags, etc.)
    meta: Dict[str, Any] = field(default_factory=dict)
    
    timestamp: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sender_pid": self.sender_pid,
            "target_pid": self.target_pid,
            "type": self.type,
            "payload": self.payload,
            "meta": self.meta,
            "timestamp": self.timestamp
        }
