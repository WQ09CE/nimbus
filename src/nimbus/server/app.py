"""FastAPI Application Factory for Nimbus Server.

This module provides:
- create_app: Factory function to create the FastAPI application
- Lifespan management for startup/shutdown
- Middleware configuration (CORS, logging)
- API route registration
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .sse import SSEHub
from .session import SessionManager
from .permission import PermissionManager
from .message_cache import MessageCache
from .log_hub import log_hub, setup_log_hub_handler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage application lifespan.

    On startup:
    - Initialize storage
    - Start SSE hub
    - Initialize session manager

    On shutdown:
    - Close all sessions
    - Stop SSE hub
    - Close storage connections
    """
    # Get configuration from environment
    db_path = os.environ.get("NIMBUS_DB", ".nimbus/nimbus.db")

    # Import here to avoid circular imports
    from nimbus.storage.sqlite import SQLiteStorage

    # Initialize components
    storage = SQLiteStorage(db_path)
    await storage.initialize()

    sse_hub = SSEHub()
    await sse_hub.start()

    permission_manager = PermissionManager()
    session_manager = SessionManager(storage, sse_hub, permission_manager)

    # Initialize message cache for conversation history
    message_cache = MessageCache(
        storage=storage,
        max_messages=50,
        cache_ttl_minutes=30,
    )

    # Set up log hub for real-time log streaming
    setup_log_hub_handler(log_hub)

    # Store in app state
    app.state.storage = storage
    app.state.log_hub = log_hub
    app.state.sse_hub = sse_hub
    app.state.permission_manager = permission_manager
    app.state.session_manager = session_manager
    app.state.message_cache = message_cache

    yield

    # Cleanup
    await session_manager.close_all()
    await sse_hub.stop()
    await storage.close()


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

    # Configure CORS - specific origins for credentials support
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    from .api import router
    app.include_router(router, prefix="/api/v1")

    # Register OpenCode compatible routes (no prefix)
    from .compat import opencode_router
    app.include_router(opencode_router, tags=["opencode"])

    # Register AI SDK compatible routes (Vercel AI SDK Data Protocol)
    from .api_ai_sdk import router as ai_sdk_router
    app.include_router(ai_sdk_router, tags=["AI SDK"])

    # Register frontend logging routes
    from .api_logs import router as logs_router
    app.include_router(logs_router, tags=["Logs"])

    return app


# For uvicorn factory mode
def get_app() -> FastAPI:
    """Get the FastAPI application (alias for create_app)."""
    return create_app()


# Create the app instance for uvicorn
app = create_app()
