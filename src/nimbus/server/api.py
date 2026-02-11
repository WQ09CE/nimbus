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

from .models import (
    # Message
    ChatRequest,
    DAGResponse,
    DAGStatsResponse,
    FileNode,
    FileType,
    HealthResponse,
    # Logging
    LogBatch,
    MCPServerList,
    MessageList,
    MessageResponse,
    # Permission
    PermissionRespond,
    PermissionResponseResult,
    PermissionRule,
    PermissionRuleList,
    PermissionRuleUpdate,
    # Config
    ServerConfig,
    # Session
    SessionCreate,
    SessionDetail,
    SessionList,
    SessionResponse,
    SessionUpdate,
    SkillList,
    SkillParameter,
    # Skill
    SkillResponse,
    TaskNodeResponse,
    TaskStatusEnum,
)

logger = logging.getLogger(__name__)

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


# =============================================================================
# Logging APIs
# =============================================================================


@router.post("/logs")
async def receive_logs(batch: LogBatch):
    """Receive logs from frontend."""
    from nimbus.core.logging import get_logger

    logger = get_logger(f"client.{batch.source}")

    for entry in batch.entries:
        msg = f"[{entry.timestamp}] {entry.message}"
        if entry.data:
            msg += f" | data={entry.data}"

        if entry.level.lower() == "debug":
            logger.debug(msg)
        elif entry.level.lower() == "info":
            logger.info(msg)
        elif entry.level.lower() == "warn" or entry.level.lower() == "warning":
            logger.warning(msg)
        elif entry.level.lower() == "error":
            logger.error(msg)
        else:
            logger.info(msg)

    return {"status": "ok"}


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
        models = await adapter.list_models()
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
        agent_mode=data.agent_mode,
    )

    from .api_utils import _format_session_response

    return _format_session_response(session)


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

    from .api_utils import _format_session_response

    return SessionList(
        items=[_format_session_response(s) for s in sessions],
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

    from .api_utils import _format_session_response

    base = _format_session_response(session)
    base_data = base.model_dump()
    base_data["message_count"] = len(messages)

    return SessionDetail(**base_data)


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    updates: SessionUpdate,
    session_manager=Depends(get_session_manager),
):
    """Update session configuration."""
    update_data = updates.model_dump(exclude_unset=True)

    # Map llm_config to model_config for internal consistency
    if "llm_config" in update_data:
        update_data["model_config"] = update_data.pop("llm_config")

    try:
        session = await session_manager.update_session(session_id, update_data)

        from .api_utils import _format_session_response

        return _format_session_response(session)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    hard: bool = False,
    session_manager=Depends(get_session_manager),
    storage=Depends(get_storage),
):
    """Delete a session.

    Args:
        session_id: Session to delete
        hard: If True, permanently delete from database. If False, soft delete (mark as deleted).
    """
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await session_manager.delete_session(session_id)

    # Hard delete if requested
    if hard:
        await storage.delete_session(session_id, hard_delete=True)

    return None


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(
    session_id: str,
    session_manager=Depends(get_session_manager),
):
    """
    Interrupt a running session.

    This will:
    1. Request the vCPU to pause at next step
    2. Hibernate the session (save checkpoint to DB)
    3. Return the checkpoint info

    Returns:
        Interrupt status and checkpoint info
    """
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = await session_manager.interrupt_session(session_id)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to interrupt"))

    return result


@router.post("/sessions/{session_id}/resume")
async def resume_session(
    session_id: str,
    session_manager=Depends(get_session_manager),
):
    """
    Resume an interrupted session.

    This will:
    1. Load checkpoint from DB
    2. Restore vCPU state
    3. Continue execution

    Returns:
        Resume status
    """
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = await session_manager.resume_session(session_id)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to resume"))

    return result


