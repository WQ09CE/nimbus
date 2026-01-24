"""OpenRouter LLM Provider for Nimbus.

This module provides an OpenRouter client that implements the LLMClient protocol,
enabling Nimbus to use various models through OpenRouter's unified API.

Features:
- Non-streaming and streaming response support
- Multi-turn conversation history
- Access to multiple model providers (OpenAI, Anthropic, Meta, etc.)
- Configurable model parameters

OpenRouter API Reference:
    https://openrouter.ai/docs

Environment Variables:
    OPENROUTER_API_KEY: API key for OpenRouter authentication
    OPENROUTER_MODEL: Default model (default: anthropic/claude-3.5-sonnet)

Example:
    ```python
    from nimbus.llm import OpenRouterClient

    # Basic usage
    client = OpenRouterClient(api_key="your-api-key")
    response = await client.complete("Hello, how are you?")

    # With specific model
    client = OpenRouterClient(
        api_key="your-api-key",
        model="openai/gpt-4-turbo"
    )

    # Streaming
    async for chunk in client.stream("Tell me a story"):
        print(chunk, end="", flush=True)
    ```
"""

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from .base import BaseLLMClient, LLMError
from ..core.logging import get_logger

logger = get_logger("openrouter")


@dataclass
class OpenRouterConfig:
    """Configuration for OpenRouter client.

    Attributes:
        model: Model identifier (e.g., anthropic/claude-3.5-sonnet).
        api_key: API key for authentication.
        base_url: OpenRouter API URL.
        temperature: Sampling temperature (0.0 - 2.0).
        max_tokens: Maximum tokens in the response.
        top_p: Top-p sampling parameter.
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retry attempts.
        retry_delay: Base delay between retries.
        site_url: Optional site URL for OpenRouter tracking.
        site_name: Optional site name for OpenRouter tracking.
    """
    model: str = "anthropic/claude-3.5-sonnet"
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    temperature: float = 0.7
    max_tokens: int = 8192
    top_p: float = 0.9
    timeout: float = 120.0
    max_retries: int = 3
    retry_delay: float = 1.0
    site_url: Optional[str] = None
    site_name: Optional[str] = None


