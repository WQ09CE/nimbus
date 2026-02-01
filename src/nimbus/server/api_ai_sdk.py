"""
AI SDK v6 UI Message Stream Protocol compatible API endpoint for Nimbus v2.

This module provides:
- /api/chat endpoint returning AI SDK v6 UI Message Stream Protocol format
- Event streaming compatible with AI SDK useChat hook + DefaultChatTransport

AI SDK v6 UI Message Stream Protocol:
https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol

Format: SSE with JSON payload
  data: {json}\n\n

Core event types:
- start: Message start with messageId
- text-start/text-delta/text-end: Text streaming
- error: Error with errorText
- finish: Message finish
"""

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# =============================================================================
# Request Models
# =============================================================================


class Message(BaseModel):
    """Chat message in AI SDK format (supports both v5 and v6)."""

    role: str  # "user" | "assistant" | "system"
    # v5 format: content as string
    content: Optional[str] = None
    # v6 format: parts array (can contain text, tool-chat, etc.)
    parts: Optional[List[Dict[str, Any]]] = None
    # v6 also includes id
    id: Optional[str] = None

    def get_text_content(self) -> str:
        """Extract text content from message (supports both v5 and v6 formats)."""
        # v5 format: direct content string
        if self.content:
            return self.content
        # v6 format: extract text from parts
        if self.parts:
            texts = []
            for p in self.parts:
                if isinstance(p, dict) and p.get("type") == "text" and "text" in p:
                    texts.append(p["text"])
            return "".join(texts)
        return ""


class AISdkChatRequest(BaseModel):
    """Request model for AI SDK chat endpoint."""

    messages: List[Message]
    sessionId: Optional[str] = None
    workspacePath: Optional[str] = None  # Workspace directory for agent tools


# =============================================================================
# Router
# =============================================================================

router = APIRouter(tags=["AI SDK"])


# =============================================================================
# AI SDK v6 UI Message Stream Protocol Helpers
# https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol
# =============================================================================


def sse_event(data: dict | str) -> str:
    """Format as SSE event with JSON payload."""
    if isinstance(data, str):
        return f"data: {data}\n\n"
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def format_done() -> str:
    """Format [DONE] marker for stream end."""
    return "data: [DONE]\n\n"


# =============================================================================
# Core Event Formatters
# =============================================================================


def format_start(message_id: str) -> str:
    """Format message start event."""
    return sse_event({"type": "start", "messageId": message_id})


def format_text_start(text_id: str) -> str:
    """Format text block start."""
    return sse_event({"type": "text-start", "id": text_id})


def format_text_delta(text_id: str, delta: str) -> str:
    """Format text content delta."""
    return sse_event({"type": "text-delta", "id": text_id, "delta": delta})


def format_text_end(text_id: str) -> str:
    """Format text block end."""
    return sse_event({"type": "text-end", "id": text_id})


def format_error(error_text: str) -> str:
    """Format error event."""
    return sse_event({"type": "error", "errorText": error_text})


def format_finish(finish_reason: str = "stop") -> str:
    """Format message finish event."""
    return sse_event({"type": "finish", "finishReason": finish_reason})


# =============================================================================
# Custom Data Event Formatters (MUST use "data-" prefix)
# =============================================================================


def format_data_event(subtype: str, data: Any, event_id: str = None, transient: bool = None) -> str:
    """Format custom data event. Type will be prefixed with 'data-'."""
    payload = {
        "type": f"data-{subtype}",
        "data": data,
    }
    if event_id:
        payload["id"] = event_id
    if transient is not None:
        payload["transient"] = transient
    return sse_event(payload)


def format_status(status: str, message: str = None) -> str:
    """Format status update event."""
    data = {"status": status}
    if message:
        data["message"] = message
    return format_data_event("status", data)


# =============================================================================
# API Endpoint
# =============================================================================


