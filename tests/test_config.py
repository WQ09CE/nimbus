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
        assert config.default_model == "google/gemini-3-flash-preview"
        assert config.max_tokens == 8192
        assert config.timeout == 300.0
        assert config.temperature is None
        assert config.server_port == 4096
        assert len(config.review_models) == 3
        assert config.agent_roles == {}
        assert config.enabled_skills == ["goal"]
        assert config.skill_paths == []
        assert config.enabled_plugins == []
        assert config.plugin_paths == []


class TestNimbusConfigJson:
    """Test config.json loading."""

    def test_load_from_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "llm": {
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
        assert config.default_model == "openai/gpt-5"
        assert config.max_tokens == 4096
        assert config.timeout == 60.0
        assert config.temperature == 0.7
        assert config.server_port == 8080
        assert config.review_models == ["a/b", "c/d"]

    def test_agent_roles_load_from_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "agent_roles": {
                "reader": "ollama/gemma4:26b",
                "worker": "openai/gpt-5",
            },
        }))

        config = NimbusConfig.load(config_path=config_file)

        assert config.agent_roles == {
            "reader": "ollama/gemma4:26b",
            "worker": "openai/gpt-5",
        }

    def test_skills_load_from_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "skills": {
                "enabled": ["goal", "custom"],
                "paths": ["/tmp/nimbus-skills"],
            },
        }))

        config = NimbusConfig.load(config_path=config_file)

        assert config.enabled_skills == ["goal", "custom"]
        assert config.skill_paths == ["/tmp/nimbus-skills"]

    def test_plugins_load_from_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "plugins": {
                "enabled": ["hello"],
                "paths": ["/tmp/nimbus-plugins"],
            },
        }))

        config = NimbusConfig.load(config_path=config_file)

        assert config.enabled_plugins == ["hello"]
        assert config.plugin_paths == ["/tmp/nimbus-plugins"]

    def test_missing_json_uses_defaults(self, tmp_path):
        config = NimbusConfig.load(config_path=tmp_path / "nonexistent.json")
        assert config.default_model == "google/gemini-3-flash-preview"

    def test_invalid_json_uses_defaults(self, tmp_path):
        bad_file = tmp_path / "config.json"
        bad_file.write_text("not json{{{")
        config = NimbusConfig.load(config_path=bad_file)
        assert config.default_model == "google/gemini-3-flash-preview"

    def test_partial_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"llm": {"max_tokens": 2048}}))
        config = NimbusConfig.load(config_path=config_file)
        assert config.max_tokens == 2048
        assert config.default_model == "google/gemini-3-flash-preview"


class TestNimbusConfigEnv:
    """Test environment variable overrides."""

    def test_env_overrides_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"llm": {"default_model": "openai/gpt-4"}}))
        with patch.dict(os.environ, {"NIMBUS_MODEL": "google/gemini-pro"}):
            config = NimbusConfig.load(config_path=config_file)
        assert config.default_model == "google/gemini-pro"

    def test_env_overrides_defaults(self, tmp_path):
        with patch.dict(os.environ, {
            "NIMBUS_MODEL": "google/gemini-pro",
            "NIMBUS_MAX_TOKENS": "2048",
        }):
            config = NimbusConfig.load(config_path=tmp_path / "nope.json")
        assert config.default_model == "google/gemini-pro"
        assert config.max_tokens == 2048

    def test_env_skill_overrides(self, tmp_path):
        with patch.dict(os.environ, {
            "NIMBUS_SKILLS": "goal, custom",
            "NIMBUS_SKILL_PATHS": f"/a{os.pathsep}/b",
        }):
            config = NimbusConfig.load(config_path=tmp_path / "nope.json")

        assert config.enabled_skills == ["goal", "custom"]
        assert config.skill_paths == ["/a", "/b"]

    def test_env_plugin_overrides(self, tmp_path):
        with patch.dict(os.environ, {
            "NIMBUS_PLUGINS": "hello, other",
            "NIMBUS_PLUGIN_PATHS": f"/a{os.pathsep}/b",
        }):
            config = NimbusConfig.load(config_path=tmp_path / "nope.json")

        assert config.enabled_plugins == ["hello", "other"]
        assert config.plugin_paths == ["/a", "/b"]


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
