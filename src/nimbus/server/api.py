"""API Routes for Nimbus Server.

This module provides REST API endpoints for:
- Session management (CRUD)
- Chat/messaging with SSE streaming
- Permission control
- DAG status (Nimbus extension)
- Skills/tools listing
- Health and configuration
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

from .models import (
    # Session
    SessionCreate,
    SessionResponse,
    SessionDetail,
    SessionList,
    SessionStatus,
    # Message
    ChatRequest,
    MessageResponse,
    MessageList,
    # Permission
    PermissionRespond,
    PermissionResponseResult,
    PermissionRule,
    PermissionRuleList,
    PermissionRuleUpdate,
    PermissionDecision,
    # DAG
    DAGResponse,
    DAGStatsResponse,
    TaskNodeResponse,
    TaskStatusEnum,
    # Skill
    SkillResponse,
    SkillList,
    SkillParameter,
    MCPServerStatus,
    MCPServerList,
    # Config
    ServerConfig,
    HealthResponse,
    ErrorResponse,
)

router = APIRouter()


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
# Health & Config
# =============================================================================

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    try:
        from nimbus import __version__
    except ImportError:
        __version__ = "0.2.0"
    return HealthResponse(status="healthy", version=__version__)


@router.get("/config", response_model=ServerConfig)
async def get_config():
    """Get server configuration."""
    return ServerConfig(
        default_memory_type="tiered",
        default_planner_type="dag",
        max_concurrent_sessions=10,
        mcp_servers=[],
    )


@router.get("/models")
async def list_models(
    session_manager=Depends(get_session_manager),
):
    """List available models via Pi Bridge."""
    adapter = await session_manager._get_shared_llm_client()
    try:
        models = await adapter.get_models()
        return {"models": models}
    except Exception as e:
        logger.error(f"Failed to list models: {e}")
        return {"models": []}


# =============================================================================
# Session APIs
# =============================================================================

@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    data: SessionCreate,
    session_manager=Depends(get_session_manager),
):
    """Create a new session."""
    session = await session_manager.create_session(
        name=data.name,
        workspace_path=data.workspace_path,
        memory_type=data.memory_type,
        planner_type=data.planner_type,
        model_config=data.llm_config,
    )

    return SessionResponse(
        id=session["id"],
        name=session.get("name"),
        created_at=session["created_at"],
        status=SessionStatus(session["status"]),
        memory_type=session["memory_type"],
        planner_type=session["planner_type"],
        message_count=0,
    )


@router.get("/sessions", response_model=SessionList)
async def list_sessions(
    status: str = "active",
    limit: int = 20,
    offset: int = 0,
    session_manager=Depends(get_session_manager),
):
    """List sessions with pagination."""
    sessions, total = await session_manager.list_sessions(
        status=status,
        limit=limit,
        offset=offset,
    )

    items = [
        SessionResponse(
            id=s["id"],
            name=s.get("name"),
            created_at=s["created_at"],
            status=SessionStatus(s["status"]),
            memory_type=s["memory_type"],
            planner_type=s["planner_type"],
            last_message_at=s.get("last_message_at"),
            message_count=s.get("message_count", 0),
        )
        for s in sessions
    ]

    return SessionList(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    session_manager=Depends(get_session_manager),
    storage=Depends(get_storage),
):
    """Get session details."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get message count
    messages = await storage.get_messages(session_id, limit=1000)

    return SessionDetail(
        id=session["id"],
        name=session.get("name"),
        created_at=session["created_at"],
        status=SessionStatus(session["status"]),
        memory_type=session["memory_type"],
        planner_type=session["planner_type"],
        workspace_path=session.get("workspace_path"),
        message_count=len(messages),
    )


@router.delete("/sessions/{session_id}", status_code=204)
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
# Chat APIs
# =============================================================================

