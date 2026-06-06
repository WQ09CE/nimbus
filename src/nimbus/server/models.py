"""Pydantic models for Nimbus Server API.

This module defines request/response models for:
- Session management
- Chat/messaging
- Permission control
- DAG execution status
- Skills/tools listing
- Server configuration
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# =============================================================================
# Enums
# =============================================================================


class SessionStatus(str, Enum):
    """Session lifecycle status."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"
    RUNNING = "running"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    ERROR = "error"


class PermissionDecision(str, Enum):
    """Permission decision types."""

    ASK = "ask"
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"


class TaskStatusEnum(str, Enum):
    """Task execution status (kept for session status)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# =============================================================================
# Session Models
# =============================================================================


class SessionCreate(BaseModel):
    """Request model for creating a new session."""

    name: Optional[str] = None
    workspace_path: Optional[str] = None
    llm_config: Optional[Dict[str, Any]] = None  # {provider, model_id, ...}
    agent_mode: str = "standard"  # standard | dual_agent
    skills: Optional[List[str]] = None
    plugins: Optional[List[str]] = None


class SessionUpdate(BaseModel):
    """Request model for updating a session."""

    name: Optional[str] = None
    workspace_path: Optional[str] = None
    llm_config: Optional[Dict[str, Any]] = None
    agent_mode: Optional[str] = None
    skills: Optional[List[str]] = None
    plugins: Optional[List[str]] = None


class SessionResponse(BaseModel):
    """Response model for session info."""

    id: str
    name: Optional[str] = None
    created_at: datetime
    status: SessionStatus
    agent_mode: str = "standard"
    workspace_path: Optional[str] = None
    llm_config: Optional[Dict[str, Any]] = None
    skills: List[str] = Field(default_factory=list)
    plugins: List[str] = Field(default_factory=list)


class SessionDetail(SessionResponse):
    """Detailed session response with memory stats."""

    memory_stats: Optional[Dict[str, Any]] = None
    workspace_path: Optional[str] = None


class SessionList(BaseModel):
    """Paginated list of sessions."""

    items: List[SessionResponse]
    total: int
    limit: int
    offset: int


# =============================================================================
# Message Models
# =============================================================================


class AttachmentCreate(BaseModel):
    """Attachment in a chat request."""

    type: str  # image, video, text, pdf, file, url
    path: Optional[str] = None
    url: Optional[str] = None
    content: Optional[str] = None
    name: Optional[str] = None
    mime_type: Optional[str] = None  # e.g. "image/png", "text/plain"


class ChatRequest(BaseModel):
    """Request model for sending a chat message."""

    content: str
    attachments: List[AttachmentCreate] = Field(default_factory=list)


class ArtifactResponse(BaseModel):
    """Artifact produced by agent execution."""

    id: str
    type: str  # file, chart, code, table, image, markdown
    title: str
    data: Any
    mime_type: Optional[str] = None
    url: Optional[str] = None


class MessageResponse(BaseModel):
    """Response model for a message."""

    id: str
    role: str  # user | assistant | system | tool
    content: Any
    created_at: datetime
    artifacts: List[Any] = Field(default_factory=list)  # Flexible artifact format
    dag_id: Optional[str] = None
    # Tool-specific fields (pass-through from MMU dicts)
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class MessageList(BaseModel):
    """List of messages."""

    items: List[MessageResponse]


# =============================================================================
# Permission Models
# =============================================================================


class PermissionRequest(BaseModel):
    """Pending permission request."""

    request_id: str
    tool: str
    args: Dict[str, Any]
    session_id: str
    created_at: datetime


class PermissionRespond(BaseModel):
    """Request model for responding to a permission request."""

    decision: PermissionDecision


class PermissionResponseResult(BaseModel):
    """Response after resolving a permission request."""

    request_id: str
    decision: PermissionDecision
    tool: str
    resolved_at: datetime


class PermissionRule(BaseModel):
    """Permission rule for a tool."""

    tool: str
    decision: PermissionDecision


class PermissionRuleList(BaseModel):
    """List of permission rules."""

    rules: List[PermissionRule]


class PermissionRuleUpdate(BaseModel):
    """Request model for updating a permission rule."""

    decision: PermissionDecision


# =============================================================================
# SSE Event Models
# =============================================================================


class SSEEvent(BaseModel):
    """Server-Sent Event structure."""

    event: str
    data: Dict[str, Any]


# =============================================================================
# Config Models
# =============================================================================


class ServerConfig(BaseModel):
    """Server configuration response."""

    max_concurrent_sessions: int = 10
    default_model: str = ""


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str


# =============================================================================
# Error Models
# =============================================================================


class ErrorResponse(BaseModel):
    """Error response model."""

    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


# =============================================================================
# Logging Models
# =============================================================================


class LogEntry(BaseModel):
    """Single log entry from client."""

    level: str
    message: str
    data: Optional[Any] = None
    timestamp: str


class LogBatch(BaseModel):
    """Batch of logs from client."""

    entries: List[LogEntry]
    source: str = "client"


# =============================================================================
# File System Models
# =============================================================================


class FileType(str, Enum):
    """File type enumeration."""

    FILE = "file"
    DIRECTORY = "directory"


class FileNode(BaseModel):
    """File system node (file or directory)."""

    name: str
    path: str  # Relative path from root
    type: FileType
    children: Optional[List["FileNode"]] = None
    size: Optional[int] = None
    last_modified: Optional[datetime] = None


FileNode.model_rebuild()
