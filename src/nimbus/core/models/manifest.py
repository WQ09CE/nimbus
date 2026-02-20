"""
Model Manifest and Features Definition.

This module defines the capabilities and behaviors of different LLM models.
It replaces hardcoded checks (e.g., if model == "gemini") with feature flags.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ModelFeatures:
    """
    Features supported by a model.
    """
    # Does the model support native tool calling?
    # If False, we might need to parse tool calls from text (not implemented yet, but good for future)
    native_tool_calling: bool = True

    # Does the model tend to "talk while working" (output text + tool calls)?
    # If True, we enable Mixed Response Splitting.
    split_mixed_responses: bool = False

    # Does the model suffer from hallucinating tool tags in text stream?
    # If True, we enable the Hallucination Firewall.
    firewall_hallucinations: bool = False

    # Specific patterns to watch for in the firewall
    hallucination_patterns: List[str] = field(default_factory=list)

    # Does the model need strict tool name correction (e.g. 'read' -> 'Read')?
    force_tool_name_repair: bool = True


@dataclass
class ModelManifest:
    """
    Manifest for a specific LLM model family or version.
    """
    model_id: str
    features: ModelFeatures
    role: str = "agent"  # Role of the agent (e.g., 'orchestrator', 'implementer', 'agent')


# =============================================================================
# Default Manifests
# =============================================================================

# Common hallucination patterns (moved from InstructionDecoder)
DEFAULT_HALLUCINATION_PATTERNS = [
    "[Called",
    "[Calling",
    "[Tool:",
    "[Execute:",
    "```tool",
    "<tool_call>",
    "<function_call>",
    "[Historical context:",
    "Do not mimic this format",
]

# GPT-4 / GPT-4o / GPT-5 (OpenAI)
# Generally behaves well, but sometimes mixes thought with action.
GPT_FEATURES = ModelFeatures(
    native_tool_calling=True,
    split_mixed_responses=True, # Enable for safety/better UX
    firewall_hallucinations=False,
    force_tool_name_repair=True,
)

# Gemini (Google)
# Prone to hallucinating XML tags and simulating tools in text.
GEMINI_FEATURES = ModelFeatures(
    native_tool_calling=True,
    split_mixed_responses=True, # Critical for Gemini
    firewall_hallucinations=True,
    hallucination_patterns=DEFAULT_HALLUCINATION_PATTERNS,
    force_tool_name_repair=True,
)

# Claude (Anthropic)
# Very strict, usually doesn't need much patching.
CLAUDE_FEATURES = ModelFeatures(
    native_tool_calling=True,
    split_mixed_responses=False, # Claude separates thought block from tool use usually
    firewall_hallucinations=False,
    force_tool_name_repair=True,
)

# Registry
_REGISTRY: Dict[str, ModelManifest] = {
    "default": ModelManifest("default", GPT_FEATURES),
    "gpt-4": ModelManifest("gpt-4", GPT_FEATURES),
    "gpt-5": ModelManifest("gpt-5", GPT_FEATURES), # Assuming similar to 4
    "gemini": ModelManifest("gemini", GEMINI_FEATURES),
    "claude": ModelManifest("claude", CLAUDE_FEATURES),
}

def get_model_manifest(model_id) -> ModelManifest:
    """Get the manifest for a given model ID (fuzzy match).

    Args:
        model_id: Model identifier string, or an LLM client object
                  (will extract model name from .config or ._model).
    """
    # Accept LLM client objects — extract model string
    if not isinstance(model_id, str):
        model_id = getattr(model_id, "_model", None) or getattr(getattr(model_id, "config", None), "model_id", None) or "default"
    model_id = model_id.lower()
    if "gemini" in model_id:
        return _REGISTRY["gemini"]
    if "claude" in model_id:
        return _REGISTRY["claude"]
    # Default to GPT behavior for others
    return _REGISTRY["default"]
