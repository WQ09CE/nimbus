"""LLM Factory for Nimbus.

This module provides a factory for creating LLM clients based on configuration.
It supports registration of custom providers and automatic provider selection.

Example:
    ```python
    from nimbus.llm.factory import LLMFactory, create_llm_client

    # Create client using default configuration
    client = create_llm_client()

    # Create client for specific provider
    client = create_llm_client(provider="gemini")

    # Create client with custom config
    client = create_llm_client(provider="ollama", model="llama3:8b")

    # Register custom provider
    @LLMFactory.register("my_provider")
    class MyLLMClient:
        def __init__(self, **kwargs):
            pass
        async def complete(self, prompt: str, history: list = None) -> str:
            return "response"
    ```
"""

from typing import Any, Callable, Dict, Optional, Type

from .base import LLMClient, LLMError
from .config import LLMConfig, ProviderConfig, get_global_config, load_config

from ..core.logging import get_logger

logger = get_logger("llm.factory")


class LLMFactory:
    """Factory for creating LLM clients.

    This factory maintains a registry of provider classes and creates
    appropriate clients based on configuration.
    """

    _providers: Dict[str, Type] = {}
    _initialized: bool = False

    @classmethod
    def register(cls, name: str, provider_class: Optional[Type] = None) -> Callable:
        """Register a provider class.

        Can be used as a decorator or direct call:

            # As decorator
            @LLMFactory.register("my_provider")
            class MyClient:
                pass

            # Direct call
            LLMFactory.register("my_provider", MyClient)

        Args:
            name: Provider name for configuration.
            provider_class: Provider class to register.

        Returns:
            Decorator function or the registered class.
        """
        def decorator(klass: Type) -> Type:
            cls._providers[name] = klass
            logger.debug(f"Registered LLM provider: {name}")
            return klass

        if provider_class is not None:
            return decorator(provider_class)
        return decorator

    @classmethod
    def unregister(cls, name: str) -> None:
        """Unregister a provider.

        Args:
            name: Provider name to unregister.
        """
        cls._providers.pop(name, None)

    @classmethod
    def list_providers(cls) -> list:
        """List registered provider names.

        Returns:
            List of registered provider names.
        """
        cls._ensure_initialized()
        return list(cls._providers.keys())

    @classmethod
    def get_provider_class(cls, name: str) -> Optional[Type]:
        """Get provider class by name.

        Args:
            name: Provider name.

        Returns:
            Provider class or None if not found.
        """
        cls._ensure_initialized()
        return cls._providers.get(name)

    @classmethod
    def _ensure_initialized(cls) -> None:
        """Ensure built-in providers are registered."""
        if cls._initialized:
            return

        cls._initialized = True

        # Register built-in providers
        try:
            from .gemini import GeminiClient
            cls.register("gemini", GeminiClient)
        except ImportError as e:
            logger.debug(f"Gemini provider not available: {e}")

        try:
            from .ollama import OllamaClient
            cls.register("ollama", OllamaClient)
        except ImportError as e:
            logger.debug(f"Ollama provider not available: {e}")

        try:
            from .openrouter import OpenRouterClient
            cls.register("openrouter", OpenRouterClient)
        except ImportError as e:
            logger.debug(f"OpenRouter provider not available: {e}")

    @classmethod
    def create(
        cls,
        provider_config: ProviderConfig,
    ) -> LLMClient:
        """Create an LLM client from provider configuration.

        Args:
            provider_config: Provider configuration.

        Returns:
            Configured LLM client.

        Raises:
            LLMError: If provider not found or creation fails.
        """
        cls._ensure_initialized()

        provider_type = provider_config.type
        provider_class = cls._providers.get(provider_type)

        if provider_class is None:
            available = list(cls._providers.keys())
            raise LLMError(
                f"Unknown provider type: {provider_type}. Available: {available}",
                provider=provider_type,
            )

        # Build kwargs from config
        kwargs: Dict[str, Any] = {
            "model": provider_config.model,
        }

        if provider_config.api_key:
            kwargs["api_key"] = provider_config.api_key
        if provider_config.base_url:
            kwargs["base_url"] = provider_config.base_url
        if provider_config.temperature:
            kwargs["temperature"] = provider_config.temperature

        # Handle max_tokens - different providers use different names
        # Gemini uses max_output_tokens, others use max_tokens
        if provider_config.max_tokens:
            if provider_type == "gemini":
                kwargs["max_output_tokens"] = provider_config.max_tokens
            else:
                kwargs["max_tokens"] = provider_config.max_tokens

        # Add extra options
        kwargs.update(provider_config.extra)

        try:
            client = provider_class(**kwargs)
            logger.info(f"Created LLM client: provider={provider_type}, model={provider_config.model}")
            return client
        except Exception as e:
            raise LLMError(
                f"Failed to create {provider_type} client: {e}",
                provider=provider_type,
            ) from e

    @classmethod
    def create_from_config(
        cls,
        config: LLMConfig,
        provider_name: Optional[str] = None,
    ) -> LLMClient:
        """Create an LLM client from full configuration.

        Args:
            config: Full LLM configuration.
            provider_name: Provider name (uses default if not specified).

        Returns:
            Configured LLM client.
        """
        provider_config = config.get_provider(provider_name)
        return cls.create(provider_config)


def create_llm_client(
    provider: Optional[str] = None,
    config: Optional[LLMConfig] = None,
    **overrides: Any,
) -> LLMClient:
    """Create an LLM client with optional overrides.

    This is the main entry point for creating LLM clients.

    Args:
        provider: Provider name. If None, uses default from config.
        config: Configuration to use. If None, uses global config.
        **overrides: Override specific configuration options (model, api_key, etc.)

    Returns:
        Configured LLM client.

    Example:
        # Use default provider
        client = create_llm_client()

        # Use specific provider
        client = create_llm_client(provider="ollama")

        # Override model
        client = create_llm_client(provider="gemini", model="gemini-pro")
    """
    if config is None:
        config = get_global_config()

    # Get base provider config
    provider_config = config.get_provider(provider)

    # Apply overrides
    if overrides:
        config_dict = provider_config.to_dict()
        config_dict.update(overrides)

        from .config import parse_provider_config
        provider_config = parse_provider_config(provider_config.type, config_dict)

    return LLMFactory.create(provider_config)


async def create_default_client() -> LLMClient:
    """Create a default LLM client (async convenience function).

    This is designed for use in async contexts where you want a quick
    default client without worrying about configuration.

    Returns:
        Default configured LLM client.
    """
    return create_llm_client()
