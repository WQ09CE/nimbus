"""FastAPI Application Factory for Nimbus Server.

This module provides:
- create_app: Factory function to create the FastAPI application
- Lifespan management for startup/shutdown
- Middleware configuration (CORS, logging)
- API route registration
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .log_hub import log_hub, setup_log_hub_handler
from .message_cache import MessageCache
from .permission import PermissionManager
from .sse import SSEHub

logger = logging.getLogger(__name__)


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

    # Use v2 session manager (AgentOS-based)
    from .session_v2 import SessionManagerV2

    session_manager = SessionManagerV2(storage, sse_hub, permission_manager)

    # Initialize message cache for conversation history
    message_cache = MessageCache(
        storage=storage,
        max_messages=50,
        cache_ttl_minutes=30,
    )

    # Set up log hub for real-time log streaming
    setup_log_hub_handler(log_hub)

    # Initialize default AgentOS for /api/chat endpoint
    from pathlib import Path

    from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig
    from nimbus.agentos import AgentOS, AgentOSConfig
    from nimbus.core.runtime.vcpu import VCPUConfig

    pi_url = os.environ.get("PI_AI_URL", "http://localhost:3031")
    model = os.environ.get("NIMBUS_MODEL", "google-antigravity/gemini-3-pro-high")

    pi_config = PiLLMConfig(base_url=pi_url, model=model)
    llm = PiLLMAdapter(pi_config)
    await llm.start()

    # Config skills path
    skill_paths = [Path("examples/skills")]
    
    vcpu_config = VCPUConfig(max_iterations=50)
    agent_config = AgentOSConfig(
        vcpu_config=vcpu_config,
        skill_paths=skill_paths
    )

    agent_os = AgentOS(llm_client=llm, config=agent_config)

    # Register default tools
    from nimbus.tools import register_default_tools

    workspace = Path.cwd()
    register_default_tools(agent_os, workspace=workspace)

    logger.info(f"Initialized default AgentOS with model={model}, workspace={workspace}")
    logger.info(f"Loaded skills from: {skill_paths}")

    # Store in app state
    app.state.storage = storage
    app.state.log_hub = log_hub
    app.state.sse_hub = sse_hub
    app.state.permission_manager = permission_manager
    app.state.session_manager = session_manager
    app.state.message_cache = message_cache
    app.state.agent_os = agent_os
    app.state.default_workspace = workspace
    app.state.workspace_agents = {}  # Cache for workspace-specific agents
    app.state.llm = llm  # Keep reference to close on shutdown

    yield

    # Cleanup LLM adapter
    await llm.stop()

    # Cleanup
    await session_manager.close_all()
    await sse_hub.stop()
    await storage.close()

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

    # Register OpenCode compatible routes (no prefix)
    from .compat import opencode_router

    app.include_router(opencode_router, tags=["opencode"])

    # Register AI SDK compatible routes (Vercel AI SDK Data Protocol)
    from .api_ai_sdk import router as ai_sdk_router

    app.include_router(ai_sdk_router, tags=["AI SDK"])

    # Register frontend logging routes
    from .api_logs import router as logs_router

    app.include_router(logs_router, tags=["Logs"])

    # Register debug routes (for inspecting agent state)
    from .api_debug import router as debug_router

    app.include_router(debug_router, tags=["Debug"])

    # Register Vibe Coding IDE compatible routes
    from .api_vibe import models_router as vibe_models_router
    from .api_vibe import router as vibe_router

    app.include_router(vibe_router, tags=["Vibe IDE"])
    app.include_router(vibe_models_router, tags=["Vibe IDE"])

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
