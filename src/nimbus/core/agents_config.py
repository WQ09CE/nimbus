"""Unified Agent Model Configuration.

This module provides centralized model configuration for all agents.
Models are configured in ~/.nimbus/config.json under the "agents" key.

Configuration Format:
    ```json
    {
      "llm": { ... },
      "agents": {
        "core": "anthropic/claude-sonnet-4.5",
        "coder": "anthropic/claude-haiku-4.5",
        "explorer": null,
        "reviewer": null
      }
    }
    ```

When agent model is null or not specified, it inherits from:
1. Parent agent's model (for subagents)
2. Default LLM provider's model (for core agent)

Usage:
    from nimbus.core.agents_config import get_agent_model, get_agents_config

    # Get model for specific agent
    model = get_agent_model("coder")  # -> "anthropic/claude-haiku-4.5"
    model = get_agent_model("explorer")  # -> None (inherit)

    # Get all agent configs
    config = get_agents_config()
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .logging import get_logger

logger = get_logger("agents_config")

# Cache for agents config
_agents_config: Optional[Dict[str, Optional[str]]] = None


def _find_config_file() -> Optional[Path]:
    """Find the config.json file.

    Search order:
    1. .nimbus/config.json in current directory
    2. ~/.nimbus/config.json in home directory

    Returns:
        Path to config file or None if not found.
    """
    # Local config
    local_config = Path(".nimbus/config.json")
    if local_config.exists():
        return local_config

    # Home config
    home_config = Path.home() / ".nimbus" / "config.json"
    if home_config.exists():
        return home_config

    return None


def _load_agents_config() -> Dict[str, Optional[str]]:
    """Load agents configuration from config.json.

    Returns:
        Dictionary mapping agent names to model names.
        Model value of None means inherit from parent/default.
    """
    config_path = _find_config_file()
    if not config_path:
        logger.debug("No config.json found, using empty agents config")
        return {}

    try:
        with open(config_path, "r") as f:
            data = json.load(f)

        agents = data.get("agents", {})
        logger.info(f"Loaded agents config from {config_path}: {list(agents.keys())}")
        return agents

    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in {config_path}: {e}")
        return {}
    except Exception as e:
        logger.warning(f"Failed to load agents config: {e}")
        return {}


def get_agents_config() -> Dict[str, Optional[str]]:
    """Get the cached agents configuration.

    Returns:
        Dictionary mapping agent names to model names.
    """
    global _agents_config
    if _agents_config is None:
        _agents_config = _load_agents_config()
    return _agents_config


def reset_agents_config() -> None:
    """Reset the cached agents config (for testing)."""
    global _agents_config
    _agents_config = None


def get_agent_model(agent_name: str) -> Optional[str]:
    """Get the model for a specific agent.

    Args:
        agent_name: Name of the agent (core, coder, explorer, etc.)

    Returns:
        Model name string, or None if not configured (inherit from parent).
    """
    config = get_agents_config()
    return config.get(agent_name)


def detect_provider_from_model(model: str) -> Optional[str]:
    """Detect LLM provider from model name.

    Args:
        model: Model name (e.g., "anthropic/claude-sonnet-4.5", "gemini-2.0-flash").

    Returns:
        Provider name or None for auto-detection.
    """
    model_lower = model.lower()

    # OpenRouter format: provider/model
    if "/" in model:
        provider_part = model.split("/")[0]
        # Map OpenRouter provider prefixes
        if provider_part in ("anthropic", "openai", "google", "meta-llama", "mistralai"):
            return "openrouter"

    # Gemini models
    if model_lower.startswith("gemini"):
        return "gemini"

    # OpenAI models (via OpenRouter)
    if model_lower.startswith(("gpt-", "o1-", "o3-")):
        return "openrouter"

    # Anthropic models (via OpenRouter)
    if model_lower.startswith("claude"):
        return "openrouter"

    # Ollama/local models (contains colon like qwen3:8b)
    if ":" in model:
        return "ollama"

    # Default to None (use default provider)
    return None


def create_llm_client_for_agent(agent_name: str, fallback_client: Any = None) -> Any:
    """Create an LLM client for the specified agent.

    Args:
        agent_name: Name of the agent.
        fallback_client: Client to return if no specific model is configured.

    Returns:
        LLM client instance.
    """
    model = get_agent_model(agent_name)

    # No specific model - use fallback
    if not model:
        if fallback_client:
            return fallback_client
        # Create default client
        from nimbus.llm import create_llm_client
        return create_llm_client()

    # Create client for specific model
    from nimbus.llm import create_llm_client

    provider = detect_provider_from_model(model)
    logger.info(f"Creating LLM client for agent '{agent_name}': provider={provider}, model={model}")

    return create_llm_client(provider=provider, model=model)
