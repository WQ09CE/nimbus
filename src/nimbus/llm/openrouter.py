"""
Nimbus v2 OpenRouter LLM Client

This module provides an OpenRouter LLM client that implements the v2 LLMClient Protocol.
It enables access to various models (Claude, GPT, etc.) through OpenRouter's unified API.

Usage:
    from nimbus.llm import OpenRouterV2Client

    client = OpenRouterV2Client(api_key="your-api-key", model="anthropic/claude-opus-4")
    response = await client.chat(messages, tools=tool_definitions)

    if response.tool_calls:
        for tc in response.tool_calls:
            print(f"Tool: {tc.function.name}, Args: {tc.function.arguments}")
    else:
        print(f"Content: {response.content}")
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp

from nimbus.core.logging import get_logger

logger = get_logger("v2.llm.openrouter")


# =============================================================================
# Response Types (Implements LLMResponse Protocol)
# =============================================================================


@dataclass
class FunctionInfo:
    """Function information in a tool call."""
    name: str
    arguments: str  # JSON string, like OpenAI format


@dataclass
class ToolCallInfo:
    """Tool call information matching OpenAI format."""
    id: str
    type: str = "function"
    function: FunctionInfo = field(default_factory=lambda: FunctionInfo("", "{}"))


@dataclass
class OpenRouterV2Response:
    """
    Response from OpenRouter that implements the LLMResponse Protocol.

    This class provides the interface expected by the vCPU decoder:
    - content: Optional text content
    - tool_calls: Optional list of tool calls
    """
    _content: Optional[str] = None
    _tool_calls: Optional[List[ToolCallInfo]] = None
    raw_response: Optional[Dict[str, Any]] = None

    @property
    def content(self) -> Optional[str]:
        """Text content from the response."""
        return self._content

    @property
    def tool_calls(self) -> Optional[List[ToolCallInfo]]:
        """Tool calls from the response."""
        return self._tool_calls


# =============================================================================
# Client Configuration
# =============================================================================


@dataclass
class OpenRouterV2Config:
    """Configuration for OpenRouter v2 client.

    Attributes:
        model: Model identifier (e.g., anthropic/claude-opus-4).
        api_key: API key for authentication.
        base_url: OpenRouter API URL.
        temperature: Sampling temperature (0.0 - 2.0).
        max_tokens: Maximum tokens in the response.
        top_p: Top-p sampling parameter.
        timeout: Request timeout in seconds.
        max_retries: Maximum retry attempts on failure.
        retry_delay: Base delay between retries in seconds.
    """
    model: str = "anthropic/claude-opus-4"
    api_key: Optional[str] = None
    base_url: str = "https://openrouter.ai/api/v1"
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 8192
    timeout: float = 120.0
    max_retries: int = 3
    retry_delay: float = 1.0


class OpenRouterV2Error(Exception):
    """Exception raised for OpenRouter API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


# =============================================================================
# OpenRouter v2 Client (Implements LLMClient Protocol)
# =============================================================================


