"""Tests for nimbus.config central configuration module."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nimbus.config import NimbusConfig, get_config, reset_config


class TestNimbusConfigDefaults:
    """Test default values."""

    def test_defaults(self):
        config = NimbusConfig()
        assert config.pi_ai_url == "http://localhost:3031"
        assert config.default_model == "anthropic/claude-sonnet-4-20250514"
        assert config.max_tokens == 8192
        assert config.timeout == 300.0
        assert config.temperature is None
        assert config.server_port == 4096
        assert len(config.review_models) == 3


class TestNimbusConfigJson:
    """Test config.json loading."""

    def test_load_from_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "llm": {
                "pi_ai_url": "http://myhost:9999",
                "default_model": "openai/gpt-5",
                "max_tokens": 4096,
                "timeout": 60.0,
                "temperature": 0.7,
            },
            "server": {"port": 8080},
            "review_committee": {
                "models": ["a/b", "c/d"],
            },
        }))
        config = NimbusConfig.load(config_path=config_file)
        assert config.pi_ai_url == "http://myhost:9999"
        assert config.default_model == "openai/gpt-5"
        assert config.max_tokens == 4096
        assert config.timeout == 60.0
        assert config.temperature == 0.7
        assert config.server_port == 8080
        assert config.review_models == ["a/b", "c/d"]

    def test_missing_json_uses_defaults(self, tmp_path):
        config = NimbusConfig.load(config_path=tmp_path / "nonexistent.json")
        assert config.pi_ai_url == "http://localhost:3031"

    def test_invalid_json_uses_defaults(self, tmp_path):
        bad_file = tmp_path / "config.json"
        bad_file.write_text("not json{{{")
        config = NimbusConfig.load(config_path=bad_file)
        assert config.pi_ai_url == "http://localhost:3031"

    def test_partial_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"llm": {"pi_ai_url": "http://x:1"}}))
        config = NimbusConfig.load(config_path=config_file)
        assert config.pi_ai_url == "http://x:1"
        assert config.default_model == "anthropic/claude-sonnet-4-20250514"


class TestNimbusConfigEnv:
    """Test environment variable overrides."""

    def test_env_overrides_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"llm": {"pi_ai_url": "http://json:1"}}))
        with patch.dict(os.environ, {"PI_AI_URL": "http://env:2"}):
            config = NimbusConfig.load(config_path=config_file)
        assert config.pi_ai_url == "http://env:2"

    def test_env_overrides_defaults(self, tmp_path):
        with patch.dict(os.environ, {
            "PI_AI_URL": "http://env:3",
            "NIMBUS_MODEL": "google/gemini-pro",
            "NIMBUS_MAX_TOKENS": "2048",
        }):
            config = NimbusConfig.load(config_path=tmp_path / "nope.json")
        assert config.pi_ai_url == "http://env:3"
        assert config.default_model == "google/gemini-pro"
        assert config.max_tokens == 2048


class TestGetConfigSingleton:
    """Test singleton behavior."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_singleton_returns_same_instance(self):
        a = get_config()
        b = get_config()
        assert a is b

    def test_force_reload(self):
        a = get_config()
        b = get_config(_force_reload=True)
        assert a is not b

    def test_reset_config(self):
        a = get_config()
        reset_config()
        b = get_config()
        assert a is not b
