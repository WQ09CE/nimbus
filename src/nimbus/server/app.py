"""FastAPI Application Factory for Nimbus Server.

This module provides:
- create_app: Factory function to create the FastAPI application
- Lifespan management for startup/shutdown
- Middleware configuration (CORS, logging)
- API route registration
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .log_hub import log_hub, setup_log_hub_handler
from .permission import PermissionManager
from .sse import SSEHub

logger = logging.getLogger(__name__)


# Coroutines the serve command runs on SIGTERM/SIGINT BEFORE letting uvicorn exit
# (uvicorn's own shutdown would first drain SSE connections, which never close).
GRACEFUL_HOOKS: list = []


# Idempotent teardown of the app's background machinery (SSE hub, ledger, handoff
# bus). Registered by lifespan; run by the graceful path because uvicorn's
# force_exit skips lifespan shutdown, and an SSE generator that is never closed
# keeps the process alive past its handoff.
TEARDOWN_HOOKS: list = []


async def run_graceful_hooks(timeout_s: float = 45.0) -> None:
    for hook in list(GRACEFUL_HOOKS):
        try:
            await asyncio.wait_for(hook(), timeout=timeout_s)
        except Exception as e:  # never block exit
            logger.error("graceful hook failed: %s", e)
    for hook in list(TEARDOWN_HOOKS):
        try:
            await asyncio.wait_for(hook(), timeout=10.0)
        except Exception as e:
            logger.error("teardown hook failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage application lifespan.

    On startup:
    - Start SSE hub
    - Initialize session manager
    - Set up log hub

    On shutdown:
    - Close all sessions
    - Stop SSE hub
    - Remove loguru handlers
    """
    sse_hub = SSEHub()
    await sse_hub.start()

    permission_manager = PermissionManager()

    # Use v2 session manager (AgentOS-based)
    from .session import SessionManagerV2

    # Multi-pod bookkeeping (nimbus-lab): heartbeat + turn ownership + orphan scanner.
    ledger = None
    if ledger_url := os.environ.get("NIMBUS_LEDGER_URL"):
        from nimbus.core.session_log import SESSION_LOG_CONTRACT
        from nimbus.infra.ledger import Ledger

        ledger = Ledger(
            ledger_url,
            pod_id=os.environ.get("NIMBUS_POD_ID") or f"pid-{os.getpid()}",
            port=int(os.environ.get("NIMBUS_PORT", "0") or 0),
            generation=int(os.environ.get("NIMBUS_GENERATION", "0") or 0),
            contract=SESSION_LOG_CONTRACT,
        )
        await ledger.start()
    session_manager = SessionManagerV2(
        sse_hub=sse_hub,
        permission_manager=permission_manager,
        ledger=ledger,
    )
    if ledger is not None:
        ledger.on_orphan = session_manager.on_orphan
        ledger.on_stranded = session_manager.on_stranded
    # Graceful handoff bus (nimbus-lab R3.3): SIGTERM pauses sessions at seams
    # and announces them; peers consume and resume.
    handoff = None
    if handoff_url := os.environ.get("NIMBUS_HANDOFF_URL"):
        from nimbus.infra.handoff import HandoffBus

        handoff = HandoffBus(handoff_url, pod_id=os.environ.get("NIMBUS_POD_ID") or f"pid-{os.getpid()}")
        await handoff.connect()
        await handoff.start_consumer(session_manager.on_handoff)

        async def _graceful() -> None:
            paused = await session_manager.handoff_all(handoff)
            logger.warning("graceful shutdown: handed off %d session(s)", len(paused))

        GRACEFUL_HOOKS.append(_graceful)
        if ledger is not None:
            # R5: a newer deploy generation is alive → leave the handoff queue group so
            # announcements land on pods that will outlive the rollout.
            async def _superseded() -> None:
                await handoff.stop_consuming()
                logger.warning("superseded by a newer generation: left the handoff queue group")

            ledger.on_superseded = _superseded
            if ledger.superseded:
                await _superseded()
    app.state.handoff = handoff

    # Set up log hub for real-time log streaming
    setup_log_hub_handler(log_hub)

    # Store in app state
    app.state.log_hub = log_hub
    app.state.sse_hub = sse_hub
    app.state.permission_manager = permission_manager
    app.state.session_manager = session_manager

    torn_down = False

    async def _teardown() -> None:
        nonlocal torn_down
        if torn_down:
            return
        torn_down = True
        await session_manager.close_all()
        if ledger is not None:
            await ledger.stop()
        if handoff is not None:
            await handoff.close()
        await sse_hub.stop()

    TEARDOWN_HOOKS.append(_teardown)
    yield

    # Cleanup (no-op if the graceful path already ran it)
    await _teardown()
    GRACEFUL_HOOKS.clear()
    TEARDOWN_HOOKS.clear()

    # Flush logs and remove handlers to prevent semaphore leaks (resource_tracker warning)
    from nimbus.core.logging import logger as loguru_logger

    loguru_logger.remove()


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(
        title="Nimbus API",
        description="Nimbus Agent Framework API - OpenWork Integration",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Configure CORS - allow all origins for development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,  # Cannot use credentials with allow_origins=["*"]
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # Middleware to support Private Network Access (CORS)
    @app.middleware("http")
    async def add_private_network_access_header(request: Request, call_next):
        """
        Handle Private Network Access headers.
        If request asks for private network access, allow it in response.
        """
        response = await call_next(request)
        if request.headers.get("Access-Control-Request-Private-Network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    # Add global exception handler to ensure CORS headers on errors
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Handle all unhandled exceptions with proper CORS headers."""
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "detail": str(exc),
                "type": type(exc).__name__,
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "*",
                "Access-Control-Allow-Headers": "*",
            },
        )

    # Register routes
    from .api import router

    app.include_router(router, prefix="/api/v1")

    # OpenCode compatibility layer removed



    # Serve static chat UI
    from pathlib import Path

    from fastapi.responses import FileResponse

    static_dir = Path(__file__).parent / "static"

    @app.get("/chat")
    async def serve_chat_ui():
        """Serve the built-in chat UI."""
        return FileResponse(static_dir / "chat.html")

    return app


# For uvicorn factory mode
def get_app() -> FastAPI:
    """Get the FastAPI application (alias for create_app)."""
    return create_app()


# Create the app instance for uvicorn
app = create_app()
