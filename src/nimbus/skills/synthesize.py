"""Synthesize skill for analyzing tool results and generating reports."""

from typing import Any, Callable, Coroutine, Protocol


class LLMClient(Protocol):
    """Protocol for LLM client interface."""

    async def complete(self, prompt: str) -> str:
        """Generate completion for the given prompt."""
        ...


SYNTHESIZE_PROMPT = """You are a helpful AI assistant.

## Pronoun Rules (IMPORTANT)
When User says "我" → they mean themselves (the human)
When User says "你" → they mean you (the AI)

Example: User asks "我问你的第一个问题是什么？"
→ Means: "What was the User's first message?"
→ Look for the first "User(人类):" message in history

## Context
{context}

## User's Question
{message}

## Instructions
Answer directly and concisely. Do not show your reasoning process.

Response:"""


async def synthesize(
    message: str,
    context: str,
    llm_client: LLMClient,
) -> str:
    """Synthesize skill for analyzing tool results and generating reports.

    Args:
        message: User's message.
        context: Context from tool results.
        llm_client: LLM client for generating response.

    Returns:
        Synthesized response.
    """
    prompt = SYNTHESIZE_PROMPT.format(
        context=context or "No prior context.",
        message=message,
    )
    return await llm_client.complete(prompt)


def create_synthesize_skill(
    llm_client: LLMClient,
) -> Callable[..., Coroutine[Any, Any, str]]:
    """Create a synthesize skill function with bound LLM client.

    Args:
        llm_client: LLM client to bind to the skill.

    Returns:
        Async function that can be registered as a skill.
    """

    async def synthesize_skill(
        message: str = "",
        context: str = "",
        **kwargs,
    ) -> str:
        # Support 'prompt' as alias for 'message' (LLM may generate either)
        actual_message = message or kwargs.get("prompt", "")
        if not actual_message:
            raise ValueError("Either 'message' or 'prompt' parameter is required")
        if kwargs.get("direct"):
            return actual_message
        return await synthesize(actual_message, context, llm_client)

    return synthesize_skill
