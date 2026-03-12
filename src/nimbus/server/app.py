"""FastAPI Application Factory for Nimbus Server.

This module provides:
- create_app: Factory function to create the FastAPI application
- Lifespan management for startup/shutdown
- Middleware configuration (CORS, logging)
- API route registration
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .log_hub import log_hub, setup_log_hub_handler
from .permission import PermissionManager
from .sse import SSEHub

logger = logging.getLogger(__name__)


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

    session_manager = SessionManagerV2(
        sse_hub=sse_hub,
        permission_manager=permission_manager,
    )

    # Set up log hub for real-time log streaming
    setup_log_hub_handler(log_hub)

    # Store in app state
    app.state.log_hub = log_hub
    app.state.sse_hub = sse_hub
    app.state.permission_manager = permission_manager
    app.state.session_manager = session_manager

    yield

    # Cleanup
    await session_manager.close_all()
    await sse_hub.stop()

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
