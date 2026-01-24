"""Tests for LLM Factory."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from nimbus.llm.factory import LLMFactory, create_llm_client
from nimbus.llm.config import LLMConfig, ProviderConfig, reset_global_config
from nimbus.llm.base import LLMError


class TestLLMFactory:
    """Tests for LLMFactory."""

    def setup_method(self):
        """Reset state before each test."""
        reset_global_config()
        # Clear any previously registered providers for clean tests
        LLMFactory._initialized = False
        LLMFactory._providers.clear()

    def teardown_method(self):
        """Cleanup after each test."""
        reset_global_config()

    def test_register_provider(self):
        """Test registering a provider class."""
        class MockClient:
            pass

        LLMFactory.register("mock", MockClient)
        assert "mock" in LLMFactory._providers
        assert LLMFactory._providers["mock"] is MockClient

    def test_register_decorator(self):
        """Test using register as a decorator."""
        @LLMFactory.register("decorated")
        class DecoratedClient:
            pass

        assert "decorated" in LLMFactory._providers
        assert LLMFactory._providers["decorated"] is DecoratedClient

    def test_unregister_provider(self):
        """Test unregistering a provider."""
        class MockClient:
            pass

        LLMFactory.register("to_remove", MockClient)
        assert "to_remove" in LLMFactory._providers

        LLMFactory.unregister("to_remove")
        assert "to_remove" not in LLMFactory._providers

    def test_list_providers(self):
        """Test listing registered providers."""
        class Client1:
            pass

        class Client2:
            pass

        LLMFactory.register("client1", Client1)
        LLMFactory.register("client2", Client2)

        providers = LLMFactory.list_providers()
        assert "client1" in providers
        assert "client2" in providers

    def test_get_provider_class(self):
        """Test getting provider class by name."""
        class MockClient:
            pass

        LLMFactory.register("mockget", MockClient)
        result = LLMFactory.get_provider_class("mockget")
        assert result is MockClient

    def test_get_unknown_provider_class(self):
        """Test getting unknown provider class returns None."""
        result = LLMFactory.get_provider_class("nonexistent")
        assert result is None

    def test_create_client(self):
        """Test creating a client from provider config."""
        class MockClient:
            def __init__(self, model, api_key=None, **kwargs):
                self.model = model
                self.api_key = api_key
                self.kwargs = kwargs

        LLMFactory.register("testcreate", MockClient)

        config = ProviderConfig(
            type="testcreate",
            model="test-model",
            api_key="test-key",
        )

        client = LLMFactory.create(config)
        assert isinstance(client, MockClient)
        assert client.model == "test-model"
        assert client.api_key == "test-key"

    def test_create_unknown_provider_raises(self):
        """Test creating with unknown provider raises LLMError."""
        config = ProviderConfig(type="unknown_type", model="test")

        with pytest.raises(LLMError, match="Unknown provider type"):
            LLMFactory.create(config)

    def test_create_from_config(self):
        """Test creating client from full config."""
        class MockClient:
            def __init__(self, model, **kwargs):
                self.model = model

        LLMFactory.register("configtest", MockClient)

        config = LLMConfig(
            default="configtest",
            providers={
                "configtest": ProviderConfig(type="configtest", model="config-model"),
            },
        )

        client = LLMFactory.create_from_config(config)
        assert isinstance(client, MockClient)
        assert client.model == "config-model"

    def test_create_from_config_specific_provider(self):
        """Test creating client for specific provider from config."""
        class MockClient:
            def __init__(self, model, **kwargs):
                self.model = model

        LLMFactory.register("specific", MockClient)

        config = LLMConfig(
            default="other",
            providers={
                "other": ProviderConfig(type="specific", model="other-model"),
                "specific": ProviderConfig(type="specific", model="specific-model"),
            },
        )

        client = LLMFactory.create_from_config(config, provider_name="specific")
        assert client.model == "specific-model"

    def test_builtin_providers_registered(self):
        """Test that built-in providers are auto-registered."""
        LLMFactory._ensure_initialized()

        # Should have at least these providers
        providers = LLMFactory.list_providers()
        assert "gemini" in providers
        assert "ollama" in providers
        assert "openrouter" in providers


class TestCreateLLMClient:
    """Tests for create_llm_client convenience function."""

    def setup_method(self):
        """Reset state before each test."""
        reset_global_config()

    def teardown_method(self):
        """Cleanup after each test."""
        reset_global_config()

    def test_create_with_explicit_config(self):
        """Test creating client with explicit config."""
        class MockClient:
            def __init__(self, model, **kwargs):
                self.model = model

        LLMFactory._initialized = False
        LLMFactory._providers.clear()
        LLMFactory.register("explicit", MockClient)

        config = LLMConfig(
            default="explicit",
            providers={
                "explicit": ProviderConfig(type="explicit", model="explicit-model"),
            },
        )

        client = create_llm_client(config=config)
        assert isinstance(client, MockClient)
        assert client.model == "explicit-model"

    def test_create_with_overrides(self):
        """Test creating client with parameter overrides."""
        class MockClient:
            def __init__(self, model, temperature=0.7, **kwargs):
                self.model = model
                self.temperature = temperature

        LLMFactory._initialized = False
        LLMFactory._providers.clear()
        LLMFactory.register("override", MockClient)

        config = LLMConfig(
            default="override",
            providers={
                "override": ProviderConfig(type="override", model="base-model", temperature=0.5),
            },
        )

        # Override the model
        client = create_llm_client(config=config, model="overridden-model")
        assert client.model == "overridden-model"

    def test_create_specific_provider(self):
        """Test creating client for specific provider."""
        class MockClient:
            def __init__(self, model, **kwargs):
                self.model = model

        LLMFactory._initialized = False
        LLMFactory._providers.clear()
        LLMFactory.register("provider_a", MockClient)
        LLMFactory.register("provider_b", MockClient)

        config = LLMConfig(
            default="provider_a",
            providers={
                "provider_a": ProviderConfig(type="provider_a", model="model-a"),
                "provider_b": ProviderConfig(type="provider_b", model="model-b"),
            },
        )

        client = create_llm_client(provider="provider_b", config=config)
        assert client.model == "model-b"


class TestFactoryWithRealProviders:
    """Integration tests with real provider classes."""

    def setup_method(self):
        """Reset state before each test."""
        reset_global_config()
        LLMFactory._initialized = False
        LLMFactory._providers.clear()

    def teardown_method(self):
        """Cleanup after each test."""
        reset_global_config()

    def test_create_gemini_client(self, monkeypatch):
        """Test creating Gemini client via factory."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        LLMFactory._ensure_initialized()

        config = ProviderConfig(
            type="gemini",
            model="gemini-2.0-flash",
            api_key="test-gemini-key",
        )

        client = LLMFactory.create(config)

        from nimbus.llm import GeminiClient
        assert isinstance(client, GeminiClient)
        assert client.config.model == "gemini-2.0-flash"

    def test_create_ollama_client(self):
        """Test creating Ollama client via factory."""
        LLMFactory._ensure_initialized()

        config = ProviderConfig(
            type="ollama",
            model="llama3:8b",
            base_url="http://localhost:11434",
        )

        client = LLMFactory.create(config)

        from nimbus.llm import OllamaClient
        assert isinstance(client, OllamaClient)
        assert client.config.model == "llama3:8b"

    def test_create_openrouter_client(self, monkeypatch):
        """Test creating OpenRouter client via factory."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

        LLMFactory._ensure_initialized()

        config = ProviderConfig(
            type="openrouter",
            model="anthropic/claude-3.5-sonnet",
            api_key="test-openrouter-key",
        )

        client = LLMFactory.create(config)

        from nimbus.llm import OpenRouterClient
        assert isinstance(client, OpenRouterClient)
        assert client.config.model == "anthropic/claude-3.5-sonnet"
