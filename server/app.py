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

    # Store in app state
    app.state.storage = storage
    app.state.sse_hub = sse_hub
    app.state.permission_manager = permission_manager
    app.state.session_manager = session_manager

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

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    from .api import router
    app.include_router(router, prefix="/api/v1")

    return app


# For uvicorn factory mode
def get_app() -> FastAPI:
    """Get the FastAPI application (alias for create_app)."""
    return create_app()
