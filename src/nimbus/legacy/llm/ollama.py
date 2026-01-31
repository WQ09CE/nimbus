"""Ollama LLM Provider for Nimbus.

This module provides an Ollama client that implements the LLMClient protocol,
enabling Nimbus to use local Ollama models as the LLM backend.

Features:
- Non-streaming and streaming response support
- Multi-turn conversation history
- Configurable model parameters
- Local model inference (no API key required)

Ollama API Reference:
    https://github.com/ollama/ollama/blob/main/docs/api.md

Environment Variables:
    NIMBUS_LLM_URL: Ollama server URL (default: http://localhost:11434)
    NIMBUS_LLM_MODEL: Default model name (default: qwen3:8b)

Example:
    ```python
    from nimbus.llm import OllamaClient

    # Basic usage
    client = OllamaClient(model="llama3:8b")
    response = await client.complete("Hello, how are you?")

    # Streaming
    async for chunk in client.stream("Tell me a story"):
        print(chunk, end="", flush=True)

    # With history
    history = [
        {"role": "user", "content": "My name is Alice"},
        {"role": "assistant", "content": "Hello Alice!"},
    ]
    response = await client.complete("What's my name?", history=history)
    ```
"""

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from .base import BaseLLMClient, CompletionResponse, LLMError, ToolCall
from ..core.logging import get_logger

logger = get_logger("ollama")


@dataclass
class OllamaConfig:
    """Configuration for Ollama client.

    Attributes:
        model: Model name to use (default: qwen3:8b).
        base_url: Ollama server URL (default: http://localhost:11434).
        temperature: Sampling temperature (0.0 - 2.0).
        max_tokens: Maximum tokens in the response (num_predict in Ollama).
        top_p: Top-p sampling parameter.
        top_k: Top-k sampling parameter.
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retry attempts.
        retry_delay: Base delay between retries.
    """
    model: str = "qwen3:8b"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.7
    max_tokens: int = 8192
    top_p: float = 0.9
    top_k: int = 40
    timeout: float = 120.0
    max_retries: int = 3
    retry_delay: float = 1.0