@router.post("/sessions/{session_id}/chat")
async def chat(
    session_id: str,
    data: ChatRequest,
    request: Request,
    session_manager=Depends(get_session_manager),
    sse_hub=Depends(get_sse_hub),
    storage=Depends(get_storage),
):
    """
    Send a chat message and receive SSE stream response.

    Returns a Server-Sent Events stream with the following events:
    - connected: Connection established
    - message_start: Processing started
    - planning: Creating execution plan
    - dag_created: DAG plan created (Nimbus)
    - task_start: Task execution started
    - tool_call: Tool being called
    - tool_result: Tool result received
    - task_done: Task completed
    - task_failed: Task failed
    - permission_request: Permission needed for tool
    - dag_complete: All tasks completed (Nimbus)
    - message: Final response
    - error: Error occurred
    """
    import json
    import os

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

    # Start background task to run chat
    async def run_chat():
        """Background task that runs the chat and emits events via SSE hub."""
        logger.info(f"🚀 run_chat started for session {session_id}, message: {data.content[:50]}...")
        try:
            logger.info(f"📞 Calling stream_chat...")
            await session_manager.stream_chat(session_id, data.content)
            logger.info(f"✅ stream_chat completed")
        except asyncio.CancelledError:
            # Client disconnected - this is expected, not an error
            logger.info(f"🛑 stream_chat cancelled for session {session_id} (client disconnected)")
            # Emit cancelled event so frontend knows
            try:
                await sse_hub.publish(
                    session_id,
                    "message",
                    {"content": "\n\n[用户已中断操作]", "done": True}
                )
            except Exception:
                pass  # Client already disconnected, ignore
            raise  # Re-raise to properly cancel the task
        except Exception as e:
            import logging
            import traceback
            error_logger = logging.getLogger(__name__)
            error_logger.error(f"❌ Error in stream_chat: {e}", exc_info=True)
            # Emit error event
            try:
                await sse_hub.publish(
                    session_id,
                    "error",
                    {"code": "server_error", "message": str(e), "traceback": traceback.format_exc()}
                )
            except Exception as pub_err:
                error_logger.error(f"❌ Failed to publish error event: {pub_err}")

    # Create task and keep reference to prevent GC
    task = asyncio.create_task(run_chat())
    
    # Log task creation
    logger.info(f"✅ Created background task for session {session_id}: {task}")
    
    # Add task done callback to catch exceptions
    def task_done_callback(t):
        try:
            if t.exception():
                logger.error(f"❌ Background task failed: {t.exception()}", exc_info=t.exception())
        except asyncio.CancelledError:
            logger.warning(f"⚠️ Background task cancelled")
    
    task.add_done_callback(task_done_callback)

    # Wrap SSE stream to detect client disconnect and cancel task
    async def stream_with_disconnect_detection():
        """SSE stream that cancels background task when client disconnects."""
        try:
            async for event in sse_hub.subscribe(session_id):
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.warning(f"🔌 Client disconnected, cancelling task for session {session_id}")
                    task.cancel()
                    break
                yield event
        except asyncio.CancelledError:
            logger.info(f"⚠️ SSE stream cancelled for session {session_id}")
            task.cancel()
        finally:
            # Ensure task is cancelled if stream ends for any reason
            if not task.done():
                logger.info(f"🛑 Cancelling background task for session {session_id}")
                task.cancel()

    # Return SSE stream (subscribe is an async generator)
    return StreamingResponse(
        stream_with_disconnect_detection(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions/{session_id}/messages", response_model=MessageList)
async def get_messages(
    session_id: str,
    limit: int = 50,
    storage=Depends(get_storage),
    session_manager=Depends(get_session_manager),
):
    """Get messages for a session."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = await storage.get_messages(session_id, limit=limit)

    items = []
    for m in messages:
        # Convert created_at string to datetime if needed
        created_at = m["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace(" ", "T"))

        # Handle None values for optional fields
        artifacts = m.get("artifacts")
        if artifacts is None:
            artifacts = []

        items.append(MessageResponse(
            id=m["id"],
            role=m["role"],
            content=m["content"],
            created_at=created_at,
            artifacts=artifacts,
            dag_id=m.get("dag_id"),
        ))

    return MessageList(items=items)


# =============================================================================
# Permission APIs
# =============================================================================

@router.post("/permissions/{request_id}/respond", response_model=PermissionResponseResult)
async def respond_to_permission(
    request_id: str,
    data: PermissionRespond,
    permission_manager=Depends(get_permission_manager),
):
    """Respond to a permission request."""
    result = await permission_manager.resolve_permission(
        request_id,
        data.decision,
    )

    if not result:
        raise HTTPException(status_code=404, detail="Permission request not found")

    return PermissionResponseResult(
        request_id=result["request_id"],
        decision=result["decision"],
        tool=result["tool"],
        resolved_at=result["resolved_at"],
    )


@router.get("/permissions/rules", response_model=PermissionRuleList)
async def get_permission_rules(
    permission_manager=Depends(get_permission_manager),
):
    """Get all permission rules."""
    rules = permission_manager.get_all_rules()
    return PermissionRuleList(
        rules=[
            PermissionRule(tool=tool, decision=decision)
            for tool, decision in rules.items()
        ]
    )


@router.put("/permissions/rules/{tool}", response_model=PermissionRule)
async def update_permission_rule(
    tool: str,
    data: PermissionRuleUpdate,
    permission_manager=Depends(get_permission_manager),
):
    """Update permission rule for a tool."""
    permission_manager.set_rule(tool, data.decision)
    return PermissionRule(tool=tool, decision=data.decision)


# =============================================================================
# DAG APIs (Nimbus Extension)
# =============================================================================

@router.get("/sessions/{session_id}/dags/{dag_id}", response_model=DAGResponse)
async def get_dag(
    session_id: str,
    dag_id: str,
    storage=Depends(get_storage),
    session_manager=Depends(get_session_manager),
):
    """Get DAG execution status."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    dag = await storage.get_dag(dag_id)
    if not dag:
        raise HTTPException(status_code=404, detail="DAG not found")

    # Convert DAG nodes to response format
    nodes = []
    stats = {"total": 0, "completed": 0, "running": 0, "pending": 0, "failed": 0, "skipped": 0}

    for node_id, node in dag.nodes.items():
        status = TaskStatusEnum(node.status.value)
        nodes.append(TaskNodeResponse(
            id=node_id,
            skill=node.skill_name,
            params=node.params,
            status=status,
            depends_on=list(node.depends_on),
            result=node.result,
            error=node.error,
        ))

        stats["total"] += 1
        if status == TaskStatusEnum.COMPLETED:
            stats["completed"] += 1
        elif status == TaskStatusEnum.RUNNING:
            stats["running"] += 1
        elif status == TaskStatusEnum.PENDING:
            stats["pending"] += 1
        elif status == TaskStatusEnum.FAILED:
            stats["failed"] += 1
        elif status == TaskStatusEnum.SKIPPED:
            stats["skipped"] += 1

    dag_status = "completed" if dag.is_completed() else "running"

    return DAGResponse(
        id=dag.id,
        goal=dag.goal,
        status=dag_status,
        created_at=datetime.now(),  # TODO: Store actual creation time
        nodes=nodes,
        stats=DAGStatsResponse(**stats),
    )


# =============================================================================
# Skill/Tool APIs
# =============================================================================

@router.get("/skills", response_model=SkillList)
async def list_skills():
    """List all available skills."""
    # TODO: Load from skill registry
    skills = [
        SkillResponse(
            name="synthesize",
            description="Synthesize tool results into human-readable reports",
            source="builtin",
            parameters=[
                SkillParameter(
                    name="message",
                    type="string",
                    description="User's question to answer based on tool results",
                    required=True,
                )
            ],
        ),
        SkillResponse(
            name="read_file",
            description="Read file contents",
            source="builtin",
            parameters=[
                SkillParameter(
                    name="path",
                    type="string",
                    description="File path to read",
                    required=True,
                )
            ],
        ),
        SkillResponse(
            name="search",
            description="Search for information",
            source="builtin",
            parameters=[
                SkillParameter(
                    name="query",
                    type="string",
                    description="Search query",
                    required=True,
                )
            ],
        ),
    ]

    return SkillList(skills=skills)


@router.get("/mcp/servers", response_model=MCPServerList)
async def list_mcp_servers():
    """List MCP servers and their status."""
    # TODO: Get actual MCP server status
    servers = []
    return MCPServerList(servers=servers)