class OpenRouterV2Client:
    """
    OpenRouter LLM client implementing the v2 LLMClient Protocol.

    This client provides access to various models through OpenRouter's unified API,
    including Claude, GPT, and other models.

    Example:
        client = OpenRouterV2Client(api_key="your-key", model="anthropic/claude-opus-4")

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Read the file /tmp/test.txt"}
        ]

        tools = [{
            "type": "function",
            "function": {
                "name": "Read",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to file"}
                    },
                    "required": ["file_path"]
                }
            }
        }]

        response = await client.chat(messages, tools=tools)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "anthropic/claude-opus-4",
        config: Optional[OpenRouterV2Config] = None,
        **kwargs: Any,
    ):
        """Initialize the OpenRouter v2 client.

        Args:
            api_key: API key for authentication. Falls back to OPENROUTER_API_KEY env var.
            model: Model identifier (default: anthropic/claude-opus-4).
            config: Optional full configuration object.
            **kwargs: Additional config options.
        """
        if config:
            self.config = config
        else:
            self.config = OpenRouterV2Config(
                model=model,
                api_key=api_key,
                **{k: v for k, v in kwargs.items() if hasattr(OpenRouterV2Config, k)},
            )

        # Resolve API key
        self._api_key = self.config.api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenRouter API key is required. Provide via api_key parameter "
                "or OPENROUTER_API_KEY env var."
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
        """Build request headers."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nimbus.ai",
            "X-Title": "Nimbus Agent Framework",
        }

    # =========================================================================
    # API Request
    # =========================================================================

    async def _request_with_retry(
        self,
        body: Dict[str, Any],
    ) -> aiohttp.ClientResponse:
        """Make request with retry logic."""
        from nimbus.llm.retry import (
            calculate_delay_seconds,
            extract_headers_from_response,
            is_retryable_status,
        )

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

                # Check if error is retryable
                if is_retryable_status(resp.status):
                    last_error = OpenRouterV2Error(
                        f"OpenRouter API error (attempt {attempt + 1}): {error_message}",
                        status_code=resp.status,
                    )
                    if attempt < self.config.max_retries - 1:
                        response_headers = extract_headers_from_response(resp)
                        delay = calculate_delay_seconds(
                            attempt + 1,
                            response_headers,
                            initial_delay_ms=self.config.retry_delay * 1000,
                        )
                        logger.warning(f"Retrying in {delay:.1f}s: {error_message}")
                        await asyncio.sleep(delay)
                        continue

                # Non-retryable error
                raise OpenRouterV2Error(
                    f"OpenRouter API error: {error_message}",
                    status_code=resp.status,
                )

            except aiohttp.ClientError as e:
                last_error = OpenRouterV2Error(f"Request failed: {str(e)}")
                if attempt < self.config.max_retries - 1:
                    delay = calculate_delay_seconds(
                        attempt + 1,
                        initial_delay_ms=self.config.retry_delay * 1000,
                    )
                    logger.warning(f"Retrying in {delay:.1f}s due to: {e}")
                    await asyncio.sleep(delay)
                    continue

        raise last_error or OpenRouterV2Error("Request failed after all retries")

    # =========================================================================
    # LLMClient Protocol Implementation
    # =========================================================================

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> OpenRouterV2Response:
        """
        Send messages to OpenRouter and get a response.

        This method implements the LLMClient Protocol expected by the vCPU.

        Args:
            messages: List of message dicts in OpenAI format
            tools: Optional list of tool definitions in OpenAI format

        Returns:
            OpenRouterV2Response with content and/or tool_calls
        """
        # Build request body (OpenAI-compatible format)
        body: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "max_tokens": self.config.max_tokens,
        }

        # Add tools if provided
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        # Make request
        logger.debug(f"OpenRouter v2 request: model={self.config.model}, messages={len(messages)}")

        resp = await self._request_with_retry(body)
        data = await resp.json()

        # Parse response
        return self._parse_response(data)

    def _parse_response(self, data: Dict[str, Any]) -> OpenRouterV2Response:
        """Parse OpenRouter API response into OpenRouterV2Response."""
        choices = data.get("choices", [])

        if not choices:
            return OpenRouterV2Response(raw_response=data)

        choice = choices[0]
        message = choice.get("message", {})

        # Extract content
        content = message.get("content")

        # Extract tool calls
        tool_calls_data = message.get("tool_calls", [])
        tool_calls = []

        for tc in tool_calls_data:
            tc_id = tc.get("id", "")
            function = tc.get("function", {})
            name = function.get("name", "")
            arguments_str = function.get("arguments", "{}")

            tool_calls.append(ToolCallInfo(
                id=tc_id,
                type="function",
                function=FunctionInfo(
                    name=name,
                    arguments=arguments_str
                )
            ))

        return OpenRouterV2Response(
            _content=content,
            _tool_calls=tool_calls if tool_calls else None,
            raw_response=data
        )

    # =========================================================================
    # Context Manager
    # =========================================================================

    async def __aenter__(self) -> "OpenRouterV2Client":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()
