"""
Model Registry for Nimbus — Unified Model IDs and Metadata.

This module provides a centralized registry for all LLMs supported by Nimbus.
It handles ID normalization, provider mapping, and capability tiers.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from nimbus.core.models.manifest import ModelManifest, GPT_FEATURES, GEMINI_FEATURES, CLAUDE_FEATURES, OLLAMA_FEATURES


@dataclass
class ModelInfo:
    """
    Metadata for a registered model.
    """
    model_id: str          # Full ID used by the provider/adapter (e.g. "claude-sonnet-4-6")
    provider: str          # Provider name (e.g. "anthropic", "google", "openai")
    tier: str              # Tier: "pro", "flash", "ultra", "coding"
    aliases: List[str]     # List of aliases (e.g. ["sonnet", "claude"])
    manifest: ModelManifest # Features and behaviors
    context_window: int = 200_000  # Context window in tokens (default 200K)
    basic_tools_only: bool = False  # If True, only register kernel tools (Bash/Read/Write/Edit)
    cost_per_million: Dict[str, float] = field(default_factory=lambda: {
        "input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0,
    })  # $/million tokens (pi-style Model.cost)

    @property
    def full_name(self) -> str:
        """Returns the canonical name in 'provider/model_id' format."""
        return f"{self.provider}/{self.model_id}"

    @property
    def rank(self) -> int:
        """Returns the escalation rank (lower is cheaper/faster, higher is more capable)."""
        ranks = {
            "flash": 1,
            "pro": 2,
            "ultra": 3,
            "coding": 4
        }
        return ranks.get(self.tier, 0)


# ---------------------------------------------------------------------------
# Helper: parse modifiers from alias strings like "sonnet[1m]"
# ---------------------------------------------------------------------------
_MODIFIER_RE = re.compile(r"^(.+?)\[(\w+)\]$")


def _strip_modifier(name: str) -> tuple[str, Optional[str]]:
    """
    Split 'base[modifier]' → ('base', 'modifier').
    Returns ('name', None) if no modifier found.
    """
    m = _MODIFIER_RE.match(name)
    if m:
        return m.group(1), m.group(2)
    return name, None


class ModelRegistry:
    """
    Unified registry for all models.
    """
    _models: Dict[str, ModelInfo] = {}
    _alias_map: Dict[str, str] = {}

    @classmethod
    def register(cls, info: ModelInfo):
        """Register a model and its aliases."""
        cls._models[info.full_name] = info
        cls._alias_map[info.full_name.lower()] = info.full_name
        for alias in info.aliases:
            cls._alias_map[alias.lower()] = info.full_name

    @classmethod
    def get(cls, name: str) -> Optional[ModelInfo]:
        """Get ModelInfo by alias or full name (supports 'base[modifier]' syntax)."""
        if not isinstance(name, str):
            return None
        if not name:
            return None
        # Strip optional modifier (e.g. "sonnet[1m]" → look up "sonnet")
        base, _ = _strip_modifier(name.lower())
        full_name = cls._alias_map.get(base, base)
        return cls._models.get(full_name)

    @classmethod
    def get_next_tier(cls, current_model: str) -> Optional[str]:
        """
        Suggests the next model in the escalation ladder.
        """
        current_info = cls.get(current_model)
        if not current_info:
            return None
            
        current_rank = current_info.rank
        candidates = [m for m in cls._models.values() if m.rank > current_rank]
        if not candidates:
            return None
            
        # Sort by rank and return the first one (lowest rank above current)
        candidates.sort(key=lambda x: x.rank)
        
        # Try to find one in the same provider first
        provider_matches = [m for m in candidates if m.provider == current_info.provider]
        if provider_matches:
            return provider_matches[0].full_name
            
        return candidates[0].full_name

    @classmethod
    def get_same_provider_fallback(cls, current_model: str) -> Optional[str]:
        """
        Returns a fallback model ID from the same provider (toggling tiers).
        Logic:
          - Google: 3.1 Pro -> 3 Pro -> 3 Flash -> 3.1 Pro (cycle)
          - Other: Pro -> Flash -> Pro (cycle)
        """
        info = cls.get(current_model)
        if not info:
            return None

        # Google Specific Logic
        if info.provider == "google":
            # 3.1 Pro -> 3 Pro
            if "3.1-pro" in info.model_id:
                fallback = cls.get("gemini-3-pro-preview")
                if fallback: return fallback.full_name
            # 3 Pro -> 3 Flash (三角轮转：3.1-pro → 3-pro → 3-flash → 3.1-pro)
            if "3-pro" in info.model_id:
                fallback = cls.get("gemini-3-flash-preview")
                if fallback: return fallback.full_name
            # 3 Flash -> 3.1 Pro (In case they start with flash)
            if "flash" in info.tier:
                fallback = cls.get("gemini-3.1-pro-preview")
                if fallback: return fallback.full_name
        
        # Generic Logic: Pro <-> Flash
        same_provider_models = [
            m for m in cls._models.values() 
            if m.provider == info.provider and m.model_id != info.model_id
        ]
        
        if info.tier == "pro":
            # Try to find flash
            flash_models = [m for m in same_provider_models if m.tier == "flash"]
            if flash_models:
                return flash_models[0].full_name
        
        elif info.tier == "flash":
            # Try to find pro
            pro_models = [m for m in same_provider_models if m.tier == "pro"]
            if pro_models:
                return pro_models[0].full_name

        # Fallback: Just return any other model from same provider
        if same_provider_models:
            return same_provider_models[0].full_name

        return None

    @classmethod
    def normalize(cls, name: str) -> str:
        """
        Normalize an alias or raw name to the canonical 'provider/model_id' format.

        Supported input formats:
          - Simple alias:          "sonnet"        → "anthropic/claude-sonnet-4-6"
          - Alias with modifier:   "sonnet[1m]"    → "anthropic/claude-sonnet-4-6"
          - Full provider path:    "anthropic/claude-sonnet-4-6" → unchanged (if registered)
          - Unknown name:          returned as-is

        The modifier (e.g. '[1m]') is accepted and stripped during lookup —
        runtime context-window selection based on modifiers is handled upstream.
        """
        if not isinstance(name, str):
            name = str(name) if name else ""
        if not name or name.lower() == "default":
            from nimbus.config import get_config
            name = get_config().default_model
        info = cls.get(name)
        return info.full_name if info else name

    @classmethod
    def list_models(cls) -> List[ModelInfo]:
        """Return all registered models."""
        return list(cls._models.values())

    @classmethod
    def get_menu_text(cls) -> str:
        """Generates a text menu of available models for prompts."""
        lines = []
        for info in cls.list_models():
            aliases_str = ", ".join(info.aliases)
            ctx_k = f"{info.context_window // 1000}K"
            lines.append(
                f"- {info.full_name} (Aliases: {aliases_str}) [{info.tier.upper()}] ctx={ctx_k}"
            )
        return "\n".join(lines)


# =============================================================================
# Default Registrations  (2026 model standard)
# =============================================================================

# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

# Claude Sonnet 4.6 — primary workhorse, supports up to 1M context via [1m] alias
ModelRegistry.register(ModelInfo(
    model_id="claude-sonnet-4-6",
    provider="anthropic",
    tier="pro",
    aliases=[
        "sonnet",
        "claude",
        "claude-sonnet",
        "claude-sonnet-4-6",
        "sonnet-4-6",
        "sonnet[1m]",        # 1M-context alias (modifier stripped during lookup)
        # Backward-compatible aliases for old model names
        "claude-3-5-sonnet",
        "claude-3-5-sonnet-20241022",
    ],
    manifest=ModelManifest("claude-sonnet-4-6", CLAUDE_FEATURES),
    context_window=1_000_000,
    cost_per_million={"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
))

# Claude Haiku — lightweight / fast tier
ModelRegistry.register(ModelInfo(
    model_id="claude-haiku-4",
    provider="anthropic",
    tier="flash",
    aliases=["haiku", "claude-haiku", "claude-haiku-4"],
    manifest=ModelManifest("claude-haiku-4", CLAUDE_FEATURES),
    context_window=200_000,
    cost_per_million={"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
))

# Claude Opus — ultra/flagship tier
ModelRegistry.register(ModelInfo(
    model_id="claude-opus-4-6",
    provider="anthropic",
    tier="ultra",
    aliases=[
        "opus",
        "claude-opus",
        "claude-opus-4-6",
        "opus-4-6"
    ],
    manifest=ModelManifest("claude-opus-4-6", CLAUDE_FEATURES),
    context_window=1_000_000,
    cost_per_million={"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
))

# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

ModelRegistry.register(ModelInfo(
    model_id="gpt-4o",
    provider="openai",
    tier="pro",
    aliases=["gpt", "gpt-4o", "4o"],
    manifest=ModelManifest("gpt-4o", GPT_FEATURES),
    context_window=128_000,
    cost_per_million={"input": 2.50, "output": 10.0, "cache_read": 1.25, "cache_write": 0.0},
))

ModelRegistry.register(ModelInfo(
    model_id="gpt-4o-mini",
    provider="openai",
    tier="flash",
    aliases=["gpt-mini", "mini"],
    manifest=ModelManifest("gpt-4o-mini", GPT_FEATURES),
    context_window=128_000,
    cost_per_million={"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_write": 0.0},
))

ModelRegistry.register(ModelInfo(
    model_id="gpt-4.5-preview",
    provider="openai",
    tier="ultra",
    aliases=["gpt-4.5", "gpt-5"],
    manifest=ModelManifest("gpt-4.5", GPT_FEATURES),
    context_window=128_000,
    cost_per_million={"input": 75.0, "output": 150.0, "cache_read": 37.5, "cache_write": 0.0},
))

# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------

# Gemini 3.1 Pro — current flagship pro tier (2026)
ModelRegistry.register(ModelInfo(
    model_id="gemini-3.1-pro-preview",
    provider="google",
    tier="pro",
    aliases=[
        "gemini",
        "pro",
        "gemini-pro",
        "gemini-3.1-pro",
        "gemini-3.1-pro-preview",
    ],
    manifest=ModelManifest("gemini-pro", GEMINI_FEATURES),
    context_window=2_000_000,  # Gemini 3.x series supports 2M context
))

# Gemini 3 Pro — previous flagship pro tier (2026)
ModelRegistry.register(ModelInfo(
    model_id="gemini-3-pro-preview",
    provider="google",
    tier="pro",
    aliases=[
        "gemini-3-pro",
        "gemini-3-pro-preview",
    ],
    manifest=ModelManifest("gemini-pro", GEMINI_FEATURES),
    context_window=2_000_000,
))

# Gemini 3 Flash — fast/cheap tier (2026)
ModelRegistry.register(ModelInfo(
    model_id="gemini-3-flash-preview",
    provider="google",
    tier="flash",
    aliases=[
        "flash",
        "gemini-flash",
        "gemini-3-flash",
        "gemini-3-flash-preview",
    ],
    manifest=ModelManifest("gemini-flash", GEMINI_FEATURES),
    context_window=1_000_000,
))

# Gemini 3.1 Flash Lite — ultra-cheap flash tier (2026)
ModelRegistry.register(ModelInfo(
    model_id="gemini-3.1-flash-lite-preview",
    provider="google",
    tier="flash",
    aliases=[
        "flash-lite",
        "gemini-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
    ],
    manifest=ModelManifest("gemini-flash-lite", GEMINI_FEATURES),
    context_window=1_000_000,
))

# ---------------------------------------------------------------------------
# Codex (Special — coding tier)
# ---------------------------------------------------------------------------
# Keep the previous model registered for backward compatibility, but move the
# generic "codex" alias to the newest supported version.
ModelRegistry.register(ModelInfo(
    model_id="gpt-5.3",
    provider="openai-codex",
    tier="coding",
    aliases=["gpt-5.3", "gpt-5.3-codex"],
    manifest=ModelManifest("codex", GPT_FEATURES),
    context_window=128_000,
))

ModelRegistry.register(ModelInfo(
    model_id="gpt-5.4",
    provider="openai-codex",
    tier="coding",
    aliases=["codex", "gpt-5.4", "gpt-5.4-codex", "codex-latest"],
    manifest=ModelManifest("codex", GPT_FEATURES),
    context_window=128_000,
))

# ── Ollama / Local Models ──────────────────────────────────────
ModelRegistry.register(ModelInfo(
    model_id="gemma4:26b",
    provider="ollama",
    tier="pro",
    aliases=[
        "gemma4",
        "gemma4-26b",
        "gemma-4-26b",
        "ollama-gemma4",
        "ollama-gemma4-26b",
    ],
    manifest=ModelManifest("gemma4", OLLAMA_FEATURES),
    context_window=128_000,
))

ModelRegistry.register(ModelInfo(
    model_id="qwen3.5:9b",
    provider="ollama",
    tier="flash",
    aliases=[
        "qwen3.5",
        "qwen",
        "ollama",
    ],
    manifest=ModelManifest("qwen3.5", OLLAMA_FEATURES),
    context_window=32_000,
))

ModelRegistry.register(ModelInfo(
    model_id="qwen3.5:4b",
    provider="ollama",
    tier="flash",
    aliases=[
        "qwen4b",
        "qwen3.5-4b",
    ],
    manifest=ModelManifest("qwen3.5", OLLAMA_FEATURES),
    context_window=32_000,
    basic_tools_only=True,  # 4B model: tool calling unreliable
))

ModelRegistry.register(ModelInfo(
    model_id="qwen3.5:2b",
    provider="ollama",
    tier="flash",
    aliases=[
        "qwen2b",
        "qwen3.5-2b",
    ],
    manifest=ModelManifest("qwen3.5", OLLAMA_FEATURES),
    context_window=32_000,
    basic_tools_only=True,  # 2B model: only Bash/Read/Write/Edit
))
