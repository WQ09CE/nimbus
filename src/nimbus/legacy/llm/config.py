"""LLM Configuration System for Nimbus.

This module provides configuration loading and management for LLM providers.
Supports multiple configuration sources:
- Environment variables (NIMBUS_LLM_PROVIDER, etc.)
- Configuration files (.nimbus/llm.json, ~/.nimbus/llm.json)
- Programmatic configuration

Configuration File Format:
    ```json
    {
        "default": "gemini",
        "providers": {
            "gemini": {
                "type": "gemini",
                "api_key": "${GEMINI_API_KEY}",
                "model": "gemini-2.0-flash"
            },
            "ollama": {
                "type": "ollama",
                "base_url": "http://localhost:11434",
                "model": "qwen3:8b"
            },
            "openrouter": {
                "type": "openrouter",
                "api_key": "${OPENROUTER_API_KEY}",
                "model": "anthropic/claude-3.5-sonnet"
            }
        }
    }
    ```

Environment Variables:
    NIMBUS_LLM_CONFIG: Path to configuration file
    NIMBUS_LLM_PROVIDER: Default provider name (overrides config file)
    NIMBUS_LLM_MODEL: Model name (overrides provider default)
    GEMINI_API_KEY: API key for Gemini
    OPENROUTER_API_KEY: API key for OpenRouter
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.logging import get_logger

logger = get_logger("llm.config")


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider.

    Attributes:
        type: Provider type (gemini, ollama, openrouter, etc.)
        model: Model name to use.
        api_key: API key for authentication (if required).
        base_url: Base URL for API calls (if configurable).
        temperature: Sampling temperature.
        max_tokens: Maximum tokens in response.
        extra: Additional provider-specific options.
    """
    type: str
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 8192
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "type": self.type,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.api_key:
            result["api_key"] = self.api_key
        if self.base_url:
            result["base_url"] = self.base_url
        if self.extra:
            result.update(self.extra)
        return result


@dataclass
class LLMConfig:
    """Complete LLM configuration.

    Attributes:
        default: Name of the default provider.
        providers: Dictionary of provider configurations.
    """
    default: str
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)

    def get_provider(self, name: Optional[str] = None) -> ProviderConfig:
        """Get provider configuration by name.

        Args:
            name: Provider name. If None, uses default.

        Returns:
            Provider configuration.

        Raises:
            KeyError: If provider not found.
        """
        provider_name = name or self.default
        if provider_name not in self.providers:
            raise KeyError(f"Provider '{provider_name}' not found. Available: {list(self.providers.keys())}")
        return self.providers[provider_name]

    def list_providers(self) -> List[str]:
        """List available provider names."""
        return list(self.providers.keys())


def expand_env_vars(value: Any) -> Any:
    """Expand environment variables in a value.

    Supports ${VAR_NAME} syntax. If the variable is not set, returns empty string.

    Args:
        value: Value to expand (string, dict, or list).

    Returns:
        Expanded value.

    Example:
        expand_env_vars("${GEMINI_API_KEY}") -> "actual-key-value"
        expand_env_vars({"key": "${MY_VAR}"}) -> {"key": "expanded"}
    """
    if isinstance(value, str):
        # Match ${VAR_NAME} pattern
        pattern = r'\$\{([^}]+)\}'

        def replacer(match: re.Match) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, "")

        return re.sub(pattern, replacer, value)
    elif isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [expand_env_vars(item) for item in value]
    return value


def parse_provider_config(name: str, data: Dict[str, Any]) -> ProviderConfig:
    """Parse a provider configuration from dictionary.

    Args:
        name: Provider name.
        data: Configuration dictionary.

    Returns:
        Parsed ProviderConfig.
    """
    # Expand environment variables
    expanded = expand_env_vars(data)

    # Extract known fields
    provider_type = expanded.get("type", name)
    model = expanded.get("model", "")
    api_key = expanded.get("api_key")
    base_url = expanded.get("base_url")
    temperature = expanded.get("temperature", 0.7)
    max_tokens = expanded.get("max_tokens", expanded.get("max_output_tokens", 8192))

    # Collect extra fields
    known_fields = {"type", "model", "api_key", "base_url", "temperature", "max_tokens", "max_output_tokens"}
    extra = {k: v for k, v in expanded.items() if k not in known_fields}

    return ProviderConfig(
        type=provider_type,
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        extra=extra,
    )