def get_or_create_agent_os(request: Request, workspace_path: Optional[str] = None):
    """
    Get or create an AgentOS instance for the given workspace.

    Each workspace gets its own AgentOS instance with tools configured
    for that workspace directory.

    Args:
        request: FastAPI request object
        workspace_path: Workspace directory path

    Returns:
        AgentOS instance for the workspace
    """
    from pathlib import Path

    # Normalize workspace path
    if workspace_path:
        workspace = Path(os.path.expanduser(workspace_path)).resolve()
        logger.info(f"🗂️  Requested workspace: {workspace_path} -> {workspace}")
    else:
        workspace = Path.cwd()
        logger.info(f"🗂️  No workspace specified, using cwd: {workspace}")

    workspace_key = str(workspace)

    # Initialize workspace cache if not exists
    if not hasattr(request.app.state, "workspace_agents"):
        request.app.state.workspace_agents = {}

    # Check if we already have an AgentOS for this workspace
    if workspace_key in request.app.state.workspace_agents:
        return request.app.state.workspace_agents[workspace_key]

    # Get the default AgentOS config
    default_agent_os = getattr(request.app.state, "agent_os", None)
    if default_agent_os is None:
        return None

    # If workspace is same as default, use default
    default_workspace = getattr(request.app.state, "default_workspace", None)
    if default_workspace and str(workspace) == str(default_workspace):
        return default_agent_os

    # Create new AgentOS for this workspace
    try:
        from nimbus.agentos import create_agent_os

        # Get the LLM client from default AgentOS
        llm_client = default_agent_os._llm

        agent_os = create_agent_os(
            llm_client=llm_client,
            system_rules=default_agent_os.config.system_rules,
            workspace=workspace,
            register_defaults=True,
        )

        # Cache it
        request.app.state.workspace_agents[workspace_key] = agent_os
        logger.info(f"Created new AgentOS for workspace: {workspace}")

        return agent_os

    except Exception as e:
        logger.error(f"Failed to create AgentOS for workspace {workspace}: {e}")
        return default_agent_os


@router.post("/api/chat")
async def chat(
    request_data: AISdkChatRequest,
    request: Request,
) -> StreamingResponse:
    """
    Handle chat request and return Vercel AI SDK Data Protocol stream.

    This endpoint is compatible with the Vercel AI SDK useChat hook.
    It converts Nimbus v2 AgentOS responses to AI SDK Data Protocol format.

    Args:
        request_data: Chat request with messages and optional sessionId.
        request: FastAPI request object.

    Returns:
        StreamingResponse with AI SDK Data Protocol formatted events.
    """
    logger.info("📥 /api/chat request received")
    logger.info(f"   Messages count: {len(request_data.messages)}")
    logger.info(f"   SessionId: {request_data.sessionId}")
    logger.info(f"   WorkspacePath: {request_data.workspacePath}")

    # Get AgentOS for the specified workspace
    agent_os = get_or_create_agent_os(request, request_data.workspacePath)
    if agent_os is None:
        # Return error stream
        async def error_stream():
            error_msg_id = f"msg_{uuid.uuid4().hex[:12]}"
            yield format_start(error_msg_id)
            yield format_error("AgentOS not initialized")
            yield format_finish("error")
            yield format_done()

        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Get or create session
    session_id = request_data.sessionId

    # Extract the last user message
    user_message = ""
    for msg in reversed(request_data.messages):
        if msg.role == "user":
            user_message = msg.get_text_content()
            break

    logger.info(f"📝 User message extracted: {user_message[:100]}...")

    if not user_message:
        # No user message found, return error
        async def error_stream():
            error_msg_id = f"msg_{uuid.uuid4().hex[:12]}"
            yield format_start(error_msg_id)
            yield format_error("No user message provided")
            yield format_finish("error")
            yield format_done()

        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def event_stream():
        """Generate SSE events for AI SDK v6 useChat hook.

        Uses UI Message Stream Protocol compatible with DefaultChatTransport.
        """
        logger.info("🚀 Starting event stream")

        # Generate unique IDs for this message
        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        text_id = f"text_{uuid.uuid4().hex[:8]}"

        try:
            # Send message start
            yield format_start(message_id)

            # Send status
            yield format_status("processing", "Thinking...")

            # Execute via AgentOS
            result = await agent_os.chat(user_message, session_id=session_id)

            # Start text block
            yield format_text_start(text_id)

            # Get the output text
            if result.status == "OK":
                output_text = (
                    result.output if isinstance(result.output, str) else str(result.output)
                )
            else:
                # Handle error
                error_msg = result.fault.message if result.fault else "Unknown error"
                output_text = f"Error: {error_msg}"

            # Stream the content in chunks (by line for better display)
            if output_text:
                lines = output_text.split("\n")
                for i, line in enumerate(lines):
                    delta = line + "\n" if i < len(lines) - 1 else line
                    if delta:
                        yield format_text_delta(text_id, delta)

            # End text block
            yield format_text_end(text_id)

            # Send finish event and done marker
            logger.info(
                f"✅ Stream complete, response: {output_text[:50] if output_text else 'empty'}..."
            )
            yield format_finish("stop")
            yield format_done()

        except Exception as e:
            logger.error(f"❌ Stream error: {e}", exc_info=True)
            yield format_text_start(text_id)
            yield format_text_delta(text_id, f"Error: {str(e)}")
            yield format_text_end(text_id)
            yield format_finish("error")
            yield format_done()

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
# Session Management Endpoints (for acp-web-client compatibility)
# =============================================================================