class OllamaError(LLMError):
    """Exception raised for Ollama API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict] = None):
        super().__init__(message, status_code=status_code, details=details, provider="ollama")


class OllamaClient(BaseLLMClient):
    """Ollama LLM client implementing the LLMClient protocol.

    This client provides both synchronous-style (complete) and streaming
    interfaces for interacting with local Ollama models.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        config: Optional[OllamaConfig] = None,
        **kwargs: Any,
    ):
        """Initialize the Ollama client.

        Args:
            model: Model name. Falls back to NIMBUS_LLM_MODEL env var, then "qwen3:8b".
            base_url: Ollama server URL. Falls back to NIMBUS_LLM_URL env var.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.
            config: Optional full configuration object.
            **kwargs: Additional config options.
        """
        if config:
            self.config = config
        else:
            # Resolve URL and model from environment if not explicitly provided
            resolved_url = base_url if base_url is not None else os.environ.get("NIMBUS_LLM_URL", "http://localhost:11434")
            # Use provided model, or fall back to env var, then default
            resolved_model = model if model is not None else os.environ.get("NIMBUS_LLM_MODEL", "qwen3:8b")

            self.config = OllamaConfig(
                model=resolved_model,
                base_url=resolved_url,
                temperature=temperature,
                max_tokens=max_tokens,
                **{k: v for k, v in kwargs.items() if hasattr(OllamaConfig, k)},
            )

        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _format_messages(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_instruction: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Format messages for Ollama chat API.

        Args:
            prompt: The user's prompt.
            history: Conversation history.
            system_instruction: Optional system instruction.

        Returns:
            List of message dicts for Ollama API.
        """
        messages = []

        # Add system instruction if provided
        if system_instruction:
            messages.append({
                "role": "system",
                "content": system_instruction,
            })

        # Add history
        if history:
            for msg in history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content:
                    messages.append({
                        "role": role,
                        "content": content,
                    })

        # Add current prompt
        messages.append({
            "role": "user",
            "content": prompt,
        })

        return messages

    def _build_options(self) -> Dict[str, Any]:
        """Build Ollama options from config.

        Returns:
            Options dict for Ollama API.
        """
        return {
            "temperature": self.config.temperature,
            "num_predict": self.config.max_tokens,
            "top_p": self.config.top_p,
            "top_k": self.config.top_k,
        }

    async def _request_with_retry(
        self,
        url: str,
        body: Dict[str, Any],
    ) -> aiohttp.ClientResponse:
        """Make request with retry logic.

        Args:
            url: API URL.
            body: Request body.

        Returns:
            aiohttp response object.

        Raises:
            OllamaError: If all retries fail.
        """
        session = await self._get_session()
        headers = {"Content-Type": "application/json"}

        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                resp = await session.post(url, json=body, headers=headers)

                if resp.status == 200:
                    return resp

                error_text = await resp.text()

                # Retryable errors
                if resp.status in (500, 502, 503, 504):
                    last_error = OllamaError(
                        f"Ollama API error (attempt {attempt + 1}): {error_text}",
                        status_code=resp.status,
                    )
                    if attempt < self.config.max_retries - 1:
                        delay = self.config.retry_delay * (2 ** attempt)
                        logger.warning(f"Retrying in {delay}s: {error_text}")
                        await asyncio.sleep(delay)
                        continue

                # Non-retryable error
                raise OllamaError(
                    f"Ollama API error: {error_text}",
                    status_code=resp.status,
                )

            except aiohttp.ClientError as e:
                last_error = OllamaError(f"Connection failed: {str(e)}")
                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"Retrying in {delay}s due to: {e}")
                    await asyncio.sleep(delay)
                    continue

        raise last_error or OllamaError("Request failed after all retries")

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
            history: Optional conversation history.
            system_instruction: Optional system instruction.
            **kwargs: Additional options (passed to Ollama).

        Returns:
            Generated text response.

        Raises:
            OllamaError: If API call fails.
        """
        url = f"{self.config.base_url}/api/chat"

        messages = self._format_messages(prompt, history, system_instruction)
        options = self._build_options()

        # Allow kwargs to override options
        if kwargs:
            options.update(kwargs)

        body = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "options": options,
        }

        logger.debug(f"Ollama request: model={self.config.model}, messages={len(messages)}")

        resp = await self._request_with_retry(url, body)
        data = await resp.json()

        # Extract response
        message = data.get("message", {})
        content = message.get("content", "")

        logger.debug(f"Ollama response: len={len(content)}")

        return content

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        """Generate a completion with tool calling support.

        Args:
            messages: List of message dicts (OpenAI format)
            tools: List of tool definitions (OpenAI format)
            **kwargs: Additional options

        Returns:
            CompletionResponse with content and/or tool_calls
        """
        url = f"{self.config.base_url}/api/chat"
        options = self._build_options()

        body = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "options": options,
        }

        # Add tools if provided
        if tools:
            body["tools"] = tools

        logger.debug(f"Ollama tool request: model={self.config.model}, tools={len(tools) if tools else 0}")

        resp = await self._request_with_retry(url, body)
        data = await resp.json()

        # Extract response
        message = data.get("message", {})
        content = message.get("content", "")
        raw_tool_calls = message.get("tool_calls", [])

        # Convert tool calls to ToolCall objects
        tool_calls = []
        for i, tc in enumerate(raw_tool_calls):
            func = tc.get("function", {})
            tool_calls.append(ToolCall(
                id=f"call_{i}",
                name=func.get("name", ""),
                arguments=func.get("arguments", {}),
            ))

        # Determine finish reason
        if tool_calls:
            finish_reason = "tool_calls"
        else:
            finish_reason = "stop"

        return CompletionResponse(
            content=content if content else None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw_response=data,
        )

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
            **kwargs: Additional options.

        Yields:
            Text chunks as they are received.

        Raises:
            OllamaError: If API call fails.
        """
        url = f"{self.config.base_url}/api/chat"

        messages = self._format_messages(prompt, history, system_instruction)
        options = self._build_options()

        if kwargs:
            options.update(kwargs)

        body = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "options": options,
        }

        logger.debug(f"Ollama stream request: model={self.config.model}")

        session = await self._get_session()
        headers = {"Content-Type": "application/json"}

        async with session.post(url, json=body, headers=headers) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise OllamaError(
                    f"Ollama streaming error: {error_text}",
                    status_code=resp.status,
                )

            # Ollama streams newline-delimited JSON
            async for line in resp.content:
                line_text = line.decode("utf-8").strip()

                if not line_text:
                    continue

                try:
                    data = json.loads(line_text)

                    # Check if done
                    if data.get("done", False):
                        return

                    # Extract content
                    message = data.get("message", {})
                    content = message.get("content", "")

                    if content:
                        yield content

                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse stream data: {e}")
                    continue

    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Generate completion using the generate API (single prompt, no chat).

        This uses Ollama's /api/generate endpoint for simpler use cases.

        Args:
            prompt: The prompt text.
            system_instruction: Optional system prompt.
            **kwargs: Additional options.

        Returns:
            Generated text response.
        """
        url = f"{self.config.base_url}/api/generate"

        options = self._build_options()
        if kwargs:
            options.update(kwargs)

        body = {
            "model": self.config.model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }

        if system_instruction:
            body["system"] = system_instruction

        resp = await self._request_with_retry(url, body)
        data = await resp.json()

        return data.get("response", "")

    async def list_models(self) -> List[Dict[str, Any]]:
        """List available models on the Ollama server.

        Returns:
            List of model information dicts.
        """
        url = f"{self.config.base_url}/api/tags"

        session = await self._get_session()

        async with session.get(url) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise OllamaError(f"Failed to list models: {error_text}", status_code=resp.status)

            data = await resp.json()
            return data.get("models", [])

    async def pull_model(self, model_name: str) -> AsyncIterator[Dict[str, Any]]:
        """Pull (download) a model from Ollama registry.

        Args:
            model_name: Name of the model to pull.

        Yields:
            Progress information dicts.
        """
        url = f"{self.config.base_url}/api/pull"

        body = {
            "name": model_name,
            "stream": True,
        }

        session = await self._get_session()

        async with session.post(url, json=body) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise OllamaError(f"Failed to pull model: {error_text}", status_code=resp.status)

            async for line in resp.content:
                line_text = line.decode("utf-8").strip()
                if line_text:
                    try:
                        yield json.loads(line_text)
                    except json.JSONDecodeError:
                        continue
