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


async def create_llm_client(
    model: str,
    base_url: str = "",
    timeout: float = 120.0,
    temperature: Optional[float] = None,
    thinking: Optional[bool] = None,
):
    """
    Create and start a PiLLMAdapter for the given model.

    Args:
        model: Model identifier in "provider/model_id" format
            e.g. "anthropic/claude-sonnet-4-20250514"
                 "openai/gpt-4o"
                 "google/gemini-2.5-pro"
        base_url: Pi-AI bridge URL (default: http://localhost:3031)
        timeout: Request timeout in seconds
        temperature: Optional temperature override
        thinking: Optional thinking mode (Claude extended thinking)

    Returns:
        Started PiLLMAdapter ready for chat() calls
    """
    from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig

    # Parse provider/model_id
    if "/" in model:
        provider, model_id = model.split("/", 1)
    else:
        # Assume anthropic if no provider prefix
        provider = "anthropic"
        model_id = model

    config = PiLLMConfig(
        base_url=base_url,
        provider=provider,
        model_id=model_id,
        timeout=timeout,
        temperature=temperature,
        thinking=thinking,
    )

    adapter = PiLLMAdapter(config)
    await adapter.__aenter__()

    logger.info(f"🤖 Created LLM client: {provider}/{model_id}")
    return adapter


def get_default_review_models() -> list[str]:
    """
    Get default models for the Review Committee.

    Reads from central config (which loads ~/.nimbus/config.json + env).
    """
    from nimbus.config import get_config
    return list(get_config().review_models)
