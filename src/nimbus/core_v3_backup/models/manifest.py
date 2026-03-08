"""
Model Manifest and Features Definition.

This module defines the capabilities and behaviors of different LLM models.
It replaces hardcoded checks (e.g., if model == "gemini") with feature flags.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any


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

    # --- Agentic Loop Strategy (NEW) ---

    # Backend specialist pure-text classification strategy:
    #   "strict": always THOUGHT, only SubmitResult terminates (needed for Claude)
    #   "heuristic": use _is_conversational_reply() heuristic (default/GPT)
    backend_reply_strategy: str = "heuristic"

    # Poke message template for backend specialist text-only responses
    poke_message: str = (
        "Continue working. If you have finished, call "
        "SubmitResult(result='your findings') to deliver your answer."
    )

    # Max text length for _DONE_PATTERNS scanning (0 = disable scanning)
    done_pattern_max_length: int = 300

    # Does the model output tool calls as JSON in content field?
    # If True, enable JsonToolCallExtractor middleware.
    json_tool_call_extraction: bool = False


@dataclass
class ModelManifest:
    """
    Manifest for a specific LLM model family or version.
    """
    model_id: str
    features: ModelFeatures
    text_is_final: bool = True   # Replaces role-based decoder judgment
    role: str = "agent"  # Kept as label (backward compat)


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
    split_mixed_responses=True,  # Critical for Gemini
    firewall_hallucinations=True,
    hallucination_patterns=DEFAULT_HALLUCINATION_PATTERNS,
    force_tool_name_repair=True,
    backend_reply_strategy="heuristic",
    done_pattern_max_length=300,
    poke_message=(
        "Your response was empty or did not include a function call. "
        "You MUST use the function calling API to call tools. "
        "Do NOT simulate tool calls as text. "
        "Continue with the task. If done, call SubmitResult."
    ),
)

# Claude (Anthropic)
# Very strict, usually doesn't need much patching.
CLAUDE_FEATURES = ModelFeatures(
    native_tool_calling=True,
    split_mixed_responses=False,  # Claude separates thought block from tool use usually
    firewall_hallucinations=False,
    force_tool_name_repair=True,
    backend_reply_strategy="strict",
    done_pattern_max_length=0,
    poke_message=(
        "You output text but did not call any tools. "
        "You MUST use the Write tool to save your work to a file, "
        "then call SubmitResult(result='summary') to finish. "
        "Do NOT output file content as plain text."
    ),
)

# Ollama / Local Models (Qwen, Llama, etc.)
# Local models vary in quality. Use conservative settings:
# split_mixed_responses + hallucination firewall for safety.
OLLAMA_FEATURES = ModelFeatures(
    native_tool_calling=True,
    split_mixed_responses=True,
    firewall_hallucinations=True,
    hallucination_patterns=DEFAULT_HALLUCINATION_PATTERNS,
    force_tool_name_repair=True,
    backend_reply_strategy="heuristic",
    done_pattern_max_length=300,
    json_tool_call_extraction=True,
    poke_message=(
        "Your response was empty or did not include a function call. "
        "You MUST use the function calling API to call tools. "
        "Do NOT simulate tool calls as text. "
        "Continue with the task. If done, call SubmitResult."
    ),
)

# Registry
_REGISTRY: Dict[str, ModelManifest] = {
    "gpt-4": ModelManifest("gpt-4", GPT_FEATURES),
    "gpt-5": ModelManifest("gpt-5", GPT_FEATURES), # Assuming similar to 4
    "gemini": ModelManifest("gemini", GEMINI_FEATURES),
    "claude": ModelManifest("claude", CLAUDE_FEATURES),
}

def get_model_manifest(model_id: Any) -> ModelManifest:
    """Get the manifest for a given model ID (fuzzy match).

    Args:
        model_id: Model identifier string, or an LLM client object
                  (will extract model name from .config or ._model).
    """
    # Circular import prevention
    try:
        from nimbus.core.models.registry import ModelRegistry
        info = ModelRegistry.get(str(model_id))
        if info:
            return info.manifest
    except (ImportError, AttributeError):
        pass

    # Accept LLM client objects — extract model string
    if not isinstance(model_id, str):
        model_id = getattr(model_id, "_model", None) or getattr(getattr(model_id, "config", None), "model_id", None) or "default"
    if model_id == "default" or not model_id:
        from nimbus.config import get_config
        model_id = get_config().default_model
    model_id = model_id.lower()
    if "gemini" in model_id:
        return _REGISTRY["gemini"]
    if "claude" in model_id:
        return _REGISTRY["claude"]
    # Default to GPT behavior for unknown models
    if "gpt" in model_id or "openai" in model_id:
        return _REGISTRY["gpt-4"]
    if "qwen" in model_id or "llama" in model_id or "ollama" in model_id:
        return ModelManifest(model_id, OLLAMA_FEATURES)
    return _REGISTRY["claude"]
