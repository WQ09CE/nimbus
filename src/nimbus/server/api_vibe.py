"""
Vibe Coding IDE Compatible API

实现与 vibe-coding-ide 前端兼容的 API 端点。

API 端点:
- POST /api/runs/         创建运行
- GET  /api/runs/{id}/events  SSE 事件流
- GET  /api/runs/{id}/resume  恢复运行

SSE 事件格式:
{
    "event_type": "xxx",
    "task_id": "task_xxx",
    "timestamp": "ISO8601",
    "data": {...},
    "error": null
}
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["vibe"])

# ============================================================================
# In-memory storage for runs (可以后续替换为 Redis/SQLite)
# ============================================================================

_run_payloads: Dict[str, Dict[str, Any]] = {}
_run_lock = asyncio.Lock()


async def set_run_payload(run_id: str, payload: Dict[str, Any]) -> None:
    """Store run payload."""
    async with _run_lock:
        _run_payloads[run_id] = payload


async def get_run_payload(run_id: str) -> Optional[Dict[str, Any]]:
    """Get run payload."""
    async with _run_lock:
        return _run_payloads.get(run_id)


async def update_run_project(run_id: str, project: Dict[str, str]) -> None:
    """Update project files in run payload."""
    async with _run_lock:
        if run_id in _run_payloads:
            _run_payloads[run_id]["project"] = project


# ============================================================================
# Simple Token (无需真正的 JWT，使用 HMAC)
# ============================================================================

_SECRET_KEY = "nimbus-vibe-secret-key"  # 生产环境应从环境变量读取


def make_stream_token(payload: Dict[str, Any]) -> str:
    """Create a simple token encoding the payload."""
    data = json.dumps(payload, sort_keys=True)
    sig = hashlib.sha256(f"{data}{_SECRET_KEY}".encode()).hexdigest()[:16]
    import base64

    encoded = base64.urlsafe_b64encode(data.encode()).decode()
    return f"{encoded}.{sig}"


def read_stream_token(token: str) -> Dict[str, Any]:
    """Read and verify token."""
    try:
        import base64

        parts = token.split(".")
        if len(parts) != 2:
            return {}
        encoded, sig = parts
        data = base64.urlsafe_b64decode(encoded.encode()).decode()
        expected_sig = hashlib.sha256(f"{data}{_SECRET_KEY}".encode()).hexdigest()[:16]
        if sig != expected_sig:
            return {}
        return json.loads(data)
    except Exception:
        return {}


# ============================================================================
# SSE Helpers
# ============================================================================

SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_format(event: Dict[str, Any]) -> str:
    """Format event as SSE data line."""
    return f"data: {json.dumps(event)}\n\n"


def emit_event(
    task_id: str,
    event_type: str,
    data: Any = None,
    error: Any = None,
) -> Dict[str, Any]:
    """Create a vibe-compatible event."""
    return {
        "event_type": event_type,
        "task_id": task_id,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "data": data,
        "error": error,
    }


def tool_started_sse(task_id: str, tool_id: str, name: str, arguments: Any) -> str:
    """Format tool started event."""
    return sse_format(
        emit_event(
            task_id,
            "progress_update_tool_action_started",
            data={
                "args": [
                    {
                        "id": tool_id,
                        "function": {
                            "name": name,
                            "arguments": arguments,
                        },
                    }
                ]
            },
        )
    )


def tool_completed_sse(
    task_id: str,
    tool_id: str,
    name: str,
    arguments: Any,
    output_data: Any,
) -> str:
    """Format tool completed event."""
    return sse_format(
        emit_event(
            task_id,
            "progress_update_tool_action_completed",
            data={
                "result": {
                    "tool_call": {
                        "id": tool_id,
                        "function": {
                            "name": name,
                            "arguments": arguments,
                        },
                    },
                    "output_data": output_data,
                }
            },
        )
    )


def tool_log_sse(task_id: str, tool_id: str, name: str, log_data: str) -> str:
    """Format tool log event."""
    return sse_format(
        emit_event(
            task_id,
            "progress_update_tool_action_log",
            data={
                "id": tool_id,
                "name": name,
                "data": log_data,
            },
        )
    )


# ============================================================================
# Request/Response Models
# ============================================================================


class RunRequest(BaseModel):
    """Request to create a new agent run."""

    user_id: str = "anonymous"
    project_id: str = "default"
    message_history: List[Dict[str, str]] = []
    query: str
    project: Dict[str, str] = {}  # path -> content
    model: Optional[str] = None


class RunResponse(BaseModel):
    """Response from creating a run."""

    task_id: str
    stream_token: str


# ============================================================================
# API Endpoints
# ============================================================================


def make_task_id() -> str:
    """Generate a unique task ID."""
    return f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


# ============================================================================
# Models Endpoint (for frontend model selection)
# ============================================================================

models_router = APIRouter(prefix="/api", tags=["vibe"])


@models_router.get("/models")
async def list_models():
    """List available models."""
    # Return model IDs that pi-ai supports
    return {
        "models": [
            "anthropic/claude-sonnet-4-20250514",
            "anthropic/claude-opus-4-20250514",
            "anthropic/claude-3-5-sonnet-20241022",
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
        ]
    }


@router.post("/", response_model=RunResponse)
async def create_run(request: RunRequest) -> RunResponse:
    """
    Create a new agent run.

    Returns a task_id and stream_token for connecting to the SSE stream.
    """
    task_id = make_task_id()

    logger.info(
        f"create_run[{task_id}] model={request.model} "
        f"query_len={len(request.query)} files={len(request.project)}"
    )

    # Store payload
    await set_run_payload(
        task_id,
        {
            "user_id": request.user_id,
            "project_id": request.project_id,
            "message_history": request.message_history,
            "query": request.query,
            "project": request.project,
            "model": request.model,
        },
    )

    # Create token
    stream_token = make_stream_token({"run_id": task_id})

    return RunResponse(task_id=task_id, stream_token=stream_token)


@router.get("/{run_id}/events")
async def run_events(
    run_id: str,
    token: str = Query(..., description="Stream token from create_run"),
):
    """
    SSE event stream for a run.

    Connect to this endpoint after creating a run to receive real-time events.
    """
    # Verify token
    token_payload = read_stream_token(token)
    if token_payload.get("run_id") != run_id:
        raise HTTPException(status_code=400, detail="Token does not match run id")

    # Get payload
    payload = await get_run_payload(run_id)
    if payload is None:

        async def missing_generator() -> AsyncGenerator[str, None]:
            yield sse_format(emit_event(run_id, "run_failed", error="Unknown or expired run id"))

        return StreamingResponse(missing_generator(), headers=SSE_HEADERS)

    # Run agent and stream events
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for chunk in run_agent_flow(payload, run_id):
                yield chunk
        except Exception as e:
            import traceback

            logger.error(f"run_events[{run_id}] error: {e}")
            tb = traceback.format_exc(limit=10)
            yield sse_format(emit_event(run_id, "run_log", data=f"Exception: {e}\n{tb}"))
            yield sse_format(emit_event(run_id, "run_failed", error=str(e)))

    return StreamingResponse(event_generator(), headers=SSE_HEADERS)


@router.get("/{run_id}/resume")
async def resume_run(
    run_id: str,
    token: str = Query(..., description="Resume token"),
    result: str = Query("", description="Execution result"),
):
    """
    Resume a run after code execution.

    Call this after receiving an exec_request event.
    """
    # Verify token
    token_payload = read_stream_token(token)
    if token_payload.get("run_id") != run_id:
        raise HTTPException(status_code=400, detail="Token does not match run id")

    # Get base payload
    base = await get_run_payload(run_id)
    if base is None:

        async def missing_generator() -> AsyncGenerator[str, None]:
            yield sse_format(emit_event(run_id, "run_failed", error="Unknown or expired run id"))

        return StreamingResponse(missing_generator(), headers=SSE_HEADERS)

    # Resume agent
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for chunk in resume_agent_flow(base, run_id, result):
                yield chunk
        except Exception as e:
            yield sse_format(emit_event(run_id, "run_failed", error=str(e)))

    return StreamingResponse(event_generator(), headers=SSE_HEADERS)


# ============================================================================
# Agent Flow
# ============================================================================


async def run_agent_flow(
    payload: Dict[str, Any],
    task_id: str,
) -> AsyncGenerator[str, None]:
    """
    Run the agent and stream events in vibe-compatible format.
    """
    import os

    from nimbus import AgentOS, AgentOSConfig
    from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig
    from nimbus.core.runtime.vcpu import VCPUConfig
    from nimbus.tools import register_default_tools

    yield sse_format(emit_event(task_id, "run_log", data="Agent run starting..."))

    # Setup LLM
    pi_url = os.environ.get("PI_AI_URL", "http://localhost:3031")
    model = payload.get("model") or "anthropic/claude-sonnet-4-20250514"

    pi_config = PiLLMConfig(base_url=pi_url, model=model)
    llm = PiLLMAdapter(pi_config)
    await llm.start()

    try:
        # Create agent
        vcpu_config = VCPUConfig(max_iterations=50)
        config = AgentOSConfig(vcpu_config=vcpu_config)
        agent = AgentOS(llm_client=llm, config=config)

        # Register tools
        workspace = Path.cwd()
        register_default_tools(agent, workspace=workspace)

        # Build input from query and project context
        query = payload.get("query", "")
        project = payload.get("project", {})

        if project:
            # Add project files as context
            file_list = "\n".join(f"- {p}" for p in project.keys())
            input_text = f"""Project files:
{file_list}

