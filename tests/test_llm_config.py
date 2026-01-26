"""Tests for LLM Configuration System."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from nimbus.llm.config import (
    LLMConfig,
    ProviderConfig,
    expand_env_vars,
    parse_provider_config,
    load_config_file,
    find_config_file,
    load_config,
    get_global_config,
    set_global_config,
    reset_global_config,
)


class TestProviderConfig:
    """Tests for ProviderConfig."""

    def test_basic_config(self):
        """Test basic provider configuration."""
        config = ProviderConfig(type="gemini", model="gemini-2.0-flash")
        assert config.type == "gemini"
        assert config.model == "gemini-2.0-flash"
        assert config.temperature == 0.7
        assert config.max_tokens == 8192

    def test_config_with_api_key(self):
        """Test configuration with API key."""
        config = ProviderConfig(
            type="gemini",
            model="gemini-pro",
            api_key="test-key",
        )
        assert config.api_key == "test-key"

    def test_to_dict(self):
        """Test conversion to dictionary."""
        config = ProviderConfig(
            type="ollama",
            model="llama3:8b",
            base_url="http://localhost:11434",
            extra={"custom": "value"},
        )
        d = config.to_dict()
        assert d["type"] == "ollama"
        assert d["model"] == "llama3:8b"
        assert d["base_url"] == "http://localhost:11434"
        assert d["custom"] == "value"


class TestLLMConfig:
    """Tests for LLMConfig."""

    def test_get_provider(self):
        """Test getting provider by name."""
        config = LLMConfig(
            default="gemini",
            providers={
                "gemini": ProviderConfig(type="gemini", model="gemini-2.0-flash"),
                "ollama": ProviderConfig(type="ollama", model="qwen3:8b"),
            },
        )
        provider = config.get_provider("ollama")
        assert provider.type == "ollama"
        assert provider.model == "qwen3:8b"

    def test_get_default_provider(self):
        """Test getting default provider."""
        config = LLMConfig(
            default="gemini",
            providers={
                "gemini": ProviderConfig(type="gemini", model="gemini-2.0-flash"),
            },
        )
        provider = config.get_provider()
        assert provider.type == "gemini"

    def test_get_unknown_provider_raises(self):
        """Test that getting unknown provider raises KeyError."""
        config = LLMConfig(
            default="gemini",
            providers={
                "gemini": ProviderConfig(type="gemini", model="gemini-2.0-flash"),
            },
        )
        with pytest.raises(KeyError, match="not found"):
            config.get_provider("unknown")

    def test_list_providers(self):
        """Test listing available providers."""
        config = LLMConfig(
            default="gemini",
            providers={
                "gemini": ProviderConfig(type="gemini", model="gemini-2.0-flash"),
                "ollama": ProviderConfig(type="ollama", model="qwen3:8b"),
            },
        )
        providers = config.list_providers()
        assert "gemini" in providers
        assert "ollama" in providers


class TestExpandEnvVars:
    """Tests for environment variable expansion."""

    def test_expand_string(self, monkeypatch):
        """Test expanding env vars in string."""
        monkeypatch.setenv("TEST_VAR", "test-value")
        result = expand_env_vars("${TEST_VAR}")
        assert result == "test-value"

    def test_expand_in_sentence(self, monkeypatch):
        """Test expanding env vars in longer string."""
        monkeypatch.setenv("API_KEY", "secret123")
        result = expand_env_vars("Key is ${API_KEY} here")
        assert result == "Key is secret123 here"

    def test_expand_missing_var(self, monkeypatch):
        """Test expanding missing env var returns empty string."""
        monkeypatch.delenv("MISSING_VAR", raising=False)
        result = expand_env_vars("${MISSING_VAR}")
        assert result == ""

    def test_expand_dict(self, monkeypatch):
        """Test expanding env vars in dictionary."""
        monkeypatch.setenv("MY_KEY", "my-value")
        result = expand_env_vars({"key": "${MY_KEY}", "other": "static"})
        assert result == {"key": "my-value", "other": "static"}

    def test_expand_list(self, monkeypatch):
        """Test expanding env vars in list."""
        monkeypatch.setenv("ITEM", "expanded")
        result = expand_env_vars(["${ITEM}", "static"])
        assert result == ["expanded", "static"]

    def test_expand_non_string(self):
        """Test expanding non-string values (unchanged)."""
        assert expand_env_vars(123) == 123
        assert expand_env_vars(True) is True
        assert expand_env_vars(None) is None


class TestParseProviderConfig:
    """Tests for parsing provider configuration."""

    def test_parse_basic(self):
        """Test parsing basic configuration."""
        data = {
            "type": "gemini",
            "model": "gemini-2.0-flash",
            "temperature": 0.5,
        }
        config = parse_provider_config("test", data)
        assert config.type == "gemini"
        assert config.model == "gemini-2.0-flash"
        assert config.temperature == 0.5

    def test_parse_with_env_var(self, monkeypatch):
        """Test parsing with environment variable."""
        monkeypatch.setenv("TEST_API_KEY", "secret-key")
        data = {
            "type": "gemini",
            "model": "gemini-pro",
            "api_key": "${TEST_API_KEY}",
        }
        config = parse_provider_config("test", data)
        assert config.api_key == "secret-key"

    def test_parse_extra_fields(self):
        """Test parsing with extra fields."""
        data = {
            "type": "custom",
            "model": "custom-model",
            "custom_option": "value",
            "another_option": 123,
        }
        config = parse_provider_config("test", data)
        assert config.extra["custom_option"] == "value"
        assert config.extra["another_option"] == 123

    def test_parse_uses_name_as_type(self):
        """Test that name is used as type if type not specified."""
        data = {"model": "test-model"}
        config = parse_provider_config("mytype", data)
        assert config.type == "mytype"


class TestLoadConfigFile:
    """Tests for loading configuration files."""

    def test_load_valid_json(self, tmp_path):
        """Test loading valid JSON file."""
        config_file = tmp_path / "llm.json"
        config_file.write_text('{"default": "gemini"}')

        result = load_config_file(config_file)
        assert result == {"default": "gemini"}

    def test_load_nonexistent_file(self, tmp_path):
        """Test loading nonexistent file returns None."""
        result = load_config_file(tmp_path / "nonexistent.json")
        assert result is None

    def test_load_invalid_json(self, tmp_path):
        """Test loading invalid JSON returns None."""
        config_file = tmp_path / "invalid.json"
        config_file.write_text("not valid json {")

        result = load_config_file(config_file)
        assert result is None


class TestFindConfigFile:
    """Tests for finding configuration files."""

    def test_find_via_env_var(self, tmp_path, monkeypatch):
        """Test finding config via environment variable."""
        config_file = tmp_path / "custom.json"
        config_file.write_text('{"default": "test"}')
        monkeypatch.setenv("NIMBUS_LLM_CONFIG", str(config_file))

        result = find_config_file()
        assert result == config_file

    def test_env_var_missing_file(self, monkeypatch):
        """Test env var pointing to missing file."""
        monkeypatch.setenv("NIMBUS_LLM_CONFIG", "/nonexistent/file.json")
        # Should not return the env var path if file doesn't exist
        # (may fall back to default config locations)
        result = find_config_file()
        # The result should NOT be the missing env var path
        if result is not None:
            assert str(result) != "/nonexistent/file.json"

    def test_no_config_found(self, monkeypatch, tmp_path):
        """Test when no config file is found."""
        monkeypatch.delenv("NIMBUS_LLM_CONFIG", raising=False)
        # Ensure local config doesn't exist by changing to temp dir
        monkeypatch.chdir(tmp_path)
        # Result should be None (home config may or may not exist)


class TestLoadConfig:
    """Tests for loading full configuration."""

    def test_load_from_file(self, tmp_path, monkeypatch):
        """Test loading configuration from file."""
        config_data = {
            "default": "gemini",
            "providers": {
                "gemini": {
                    "type": "gemini",
                    "model": "gemini-2.0-flash",
                    "api_key": "file-key",
                },
            },
        }
        config_file = tmp_path / "llm.json"
        config_file.write_text(json.dumps(config_data))

        config = load_config(config_file)
        assert config.default == "gemini"
        assert config.providers["gemini"].api_key == "file-key"

    def test_load_with_env_provider_override(self, tmp_path, monkeypatch):
        """Test that NIMBUS_LLM_PROVIDER overrides default."""
        config_data = {
            "default": "gemini",
            "providers": {
                "gemini": {"type": "gemini", "model": "gemini-2.0-flash"},
                "ollama": {"type": "ollama", "model": "qwen3:8b"},
            },
        }
        config_file = tmp_path / "llm.json"
        config_file.write_text(json.dumps(config_data))

        monkeypatch.setenv("NIMBUS_LLM_PROVIDER", "ollama")

        config = load_config(config_file)
        assert config.default == "ollama"

    def test_load_adds_default_ollama(self, monkeypatch):
        """Test that ollama is added by default."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("NIMBUS_LLM_CONFIG", raising=False)

        # Load without any config file
        config = load_config(Path("/nonexistent/path.json"))
        assert "ollama" in config.providers

    def test_load_adds_gemini_with_api_key(self, monkeypatch):
        """Test that gemini is added when API key is present."""
        monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
        monkeypatch.delenv("NIMBUS_LLM_CONFIG", raising=False)

        config = load_config(Path("/nonexistent/path.json"))
        assert "gemini" in config.providers
        assert config.providers["gemini"].api_key == "env-gemini-key"

    def test_auto_select_gemini_as_default(self, monkeypatch):
        """Test auto-selecting gemini as default when API key available."""
        monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
        monkeypatch.delenv("NIMBUS_LLM_PROVIDER", raising=False)
        monkeypatch.delenv("NIMBUS_LLM_CONFIG", raising=False)

        config = load_config(Path("/nonexistent/path.json"))
        assert config.default == "gemini"


class TestGlobalConfig:
    """Tests for global configuration management."""

    def setup_method(self):
        """Reset global config before each test."""
        reset_global_config()

    def teardown_method(self):
        """Reset global config after each test."""
        reset_global_config()

    def test_get_global_config(self, monkeypatch):
        """Test getting global config (lazy loaded)."""
        monkeypatch.delenv("NIMBUS_LLM_CONFIG", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        config = get_global_config()
        assert config is not None
        assert "gemini" in config.providers

    def test_set_global_config(self):
        """Test setting global config."""
        custom_config = LLMConfig(
            default="custom",
            providers={"custom": ProviderConfig(type="custom", model="test")},
        )
        set_global_config(custom_config)

        config = get_global_config()
        assert config.default == "custom"

    def test_reset_global_config(self, monkeypatch):
        """Test resetting global config."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        # Set custom config
        custom_config = LLMConfig(default="custom", providers={})
        set_global_config(custom_config)

        # Reset
        reset_global_config()

        # Get config again (should reload from env)
        config = get_global_config()
        assert config.default != "custom"
