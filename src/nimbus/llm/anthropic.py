"""
Nimbus v2 Anthropic LLM Client

This module provides an Anthropic LLM client that implements the v2 LLMClient Protocol.
It is designed to work with the vCPU as the ALU (Arithmetic Logic Unit).

The client translates between:
- OpenAI-style messages/tools format (v2 protocol)
- Anthropic API format (Claude API)

Key Features:
- Implements LLMClient Protocol (chat method with messages/tools)
- Returns LLMResponse with content and tool_calls properties
- Supports both text and function calling responses
- Handles Anthropic's unique message format and tool calling

Usage:
    from nimbus.llm import AnthropicV2Client

    client = AnthropicV2Client(api_key="your-api-key", model="claude-sonnet-4-20250514")
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
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp

from nimbus.core.logging import get_logger

logger = get_logger("v2.llm.anthropic")


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
class AnthropicV2Response:
    """
    Response from Anthropic that implements the LLMResponse Protocol.

    This class provides the interface expected by the vCPU decoder:
    - content: Optional text content
    - tool_calls: Optional list of tool calls

    The decoder will use these properties to create ActionIR instructions.
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
class AnthropicV2Config:
    """Configuration for Anthropic v2 client.

    Attributes:
        model: Model name (default: claude-sonnet-4-20250514).
        api_key: API key for authentication.
        base_url: Base URL for Anthropic API.
        max_tokens: Maximum tokens in the response.
        temperature: Sampling temperature (0.0 - 1.0).
        timeout: Request timeout in seconds.
        max_retries: Maximum retry attempts on failure.
        retry_delay: Base delay between retries in seconds.
    """
    model: str = "claude-sonnet-4-20250514"
    api_key: Optional[str] = None
    base_url: str = "https://api.anthropic.com/v1"
    max_tokens: int = 8192
    temperature: float = 0.7
    timeout: float = 120.0
    max_retries: int = 3
    retry_delay: float = 1.0


