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

    adapter = DirectAdapter(config)
    await adapter.__aenter__()

    logger.info(f"🤖 Created DirectLLM client: {provider}/{model_id}")
    return adapter


def get_default_review_models() -> list[str]:
    """
    Get default models for the Review Committee.

    Reads from central config (which loads ~/.nimbus/config.json + env).
    """
    from nimbus.config import get_config
    return list(get_config().review_models)