class CreateSessionRequest(BaseModel):
    """Request for creating a session."""

    name: Optional[str] = "New Chat"
    workspacePath: Optional[str] = None


class CreateSessionResponse(BaseModel):
    """Response for session creation."""

    id: str
    name: str
    createdAt: str


@router.post("/api/v1/sessions")
async def create_session(
    request_data: CreateSessionRequest,
    request: Request,
) -> CreateSessionResponse:
    """Create a new chat session."""
    import datetime

    session_id = f"session-{uuid.uuid4().hex[:12]}"

    return CreateSessionResponse(
        id=session_id,
        name=request_data.name or "New Chat",
        createdAt=datetime.datetime.now().isoformat(),
    )


@router.get("/api/v1/sessions")
async def list_sessions(request: Request) -> Dict[str, Any]:
    """List all sessions."""
    agent_os = getattr(request.app.state, "agent_os", None)
    if agent_os is None:
        return {"sessions": []}

    # Get sessions from AgentOS processes
    sessions = []
    for pid in agent_os.list_processes():
        process = agent_os.get_process(pid)
        if process and process.role == "chat":
            sessions.append(
                {
                    "id": pid,
                    "name": process.goal or "Chat Session",
                    "state": process.state,
                }
            )

    return {"sessions": sessions}


@router.get("/api/v1/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> Dict[str, Any]:
    """Get a session by ID."""
    agent_os = getattr(request.app.state, "agent_os", None)
    if agent_os is None:
        return {"error": "AgentOS not initialized"}

    process = agent_os.get_process(session_id)
    if not process:
        return {"error": "Session not found"}

    return {
        "id": session_id,
        "name": process.goal or "Chat Session",
        "state": process.state,
    }


@router.delete("/api/v1/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> Dict[str, Any]:
    """Delete a session."""
    agent_os = getattr(request.app.state, "agent_os", None)
    if agent_os is None:
        return {"error": "AgentOS not initialized"}

    agent_os.end_session(session_id)
    return {"success": True}


# =============================================================================
# Path Completion API (for workspace selector)
# =============================================================================


class PathCompletionResponse(BaseModel):
    """Response model for path completion."""

    prefix: str
    completions: List[Dict[str, Any]]  # [{path, name, isDir}]


@router.get("/api/path/complete")
async def complete_path(
    prefix: str = "",
    limit: int = 20,
) -> PathCompletionResponse:
    """
    Complete file system paths for workspace selection.

    Args:
        prefix: Path prefix to complete (e.g., "~/pro" or "/Users/x/Do")
        limit: Maximum number of completions to return.

    Returns:
        List of matching paths with metadata.
    """
    from pathlib import Path

    # Expand ~ to home directory
    if prefix.startswith("~"):
        expanded = os.path.expanduser(prefix)
    else:
        expanded = prefix

    # Handle empty or root
    if not expanded:
        expanded = os.path.expanduser("~")

    completions = []

    try:
        path = Path(expanded)

        # If the path exists and is a directory, list its contents
        if path.exists() and path.is_dir():
            parent = path
            pattern = ""
        else:
            # Otherwise, use parent directory and filter by name prefix
            parent = path.parent
            pattern = path.name.lower()

        if parent.exists() and parent.is_dir():
            for item in sorted(parent.iterdir()):
                # Skip hidden files unless prefix starts with .
                if item.name.startswith(".") and not pattern.startswith("."):
                    continue

                # Filter by pattern
                if pattern and not item.name.lower().startswith(pattern):
                    continue

                # Only include directories for workspace selection
                if not item.is_dir():
                    continue

                # Convert back to ~ format if in home directory
                item_path = str(item)
                home = os.path.expanduser("~")
                if item_path.startswith(home):
                    display_path = "~" + item_path[len(home) :]
                else:
                    display_path = item_path

                completions.append(
                    {
                        "path": display_path,
                        "name": item.name,
                        "isDir": item.is_dir(),
                    }
                )

                if len(completions) >= limit:
                    break

    except PermissionError:
        pass  # Skip directories we can't access
    except Exception as e:
        logger.warning(f"Path completion error: {e}")

    # Restore ~ prefix in response
    response_prefix = prefix
    if not prefix and expanded == os.path.expanduser("~"):
        response_prefix = "~"

    return PathCompletionResponse(
        prefix=response_prefix,
        completions=completions,
    )


# =============================================================================
# Message History Endpoint
# =============================================================================


@router.get("/api/v1/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    request: Request,
) -> Dict[str, Any]:
    """Get message history for a session."""
    # Currently v2 AgentOS doesn't persist message history to storage
    # The messages are in MMU memory only
    # For now, return empty list - can be enhanced later
    return {"messages": []}