class AnthropicV2Error(Exception):
    """Exception raised for Anthropic API errors."""

    def __init__(
        self, message: str, status_code: Optional[int] = None, details: Optional[Dict] = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


# =============================================================================
# Anthropic v2 Client (Implements LLMClient Protocol)
# =============================================================================


class AnthropicV2Client:
    """
    Anthropic LLM client implementing the v2 LLMClient Protocol.

    This client is designed to work with the vCPU as the ALU. It implements
    the chat() method expected by the LLMClient Protocol.

    The chat method accepts:
    - messages: List of message dicts in OpenAI format
    - tools: Optional list of tool definitions in OpenAI format

    And returns an AnthropicV2Response with:
    - content: Optional text content
    - tool_calls: Optional list of tool calls

    Example:
        client = AnthropicV2Client(api_key="your-key")

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
        model: str = "claude-sonnet-4-20250514",
        config: Optional[AnthropicV2Config] = None,
        **kwargs: Any,
    ):
        """Initialize the Anthropic v2 client.

        Args:
            api_key: API key for authentication. Falls back to ANTHROPIC_API_KEY env var.
            model: Model name (default: claude-sonnet-4-20250514).
            config: Optional full configuration object.
            **kwargs: Additional config options.
        """
        if config:
            self.config = config
        else:
            self.config = AnthropicV2Config(
                model=model,
                api_key=api_key,
                **{k: v for k, v in kwargs.items() if hasattr(AnthropicV2Config, k)},
            )

        # Resolve API key
        self._api_key = self.config.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Anthropic API key is required. Provide via api_key parameter "
                "or ANTHROPIC_API_KEY env var."
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

    def _build_url(self, endpoint: str = "messages") -> str:
        """Build API URL for the given endpoint."""
        return f"{self.config.base_url}/{endpoint}"

    # =========================================================================
    # Message Format Conversion
    # =========================================================================

    def _convert_messages_to_anthropic(
        self,
        messages: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Convert OpenAI-style messages to Anthropic format.

        OpenAI format:
            [
                {"role": "system", "content": "..."},
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "...", "tool_calls": [...]},
                {"role": "tool", "tool_call_id": "...", "content": "..."},
            ]

        Anthropic format:
            messages: [
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": [{"type": "text", "text": "..."}]},
                {"role": "user", "content": [{"type": "tool_result", ...}]},
            ]
            system: "..."

        Returns:
            Tuple of (messages list, system prompt or None)
        """
        anthropic_messages = []
        system_prompt = None

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Extract system prompt
            if role == "system":
                if system_prompt:
                    system_prompt += "\n\n" + content
                else:
                    system_prompt = content
                continue

            # Handle tool results - convert to Anthropic's tool_result format
            if role == "tool":
                tool_call_id = msg.get("tool_call_id", str(uuid.uuid4()))
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": content,
                    }]
                })
                continue

            # Handle assistant messages with tool_calls
            if role == "assistant" and msg.get("tool_calls"):
                content_blocks = []

                # Add text content if present
                if content:
                    content_blocks.append({
                        "type": "text",
                        "text": content,
                    })

                # Add tool_use blocks for each tool call
                for tc in msg.get("tool_calls", []):
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args = func.get("arguments", "{}")

                    # Parse arguments if string
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}

                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        "name": name,
                        "input": args,
                    })

                if content_blocks:
                    anthropic_messages.append({
                        "role": "assistant",
                        "content": content_blocks,
                    })
                continue

            # Regular user or assistant message
            if content:
                anthropic_messages.append({
                    "role": role,
                    "content": content,
                })

        return anthropic_messages, system_prompt

    def _convert_tools_to_anthropic(
        self,
        tools: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Convert OpenAI-style tools to Anthropic format.

        OpenAI format:
            [{
                "type": "function",
                "function": {
                    "name": "...",
                    "description": "...",
                    "parameters": {...}
                }
            }]

        Anthropic format:
            [{
                "name": "...",
                "description": "...",
                "input_schema": {...}
            }]
        """
        # Check if already in Anthropic format
        if tools and "input_schema" in tools[0]:
            return tools

        # Convert from OpenAI format
        anthropic_tools = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool.get("function", {})
                anthropic_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                })

        return anthropic_tools

    # =========================================================================
    # API Request
    # =========================================================================

    async def _request_with_retry(
        self,
        url: str,
        body: Dict[str, Any],
    ) -> aiohttp.ClientResponse:
        """Make request with retry logic."""
        session = await self._get_session()
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }

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

                # Retryable errors: 429 (rate limit), 500, 502, 503, 504, 529 (overloaded)
                if resp.status in (429, 500, 502, 503, 504, 529):
                    last_error = AnthropicV2Error(
                        f"Anthropic API error (attempt {attempt + 1}): {error_message}",
                        status_code=resp.status,
                    )
                    if attempt < self.config.max_retries - 1:
                        delay = self.config.retry_delay * (2 ** attempt)
                        logger.warning(f"Retrying in {delay}s: {error_message}")
                        await asyncio.sleep(delay)
                        continue

                # Non-retryable error
                raise AnthropicV2Error(
                    f"Anthropic API error: {error_message}",
                    status_code=resp.status,
                )

            except aiohttp.ClientError as e:
                last_error = AnthropicV2Error(f"Request failed: {str(e)}")
                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"Retrying in {delay}s due to: {e}")
                    await asyncio.sleep(delay)
                    continue

        raise last_error or AnthropicV2Error("Request failed after all retries")

    # =========================================================================
    # LLMClient Protocol Implementation
    # =========================================================================

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AnthropicV2Response:
        """
        Send messages to Anthropic and get a response.

        This method implements the LLMClient Protocol expected by the vCPU.

        Args:
            messages: List of message dicts in OpenAI format
            tools: Optional list of tool definitions in OpenAI format

        Returns:
            AnthropicV2Response with content and/or tool_calls
        """
        # Convert messages and extract system prompt
        anthropic_messages, system_prompt = self._convert_messages_to_anthropic(messages)

        if not anthropic_messages:
            raise ValueError("No valid messages to send")

        # Build request body
        body: Dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": anthropic_messages,
        }

        # Add system prompt if present
        if system_prompt:
            body["system"] = system_prompt

        # Add temperature
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature

        # Add tools if provided
        if tools:
            anthropic_tools = self._convert_tools_to_anthropic(tools)
            if anthropic_tools:
                body["tools"] = anthropic_tools
                logger.info(f"🔧 Tools provided: {len(anthropic_tools)} tools - {[t['name'] for t in anthropic_tools]}")
            else:
                logger.warning(f"⚠️ Tools list was provided but conversion resulted in empty list!")
        else:
            logger.warning(f"⚠️ No tools provided to LLM chat() call!")

        # Make request
        url = self._build_url("messages")
        logger.debug(f"Anthropic v2 request: model={self.config.model}, messages={len(messages)}, tools={len(body.get('tools', []))}")

        resp = await self._request_with_retry(url, body)
        data = await resp.json()

        # Parse response
        return self._parse_response(data)

    def _parse_response(self, data: Dict[str, Any]) -> AnthropicV2Response:
        """Parse Anthropic API response into AnthropicV2Response."""
        content_blocks = data.get("content", [])
        stop_reason = data.get("stop_reason", "")

        # Extract text and tool use blocks
        texts = []
        tool_calls = []

        for block in content_blocks:
            block_type = block.get("type", "")

            if block_type == "text":
                texts.append(block.get("text", ""))

            elif block_type == "tool_use":
                # Convert to OpenAI-style tool call format
                tool_id = block.get("id", f"call_{uuid.uuid4().hex[:8]}")
                name = block.get("name", "")
                input_args = block.get("input", {})

                # Convert input to JSON string (OpenAI format)
                args_str = json.dumps(input_args)

                tool_calls.append(ToolCallInfo(
                    id=tool_id,
                    type="function",
                    function=FunctionInfo(
                        name=name,
                        arguments=args_str
                    )
                ))

        content = "".join(texts) if texts else None

        return AnthropicV2Response(
            _content=content,
            _tool_calls=tool_calls if tool_calls else None,
            raw_response=data
        )

    # =========================================================================
    # Context Manager
    # =========================================================================

    async def __aenter__(self) -> "AnthropicV2Client":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()
