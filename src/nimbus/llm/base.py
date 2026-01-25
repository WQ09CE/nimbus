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
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, Union, runtime_checkable


@dataclass
class ToolCall:
    """Represents a tool call from the LLM.

    Attributes:
        id: Unique identifier for this tool call.
        name: Name of the tool to call.
        arguments: Arguments to pass to the tool (as dict).
    """
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ToolResult:
    """Result of a tool execution.

    Attributes:
        tool_call_id: ID of the tool call this is responding to.
        content: Result content (string or structured data).
        is_error: Whether this result represents an error.
    """
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class CompletionResponse:
    """Response from LLM completion with tool calling support.

    Attributes:
        content: Text content of the response (may be None if tool calls).
        tool_calls: List of tool calls requested by the LLM.
        finish_reason: Why the completion stopped ('stop', 'tool_calls', 'length').
        raw_response: Original response from provider for debugging.
    """
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    raw_response: Optional[Dict[str, Any]] = None

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0

    @property
    def is_complete(self) -> bool:
        """Check if this is a final response (no more tool calls)."""
        return not self.has_tool_calls and self.finish_reason == "stop"


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

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        """Generate completion with tool calling support.

        This is the core method for agentic loops. The LLM can either:
        1. Return text content (final response)
        2. Request tool calls (to be executed and fed back)

        Args:
            messages: Conversation messages in OpenAI format.
            tools: List of tool definitions (OpenAI function calling format).
            system_instruction: Optional system instruction.
            **kwargs: Additional provider-specific options.

        Returns:
            CompletionResponse with content and/or tool calls.
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

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        """Generate completion with tool calling support.

        Default implementation raises NotImplementedError.
        Providers should override this for tool calling support.

        Args:
            messages: Conversation messages in OpenAI format.
            tools: List of tool definitions.
            system_instruction: Optional system instruction.
            **kwargs: Additional options.

        Returns:
            CompletionResponse with content and/or tool calls.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support tool calling. "
            "Override complete_with_tools() to add support."
        )

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
