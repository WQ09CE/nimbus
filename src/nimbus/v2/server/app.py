"""
FastAPI Application Factory for Nimbus v2 Server.

This module provides:
- create_app: Factory function to create the FastAPI application
- Lifespan management for AgentOS initialization
- CORS and middleware configuration
- OpenAI-compatible API route registration

Usage:
    # Programmatic
    from nimbus.v2.server import create_app
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)

    # CLI
    uvicorn nimbus.v2.server:app --host 0.0.0.0 --port 8000

Environment Variables:
    GEMINI_API_KEY: Google Gemini API key
    ANTHROPIC_API_KEY: Anthropic API key
    OPENROUTER_API_KEY: OpenRouter API key
    NIMBUS_LLM_PROVIDER: LLM provider (gemini, anthropic, openrouter) - default: gemini
    NIMBUS_LLM_MODEL: Model name - default: gemini-2.0-flash
    NIMBUS_PORT: Server port - default: 8000
    NIMBUS_HOST: Server host - default: 0.0.0.0

Config File (~/.nimbus/config.json):
    {
      "llm": {
        "default_provider": "gemini",
        "providers": {
          "gemini": {
            "api_key": "...",
            "model": "gemini-2.0-flash"
          },
          "anthropic": {
            "api_key": "...",
            "model": "claude-sonnet-4-20250514"
          }
        }
      }
    }
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


def load_config() -> Dict[str, Any]:
    """
    Load configuration from config file and environment.

    Priority (highest to lowest):
    1. Environment variables
    2. Config file (~/.nimbus/config.json)
    3. Defaults
    """
    config: Dict[str, Any] = {
        "llm": {
            "default_provider": "gemini",
            "providers": {},
        },
        "server": {
            "host": "0.0.0.0",
            "port": 8000,
        },
    }

    # Load from config file
    config_path = Path.home() / ".nimbus" / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                file_config = json.load(f)
                # Merge llm config
                if "llm" in file_config:
                    config["llm"].update(file_config["llm"])
                # Merge server config
                if "server" in file_config:
                    config["server"].update(file_config["server"])
        except Exception as e:
            logger.warning(f"Failed to load config file: {e}")

    # Override with environment variables
    if os.environ.get("NIMBUS_LLM_PROVIDER"):
        config["llm"]["default_provider"] = os.environ["NIMBUS_LLM_PROVIDER"]

    if os.environ.get("NIMBUS_HOST"):
        config["server"]["host"] = os.environ["NIMBUS_HOST"]

    if os.environ.get("NIMBUS_PORT"):
        config["server"]["port"] = int(os.environ["NIMBUS_PORT"])

    return config


def create_llm_client(config: Dict[str, Any]):
    """
    Create an LLM client based on configuration.

    Args:
        config: Configuration dictionary

    Returns:
        LLM client instance or None if configuration is missing
    """
    llm_config = config.get("llm", {})
    provider = llm_config.get("default_provider", "gemini")
    providers = llm_config.get("providers", {})

    # Get provider-specific config
    provider_config = providers.get(provider, {})

    # Try to get API key from config, then environment
    api_key = provider_config.get("api_key")
    model = provider_config.get("model")

    if provider == "gemini":
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        model = model or os.environ.get("NIMBUS_LLM_MODEL", "gemini-2.0-flash")

        if not api_key:
            logger.warning("No Gemini API key found")
            return None

        from nimbus.v2.llm import GeminiV2Client

        logger.info(f"Creating Gemini client with model: {model}")
        return GeminiV2Client(api_key=api_key, model=model)

    elif provider == "anthropic":
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        model = model or os.environ.get("NIMBUS_LLM_MODEL", "claude-sonnet-4-20250514")

        if not api_key:
            logger.warning("No Anthropic API key found")
            return None

        from nimbus.v2.llm import AnthropicV2Client

        logger.info(f"Creating Anthropic client with model: {model}")
        return AnthropicV2Client(api_key=api_key, model=model)

    elif provider == "openrouter":
        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        model = model or os.environ.get("NIMBUS_LLM_MODEL", "anthropic/claude-sonnet-4")

        if not api_key:
            logger.warning("No OpenRouter API key found")
            return None

        from nimbus.v2.llm import OpenRouterV2Client

        logger.info(f"Creating OpenRouter client with model: {model}")
        return OpenRouterV2Client(api_key=api_key, model=model)

    else:
        logger.warning(f"Unknown LLM provider: {provider}")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage application lifespan.

    On startup:
    - Load configuration
    - Initialize LLM client
    - Create AgentOS

    On shutdown:
    - Cleanup resources
    """
    logger.info("Starting Nimbus v2 Server...")

    # Load configuration
    config = load_config()
    app.state.config = config

    # Create LLM client
    llm_client = create_llm_client(config)

    if llm_client:
        try:
            from nimbus.v2.agentos import create_agent_os

            # Create AgentOS with default tools
            agent_os = create_agent_os(
                llm_client=llm_client,
                system_rules="You are a helpful coding assistant. Be concise and precise.",
                workspace=Path.cwd(),
                register_defaults=True,
            )

            app.state.agent_os = agent_os
            app.state.default_workspace = Path.cwd()
            app.state.workspace_agents = {}  # Cache for workspace-specific AgentOS instances
            logger.info("AgentOS initialized successfully")
            logger.info(f"Default workspace: {Path.cwd()}")
            logger.info(f"Available tools: {agent_os.list_tools()}")

        except Exception as e:
            logger.error(f"Failed to create AgentOS: {e}")
            app.state.agent_os = None
    else:
        logger.warning("No LLM client available, AgentOS not initialized")
        app.state.agent_os = None

    yield

    # Cleanup
    logger.info("Shutting down Nimbus v2 Server...")
    if hasattr(app.state, "agent_os") and app.state.agent_os:
        # End any active sessions
        for pid in app.state.agent_os.list_processes():
            try:
                app.state.agent_os.kill(pid)
            except Exception:
                pass


