"""OpenCode Compatible API Routes.

This module provides API routes compatible with OpenCode API format,
enabling OpenWork frontend to connect directly to Nimbus Server.

OpenCode API Format Reference:
- Session.Info: {id, title, directory, time: {created, updated}, modelID, providerID}
- Message: {info: MessageInfo, parts: Part[]}

Routes:
- GET /session - List sessions
- POST /session - Create session
- GET /session/:sessionID - Get session details
- DELETE /session/:sessionID - Delete session
- GET /session/:sessionID/message - Get messages
- POST /session/:sessionID/message - Send message (SSE stream)
- POST /session/:sessionID/abort - Abort session
- GET /event - Global SSE event stream
- POST /permission/:permissionID - Respond to permission request
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()


# =============================================================================
# OpenCode Compatible Models
# =============================================================================

class TimeInfo(BaseModel):
    """Time information for OpenCode format."""
    created: int  # Unix timestamp in milliseconds
    updated: int  # Unix timestamp in milliseconds


class SessionInfo(BaseModel):
    """Session info in OpenCode format."""
    id: str
    title: str = ""
    directory: str = ""
    time: TimeInfo
    modelID: str = "nimbus"
    providerID: str = "nimbus"


class SessionCreateRequest(BaseModel):
    """Request to create a session."""
    directory: Optional[str] = None
    title: Optional[str] = None


class MessagePartText(BaseModel):
    """Text part in a message."""
    type: str = "text"
    text: str


class MessagePartToolUse(BaseModel):
    """Tool use part in a message."""
    type: str = "tool-use"
    id: str
    name: str
    input: Dict[str, Any] = Field(default_factory=dict)


class MessagePartToolResult(BaseModel):
    """Tool result part in a message."""
    type: str = "tool-result"
    id: str
    content: str


class MessageInfo(BaseModel):
    """Message metadata in OpenCode format."""
    id: str
    role: str  # user | assistant
    time: TimeInfo


class MessageResponse(BaseModel):
    """Message in OpenCode format."""
    info: MessageInfo
    parts: List[Any] = Field(default_factory=list)


class MessageSendRequest(BaseModel):
    """Request to send a message."""
    content: str
    attachments: List[Any] = Field(default_factory=list)


class PermissionRespondRequest(BaseModel):
    """Request to respond to a permission."""
    allow: bool


# =============================================================================
# Dependencies
# =============================================================================

async def get_storage(request: Request):
    """Get storage from app state."""
    return request.app.state.storage


async def get_session_manager(request: Request):
    """Get session manager from app state."""
    return request.app.state.session_manager


async def get_sse_hub(request: Request):
    """Get SSE hub from app state."""
    return request.app.state.sse_hub


async def get_permission_manager(request: Request):
    """Get permission manager from app state."""
    return request.app.state.permission_manager


# =============================================================================
# Helper Functions
# =============================================================================

def datetime_to_ms(dt: datetime) -> int:
    """Convert datetime to Unix timestamp in milliseconds."""
    return int(dt.timestamp() * 1000)


def to_session_info(session: Dict[str, Any]) -> SessionInfo:
    """Convert internal session format to OpenCode SessionInfo."""
    created_at = session.get("created_at", datetime.now())
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at.replace(" ", "T"))

    updated_at = session.get("updated_at", created_at)
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at.replace(" ", "T"))

    return SessionInfo(
        id=session["id"],
        title=session.get("name") or session["id"],
        directory=session.get("workspace_path") or "",
        time=TimeInfo(
            created=datetime_to_ms(created_at),
            updated=datetime_to_ms(updated_at),
        ),
        modelID=session.get("model_id", "nimbus"),
        providerID=session.get("provider_id", "nimbus"),
    )


def to_message_response(msg: Dict[str, Any]) -> MessageResponse:
    """Convert internal message format to OpenCode MessageResponse."""
    created_at = msg.get("created_at", datetime.now())
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at.replace(" ", "T"))

    # Build parts from content
    parts = []
    content = msg.get("content", "")
    if content:
        parts.append({"type": "text", "text": content})

    # Add artifacts as parts if any
    artifacts = msg.get("artifacts") or []
    for artifact in artifacts or []:
        if artifact.get("type") == "code":
            parts.append({
                "type": "text",
                "text": f"```{artifact.get('language', '')}\n{artifact.get('data', '')}\n```"
            })

    return MessageResponse(
        info=MessageInfo(
            id=msg["id"],
            role=msg["role"],
            time=TimeInfo(
                created=datetime_to_ms(created_at),
                updated=datetime_to_ms(created_at),
            ),
        ),
        parts=parts,
    )


# =============================================================================
# Session Routes
# =============================================================================

@router.get("/session", response_model=List[SessionInfo])
async def list_sessions(
    session_manager=Depends(get_session_manager),
):
    """List all sessions in OpenCode format."""
    sessions, _ = await session_manager.list_sessions(status="active", limit=100, offset=0)
    return [to_session_info(s) for s in sessions]


@router.post("/session", response_model=SessionInfo, status_code=201)
async def create_session(
    data: SessionCreateRequest,
    session_manager=Depends(get_session_manager),
):
    """Create a new session."""
    session = await session_manager.create_session(
        name=data.title,
        workspace_path=data.directory,
        memory_type="tiered",
        planner_type="dag",
    )
    return to_session_info(session)


@router.get("/session/{session_id}", response_model=SessionInfo)
async def get_session(
    session_id: str,
    session_manager=Depends(get_session_manager),
):
    """Get session details."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return to_session_info(session)


