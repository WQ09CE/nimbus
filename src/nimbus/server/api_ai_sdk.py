"""AI SDK v6 UI Message Stream Protocol compatible API endpoint.

This module provides:
- /api/chat endpoint returning AI SDK v6 UI Message Stream Protocol format
- Event streaming compatible with AI SDK useChat hook + DefaultChatTransport
- Message history handling

AI SDK v6 UI Message Stream Protocol:
https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol

Format: SSE with JSON payload
  data: {json}\n\n

Core event types:
- start: Message start with messageId
- text-start/text-delta/text-end: Text streaming
- tool-input-start/tool-input-available/tool-output-available: Tool calls
- error: Error with errorText
- finish: Message finish

Custom data events (MUST start with "data-"):
- data-status: Status updates
- data-dag-start/data-dag-progress/data-dag-end: Multi-agent DAG
- data-agent-start/data-agent-end: Sub-agent execution
"""

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)
logger.setLevel(logging.DEBUG)

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
# Dependencies
# =============================================================================

async def get_storage(request: Request):
    """Get storage from app state."""
    return request.app.state.storage


async def get_session_manager(request: Request):
    """Get session manager from app state."""
    return request.app.state.session_manager


async def get_message_cache(request: Request):
    """Get message cache from app state."""
    return request.app.state.message_cache


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
# Tool Event Formatters
# =============================================================================

def format_tool_input_start(tool_call_id: str, tool_name: str) -> str:
    """Format tool input start event."""
    return sse_event({
        "type": "tool-input-start",
        "toolCallId": tool_call_id,
        "toolName": tool_name,
    })


def format_tool_input_available(tool_call_id: str, tool_name: str, input_data: Any) -> str:
    """Format tool input available event."""
    return sse_event({
        "type": "tool-input-available",
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "input": input_data,
    })


def format_tool_output_available(tool_call_id: str, output: Any) -> str:
    """Format tool output available event."""
    return sse_event({
        "type": "tool-output-available",
        "toolCallId": tool_call_id,
        "output": output,
    })


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


def format_dag_start(dag_id: str, goal: str, total_tasks: int) -> str:
    """Format DAG start event."""
    return format_data_event("dag-start", {
        "dagId": dag_id,
        "goal": goal,
        "totalTasks": total_tasks,
    })


def format_dag_progress(dag_id: str, completed: int, total: int) -> str:
    """Format DAG progress event."""
    return format_data_event("dag-progress", {
        "dagId": dag_id,
        "completed": completed,
        "total": total,
    })


def format_dag_end(dag_id: str, status: str, summary: str = None) -> str:
    """Format DAG end event."""
    data = {"dagId": dag_id, "status": status}
    if summary:
        data["summary"] = summary
    return format_data_event("dag-end", data)


def format_agent_start(agent_id: str, agent_name: str, task_id: str, parent_task_id: str = None) -> str:
    """Format agent start event."""
    data = {
        "agentId": agent_id,
        "agentName": agent_name,
        "taskId": task_id,
    }
    if parent_task_id:
        data["parentTaskId"] = parent_task_id
    return format_data_event("agent-start", data)


def format_agent_end(agent_id: str, status: str, output: Any = None) -> str:
    """Format agent end event."""
    data = {"agentId": agent_id, "status": status}
    if output is not None:
        data["output"] = output
    return format_data_event("agent-end", data)


# =============================================================================
# API Endpoint
# =============================================================================

