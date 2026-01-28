"""
Nimbus v2 Gemini LLM Client

This module provides a Gemini LLM client that implements the v2 LLMClient Protocol.
It is designed to work with the vCPU as the ALU (Arithmetic Logic Unit).

The client translates between:
- OpenAI-style messages/tools format (v2 protocol)
- Gemini API format (Google API)

Key Features:
- Implements LLMClient Protocol (chat method with messages/tools)
- Returns LLMResponse with content and tool_calls properties
- Supports both text and function calling responses
- Handles Gemini's unique function calling format

Usage:
    from nimbus.v2.llm import GeminiV2Client

    client = GeminiV2Client(api_key="your-api-key", model="gemini-2.0-flash-exp")
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

logger = get_logger("v2.llm.gemini")


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
class GeminiV2Response:
    """
    Response from Gemini that implements the LLMResponse Protocol.

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
class GeminiV2Config:
    """Configuration for Gemini v2 client.

    Attributes:
        model: Model name (default: gemini-2.0-flash-exp for latest flash).
        api_key: API key for authentication.
        base_url: Base URL for Gemini API.
        temperature: Sampling temperature (0.0 - 2.0).
        top_p: Top-p sampling parameter.
        top_k: Top-k sampling parameter.
        max_output_tokens: Maximum tokens in the response.
        timeout: Request timeout in seconds.
        max_retries: Maximum retry attempts on failure.
        retry_delay: Base delay between retries in seconds.
    """
    model: str = "gemini-2.0-flash-exp"
    api_key: Optional[str] = None
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    temperature: float = 0.7
    top_p: float = 0.95
    top_k: int = 40
    max_output_tokens: int = 8192
    timeout: float = 120.0
    max_retries: int = 3
    retry_delay: float = 1.0


