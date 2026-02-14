"""
Mock LLM Adapter for Nimbus integration testing.

Provides a deterministic, rule-based LLM replacement that implements
the same interface as PiLLMAdapter. Activated via NIMBUS_LLM=mock.

Rules (priority order):
1. /^hello|hi|hey/i            -> text reply
2. /echo\\s+(.+)/i             -> Bash tool_call
3. /read\\s+(.+)/i             -> Read tool_call
4. /count\\s+to\\s+(\\d+)/i    -> multi-step counting (stateful via message history)
5. /error/i                    -> error message
6. default                     -> generic acknowledgement

Usage:
    from nimbus.testing.mock_llm import MockLLMAdapter

    llm = MockLLMAdapter()
    response = await llm.chat(messages, tools=tools)
"""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Response Object
# =============================================================================


@dataclass
class MockLLMResponse:
    """Concrete LLM response compatible with VcpuLLMResponse / LLMResponse Protocol."""

    _content: Optional[str] = None
    _tool_calls: Optional[List[Any]] = None

    @property
    def content(self) -> Optional[str]:
        return self._content

    @property
    def tool_calls(self) -> Optional[List[Any]]:
        return self._tool_calls


# =============================================================================
# Rule Helpers
# =============================================================================


def _make_tool_call(
    name: str,
    arguments: Dict[str, Any],
    call_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a tool_call dict in the format expected by vCPU decoder."""
    return {
        "id": call_id or f"mock_{uuid.uuid4().hex[:12]}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def _extract_last_user_content(messages: List[Dict[str, Any]]) -> str:
    """Extract the text content from the last user message."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            # Handle structured content (list of content blocks)
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        parts.append(block)
                return " ".join(parts)
    return ""


def _has_tool_result_from(
    messages: List[Dict[str, Any]], tool_name: str
) -> bool:
    """Check whether the conversation contains a tool result from a given tool.

    Scans backwards from the end of the message list.  Returns True if a
    ``role=tool`` message whose ``name`` matches *tool_name* is found before
    hitting a ``role=user`` or ``role=system`` boundary.  This lets rules
    detect that the vCPU already executed the tool and the current call is a
    *continuation* -- meaning the rule should return text-only so the vCPU
    can terminate the turn.
    """
    for msg in reversed(messages):
        role = msg.get("role")
        if role == "tool" and msg.get("name") == tool_name:
            return True
        # Stop scanning at user/system boundary -- anything before that
        # belongs to a previous turn.
        if role in ("user", "system"):
            return False
    return False


# =============================================================================
# Counting State Parser
# =============================================================================


def _parse_count_state(messages: List[Dict[str, Any]]) -> int:
    """
    Parse the current count from message history.

    The "Count is X" text lives in the Write tool_call arguments (the
    ``content`` field that gets written to count.txt), **not** in the tool
    result message (which says "Successfully wrote N bytes ...").

    Search order (most recent first):
    1. Assistant messages with tool_calls whose Write ``arguments.content``
       contains "Count is X".
    2. Tool result messages whose ``content`` literally contains "Count is X"
       (legacy / test-helper fallback).
    """
    current_count = 0
    for msg in reversed(messages):
        # --- Path 1: assistant tool_call arguments ---
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []) or []:
                func = tc.get("function", {})
                if func.get("name") != "Write":
                    continue
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    continue
                match = re.search(r"Count is (\d+)", args.get("content", ""))
                if match:
                    current_count = int(match.group(1))
                    return current_count

        # --- Path 2: tool result content (fallback) ---
        content = str(msg.get("content", ""))
        match = re.search(r"Count is (\d+)", content)
        if match:
            current_count = int(match.group(1))
            return current_count

    return current_count