class OpenRouterError(LLMError):
    """Exception raised for OpenRouter API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict] = None):
        super().__init__(message, status_code=status_code, details=details, provider="openrouter")


class OpenRouterClient(BaseLLMClient):
    """OpenRouter LLM client implementing the LLMClient protocol.

    OpenRouter provides access to various models through a unified API,
    including models from OpenAI, Anthropic, Meta, Google, and more.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "anthropic/claude-3.5-sonnet",
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        config: Optional[OpenRouterConfig] = None,
        **kwargs: Any,
    ):
        """Initialize the OpenRouter client.

        Args:
            api_key: API key for authentication. Falls back to OPENROUTER_API_KEY env var.
            model: Model identifier (default: anthropic/claude-3.5-sonnet).
            base_url: Optional custom base URL.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.
            config: Optional full configuration object.
            **kwargs: Additional config options.
        """
        if config:
            self.config = config
        else:
            self.config = OpenRouterConfig(
                model=model or os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet"),
                api_key=api_key,
                base_url=base_url or "https://openrouter.ai/api/v1",
                temperature=temperature,
                max_tokens=max_tokens,
                **{k: v for k, v in kwargs.items() if hasattr(OpenRouterConfig, k)},
            )

        # Resolve API key
        self._api_key = self.config.api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenRouter API key is required. Provide via api_key parameter or OPENROUTER_API_KEY env var."
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

    def _build_headers(self) -> Dict[str, str]:
        """Build request headers.

        Returns:
            Headers dict for API requests.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # Optional tracking headers
        if self.config.site_url:
            headers["HTTP-Referer"] = self.config.site_url
        if self.config.site_name:
            headers["X-Title"] = self.config.site_name

        return headers

    def _format_messages(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_instruction: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Format messages for OpenRouter API (OpenAI-compatible format).

        Args:
            prompt: The user's prompt.
            history: Conversation history.
            system_instruction: Optional system instruction.

        Returns:
            List of message dicts.
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

    async def _request_with_retry(
        self,
        body: Dict[str, Any],
        stream: bool = False,
    ) -> aiohttp.ClientResponse:
        """Make request with retry logic.

        Args:
            body: Request body.
            stream: Whether this is a streaming request.

        Returns:
            aiohttp response object.

        Raises:
            OpenRouterError: If all retries fail.
        """
        url = f"{self.config.base_url}/chat/completions"
        session = await self._get_session()
        headers = self._build_headers()

        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                resp = await session.post(url, json=body, headers=headers)

                if resp.status == 200:
                    return resp

                error_text = await resp.text()
                try:
                    error_data = json.loads(error_text)
                    error_message = error_data.get("error", {}).get("message", error_text)
                except json.JSONDecodeError:
                    error_message = error_text

                # Retryable errors
                if resp.status in (429, 500, 502, 503, 504):
                    last_error = OpenRouterError(
                        f"OpenRouter API error (attempt {attempt + 1}): {error_message}",
                        status_code=resp.status,
                    )
                    if attempt < self.config.max_retries - 1:
                        delay = self.config.retry_delay * (2 ** attempt)
                        logger.warning(f"Retrying in {delay}s: {error_message}")
                        await asyncio.sleep(delay)
                        continue

                # Non-retryable error
                raise OpenRouterError(
                    f"OpenRouter API error: {error_message}",
                    status_code=resp.status,
                )

            except aiohttp.ClientError as e:
                last_error = OpenRouterError(f"Request failed: {str(e)}")
                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"Retrying in {delay}s due to: {e}")
                    await asyncio.sleep(delay)
                    continue

        raise last_error or OpenRouterError("Request failed after all retries")

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
            **kwargs: Additional options (model, temperature, etc.)

        Returns:
            Generated text response.

        Raises:
            OpenRouterError: If API call fails.
        """
        messages = self._format_messages(prompt, history, system_instruction)

        body = {
            "model": kwargs.get("model", self.config.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.config.temperature),
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "top_p": kwargs.get("top_p", self.config.top_p),
            "stream": False,
        }

        logger.debug(f"OpenRouter request: model={body['model']}, messages={len(messages)}")

        resp = await self._request_with_retry(body)
        data = await resp.json()

        # Extract response (OpenAI-compatible format)
        try:
            choices = data.get("choices", [])
            if not choices:
                return ""

            message = choices[0].get("message", {})
            content = message.get("content", "")

            logger.debug(f"OpenRouter response: len={len(content)}")
            return content

        except (KeyError, IndexError) as e:
            raise OpenRouterError(f"Failed to parse response: {e}")

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
            OpenRouterError: If API call fails.
        """
        url = f"{self.config.base_url}/chat/completions"

        messages = self._format_messages(prompt, history, system_instruction)

        body = {
            "model": kwargs.get("model", self.config.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.config.temperature),
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "top_p": kwargs.get("top_p", self.config.top_p),
            "stream": True,
        }

        logger.debug(f"OpenRouter stream request: model={body['model']}")

        session = await self._get_session()
        headers = self._build_headers()

        async with session.post(url, json=body, headers=headers) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise OpenRouterError(
                    f"OpenRouter streaming error: {error_text}",
                    status_code=resp.status,
                )

            # Parse SSE stream (OpenAI-compatible format)
            async for line in resp.content:
                line_text = line.decode("utf-8").strip()

                if not line_text:
                    continue

                if line_text.startswith("data: "):
                    json_str = line_text[6:]

                    # Check for stream end
                    if json_str.strip() == "[DONE]":
                        return

                    try:
                        data = json.loads(json_str)
                        choices = data.get("choices", [])

                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")

                            if content:
                                yield content

                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse SSE data: {e}")
                        continue

    async def list_models(self) -> List[Dict[str, Any]]:
        """List available models on OpenRouter.

        Returns:
            List of model information dicts.
        """
        url = f"{self.config.base_url}/models"

        session = await self._get_session()
        headers = self._build_headers()

        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise OpenRouterError(f"Failed to list models: {error_text}", status_code=resp.status)

            data = await resp.json()
            return data.get("data", [])
