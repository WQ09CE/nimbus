"""Basic chat skill for conversational interactions."""

from typing import Any, Callable, Coroutine, Protocol


class LLMClient(Protocol):
    """Protocol for LLM client interface."""

    async def complete(self, prompt: str) -> str:
        """Generate completion for the given prompt."""
        ...


CHAT_PROMPT = """You are a helpful assistant in a notebook environment.

Context:
{context}

User message: {message}

Respond naturally and helpfully."""


async def chat(
    message: str,
    context: str,
    llm_client: LLMClient,
) -> str:
    """Basic chat skill for general conversation.

    Args:
        message: User's message.
        context: Conversation context.
        llm_client: LLM client for generating response.

    Returns:
        Assistant's response.
    """
    prompt = CHAT_PROMPT.format(
        context=context or "No prior context.",
        message=message,
    )
    return await llm_client.complete(prompt)


def create_chat_skill(
    llm_client: LLMClient,
) -> Callable[..., Coroutine[Any, Any, str]]:
    """Create a chat skill function with bound LLM client.

    Args:
        llm_client: LLM client to bind to the skill.

    Returns:
        Async function that can be registered as a skill.
    """

    async def chat_skill(message: str, context: str = "") -> str:
        return await chat(message, context, llm_client)

    return chat_skill