def create_app(
    llm_client: Optional[Any] = None,
    config: Optional[Dict[str, Any]] = None,
) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        llm_client: Optional pre-configured LLM client. If not provided,
                    one will be created from config/environment.
        config: Optional configuration dictionary. If not provided,
                configuration is loaded from file/environment.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(
        title="Nimbus v2 API",
        description="OpenAI-compatible API for Nimbus v2 AgentOS",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Store optional pre-configured components
    if llm_client:
        app.state._preset_llm_client = llm_client
    if config:
        app.state._preset_config = config

    # Configure CORS - allow all origins for API access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register OpenAI-compatible routes
    from .api_openai import router as openai_router

    app.include_router(openai_router)

    # Register AI SDK v6 routes (for acp-web-client compatibility)
    from .api_ai_sdk import router as ai_sdk_router

    app.include_router(ai_sdk_router)

    # Add root endpoint
    @app.get("/")
    async def root():
        return {
            "name": "Nimbus v2 API Server",
            "version": "2.0.0",
            "api": ["OpenAI-compatible", "AI SDK v6"],
            "endpoints": {
                "chat_openai": "/v1/chat/completions",
                "chat_ai_sdk": "/api/chat",
                "sessions": "/api/v1/sessions",
                "models": "/v1/models",
                "health": "/v1/health",
            },
        }

    return app


def get_app() -> FastAPI:
    """Get the FastAPI application (alias for create_app)."""
    return create_app()


# Create the app instance for uvicorn
app = create_app()


# =============================================================================
# CLI Entry Point
# =============================================================================


def main():
    """CLI entry point for running the server."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Nimbus v2 API Server")
    parser.add_argument(
        "--host",
        default=os.environ.get("NIMBUS_HOST", "0.0.0.0"),
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("NIMBUS_PORT", "8000")),
        help="Port to bind to (default: 8000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Log level (default: info)",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    print(f"Starting Nimbus v2 Server on {args.host}:{args.port}")
    print("API endpoints:")
    print("  OpenAI-compatible:")
    print(f"    - POST http://{args.host}:{args.port}/v1/chat/completions")
    print(f"    - GET  http://{args.host}:{args.port}/v1/models")
    print(f"    - GET  http://{args.host}:{args.port}/v1/health")
    print("  AI SDK v6 (acp-web-client):")
    print(f"    - POST http://{args.host}:{args.port}/api/chat")
    print(f"    - POST http://{args.host}:{args.port}/api/v1/sessions")

    uvicorn.run(
        "nimbus.v2.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