def load_config_file(path: Path) -> Optional[Dict[str, Any]]:
    """Load configuration from a JSON file.

    Supports two formats:
    1. Direct LLM config (llm.json): {"default": "...", "providers": {...}}
    2. General config (config.json): {"llm": {"default": "...", "providers": {...}}}

    Args:
        path: Path to configuration file.

    Returns:
        Configuration dictionary or None if file not found.
    """
    if not path.exists():
        return None

    try:
        with open(path, "r") as f:
            data = json.load(f)

        # If this is a general config.json, extract the llm section
        if path.name == "config.json" and "llm" in data:
            return data["llm"]

        # Otherwise treat the whole file as LLM config
        return data
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in config file {path}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to load config file {path}: {e}")
        return None


def find_config_file() -> Optional[Path]:
    """Find LLM configuration file.

    Search order:
    1. NIMBUS_LLM_CONFIG environment variable
    2. .nimbus/llm.json in current directory
    3. .nimbus/config.json in current directory (llm section)
    4. ~/.nimbus/llm.json in home directory
    5. ~/.nimbus/config.json in home directory (llm section)

    Returns:
        Path to configuration file or None if not found.
    """
    # Check environment variable
    env_path = os.environ.get("NIMBUS_LLM_CONFIG")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path
        logger.warning(f"NIMBUS_LLM_CONFIG set but file not found: {env_path}")

    # Check local directory - llm.json first, then config.json
    local_llm = Path(".nimbus/llm.json")
    if local_llm.exists():
        return local_llm

    local_config = Path(".nimbus/config.json")
    if local_config.exists():
        return local_config

    # Check home directory - llm.json first, then config.json
    home_llm = Path.home() / ".nimbus" / "llm.json"
    if home_llm.exists():
        return home_llm

    home_config = Path.home() / ".nimbus" / "config.json"
    if home_config.exists():
        return home_config

    return None


def load_config(config_path: Optional[Path] = None) -> LLMConfig:
    """Load LLM configuration from file or environment.

    Priority:
    1. Specified config_path
    2. Auto-detected config file
    3. Environment variables fallback

    Args:
        config_path: Optional explicit path to configuration file.

    Returns:
        Loaded LLMConfig.
    """
    config_data: Dict[str, Any] = {}

    # Try to load from file
    if config_path:
        config_data = load_config_file(config_path) or {}
    else:
        found_path = find_config_file()
        if found_path:
            config_data = load_config_file(found_path) or {}
            logger.info(f"Loaded LLM config from {found_path}")

    # Build provider configurations
    providers: Dict[str, ProviderConfig] = {}

    # Parse providers from config file
    for name, provider_data in config_data.get("providers", {}).items():
        providers[name] = parse_provider_config(name, provider_data)

    # Add default providers from environment if not configured
    if "gemini" not in providers:
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if gemini_key:
            providers["gemini"] = ProviderConfig(
                type="gemini",
                model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
                api_key=gemini_key,
            )

    if "ollama" not in providers:
        providers["ollama"] = ProviderConfig(
            type="ollama",
            model=os.environ.get("NIMBUS_LLM_MODEL", "qwen3:8b"),
            base_url=os.environ.get("NIMBUS_LLM_URL", "http://localhost:11434"),
        )

    if "openrouter" not in providers:
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        if openrouter_key:
            providers["openrouter"] = ProviderConfig(
                type="openrouter",
                model=os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet"),
                api_key=openrouter_key,
            )

    # Determine default provider
    default = config_data.get("default")
    env_provider = os.environ.get("NIMBUS_LLM_PROVIDER")

    if env_provider:
        default = env_provider
    elif not default:
        # Auto-select based on available providers
        if "gemini" in providers and providers["gemini"].api_key:
            default = "gemini"
        elif "openrouter" in providers and providers["openrouter"].api_key:
            default = "openrouter"
        elif "ollama" in providers:
            default = "ollama"
        elif providers:
            default = list(providers.keys())[0]
        else:
            default = "ollama"  # Fallback

    return LLMConfig(default=default, providers=providers)


def get_default_config() -> LLMConfig:
    """Get the default LLM configuration.

    This is a convenience function that loads configuration using all defaults.

    Returns:
        Loaded LLMConfig.
    """
    return load_config()


# Singleton instance for global access
_global_config: Optional[LLMConfig] = None


def get_global_config() -> LLMConfig:
    """Get the global LLM configuration (lazy-loaded singleton).

    Returns:
        Global LLMConfig instance.
    """
    global _global_config
    if _global_config is None:
        _global_config = load_config()
    return _global_config


def set_global_config(config: LLMConfig) -> None:
    """Set the global LLM configuration.

    Args:
        config: Configuration to set.
    """
    global _global_config
    _global_config = config


def reset_global_config() -> None:
    """Reset the global configuration (for testing)."""
    global _global_config
    _global_config = None