User request: {query}"""
        else:
            input_text = query

        # Track tool calls for event emission
        tool_call_counter = 0
        current_tool: Dict[str, Any] = {}

        # Run with streaming
        async for event in agent.run_stream(input_text):
            event_type = event.get("type")

            if event_type == "planning":
                yield sse_format(
                    emit_event(task_id, "run_log", data=event.get("content", "Planning..."))
                )

            elif event_type == "tool_call":
                tool_call_counter += 1
                tool_id = event.get("call_id") or f"call_{tool_call_counter}"
                tool_name = event.get("name", "unknown")
                arguments = event.get("args", {})

                # Store for matching with result
                current_tool = {
                    "id": tool_id,
                    "name": tool_name,
                    "arguments": arguments,
                }

                yield tool_started_sse(task_id, tool_id, tool_name, arguments)

            elif event_type == "tool_result":
                tool_id = event.get("tool_use_id") or current_tool.get(
                    "id", f"call_{tool_call_counter}"
                )
                tool_name = event.get("name") or current_tool.get("name", "unknown")
                arguments = event.get("args") or current_tool.get("arguments", {})
                output = event.get("content", "")

                yield tool_completed_sse(task_id, tool_id, tool_name, arguments, output)

            elif event_type == "text":
                # Assistant thinking/content - emit as log
                content = event.get("content", "")
                if content:
                    yield sse_format(emit_event(task_id, "run_log", data=content))

            elif event_type == "done":
                result = event.get("result", {})
                output = result.get("output", "") if isinstance(result, dict) else str(result)
                yield sse_format(emit_event(task_id, "agent_output", data=output))
                return

            elif event_type == "error":
                error = event.get("message", "Unknown error")
                yield sse_format(emit_event(task_id, "run_failed", error=str(error)))
                return

        # If no explicit done event, send completion
        yield sse_format(emit_event(task_id, "agent_output", data="Task completed"))

    finally:
        await llm.stop()


async def resume_agent_flow(
    base: Dict[str, Any],
    task_id: str,
    exec_result: str,
) -> AsyncGenerator[str, None]:
    """
    Resume agent after code execution.

    For now, just acknowledge the result. Full implementation would
    continue the agent conversation with the execution result.
    """
    yield sse_format(
        emit_event(task_id, "run_log", data=f"Execution result received: {len(exec_result)} chars")
    )

    # Add exec result to context and continue
    # For MVP, just return success
    yield sse_format(
        emit_event(
            task_id,
            "agent_output",
            data=f"Code execution completed.\n\nResult:\n{exec_result[:1000]}...",
        )
    )
