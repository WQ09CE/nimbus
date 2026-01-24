"""LLM Provider Adapters for Nimbus.

This module provides LLM client implementations for various providers:
- Gemini (Google AI)
- Ollama (local models)
- OpenRouter (multi-provider gateway)

The module also provides a flexible configuration system and factory for
creating LLM clients based on configuration.

Basic Usage:
    ```python
    from nimbus.llm import GeminiClient, OllamaClient

    # Create specific client directly
    client = GeminiClient(api_key="your-api-key")

    # Or use environment variable
    client = GeminiClient()  # Uses GEMINI_API_KEY
    ```

Factory Usage:
    ```python
    from nimbus.llm import create_llm_client, LLMFactory

    # Create client using default configuration
    client = create_llm_client()

    # Create client for specific provider
    client = create_llm_client(provider="ollama")

    # Create with overrides
    client = create_llm_client(provider="gemini", model="gemini-pro")
    ```

Configuration:
    ```python
    from nimbus.llm import load_config, LLMConfig

    # Load from file or environment
    config = load_config()

    # Get provider configuration
    provider = config.get_provider("gemini")
    ```

Environment Variables:
    NIMBUS_LLM_PROVIDER: Default provider name
    NIMBUS_LLM_CONFIG: Path to configuration file
    GEMINI_API_KEY: API key for Gemini
    OPENROUTER_API_KEY: API key for OpenRouter
    NIMBUS_LLM_URL: Ollama server URL
    NIMBUS_LLM_MODEL: Default model name
"""

# Base protocol and errors
from .base import LLMClient, BaseLLMClient, LLMError

# Configuration
from .config import (
    LLMConfig,
    ProviderConfig,
    load_config,
    get_global_config,
    set_global_config,
    reset_global_config,
)

# Factory
from .factory import (
    LLMFactory,
    create_llm_client,
    create_default_client,
)

# Providers
from .gemini import GeminiClient, GeminiConfig, GeminiError
from .ollama import OllamaClient, OllamaConfig, OllamaError
from .openrouter import OpenRouterClient, OpenRouterConfig, OpenRouterError

__all__ = [
    # Protocol and base
    "LLMClient",
    "BaseLLMClient",
    "LLMError",
    # Configuration
    "LLMConfig",
    "ProviderConfig",
    "load_config",
    "get_global_config",
    "set_global_config",
    "reset_global_config",
    # Factory
    "LLMFactory",
    "create_llm_client",
    "create_default_client",
    # Gemini
    "GeminiClient",
    "GeminiConfig",
    "GeminiError",
    # Ollama
    "OllamaClient",
    "OllamaConfig",
    "OllamaError",
    # OpenRouter
    "OpenRouterClient",
    "OpenRouterConfig",
    "OpenRouterError",
]