@router.post("/api/chat")
async def chat(
    request_data: AISdkChatRequest,
    request: Request,
    session_manager=Depends(get_session_manager),
    storage=Depends(get_storage),
    message_cache=Depends(get_message_cache),
) -> StreamingResponse:
    """
    Handle chat request and return Vercel AI SDK Data Protocol stream.

    This endpoint is compatible with the Vercel AI SDK useChat hook.
    It converts Nimbus agent events to AI SDK Data Protocol format.

    Args:
        request_data: Chat request with messages and optional sessionId.
        request: FastAPI request object.
        session_manager: Session manager dependency.
        storage: Storage dependency.

    Returns:
        StreamingResponse with AI SDK Data Protocol formatted events.
    """
    logger.info("📥 /api/chat request received")
    logger.debug(f"   Messages count: {len(request_data.messages)}")
    logger.debug(f"   SessionId: {request_data.sessionId}")

    # Log each message
    for i, msg in enumerate(request_data.messages):
        content = msg.get_text_content()[:50] + "..." if len(msg.get_text_content()) > 50 else msg.get_text_content()
        logger.debug(f"   Message[{i}]: role={msg.role}, content={content}")

    # Get or create session
    session_id = request_data.sessionId
    # Use provided workspace or default to home directory (more permissive)
    # Always expand ~ to full path
    workspace_path = os.path.expanduser(request_data.workspacePath or "~")

    if not session_id:
        # Create a new session for this chat
        session = await session_manager.create_session(
            name="AI SDK Chat",
            memory_type="tiered",
            planner_type="dag",
            workspace_path=workspace_path,
        )
        session_id = session["id"]
    else:
        # Verify session exists
        session = await session_manager.get_session(session_id)
        if not session:
            # Create session with the provided ID (preserve frontend session ID)
            session = await session_manager.create_session(
                name="AI SDK Chat",
                memory_type="tiered",
                planner_type="dag",
                workspace_path=workspace_path,
                session_id=session_id,  # Use the frontend-provided session ID
            )
            # session_id remains unchanged

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

    # Build conversation history from frontend messages
    # The frontend (Vercel AI SDK) sends the complete conversation history
    # in each request, so we use that directly instead of loading from cache
    full_history = []
    for msg in request_data.messages:
        full_history.append({
            "role": msg.role,
            "content": msg.get_text_content(),
        })
    logger.debug(f"   Using {len(full_history)} messages from frontend")

    # Save user message to storage for persistence (optional, for session recovery)
    await message_cache.add_message(
        session_id=session_id,
        role="user",
        content=user_message,
    )

    async def event_stream():
        """Generate SSE events for AI SDK v6 useChat hook.

        Uses UI Message Stream Protocol compatible with DefaultChatTransport.
        """
        logger.info("🚀 Starting event stream")

        # Generate unique IDs for this message
        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        text_id = f"text_{uuid.uuid4().hex[:8]}"

        # Track state
        response_text = ""
        event_count = 0
        current_dag_id = None
        text_started = False
        current_tool_id = None
        current_tool_name = None

        try:
            # Send message start
            yield format_start(message_id)

            # Get or create agent
            agent = await session_manager.get_or_create_agent(session_id)
            logger.debug(f"   Agent ready for session: {session_id}")

            # Run agent with streaming (pass conversation history)
            async for status in agent.run_stream(user_message, history=full_history):
                status_type = status.get("type", "unknown")
                event_count += 1
                logger.debug(f"   📨 Event[{event_count}]: {status_type}")

                # =============================================================
                # DAG/Task Events (from SubagentRuntime)
                # Core sends: task_start, task_complete
                # =============================================================
                if status_type == "task_start":
                    # DAG execution start
                    dag_id = status.get("dag_id", f"dag_{uuid.uuid4().hex[:8]}")
                    goal = status.get("goal", "")
                    total_tasks = status.get("nodes", 0)
                    current_dag_id = dag_id
                    yield format_dag_start(dag_id, goal, total_tasks)

                elif status_type == "task_complete":
                    # DAG execution complete
                    dag_id = status.get("dag_id", current_dag_id or "")
                    dag_status = status.get("status", "completed")
                    final_summary = status.get("final_summary", "")
                    yield format_dag_end(dag_id, dag_status, final_summary[:200] if final_summary else None)
                    current_dag_id = None

                elif status_type == "task_dag":
                    # DAG structure event (from CodeAgent.run_stream)
                    dag_data = status.get("dag", {})
                    yield format_data_event("dag-structure", dag_data)

                # =============================================================
                # Subagent Events (from SubagentRuntime)
                # Core sends: subagent_start, subagent_progress, subagent_complete
                # =============================================================
                elif status_type == "subagent_start":
                    node_id = status.get("node_id", "")
                    subagent_type = status.get("subagent_type", "agent")
                    goal = status.get("goal", "")
                    yield format_agent_start(
                        agent_id=node_id,
                        agent_name=subagent_type.upper(),
                        task_id=f"task_{node_id}",
                    )

                elif status_type == "subagent_progress":
                    # Tool call or tool result from subagent
                    node_id = status.get("node_id", "")
                    event_type = status.get("event_type", "")

                    if event_type == "tool_call":
                        tool_name = status.get("tool_name", "unknown")
                        arguments = status.get("arguments", {})
                        call_id = status.get("call_id", f"tool_{uuid.uuid4().hex[:8]}")
                        current_tool_id = call_id
                        current_tool_name = tool_name

                        # Send tool-input-start first
                        yield format_tool_input_start(call_id, tool_name)
                        # Then send tool-input-available with full args
                        yield format_tool_input_available(call_id, tool_name, arguments)

                    elif event_type == "tool_result":
                        call_id = status.get("call_id", current_tool_id or "")
                        result = status.get("result_preview", "")
                        is_error = status.get("is_error", False)

                        if is_error:
                            yield format_tool_output_available(call_id, {"error": str(result)})
                        else:
                            # Truncate large results for display
                            if isinstance(result, str) and len(result) > 2000:
                                result = result[:2000] + "...(truncated)"
                            yield format_tool_output_available(call_id, result)

                        current_tool_id = None
                        current_tool_name = None

                elif status_type == "subagent_complete":
                    node_id = status.get("node_id", "")
                    subagent_status = status.get("status", "completed")
                    summary = status.get("summary", "")
                    error = status.get("error")
                    output = {"summary": summary, "error": error} if error else summary
                    yield format_agent_end(node_id, subagent_status, output)

                # =============================================================
                # Text Events - Standard AI SDK v6 format
                # Core sends: response, complete
                # =============================================================
                elif status_type == "response":
                    # Final response text
                    content = status.get("content", "")
                    if content:
                        # Start text block if not started
                        if not text_started:
                            yield format_text_start(text_id)
                            text_started = True
                        # Send content in chunks (by line for better performance)
                        lines = content.split('\n')
                        for i, line in enumerate(lines):
                            delta = line + '\n' if i < len(lines) - 1 else line
                            if delta:
                                yield format_text_delta(text_id, delta)
                        response_text = content

                elif status_type == "complete":
                    # Completion event - use content if response wasn't already sent
                    content = status.get("content", "")
                    if content and not response_text:
                        # Start text block if not started
                        if not text_started:
                            yield format_text_start(text_id)
                            text_started = True
                        # Send content in chunks (by line for better performance)
                        lines = content.split('\n')
                        for i, line in enumerate(lines):
                            delta = line + '\n' if i < len(lines) - 1 else line
                            if delta:
                                yield format_text_delta(text_id, delta)
                        response_text = content

                elif status_type == "text_delta":
                    # Streaming text delta (if core supports it in future)
                    delta = status.get("delta", "")
                    if delta:
                        # Start text block if not started
                        if not text_started:
                            yield format_text_start(text_id)
                            text_started = True
                        yield format_text_delta(text_id, delta)
                        response_text += delta

                # =============================================================
                # Status/Progress Events - Custom data events
                # Core sends: status
                # =============================================================
                elif status_type == "status":
                    message = status.get("content", "")
                    yield format_status("processing", message)

                elif status_type in ("planning", "executing", "thinking"):
                    message = status.get("message", status_type)
                    yield format_status(status_type, message)

                elif status_type == "error":
                    # Error event from core
                    error_content = status.get("content", "Unknown error")
                    yield format_error(error_content)

            # End text block if started
            if text_started:
                yield format_text_end(text_id)

            # Send finish event and done marker
            logger.info(f"✅ Stream complete, {event_count} events, response: {response_text[:50] if response_text else 'empty'}...")
            yield format_finish("stop")
            yield format_done()

            # Save assistant message to cache (also persists to storage)
            if response_text:
                await message_cache.add_message(
                    session_id=session_id,
                    role="assistant",
                    content=response_text,
                )

        except Exception as e:
            logger.error(f"❌ Stream error: {e}", exc_info=True)
            # End text block if started
            if text_started:
                yield format_text_end(text_id)
            yield format_error(str(e))
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
# Path Completion API
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
    import os
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
                    display_path = "~" + item_path[len(home):]
                else:
                    display_path = item_path

                completions.append({
                    "path": display_path,
                    "name": item.name,
                    "isDir": item.is_dir(),
                })

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
