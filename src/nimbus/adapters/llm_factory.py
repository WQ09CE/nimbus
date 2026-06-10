"""
LLM Factory — Create LLM clients for different models/providers.

Usage:
    from nimbus.adapters.llm_factory import create_llm_client

    llm = await create_llm_client("openai/gpt-4o")
    llm = await create_llm_client("anthropic/claude-sonnet-4-20250514")
    llm = await create_llm_client("google/gemini-2.5-pro")
"""

from typing import Optional

from loguru import logger

from nimbus.core.models.registry import ModelRegistry

def resolve_model_alias(model: str) -> str:
    """Resolve a short alias to full provider/model_id, or return as-is."""
    return ModelRegistry.normalize(model)


async def create_llm_client(
    model: str,
    base_url: str = "",
    timeout: float = 120.0,
    temperature: Optional[float] = None,
    thinking: Optional[bool] = None,
):
    """
    Create and start a DirectAdapter (LiteLLM) for the given model.

    Args:
        model: Model identifier — short alias (e.g. "sonnet", "gemini")
            or full "provider/model_id" format (e.g. "anthropic/claude-sonnet-4-6")
        base_url: Optional base URL override
        timeout: Request timeout in seconds
        temperature: Optional temperature override
        thinking: Optional thinking mode (Claude extended thinking)

    Returns:
        Started DirectAdapter ready for chat() calls
    """
    from nimbus.adapters.direct_adapter import DirectAdapter
    from nimbus.adapters.types import LLMConfig

    # Resolve alias first
    model = resolve_model_alias(model)

    # Parse provider/model_id
    if "/" in model:
        provider, model_id = model.split("/", 1)
    else:
        # Assume anthropic if no provider prefix
        provider = "anthropic"
        model_id = model

    config = LLMConfig(
        base_url=base_url,
        provider=provider,
        model_id=model_id,
        timeout=timeout,
        temperature=temperature,
        thinking=thinking,
    )

    # Set Ollama base_url from global config. Key off the provider parsed from
    # the model string ("ollama/...") rather than the ModelRegistry — the
    # registry may not know newer model tags (e.g. gemma4:12b-it-qat), and
    # without the base_url litellm silently falls back to localhost:11434.
    if provider == "ollama":
        from nimbus.config import get_config
        config.base_url = get_config().ollama_base_url
    elif provider in ("pi-codex", "pi-claude"):
        # Models served by the local pi-ai sidecar (OpenAI-compatible; the
        # sidecar holds and refreshes the OAuth credentials). Route via the
        # LiteLLM channel at the sidecar URL, with the pi-style
        # "provider/model" name so the sidecar selects the right backend —
        # a bare id would hit its fallback model. The legacy codex-only
        # sidecar strips the prefix, so both variants accept this form.
        import os
        from nimbus.config import get_config
        cfg = get_config()
        sidecar_provider = "anthropic" if provider == "pi-claude" else "openai-codex"
        config.provider = "openai"
        config.model_id = f"{sidecar_provider}/{model_id}"
        config.base_url = base_url or cfg.pi_sidecar_url
        config.via_sidecar = True
        # litellm sends OPENAI_API_KEY as the Bearer. When the sidecar enforces a
        # shared secret (non-loopback bind), this must match PI_SIDECAR_TOKEN.
        if cfg.pi_sidecar_token:
            os.environ["OPENAI_API_KEY"] = cfg.pi_sidecar_token
        else:
            os.environ.setdefault("OPENAI_API_KEY", "sk-pi-sidecar")

    adapter = DirectAdapter(config)
    await adapter.__aenter__()

    # Preserve the logical (pre-rewrite) model identity. Sidecar/ollama branches
    # rewrite provider/model_id for the wire; anything that propagates the model
    # (e.g. spawn_agent inheriting the parent model) must use this name so it
    # re-enters this factory through the same branch.
    adapter._logical_model = model

    logger.info(f"🤖 Created DirectLLM client: {provider}/{model_id}")
    return adapter


def get_default_review_models() -> list[str]:
    """
    Get default models for the Review Committee.

    Reads from central config (which loads ~/.nimbus/config.json + env).
    """
    from nimbus.config import get_config
    return list(get_config().review_models)
