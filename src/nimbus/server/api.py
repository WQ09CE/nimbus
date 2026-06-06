"""API Routes for Nimbus Server.

Endpoints for session management, chat streaming, file browsing,
and permission control.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from nimbus.config import get_config as get_nimbus_config

from .models import (
    ChatRequest,
    FileNode,
    FileType,
    HealthResponse,
    LogBatch,
    MessageList,
    PermissionRespond,
    PermissionResponseResult,
    PermissionRule,
    PermissionRuleList,
    PermissionRuleUpdate,
    ServerConfig,
    SessionCreate,
    SessionDetail,
    SessionList,
    SessionResponse,
    SessionUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Dependencies
# =============================================================================


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
async def get_server_config():
    """Get server configuration."""
    nimbus_config = get_nimbus_config()
    return ServerConfig(
        max_concurrent_sessions=10,
        default_model=nimbus_config.default_model,
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
        workspace_path=getattr(data, "workspace_path", None) or getattr(data, "workspace_id", None),
        model_config=data.llm_config,
        agent_mode=data.agent_mode,
        skills=data.skills,
        plugins=data.plugins,
    )

    return SessionResponse(**session)


@router.get("/skills")
async def list_skills():
    """List discoverable Nimbus skills."""
    from nimbus.skills import SkillManager

    manager = SkillManager.from_config(get_nimbus_config())
    return {"skills": manager.list_skills()}


@router.get("/plugins")
async def list_plugins():
    """List discoverable Nimbus plugins without activating plugin code."""
    from nimbus.plugins import PluginManager

    manager = PluginManager.from_config(get_nimbus_config())
    return {"plugins": manager.list_plugins()}


@router.get("/sessions", response_model=SessionList)
async def list_sessions(
    status: str = "active",
    limit: int = 20,
    offset: int = 0,
    session_manager=Depends(get_session_manager),
):
    """List sessions with pagination."""
    sessions, total = await session_manager.list_sessions(
        limit=limit,
        offset=offset,
    )

    return SessionList(
        items=[SessionResponse(**s) for s in sessions],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    session_manager=Depends(get_session_manager),
):
    """Get session details."""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    base_data = dict(session)
    # message_count is already injected by SessionManager now

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

        updated = await session_manager.get_session(session_id)
        return SessionResponse(**updated)
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
):
    """Delete a session.
    
    Args:
        session_id: Session to delete
    """
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await session_manager.delete_session(session_id)

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


@router.post("/sessions/{session_id}/inject")
async def inject_message(
    session_id: str,
    data: ChatRequest,
    session_manager=Depends(get_session_manager),
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

    # Build message content (with optional attachments) — same logic as /chat
    logger.info(f"📎 [inject] Attachments received: {len(data.attachments)} items" if data.attachments else "📎 [inject] No attachments")
    inject_content: str | list = data.content
    if data.attachments:
        content_parts = []
        if data.content:
            content_parts.append({"type": "text", "text": data.content})
        for att in data.attachments:
            if att.type == "image" and att.content:
                content_parts.append({
                    "type": "image",
                    "data": att.content,
                    "mimeType": att.mime_type or "image/png",
                })
            elif att.type in ("text", "pdf") and att.content:
                file_label = att.name or "attachment"
                content_parts.append({
                    "type": "text",
                    "text": f"\n\n--- {file_label} ---\n{att.content}\n--- end of {file_label} ---",
                })
        if content_parts:
            inject_content = content_parts

    # Inject via SessionManager -> SessionPool -> vCPU
    success = await session_manager.inject_message(session_id, inject_content)

    if success:
        return {"status": "injected", "message": "Message injected into execution loop"}
    else:
        # Process is not RUNNING
        # We stripped legacy SQLite persistence and AgentOS gets MMUs dynamically
        return {"status": "queued", "message": "Session not active, message ignored in stripped mode"}


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
):
    """
    Send a chat message and receive SSE stream response.

    Returns a Server-Sent Events stream with the following events:
    - connected: Connection established
    - message_start: Processing started
    - message: Text content delta
    - tool_call: Tool being called
    - tool_result: Tool result received
    - done: Agent turn completed
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
            # Only triggered by explicit interrupt, NOT by client disconnect
            logger.info(f"🛑 stream_chat cancelled for session {session_id}")
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
            import traceback as _tb
            import uuid as _uuid

            error_logger = logging.getLogger(__name__)
            _error_id = _uuid.uuid4().hex[:8]
            # Log full traceback server-side only (never expose to client)
            error_logger.error(
                f"❌ Error in stream_chat [#{_error_id}]: {e}\n{_tb.format_exc()}"
            )
            # Classify for user-facing message
            _err_str = str(e).lower()
            if "rate limit" in _err_str or "429" in _err_str or "resource exhausted" in _err_str:
                _code, _msg, _retry = "llm_rate_limit", "模型请求过频，请稍后重试", True
            elif "timeout" in _err_str or "timed out" in _err_str:
                _code, _msg, _retry = "resource_timeout", "请求超时，请重试", True
            elif "budget" in _err_str or "budget_exceeded" in _err_str:
                _code, _msg, _retry = "llm_ctx_overflow", "上下文长度已超限", False
            elif "auth" in _err_str or "401" in _err_str or "403" in _err_str:
                _code, _msg, _retry = "auth_error", "认证失败，请检查 API 密钥", False
            else:
                _code, _msg, _retry = "kernel_system_error", f"系统错误 [#{_error_id}]", False
            try:
                await sse_hub.publish(
                    session_id,
                    "error",
                    {
                        "code": _code,
                        "message": _msg,
                        "retryable": _retry,
                        "error_id": _error_id,
                    },
                )
                await sse_hub.publish(session_id, "done", {"status": "ERROR"})
            except Exception as pub_err:
                error_logger.error(f"❌ Failed to publish error event: {pub_err}")
        finally:
            session_manager.unregister_task(session_id)

    # Prepare SSE buffer before starting background work (fix publish-before-subscribe race)
    sse_hub.prepare_session(session_id)

    # Create task and keep reference to prevent GC
    task = asyncio.create_task(run_chat())
    session_manager.register_task(session_id, task)

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

    # Wrap SSE stream to detect client disconnect
    async def stream_with_disconnect_detection():
        """SSE stream - agent task continues in background when client disconnects."""
        try:
            async for event in sse_hub.subscribe(session_id):
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info(
                        f"🔌 Client disconnected for session {session_id}, "
                        f"agent continues in background"
                    )
                    break  # Just break, do NOT cancel the task
                yield event
        except asyncio.CancelledError:
            logger.info(f"⚠️ SSE stream cancelled for session {session_id}")
            # Do NOT cancel the task - let agent continue
        finally:
            # Do NOT cancel the task - let agent finish in background
            pass

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
    session_manager=Depends(get_session_manager),
):
    """Get messages for a session."""
    import hashlib

    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    dump = session_manager._storage.load_session(session_id)
    if not dump:
        return MessageList(items=[])

    messages = dump.get("messages", [])

    # Optional sorting
    if order == "DESC":
        messages.reverse()

    # Optional pagination
    if limit > 0:
        messages = messages[offset:offset+limit]

    # Wrap raw MMU dicts into MessageResponse-compatible dicts
    now_iso = datetime.now(timezone.utc).isoformat()
    items = []
    for i, msg in enumerate(messages):
        items.append({
            "id": hashlib.md5(f"{session_id}:{i}".encode()).hexdigest()[:12],
            "role": msg.get("role", "unknown"),
            "content": msg.get("content", ""),
            "created_at": now_iso,
            "artifacts": [],
            "name": msg.get("name"),
            "tool_call_id": msg.get("tool_call_id"),
            "tool_calls": msg.get("tool_calls"),
        })

    return MessageList(items=items)


# =============================================================================
# Session Status & Event Reconnection APIs
# =============================================================================


@router.get("/sessions/{session_id}/status")
async def get_session_status(
    session_id: str,
    session_manager=Depends(get_session_manager),
):
    """Check if a session has an active running task."""
    running = session_manager.is_session_running(session_id)
    return {"session_id": session_id, "running": running}


@router.get("/sessions/{session_id}/events")
async def subscribe_events(
    session_id: str,
    request: Request,
    session_manager=Depends(get_session_manager),
    sse_hub=Depends(get_sse_hub),
):
    """Subscribe to SSE events for a session.

    Supports both reconnection (mid-stream) and multi-client observation.
    When no task is running, the subscriber stays connected and will receive
    events when the next task starts (event log replay via SSEHub).
    """
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_stream():
        async for event in sse_hub.subscribe(session_id):
            if await request.is_disconnected():
                break
            yield event

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
                last_modified=datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc),
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
