"""Gemini LLM Provider for Nimbus.

This module provides a Gemini API client that implements the LLMClient protocol,
enabling Nimbus to use Google's Gemini models as the LLM backend.

Features:
- Non-streaming and streaming response support
- Multi-turn conversation history
- Tool calling (function calling) support
- Configurable model parameters
- Error handling with retries

API Reference:
    https://ai.google.dev/gemini-api/docs

Environment Variables:
    GEMINI_API_KEY: API key for Gemini authentication

Example:
    ```python
    from nimbus.llm import GeminiClient

    # Basic usage
    client = GeminiClient(api_key="your-api-key")
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

from ..core.logging import get_logger

logger = get_logger("gemini")


@dataclass
class GeminiConfig:
    """Configuration for Gemini client.

    Attributes:
        model: Model name to use (default: gemini-2.0-flash).
        api_key: API key for authentication. If not provided, uses GEMINI_API_KEY env var.
        base_url: Base URL for Gemini API.
        temperature: Sampling temperature (0.0 - 2.0).
        top_p: Top-p sampling parameter.
        top_k: Top-k sampling parameter.
        max_output_tokens: Maximum tokens in the response.
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retry attempts on failure.
        retry_delay: Base delay between retries in seconds.
    """
    model: str = "gemini-2.0-flash"
    api_key: Optional[str] = None
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    temperature: float = 0.7
    top_p: float = 0.95
    top_k: int = 40
    max_output_tokens: int = 8192
    timeout: float = 120.0
    max_retries: int = 3
    retry_delay: float = 1.0


class GeminiError(Exception):
    """Exception raised for Gemini API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class GeminiClient:
    """Gemini LLM client implementing the LLMClient protocol.

    This client provides both synchronous-style (complete) and streaming
    interfaces for interacting with Google's Gemini models.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.0-flash",
        config: Optional[GeminiConfig] = None,
        **kwargs: Any,
    ):
        """Initialize the Gemini client.

        Args:
            api_key: API key for authentication. Falls back to GEMINI_API_KEY env var.
            model: Model name (default: gemini-2.0-flash).
            config: Optional full configuration object.
            **kwargs: Additional config options (temperature, max_output_tokens, etc.)
        """
        if config:
            self.config = config
        else:
            self.config = GeminiConfig(
                model=model,
                api_key=api_key,
                **kwargs,
            )

        # Resolve API key
        self._api_key = self.config.api_key or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Gemini API key is required. Provide via api_key parameter or GEMINI_API_KEY env var."
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

    def _build_url(self, action: str = "generateContent") -> str:
        """Build API URL for the given action.

        Args:
            action: API action (generateContent, streamGenerateContent, etc.)

        Returns:
            Full API URL with model and API key.
        """
        return (
            f"{self.config.base_url}/models/{self.config.model}:{action}"
            f"?key={self._api_key}"
        )

    def _format_message(self, role: str, content: str) -> Dict[str, Any]:
        """Format a message for Gemini API.

        Args:
            role: Message role (user, assistant/model).
            content: Message content.

        Returns:
            Formatted message dict for Gemini API.
        """
        # Gemini uses "model" instead of "assistant"
        gemini_role = "model" if role in ("assistant", "model") else "user"
        return {
            "role": gemini_role,
            "parts": [{"text": content}],
        }

    def _format_history(
        self, history: Optional[List[Dict[str, str]]] = None
    ) -> List[Dict[str, Any]]:
        """Format conversation history for Gemini API.

        Args:
            history: List of messages with 'role' and 'content' keys.

        Returns:
            Formatted contents list for Gemini API.
        """
        if not history:
            return []

        contents = []
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content:
                contents.append(self._format_message(role, content))
        return contents

    def _build_request_body(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_instruction: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Build the request body for Gemini API.

        Args:
            prompt: The user's prompt.
            history: Conversation history.
            system_instruction: Optional system instruction.
            tools: Optional list of tool definitions.

        Returns:
            Request body dict.
        """
        # Build contents from history + current prompt
        contents = self._format_history(history)
        contents.append(self._format_message("user", prompt))

        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.config.temperature,
                "topP": self.config.top_p,
                "topK": self.config.top_k,
                "maxOutputTokens": self.config.max_output_tokens,
            },
        }

        # Add system instruction if provided
        if system_instruction:
            body["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        # Add tools if provided (for function calling)
        if tools:
            body["tools"] = tools

        return body

    async def _request_with_retry(
        self,
        url: str,
        body: Dict[str, Any],
        stream: bool = False,
    ) -> aiohttp.ClientResponse:
        """Make request with retry logic.

        Args:
            url: API URL.
            body: Request body.
            stream: Whether this is a streaming request.

        Returns:
            aiohttp response object.

        Raises:
            GeminiError: If all retries fail.
        """
        session = await self._get_session()
        headers = {"Content-Type": "application/json"}

        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                resp = await session.post(url, json=body, headers=headers)

                if resp.status == 200:
                    return resp

                # Handle specific error codes
                error_text = await resp.text()
                try:
                    error_data = json.loads(error_text)
                    error_message = error_data.get("error", {}).get("message", error_text)
                except json.JSONDecodeError:
                    error_message = error_text

                # Retryable errors: 429 (rate limit), 500, 502, 503, 504
                if resp.status in (429, 500, 502, 503, 504):
                    last_error = GeminiError(
                        f"Gemini API error (attempt {attempt + 1}): {error_message}",
                        status_code=resp.status,
                    )
                    if attempt < self.config.max_retries - 1:
                        delay = self.config.retry_delay * (2 ** attempt)
                        logger.warning(f"Retrying in {delay}s: {error_message}")
                        await asyncio.sleep(delay)
                        continue

                # Non-retryable error
                raise GeminiError(
                    f"Gemini API error: {error_message}",
                    status_code=resp.status,
                    details=error_data if 'error_data' in dir() else {},
                )

            except aiohttp.ClientError as e:
                last_error = GeminiError(f"Request failed: {str(e)}")
                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"Retrying in {delay}s due to: {e}")
                    await asyncio.sleep(delay)
                    continue

        raise last_error or GeminiError("Request failed after all retries")

    def _extract_text_from_response(self, data: Dict[str, Any]) -> str:
        """Extract text content from Gemini response.

        Args:
            data: Response JSON data.

        Returns:
            Extracted text content.

        Raises:
            GeminiError: If response format is unexpected.
        """
        try:
            candidates = data.get("candidates", [])
            if not candidates:
                # Check for prompt blocked
                if "promptFeedback" in data:
                    block_reason = data["promptFeedback"].get("blockReason", "unknown")
                    raise GeminiError(f"Prompt blocked: {block_reason}")
                return ""

            candidate = candidates[0]
            content = candidate.get("content", {})
            parts = content.get("parts", [])

            texts = []
            for part in parts:
                if "text" in part:
                    texts.append(part["text"])

            return "".join(texts)

        except (KeyError, IndexError) as e:
            logger.error(f"Unexpected response format: {data}")
            raise GeminiError(f"Failed to parse response: {e}")

    async def complete(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_instruction: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Generate a completion for the given prompt.

        This method implements the LLMClient protocol for Nimbus integration.

        Args:
            prompt: The user's prompt/message.
            history: Optional conversation history.
            system_instruction: Optional system instruction.
            tools: Optional tool definitions for function calling.

        Returns:
            Generated text response.

        Raises:
            GeminiError: If API call fails.
        """
        url = self._build_url("generateContent")
        body = self._build_request_body(prompt, history, system_instruction, tools)

        logger.debug(f"Gemini request: model={self.config.model}, prompt_len={len(prompt)}")

        resp = await self._request_with_retry(url, body)
        data = await resp.json()

        text = self._extract_text_from_response(data)
        logger.debug(f"Gemini response: len={len(text)}")

        return text

    async def stream(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_instruction: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[str]:
        """Stream a completion for the given prompt.

        Args:
            prompt: The user's prompt/message.
            history: Optional conversation history.
            system_instruction: Optional system instruction.
            tools: Optional tool definitions.

        Yields:
            Text chunks as they are received.

        Raises:
            GeminiError: If API call fails.
        """
        url = self._build_url("streamGenerateContent") + "&alt=sse"
        body = self._build_request_body(prompt, history, system_instruction, tools)

        logger.debug(f"Gemini stream request: model={self.config.model}")

        session = await self._get_session()
        headers = {"Content-Type": "application/json"}

        async with session.post(url, json=body, headers=headers) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise GeminiError(
                    f"Gemini streaming error: {error_text}",
                    status_code=resp.status,
                )

            # Parse SSE stream
            # Gemini uses "data: {...}\r\n\r\n" format
            async for line in resp.content:
                line_text = line.decode("utf-8").strip()

                # Skip empty lines
                if not line_text:
                    continue

                # Process data lines
                if line_text.startswith("data: "):
                    json_str = line_text[6:]

                    # Check for stream end marker
                    if json_str.strip() == "[DONE]":
                        return

                    try:
                        data = json.loads(json_str)
                        text = self._extract_text_from_response(data)
                        if text:
                            yield text
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse SSE data: {e}")
                        continue

    async def complete_with_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        history: Optional[List[Dict[str, str]]] = None,
        system_instruction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a completion with tool calling support.

        This method handles Gemini's function calling feature, returning
        either text or function call requests.

        Args:
            prompt: The user's prompt.
            tools: List of tool/function definitions.
            history: Optional conversation history.
            system_instruction: Optional system instruction.

        Returns:
            Dict with either 'text' key or 'function_calls' key.

        Example tools format:
            ```python
            tools = [{
                "function_declarations": [{
                    "name": "get_weather",
                    "description": "Get current weather",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"}
                        },
                        "required": ["location"]
                    }
                }]
            }]
            ```
        """
        url = self._build_url("generateContent")
        body = self._build_request_body(prompt, history, system_instruction, tools)

        resp = await self._request_with_retry(url, body)
        data = await resp.json()

        try:
            candidates = data.get("candidates", [])
            if not candidates:
                return {"text": "", "function_calls": []}

            candidate = candidates[0]
            content = candidate.get("content", {})
            parts = content.get("parts", [])

            texts = []
            function_calls = []

            for part in parts:
                if "text" in part:
                    texts.append(part["text"])
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    function_calls.append({
                        "name": fc.get("name"),
                        "arguments": fc.get("args", {}),
                    })

            return {
                "text": "".join(texts),
                "function_calls": function_calls,
            }

        except (KeyError, IndexError) as e:
            raise GeminiError(f"Failed to parse tool response: {e}")

    async def __aenter__(self) -> "GeminiClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()
