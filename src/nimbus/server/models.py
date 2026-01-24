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


class PermissionDecision(str, Enum):
    """Permission decision types."""
    ASK = "ask"
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"


class TaskStatusEnum(str, Enum):
    """Task execution status."""
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
    memory_type: str = "tiered"  # simple | tiered
    planner_type: str = "dag"    # simple | dag


class SessionResponse(BaseModel):
    """Response model for session info."""
    id: str
    name: Optional[str] = None
    created_at: datetime
    status: SessionStatus
    memory_type: str
    planner_type: str
    last_message_at: Optional[datetime] = None
    message_count: int = 0


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
    type: str  # file, url, text
    path: Optional[str] = None
    url: Optional[str] = None
    content: Optional[str] = None
    name: Optional[str] = None


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
    role: str  # user | assistant | system
    content: str
    created_at: datetime
    artifacts: List[ArtifactResponse] = Field(default_factory=list)
    dag_id: Optional[str] = None


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
# DAG Models (Nimbus Extension)
# =============================================================================

class TaskNodeResponse(BaseModel):
    """Task node in a DAG."""
    id: str
    skill: str
    params: Dict[str, Any] = Field(default_factory=dict)
    status: TaskStatusEnum
    depends_on: List[str] = Field(default_factory=list)
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class DAGStatsResponse(BaseModel):
    """Statistics for DAG execution."""
    total: int
    completed: int
    running: int
    pending: int
    failed: int
    skipped: int


class DAGResponse(BaseModel):
    """Response model for DAG status."""
    id: str
    goal: str
    status: str  # pending | running | completed | failed
    created_at: datetime
    nodes: List[TaskNodeResponse]
    stats: DAGStatsResponse


# =============================================================================
# Skill/Tool Models
# =============================================================================

class SkillParameter(BaseModel):
    """Parameter definition for a skill."""
    name: str
    type: str
    description: str
    required: bool = False
    default: Optional[Any] = None


class SkillResponse(BaseModel):
    """Response model for a skill."""
    name: str
    description: str
    source: str  # builtin | mcp:{server_name} | markdown
    parameters: List[SkillParameter]


class SkillList(BaseModel):
    """List of available skills."""
    skills: List[SkillResponse]


class MCPServerStatus(BaseModel):
    """Status of an MCP server."""
    name: str
    status: str  # connected | disconnected | error
    tools: List[str]
    error: Optional[str] = None


class MCPServerList(BaseModel):
    """List of MCP servers."""
    servers: List[MCPServerStatus]


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
    default_memory_type: str = "tiered"
    default_planner_type: str = "dag"
    max_concurrent_sessions: int = 10
    mcp_servers: List[str] = Field(default_factory=list)


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