@router.post("/sessions/{session_id}/inject")
async def inject_message(
    session_id: str,
    data: ChatRequest,
    session_manager=Depends(get_session_manager),
    storage=Depends(get_storage),
):
    """
    Inject a user message into a running session (Human-in-the-loop).

    This allows steering the agent while it is executing tasks.
    The message will be processed at the start of the next Think-Act-Observe cycle.

    Returns:
        Status object
    """
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Inject via SessionManager -> SessionPool -> vCPU
    success = await session_manager.inject_message(session_id, data.content)

    if success:
        # NOTE: We do NOT save to storage here directly.
        # The vCPU will add it to MMU, and session_v2._save_conversation_to_storage
        # will persist it along with the assistant's response and tool executions.
        # This ensures the history strictly reflects what the vCPU actually processed.
        return {"status": "injected", "message": "Message injected into execution loop"}
    else:
        # Fallback: If session is not running, we MUST save it manually
        # because the vCPU won't pick it up dynamically.
        import uuid
        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        await storage.add_message(
            message_id=message_id,
            session_id=session_id,
            role="user",
            content=f"[Intervention] {data.content}",
        )
        return {"status": "queued", "message": "Session not active, message saved to history"}


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

    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Build message content (with optional attachments)
    logger.info(f"📎 Attachments received: {len(data.attachments)} items" if data.attachments else "📎 No attachments")
    chat_content: str | list = data.content
    if data.attachments:
        content_parts = []
        # Add text part
        if data.content:
            content_parts.append({"type": "text", "text": data.content})
        # Add attachment parts
        for att in data.attachments:
            if att.type == "image" and att.content:
                content_parts.append({
                    "type": "image",
                    "data": att.content,
                    "mimeType": att.mime_type or "image/png",
                })
            elif att.type in ("text", "pdf") and att.content:
                # Text files: append content inline
                file_label = att.name or "attachment"
                content_parts.append({
                    "type": "text",
                    "text": f"\n\n--- {file_label} ---\n{att.content}\n--- end of {file_label} ---",
                })
        if content_parts:
            chat_content = content_parts

    # Save user message (store text-only version for persistence)
    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    await storage.add_message(
        message_id=message_id,
        session_id=session_id,
        role="user",
        content=data.content,  # Always save the text version
    )

    # Start background task to run chat
    async def run_chat():
        """Background task that runs the chat and emits events via SSE hub."""
        logger.info(
            f"🚀 run_chat started for session {session_id}, message: {str(data.content)[:50]}..."
        )
        try:
            logger.info("📞 Calling stream_chat...")
            await session_manager.stream_chat(session_id, chat_content)
            logger.info("✅ stream_chat completed")
        except asyncio.CancelledError:
            # Client disconnected - this is expected, not an error
            logger.info(f"🛑 stream_chat cancelled for session {session_id} (client disconnected)")
            # Emit cancelled event so frontend knows
            try:
                await sse_hub.publish(
                    session_id, "message", {"content": "\n\n[用户已中断操作]", "done": True}
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
                    {
                        "code": "server_error",
                        "message": str(e),
                        "traceback": traceback.format_exc(),
                    },
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
            logger.warning("⚠️ Background task cancelled")

    task.add_done_callback(task_done_callback)

    # Wrap SSE stream to detect client disconnect and cancel task
    async def stream_with_disconnect_detection():
        """SSE stream that cancels background task when client disconnects."""
        try:
            async for event in sse_hub.subscribe(session_id):
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.warning(
                        f"🔌 Client disconnected, cancelling task for session {session_id}"
                    )
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
    limit: int = 100,
    offset: int = 0,
    order: str = "ASC",
    storage=Depends(get_storage),
    session_manager=Depends(get_session_manager),
):
    """Get messages for a session."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = await storage.get_messages(
        session_id, limit=limit, offset=offset, order=order
    )

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

        items.append(
            MessageResponse(
                id=m["id"],
                role=m["role"],
                content=m["content"],
                created_at=created_at,
                artifacts=artifacts,
                dag_id=m.get("dag_id"),
            )
        )

    return MessageList(items=items)


# =============================================================================
# File System APIs
# =============================================================================


@router.get("/sessions/{session_id}/files", response_model=List[FileNode])
async def list_files(
    session_id: str,
    path: str = "",
    session_manager=Depends(get_session_manager),
):
    """List files in session workspace."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    workspace_path = session.get("workspace_path")

    import os
    from pathlib import Path

    # Resolve workspace root (fallback to cwd like the agent does)
    try:
        if workspace_path:
            root = Path(os.path.expanduser(workspace_path)).resolve()
        else:
            root = Path.cwd().resolve()
    except Exception:
        return []

    if not root.exists():
        return []

    # Calculate target directory
    target_dir = root
    if path:
        target_dir = (root / path).resolve()
        # Verify path is inside workspace
        try:
            target_dir.relative_to(root)
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied: Path outside workspace")

    if not target_dir.exists() or not target_dir.is_dir():
        return []

    nodes = []
    try:
        # Shallow scan
        for entry in os.scandir(target_dir):
            if entry.name.startswith("."):
                continue  # Skip hidden files

            is_dir = entry.is_dir()
            # Calculate relative path safely
            try:
                rel_path = str(Path(entry.path).relative_to(root))
            except ValueError:
                continue

            node = FileNode(
                name=entry.name,
                path=rel_path,
                type=FileType.DIRECTORY if is_dir else FileType.FILE,
                size=entry.stat().st_size if not is_dir else None,
                last_modified=datetime.fromtimestamp(entry.stat().st_mtime),
            )
            nodes.append(node)

    except PermissionError:
        pass

    # Sort: directories first, then files by name
    nodes.sort(key=lambda x: (x.type != FileType.DIRECTORY, x.name.lower()))

    return nodes


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
        rules=[PermissionRule(tool=tool, decision=decision) for tool, decision in rules.items()]
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
        nodes.append(
            TaskNodeResponse(
                id=node_id,
                skill=node.skill_name,
                params=node.params,
                status=status,
                depends_on=list(node.depends_on),
                result=node.result,
                error=node.error,
            )
        )

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


