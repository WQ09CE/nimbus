"""
OpenAI Compatible API Endpoints for Nimbus v2

This module provides OpenAI API-compatible endpoints:
- POST /v1/chat/completions - Chat completions (streaming and non-streaming)
- GET /v1/models - List available models

The streaming format follows OpenAI's Server-Sent Events (SSE) format exactly,
making it compatible with clients like elia that expect OpenAI API format.
"""

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1", tags=["OpenAI Compatible"])


# =============================================================================
# Request/Response Models
# =============================================================================


class ChatMessage(BaseModel):
    """OpenAI chat message format."""

    role: str  # "system", "user", "assistant"
    content: str


class ChatCompletionRequest(BaseModel):
    """OpenAI chat completion request format."""

    model: str = "nimbus"
    messages: List[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stop: Optional[Union[str, List[str]]] = None
    user: Optional[str] = None


class ChatCompletionChoice(BaseModel):
    """OpenAI chat completion choice."""

    index: int = 0
    message: ChatMessage
    finish_reason: Optional[str] = "stop"


class UsageInfo(BaseModel):
    """Token usage information."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    """OpenAI chat completion response format."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: UsageInfo = Field(default_factory=UsageInfo)


class ModelInfo(BaseModel):
    """OpenAI model information."""

    id: str
    object: str = "model"
    created: int
    owned_by: str = "nimbus"


class ModelListResponse(BaseModel):
    """OpenAI model list response."""

    object: str = "list"
    data: List[ModelInfo]


# =============================================================================
# Helper Functions
# =============================================================================


def generate_completion_id() -> str:
    """Generate a unique completion ID."""
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def create_stream_chunk(
    completion_id: str,
    model: str,
    created: int,
    delta: Dict[str, Any],
    finish_reason: Optional[str] = None,
) -> str:
    """Create a streaming chunk in OpenAI SSE format."""
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def extract_session_id(request: Request) -> Optional[str]:
    """Extract session ID from request headers or parameters."""
    # Try X-Session-ID header
    session_id = request.headers.get("X-Session-ID")
    if session_id:
        return session_id

    # Try query parameter
    session_id = request.query_params.get("session_id")
    if session_id:
        return session_id

    return None


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/models", response_model=ModelListResponse)
async def list_models() -> ModelListResponse:
    """
    List available models.

    Returns a list of models available for chat completions.
    """
    return ModelListResponse(
        data=[
            ModelInfo(
                id="nimbus",
                created=int(time.time()),
                owned_by="nimbus",
            ),
            ModelInfo(
                id="nimbus-v2",
                created=int(time.time()),
                owned_by="nimbus",
            ),
        ]
    )


@router.post("/chat/completions", response_model=None)
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
):
    """
    Create a chat completion.

    Supports both streaming and non-streaming modes.
    - Non-streaming: Returns a complete ChatCompletionResponse
    - Streaming: Returns SSE stream in OpenAI format

    The session ID can be provided via:
    - X-Session-ID header
    - session_id query parameter

    If no session ID is provided, a new session is created for each request.
    """
    # Get AgentOS from app state
    agent_os = getattr(request.app.state, "agent_os", None)
    if agent_os is None:
        raise HTTPException(status_code=503, detail="AgentOS not initialized")

    # Extract session ID
    session_id = extract_session_id(request)

    # Build the user message from the messages list
    # We take the last user message as the current query
    user_message = None

    for msg in body.messages:
        if msg.role == "user":
            # Take the last user message as the current query
            user_message = msg.content

    if not user_message:
        raise HTTPException(status_code=400, detail="No user message provided")

    # Prepare the message for AgentOS
    # Note: System prompts are configured in AgentOS config, not passed per-request
    message_to_send = user_message

    if body.stream:
        return StreamingResponse(
            stream_response(
                agent_os=agent_os,
                message=message_to_send,
                session_id=session_id,
                model=body.model,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        return await non_streaming_response(
            agent_os=agent_os,
            message=message_to_send,
            session_id=session_id,
            model=body.model,
        )


async def stream_response(
    agent_os: Any,
    message: str,
    session_id: Optional[str],
    model: str,
):
    """
    Generate streaming response in OpenAI SSE format.

    Yields:
        SSE chunks in OpenAI format
    """
    completion_id = generate_completion_id()
    created = int(time.time())

    try:
        # Send initial chunk with role
        yield create_stream_chunk(
            completion_id=completion_id,
            model=model,
            created=created,
            delta={"role": "assistant"},
        )

        # Execute the chat request
        result = await agent_os.chat(message, session_id=session_id)

        # Get the output text
        if result.status == "OK":
            output_text = result.output if isinstance(result.output, str) else str(result.output)
        else:
            # Handle error
            error_msg = result.fault.message if result.fault else "Unknown error"
            output_text = f"Error: {error_msg}"

        # Stream the content in chunks
        # For simplicity, we send the entire content in one chunk
        # In a more sophisticated implementation, we could split it up
        if output_text:
            yield create_stream_chunk(
                completion_id=completion_id,
                model=model,
                created=created,
                delta={"content": output_text},
            )

        # Send final chunk with finish_reason
        yield create_stream_chunk(
            completion_id=completion_id,
            model=model,
            created=created,
            delta={},
            finish_reason="stop",
        )

        # Send done marker
        yield "data: [DONE]\n\n"

    except Exception as e:
        # Send error as content
        yield create_stream_chunk(
            completion_id=completion_id,
            model=model,
            created=created,
            delta={"content": f"Error: {str(e)}"},
        )
        yield create_stream_chunk(
            completion_id=completion_id,
            model=model,
            created=created,
            delta={},
            finish_reason="stop",
        )
        yield "data: [DONE]\n\n"


async def non_streaming_response(
    agent_os: Any,
    message: str,
    session_id: Optional[str],
    model: str,
) -> ChatCompletionResponse:
    """
    Generate non-streaming response in OpenAI format.

    Returns:
        ChatCompletionResponse with the complete response
    """
    completion_id = generate_completion_id()
    created = int(time.time())

    try:
        # Execute the chat request
        result = await agent_os.chat(message, session_id=session_id)

        # Get the output text
        if result.status == "OK":
            output_text = result.output if isinstance(result.output, str) else str(result.output)
            finish_reason = "stop"
        else:
            # Handle error
            error_msg = result.fault.message if result.fault else "Unknown error"
            output_text = f"Error: {error_msg}"
            finish_reason = "stop"

        return ChatCompletionResponse(
            id=completion_id,
            created=created,
            model=model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=output_text),
                    finish_reason=finish_reason,
                )
            ],
            usage=UsageInfo(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            ),
        )

    except Exception as e:
        return ChatCompletionResponse(
            id=completion_id,
            created=created,
            model=model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=f"Error: {str(e)}"),
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(),
        )


# =============================================================================
# Health Check
# =============================================================================


@router.get("/health")
async def health_check(request: Request) -> Dict[str, Any]:
    """Health check endpoint."""
    agent_os = getattr(request.app.state, "agent_os", None)
    return {
        "status": "ok" if agent_os else "degraded",
        "agent_os": "initialized" if agent_os else "not_initialized",
        "version": "v2",
    }
