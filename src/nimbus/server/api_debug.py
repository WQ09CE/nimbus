"""
Debug API endpoints for inspecting agent state.

Endpoints:
- GET /debug/sessions/{id}/context - Get full context for a session
- GET /debug/sessions/{id}/state - Get VCPU state
- GET /debug/sessions/{id}/messages - Get all messages (raw)
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel


async def get_session_manager(request: Request):
    """Get session manager from app state."""
    return request.app.state.session_manager


router = APIRouter(prefix="/debug", tags=["debug"])


class ContextMessage(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class DebugContext(BaseModel):
    session_id: str
    total_messages: int
    total_tokens: int
    pinned_tokens: int
    frame_tokens: int
    messages: List[Dict[str, Any]]


class DebugState(BaseModel):
    session_id: str
    iteration: int
    status: str
    frame_depth: int
    pending_tool_calls: int
    mmu_state: Dict[str, Any]


@router.get("/sessions/{session_id}/context", response_model=DebugContext)
async def get_context(
    session_id: str,
    session_manager=Depends(get_session_manager),
):
    """Get the full assembled context for a session."""
    agent_os = await session_manager.get_or_create_agent(session_id)

    process = agent_os.get_process(session_id) if agent_os else None
    if not process or not process.vcpu:
        raise HTTPException(status_code=404, detail="Session or VCPU not found")

    vcpu = process.vcpu
    mmu = process.mmu

    # Assemble full context
    messages = mmu.assemble_context(filter_discardable=False)

    # Estimate tokens
    pinned = mmu._pinned_context
    pinned_tokens = pinned.token_estimate() if pinned else 0
    frame_tokens = sum(sum(m.token_estimate() for m in frame._messages) for frame in mmu._stack)

    return DebugContext(
        session_id=session_id,
        total_messages=len(messages),
        total_tokens=pinned_tokens + frame_tokens,
        pinned_tokens=pinned_tokens,
        frame_tokens=frame_tokens,
        messages=messages,
    )


@router.get("/sessions/{session_id}/state", response_model=DebugState)
async def get_state(
    session_id: str,
    session_manager=Depends(get_session_manager),
):
    """Get the VCPU state for a session."""
    agent_os = await session_manager.get_or_create_agent(session_id)

    process = agent_os.get_process(session_id) if agent_os else None
    if not process or not process.vcpu:
        raise HTTPException(status_code=404, detail="Session or VCPU not found")

    vcpu = process.vcpu
    state = vcpu.get_state()

    return DebugState(
        session_id=session_id,
        iteration=state.get("iteration", 0),
        status=state.get("status", "unknown"),
        frame_depth=state.get("frame_depth", 0),
        pending_tool_calls=len(state.get("pending_tool_calls", [])),
        mmu_state=state.get("mmu", {}),
    )


@router.get("/sessions/{session_id}/messages")
async def get_messages_raw(
    session_id: str,
    session_manager=Depends(get_session_manager),
):
    """Get raw messages from all frames."""
    agent_os = await session_manager.get_or_create_agent(session_id)

    process = agent_os.get_process(session_id) if agent_os else None
    if not process or not process.vcpu:
        raise HTTPException(status_code=404, detail="Session or VCPU not found")

    mmu = process.mmu

    frames = []
    for i, frame in enumerate(mmu._stack):
        frame_data = {
            "frame_id": frame.frame_id,
            "index": i,
            "state": frame.state,
            "goal": frame.goal,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content[:500] + "..."
                    if m.content and len(m.content) > 500
                    else m.content,
                    "tool_calls": m.tool_calls,
                    "tool_call_id": m.tool_call_id,
                    "name": m.name,
                }
                for m in frame._messages
            ],
        }
        frames.append(frame_data)

    return {
        "session_id": session_id,
        "pinned_context": mmu._pinned_context.to_dict() if mmu._pinned_context else None,
        "frames": frames,
    }