# =============================================================================
# Filesystem APIs (for workspace selection)
# =============================================================================


@router.get("/fs/complete")
async def complete_path(path: str = "", limit: int = 20):
    """
    Complete a filesystem path for workspace selection.

    Args:
        path: Partial path to complete (supports ~ for home)
        limit: Maximum number of results

    Returns:
        List of matching directory paths
    """
    import os
    from pathlib import Path

    # Expand ~ to home directory
    if path.startswith("~"):
        expanded = os.path.expanduser(path)
    else:
        expanded = path or "."

    try:
        base_path = Path(expanded)

        # If path ends with /, list contents of that directory
        # Otherwise, complete the last component
        if path.endswith("/") or path == "" or path == "~":
            search_dir = base_path
            prefix = ""
        else:
            search_dir = base_path.parent
            prefix = base_path.name.lower()

        if not search_dir.exists():
            return {"path": path, "completions": [], "error": "Directory not found"}

        completions = []
        try:
            for item in search_dir.iterdir():
                # Only show directories
                if not item.is_dir():
                    continue

                # Skip hidden directories unless user is explicitly looking for them
                if item.name.startswith(".") and not prefix.startswith("."):
                    continue

                # Match prefix
                if prefix and not item.name.lower().startswith(prefix):
                    continue

                # Build the completion path
                if path.startswith("~"):
                    home = os.path.expanduser("~")
                    if str(item).startswith(home):
                        completion = "~" + str(item)[len(home):]
                    else:
                        completion = str(item)
                else:
                    completion = str(item)

                completions.append({
                    "path": completion,
                    "name": item.name,
                    "is_dir": True,
                })

                if len(completions) >= limit:
                    break

        except PermissionError:
            return {"path": path, "completions": [], "error": "Permission denied"}

        # Sort by name
        completions.sort(key=lambda x: x["name"].lower())

        return {
            "path": path,
            "completions": completions,
            "cwd": str(Path.cwd()),
        }

    except Exception as e:
        logger.error(f"Path completion error: {e}")
        return {"path": path, "completions": [], "error": str(e)}
