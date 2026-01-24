"""Base LLM Client Protocol for Nimbus.

This module defines the LLMClient protocol that all LLM providers must implement.
It provides a unified interface for text completion and streaming.

Example:
    ```python
    from nimbus.llm.base import LLMClient

    class MyLLMClient:
        async def complete(self, prompt: str, history: list = None) -> str:
            # Implementation
            pass

        async def stream(self, prompt: str, history: list = None):
            # Implementation
            yield "chunk"

    # Type checking
    client: LLMClient = MyLLMClient()
    ```
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Protocol defining the LLM client interface.

    All LLM providers (Gemini, Ollama, OpenRouter, etc.) must implement this protocol.
    This enables type-safe dependency injection and easy provider switching.
    """

    async def complete(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Generate a completion for the given prompt.

        Args:
            prompt: The user's prompt/message.
            history: Optional conversation history. Each dict should have 'role' and 'content' keys.
            system_instruction: Optional system instruction/prompt.
            **kwargs: Additional provider-specific options.

        Returns:
            Generated text response.
        """
        ...

    async def stream(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream a completion for the given prompt.

        Args:
            prompt: The user's prompt/message.
            history: Optional conversation history.
            system_instruction: Optional system instruction.
            **kwargs: Additional provider-specific options.

        Yields:
            Text chunks as they are received.
        """
        ...


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients.

    Provides common functionality and enforces interface implementation.
    Concrete implementations should inherit from this class.
    """

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Generate a completion for the given prompt."""
        pass

    @abstractmethod
    async def stream(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream a completion for the given prompt."""
        pass

    async def close(self) -> None:
        """Close any resources held by the client.

        Override this method if the client holds resources that need cleanup.
        """
        pass

    async def __aenter__(self) -> "BaseLLMClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()


class LLMError(Exception):
    """Base exception for LLM errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        provider: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}
        self.provider = provider

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.provider:
            parts.insert(0, f"[{self.provider}]")
        if self.status_code:
            parts.append(f"(status={self.status_code})")
        return " ".join(parts)