def _parse_count_target(user_text: str) -> int:
    """Extract the target number from 'count to N'."""
    match = re.search(r"count\s+to\s+(\d+)", user_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 5  # Default target


# =============================================================================
# Mock LLM Adapter
# =============================================================================


class MockLLMAdapter:
    """
    Deterministic, rule-based LLM adapter for integration testing.

    Implements the same interface as PiLLMAdapter so it can be swapped
    in via NIMBUS_LLM=mock without changing any other code.

    Features:
    - Pattern-matched deterministic responses
    - Streaming simulation via on_chunk callback
    - Context-aware counting (reads tool_result history)
    - No external dependencies (no HTTP, no API keys)
    """

    def __init__(self) -> None:
        self._started = False

    # -- Lifecycle (mirrors PiLLMAdapter) --

    async def __aenter__(self) -> "MockLLMAdapter":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        """Start the adapter (no-op for mock)."""
        self._started = True
        logger.info("MockLLMAdapter started (deterministic mode)")

    async def stop(self) -> None:
        """Stop the adapter (no-op for mock)."""
        self._started = False
        logger.info("MockLLMAdapter stopped")

    async def health_check(self) -> bool:
        """Always healthy."""
        return True

    # -- Core Interface --

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> MockLLMResponse:
        """
        Implement LLMClient protocol with deterministic rule-based responses.

        Args:
            messages: vCPU message list
            tools: Tool definitions (unused but accepted for interface compat)
            on_chunk: Streaming callback, receives text increments

        Returns:
            MockLLMResponse with content and/or tool_calls
        """
        # Small delay to simulate LLM latency and allow async scheduling
        await asyncio.sleep(0.01)

        user_text = _extract_last_user_content(messages)

        # Dispatch through rules in priority order
        response = (
            self._rule_greeting(user_text)
            or self._rule_echo(user_text, messages)
            or self._rule_read(user_text, messages)
            or self._rule_count(user_text, messages)
            or self._rule_error(user_text)
            or self._rule_default(user_text)
        )

        # Simulate streaming if callback provided and there is text content
        if on_chunk and response.content:
            await self._simulate_streaming(response.content, on_chunk)

        return response

    # -- Rules --

    def _rule_greeting(self, text: str) -> Optional[MockLLMResponse]:
        """Rule 1: Greeting pattern."""
        if re.match(r"^(hello|hi|hey)\b", text, re.IGNORECASE):
            return MockLLMResponse(
                _content="Hello! I'm Nimbus mock agent. How can I help?"
            )
        return None

    def _rule_echo(
        self, text: str, messages: List[Dict[str, Any]]
    ) -> Optional[MockLLMResponse]:
        """Rule 2: Echo command -> Bash tool_call.

        On the initial request, returns a Bash tool_call to execute ``echo <payload>``.
        On continuation (after the tool result comes back), returns a text-only
        response so that the vCPU terminates the turn cleanly instead of looping.
        """
        match = re.match(r"echo\s+(.+)", text, re.IGNORECASE)
        if match:
            # Detect continuation: if the most recent non-system/non-user message
            # is a tool result, the Bash tool has already executed.
            if _has_tool_result_from(messages, "Bash"):
                return MockLLMResponse(
                    _content="Done. Echoed the text successfully."
                )
            payload = match.group(1).strip()
            return MockLLMResponse(
                _content="I'll echo that for you.",
                _tool_calls=[
                    _make_tool_call("Bash", {"command": f"echo {payload}"})
                ],
            )
        return None

    def _rule_read(
        self, text: str, messages: List[Dict[str, Any]]
    ) -> Optional[MockLLMResponse]:
        """Rule 3: Read file command -> Read tool_call.

        On continuation (after the tool result comes back), returns a text-only
        response to terminate the vCPU turn cleanly.
        """
        match = re.match(r"read\s+(.+)", text, re.IGNORECASE)
        if match:
            if _has_tool_result_from(messages, "Read"):
                return MockLLMResponse(
                    _content="Done. Here is the file content above."
                )
            file_path = match.group(1).strip()
            return MockLLMResponse(
                _content="I'll read the file for you.",
                _tool_calls=[
                    _make_tool_call("Read", {"file_path": file_path})
                ],
            )
        return None

    def _rule_count(
        self, text: str, messages: List[Dict[str, Any]]
    ) -> Optional[MockLLMResponse]:
        """
        Rule 4: Count to N -> multi-step counting via Write tool_calls.

        This rule is context-aware: it inspects the message history for
        previous "Count is X" results to determine the next step,
        mirroring StatefulMockLLM from e2e_session_lifecycle.py.
        """
        # Check if this is a counting task (either initial or continuation)
        is_count_task = bool(
            re.search(r"count\s+to\s+\d+", text, re.IGNORECASE)
        )
        # Also detect continuation: check tool results AND assistant tool_call
        # arguments for "Count is" pattern. The real vCPU pipeline stores
        # "Count is N" in the Write tool_call arguments, not in the tool result.
        is_continuation = False
        for msg in messages:
            if msg.get("role") == "tool" and "Count is" in str(msg.get("content", "")):
                is_continuation = True
                break
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls", []) or []:
                    func = tc.get("function", {})
                    if func.get("name") == "Write":
                        args_str = func.get("arguments", "")
                        if "Count is" in args_str:
                            is_continuation = True
                            break
                if is_continuation:
                    break

        if not is_count_task and not is_continuation:
            return None

        target = _parse_count_target(text)
        current = _parse_count_state(messages)
        next_count = current + 1

        if next_count > target:
            return MockLLMResponse(
                _content=f"Task completed. Count reached {target}."
            )

        return MockLLMResponse(
            _content=f"Counting... next is {next_count}",
            _tool_calls=[
                _make_tool_call(
                    "Write",
                    {
                        "file_path": "count.txt",
                        "content": f"Count is {next_count}",
                    },
                    call_id=f"call_{next_count}",
                )
            ],
        )

    def _rule_error(self, text: str) -> Optional[MockLLMResponse]:
        """Rule 5: Error trigger."""
        if re.search(r"\berror\b", text, re.IGNORECASE):
            return MockLLMResponse(
                _content="An error occurred while processing your request. "
                "Please check the input and try again."
            )
        return None

    def _rule_default(self, text: str) -> MockLLMResponse:
        """Rule 6: Default fallback."""
        return MockLLMResponse(
            _content="I understand. Let me help you with that."
        )

    # -- Streaming Simulation --

    async def _simulate_streaming(
        self, text: str, on_chunk: Callable[[str], None]
    ) -> None:
        """
        Simulate streaming by emitting text in small chunks.

        Splits on word boundaries to mimic real token-by-token delivery.
        """
        words = text.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == 0 else " " + word
            on_chunk(chunk)
            # Tiny yield to keep event loop responsive
            await asyncio.sleep(0.001)