class GeminiV2Error(Exception):
    """Exception raised for Gemini API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


# =============================================================================
# Gemini v2 Client (Implements LLMClient Protocol)
# =============================================================================


class GeminiV2Client:
    """
    Gemini LLM client implementing the v2 LLMClient Protocol.

    This client is designed to work with the vCPU as the ALU. It implements
    the chat() method expected by the LLMClient Protocol.

    The chat method accepts:
    - messages: List of message dicts in OpenAI format
    - tools: Optional list of tool definitions in OpenAI format

    And returns a GeminiV2Response with:
    - content: Optional text content
    - tool_calls: Optional list of tool calls

    Example:
        client = GeminiV2Client(api_key="your-key")

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
        model: str = "gemini-2.0-flash-exp",
        config: Optional[GeminiV2Config] = None,
        **kwargs: Any,
    ):
        """Initialize the Gemini v2 client.

        Args:
            api_key: API key for authentication. Falls back to GEMINI_API_KEY env var.
            model: Model name (default: gemini-2.0-flash-exp).
            config: Optional full configuration object.
            **kwargs: Additional config options.
        """
        if config:
            self.config = config
        else:
            self.config = GeminiV2Config(
                model=model,
                api_key=api_key,
                **{k: v for k, v in kwargs.items() if hasattr(GeminiV2Config, k)},
            )

        # Resolve API key (supports both GEMINI_API_KEY and GOOGLE_API_KEY)
        self._api_key = (
            self.config.api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not self._api_key:
            raise ValueError(
                "Gemini API key is required. Provide via api_key parameter "
                "or GEMINI_API_KEY/GOOGLE_API_KEY env var."
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
        """Build API URL for the given action."""
        return (
            f"{self.config.base_url}/models/{self.config.model}:{action}"
            f"?key={self._api_key}"
        )

    # =========================================================================
    # Message Format Conversion
    # =========================================================================

    def _convert_messages_to_gemini(
        self,
        messages: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Convert OpenAI-style messages to Gemini format.

        OpenAI format:
            [
                {"role": "system", "content": "..."},
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "...", "tool_calls": [...]},
                {"role": "tool", "tool_call_id": "...", "content": "..."},
            ]

        Gemini format:
            contents: [
                {"role": "user", "parts": [{"text": "..."}]},
                {"role": "model", "parts": [{"text": "..."}]},
            ]
            systemInstruction: {"parts": [{"text": "..."}]}

        Returns:
            Tuple of (contents list, system_instruction or None)
        """
        contents = []
        system_instruction = None

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Extract system instruction
            if role == "system":
                system_instruction = content
                continue

            # Convert role names
            gemini_role = "model" if role == "assistant" else "user"

            # Handle tool results - convert to user messages for Gemini
            if role == "tool":
                tool_call_id = msg.get("tool_call_id", "unknown")
                tool_name = msg.get("name", "tool")
                contents.append({
                    "role": "user",
                    "parts": [{"text": f"[Tool Result: {tool_name} (id: {tool_call_id})]\n{content}"}]
                })
                continue

            # Handle assistant messages with tool_calls
            if role == "assistant" and msg.get("tool_calls"):
                parts = []
                if content:
                    parts.append({"text": content})

                # Add function calls as parts
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
                    parts.append({
                        "functionCall": {
                            "name": name,
                            "args": args
                        }
                    })

                if parts:
                    contents.append({"role": gemini_role, "parts": parts})
                continue

            # Regular message
            if content:
                contents.append({
                    "role": gemini_role,
                    "parts": [{"text": content}]
                })

        return contents, system_instruction

    def _convert_tools_to_gemini(
        self,
        tools: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Convert OpenAI-style tools to Gemini format.

        OpenAI format:
            [{
                "type": "function",
                "function": {
                    "name": "...",
                    "description": "...",
                    "parameters": {...}
                }
            }]

        Gemini format:
            [{
                "function_declarations": [{
                    "name": "...",
                    "description": "...",
                    "parameters": {...}
                }]
            }]
        """
        # Check if already in Gemini format
        if tools and "function_declarations" in tools[0]:
            return tools

        # Convert from OpenAI format
        function_declarations = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool.get("function", {})
                function_declarations.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {}),
                })

        return [{"function_declarations": function_declarations}] if function_declarations else []

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
        headers = {"Content-Type": "application/json"}

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

                # Retryable errors: 429 (rate limit), 500, 502, 503, 504
                if resp.status in (429, 500, 502, 503, 504):
                    last_error = GeminiV2Error(
                        f"Gemini API error (attempt {attempt + 1}): {error_message}",
                        status_code=resp.status,
                    )
                    if attempt < self.config.max_retries - 1:
                        delay = self.config.retry_delay * (2 ** attempt)
                        logger.warning(f"Retrying in {delay}s: {error_message}")
                        await asyncio.sleep(delay)
                        continue

                # Non-retryable error
                raise GeminiV2Error(
                    f"Gemini API error: {error_message}",
                    status_code=resp.status,
                )

            except aiohttp.ClientError as e:
                last_error = GeminiV2Error(f"Request failed: {str(e)}")
                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"Retrying in {delay}s due to: {e}")
                    await asyncio.sleep(delay)
                    continue

        raise last_error or GeminiV2Error("Request failed after all retries")

    # =========================================================================
    # LLMClient Protocol Implementation
    # =========================================================================

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> GeminiV2Response:
        """
        Send messages to Gemini and get a response.

        This method implements the LLMClient Protocol expected by the vCPU.

        Args:
            messages: List of message dicts in OpenAI format
            tools: Optional list of tool definitions in OpenAI format

        Returns:
            GeminiV2Response with content and/or tool_calls
        """
        # Convert messages and extract system instruction
        contents, system_instruction = self._convert_messages_to_gemini(messages)

        if not contents:
            raise ValueError("No valid messages to send")

        # Build request body
        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.config.temperature,
                "topP": self.config.top_p,
                "topK": self.config.top_k,
                "maxOutputTokens": self.config.max_output_tokens,
            },
        }

        # Add system instruction if present
        if system_instruction:
            body["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        # Add tools if provided
        if tools:
            gemini_tools = self._convert_tools_to_gemini(tools)
            if gemini_tools:
                body["tools"] = gemini_tools
                body["toolConfig"] = {
                    "functionCallingConfig": {
                        "mode": "AUTO"
                    }
                }

        # Make request
        url = self._build_url("generateContent")
        logger.debug(f"Gemini v2 request: model={self.config.model}, messages={len(messages)}")

        resp = await self._request_with_retry(url, body)
        data = await resp.json()

        # Parse response
        return self._parse_response(data)

    def _parse_response(self, data: Dict[str, Any]) -> GeminiV2Response:
        """Parse Gemini API response into GeminiV2Response."""
        candidates = data.get("candidates", [])

        if not candidates:
            # Check for prompt blocked
            if "promptFeedback" in data:
                block_reason = data["promptFeedback"].get("blockReason", "unknown")
                raise GeminiV2Error(f"Prompt blocked: {block_reason}")
            return GeminiV2Response(raw_response=data)

        candidate = candidates[0]
        content_obj = candidate.get("content", {})
        parts = content_obj.get("parts", [])

        # Extract text and function calls
        texts = []
        tool_calls = []

        for part in parts:
            if "text" in part:
                texts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                call_id = f"call_{uuid.uuid4().hex[:8]}"
                # Convert args to JSON string (OpenAI format)
                args_dict = fc.get("args", {})
                args_str = json.dumps(args_dict)
                tool_calls.append(ToolCallInfo(
                    id=call_id,
                    type="function",
                    function=FunctionInfo(
                        name=fc.get("name", ""),
                        arguments=args_str
                    )
                ))

        content = "".join(texts) if texts else None

        return GeminiV2Response(
            _content=content,
            _tool_calls=tool_calls if tool_calls else None,
            raw_response=data
        )

    # =========================================================================
    # Context Manager
    # =========================================================================

    async def __aenter__(self) -> "GeminiV2Client":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()
