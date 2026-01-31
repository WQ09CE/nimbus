"""
Nimbus v2 Server - Multi-Protocol API Server

This module provides an API server for Nimbus v2 AgentOS with multiple protocol support:
- OpenAI-compatible API (/v1/chat/completions)
- Vercel AI SDK v6 protocol (/api/chat) - for acp-web-client

Key Components:
- api_openai: OpenAI-compatible /v1/chat/completions endpoint
- api_ai_sdk: AI SDK v6 /api/chat endpoint
- app: FastAPI application factory

Usage:
    from nimbus.v2.server import create_app

    # Create the app
    app = create_app()

    # Run with uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

Or via CLI:
    uvicorn nimbus.v2.server:app --host 0.0.0.0 --port 8000
"""

from nimbus.v2.server.app import app, create_app, get_app

__all__ = [
    "create_app",
    "get_app",
    "app",
]