@router.delete("/session/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    session_manager=Depends(get_session_manager),
):
    """Delete a session."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await session_manager.delete_session(session_id)
    return None


# =============================================================================
# Message Routes
# =============================================================================

@router.get("/session/{session_id}/message", response_model=List[MessageResponse])
async def get_messages(
    session_id: str,
    storage=Depends(get_storage),
    session_manager=Depends(get_session_manager),
):
    """Get messages for a session."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = await storage.get_messages(session_id, limit=1000)
    return [to_message_response(m) for m in messages]


@router.post("/session/{session_id}/message")
async def send_message(
    session_id: str,
    data: MessageSendRequest,
    request: Request,
    session_manager=Depends(get_session_manager),
    sse_hub=Depends(get_sse_hub),
    storage=Depends(get_storage),
):
    """
    Send a message and receive SSE stream response.

    OpenCode SSE Event Format:
    - event.start: Message processing started
    - content.delta: Content chunk
    - content.done: Content complete
    - tool.start: Tool execution started
    - tool.done: Tool execution complete
    - event.done: Message complete
    - event.error: Error occurred
    """
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save user message
    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    await storage.add_message(
        message_id=message_id,
        session_id=session_id,
        role="user",
        content=data.content,
    )

    async def event_stream():
        """Generate SSE events in OpenCode format."""
        def format_sse(event_type: str, event_data: dict) -> str:
            return f"event: {event_type}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"

        # Send start event
        response_id = f"msg_{uuid.uuid4().hex[:12]}"
        yield format_sse("event.start", {
            "messageID": response_id,
            "sessionID": session_id,
        })

        try:
            # Get or create agent
            agent = await session_manager.get_or_create_agent(session_id)

            response_text = ""
            dag_id = None

            async for status in agent.run_stream(data.content):
                status_type = status.get("type", "unknown")

                if status_type == "planning":
                    yield format_sse("event.status", {
                        "status": "planning",
                        "message": status.get("content", "Creating plan..."),
                    })

                elif status_type == "dag_created":
                    dag_id = status.get("dag_id")
                    yield format_sse("event.status", {
                        "status": "executing",
                        "dagID": dag_id,
                        "totalTasks": status.get("total_tasks", 0),
                    })

                elif status_type == "task_start":
                    yield format_sse("tool.start", {
                        "taskID": status.get("task_id", ""),
                        "name": status.get("skill", ""),
                        "input": status.get("params", {}),
                    })

                elif status_type == "task_done":
                    yield format_sse("tool.done", {
                        "taskID": status.get("task_id", ""),
                        "result": str(status.get("result", ""))[:1000],
                        "durationMs": status.get("duration_ms", 0),
                    })

                elif status_type == "task_failed":
                    yield format_sse("tool.error", {
                        "taskID": status.get("task_id", ""),
                        "error": status.get("error", "Unknown error"),
                    })

                elif status_type == "direct":
                    response_text = status.get("content", "")
                    # Send content in chunks for streaming effect
                    yield format_sse("content.delta", {"text": response_text})
                    yield format_sse("content.done", {})

                elif status_type == "complete":
                    response_text = status.get("content", "")
                    yield format_sse("content.delta", {"text": response_text})
                    yield format_sse("content.done", {})

                elif status_type == "error":
                    yield format_sse("event.error", {
                        "code": "execution_error",
                        "message": status.get("content", "Unknown error"),
                    })

            # Save assistant message
            if response_text:
                await storage.add_message(
                    message_id=response_id,
                    session_id=session_id,
                    role="assistant",
                    content=response_text,
                    dag_id=dag_id,
                )

            # Send done event
            yield format_sse("event.done", {
                "messageID": response_id,
                "sessionID": session_id,
            })

        except Exception as e:
            yield format_sse("event.error", {
                "code": "server_error",
                "message": str(e),
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/session/{session_id}/abort", status_code=204)
async def abort_session(
    session_id: str,
    session_manager=Depends(get_session_manager),
):
    """Abort the current operation in a session."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # TODO: Implement actual abort logic
    # For now, this is a placeholder that returns success
    return None


# =============================================================================
# Global Event Stream
# =============================================================================

@router.get("/event")
async def global_event_stream(
    request: Request,
    sse_hub=Depends(get_sse_hub),
):
    """
    Global SSE event stream for all sessions.

    This endpoint provides a single stream for all events across sessions,
    which is useful for dashboard/monitoring scenarios.

    Events:
    - session.created: New session created
    - session.deleted: Session deleted
    - message.start: Message processing started
    - message.done: Message processing complete
    - task.start: Task execution started
    - task.done: Task execution complete
    - task.failed: Task execution failed
    """
    async def event_stream():
        """Generate global SSE events."""
        def format_sse(event_type: str, event_data: dict) -> str:
            return f"event: {event_type}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"

        # Send connected event
        yield format_sse("connected", {"timestamp": datetime.now().isoformat()})

        # Subscribe to all sessions
        queue = asyncio.Queue()

        # Register as global listener
        # TODO: Implement proper global event subscription in SSEHub
        # For now, send heartbeats
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield format_sse(event["type"], event["data"])
                except asyncio.TimeoutError:
                    # Send heartbeat
                    yield format_sse("heartbeat", {"timestamp": datetime.now().isoformat()})
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# Permission Routes
# =============================================================================

@router.post("/permission/{permission_id}", status_code=200)
async def respond_to_permission(
    permission_id: str,
    data: PermissionRespondRequest,
    permission_manager=Depends(get_permission_manager),
):
    """Respond to a permission request."""
    from ..models import PermissionDecision

    decision = PermissionDecision.ALLOW_ONCE if data.allow else PermissionDecision.DENY

    result = await permission_manager.resolve_permission(permission_id, decision)
    if not result:
        raise HTTPException(status_code=404, detail="Permission request not found")

    return {
        "permissionID": permission_id,
        "allowed": data.allow,
    }
