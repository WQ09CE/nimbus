"""
Nimbus Global Configuration — Single Source of Truth.

Loading priority: code defaults → ~/.nimbus/config.json → environment variables.

Usage:
    from nimbus.config import get_config

    model = get_config().default_model
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

NIMBUS_HOME = Path.home() / ".nimbus"
CONFIG_PATH = NIMBUS_HOME / "config.json"
DEFAULT_MEMORY_PATH = NIMBUS_HOME / "memory.md"
DEFAULT_OAUTH_PATH = Path.home() / ".pi" / "agent" / "auth.json"


@dataclass
class NimbusConfig:
    """Global configuration with layered loading."""

    # Default model (provider/model_id format)
    default_model: str = "google/gemini-3-flash-preview"

    # Agent profile: "orchestrator", "standard"
    agent_profile: str = "orchestrator"

    # LLM parameters
    max_tokens: int = 8192
    timeout: float = 300.0
    temperature: Optional[float] = None

    # Provider Keys
    gemini_api_key: Optional[str] = None

    # Anthropic OAuth
    anthropic_oauth_path: str = str(DEFAULT_OAUTH_PATH)
    anthropic_use_oauth: bool = True

    # OpenAI Codex OAuth
    codex_use_oauth: bool = True

    ollama_base_url: str = "http://localhost:11434"

    # User memory file (Pinned into MMU at session start, human-editable)
    memory_path: str = str(DEFAULT_MEMORY_PATH)

    # Nimbus Server
    server_port: int = 4096

    # Review Committee default models
    review_models: list = field(default_factory=lambda: [
        "anthropic/claude-opus-4-6",
        "openai-codex/gpt-5.3-codex",
        "google/gemini-3.1-pro-preview",
    ])

    # Sub-agent role → model mapping (spawn_agent)
    agent_roles: Dict[str, str] = field(default_factory=lambda: {
        "reader": "gemini-3-flash-preview",
        "worker": "gemini-3-flash-preview",
    })

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "NimbusConfig":
        """Load config: defaults → config.json → env vars."""
        config = cls()
        path = config_path or CONFIG_PATH

        # Layer 1: config.json
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _apply_json(config, data)
            except (json.JSONDecodeError, OSError):
                pass

        # Layer 2: environment variables
        _apply_env(config)

        return config


def _apply_json(config: NimbusConfig, data: dict) -> None:
    """Apply config.json values to config."""
    llm = data.get("llm", {})
    if v := llm.get("default_model"):
        config.default_model = v
    if v := llm.get("max_tokens"):
        config.max_tokens = int(v)
    if v := llm.get("timeout"):
        config.timeout = float(v)
    if "temperature" in llm and llm["temperature"] is not None:
        config.temperature = float(llm["temperature"])
    
    if providers := llm.get("providers"):
        if gemini := providers.get("gemini"):
            if api_key := gemini.get("api_key"):
                config.gemini_api_key = api_key
        if anthropic := providers.get("anthropic"):
            if oauth_path := anthropic.get("oauth_path"):
                config.anthropic_oauth_path = oauth_path
            if "use_oauth" in anthropic:
                config.anthropic_use_oauth = bool(anthropic["use_oauth"])
        if codex := providers.get("openai-codex"):
            if "use_oauth" in codex:
                config.codex_use_oauth = bool(codex["use_oauth"])
        if ollama := providers.get("ollama"):
            if base_url := ollama.get("base_url"):
                config.ollama_base_url = base_url

    server = data.get("server", {})
    if v := server.get("port"):
        config.server_port = int(v)

    rc = data.get("review_committee", {})
    if models := rc.get("models"):
        config.review_models = list(models)

    if roles := data.get("agent_roles"):
        if isinstance(roles, dict):
            config.agent_roles = dict(roles)

    _VALID_PROFILES = {"orchestrator", "standard", "executor"}
    # Agent profile (top-level or under "agent" section)
    if v := data.get("agent_profile"):
        if v not in _VALID_PROFILES:
            import warnings
            warnings.warn(f"Unknown agent_profile '{v}', ignoring. Valid: {_VALID_PROFILES}")
        else:
            config.agent_profile = v
    elif agent := data.get("agent"):
        if v := agent.get("profile"):
            if v not in _VALID_PROFILES:
                import warnings
                warnings.warn(f"Unknown agent_profile '{v}', ignoring. Valid: {_VALID_PROFILES}")
            else:
                config.agent_profile = v


def _apply_env(config: NimbusConfig) -> None:
    """Apply environment variable overrides."""
    if v := os.environ.get("NIMBUS_MODEL"):
        config.default_model = v
    if v := os.environ.get("NIMBUS_MAX_TOKENS"):
        config.max_tokens = int(v)
    if v := os.environ.get("NIMBUS_TIMEOUT"):
        config.timeout = float(v)
    if v := os.environ.get("NIMBUS_SERVER_PORT"):
        config.server_port = int(v)
    
    if v := os.environ.get("GEMINI_API_KEY"):
        config.gemini_api_key = v
    elif v := os.environ.get("GOOGLE_API_KEY"):
        config.gemini_api_key = v

    if v := os.environ.get("NIMBUS_ANTHROPIC_OAUTH_PATH"):
        config.anthropic_oauth_path = v
    if v := os.environ.get("NIMBUS_ANTHROPIC_USE_OAUTH"):
        config.anthropic_use_oauth = v.lower() not in ("0", "false", "no")

    if v := os.environ.get("NIMBUS_CODEX_USE_OAUTH"):
        config.codex_use_oauth = v.lower() not in ("0", "false", "no")

    if v := os.environ.get("OLLAMA_BASE_URL"):
        config.ollama_base_url = v

    if v := os.environ.get("NIMBUS_AGENT_PROFILE"):
        _VALID_PROFILES = {"orchestrator", "standard", "executor"}
        if v not in _VALID_PROFILES:
            import warnings
            warnings.warn(f"Unknown NIMBUS_AGENT_PROFILE '{v}', ignoring. Valid: {_VALID_PROFILES}")
        else:
            config.agent_profile = v


# Singleton
_config: Optional[NimbusConfig] = None


def get_config(*, _force_reload: bool = False) -> NimbusConfig:
    """Get the global NimbusConfig singleton."""
    global _config
    if _config is None or _force_reload:
        _config = NimbusConfig.load()
    return _config


def reset_config() -> None:
    """Reset the singleton (for testing)."""
    global _config
    _config = None
