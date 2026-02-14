"""
Tests for MockLLMAdapter.

Verifies all rule-based response patterns and interface compatibility.
"""

import asyncio
import json

import pytest

from nimbus.testing.mock_llm import MockLLMAdapter, MockLLMResponse


@pytest.fixture
def adapter():
    return MockLLMAdapter()


def _user_msg(text: str) -> dict:
    """Helper to create a user message dict."""
    return {"role": "user", "content": text}


def _tool_result_msg(content: str, tool_call_id: str = "tc_1", name: str = "Write") -> dict:
    """Helper to create a tool result message dict."""
    return {"role": "tool", "content": content, "tool_call_id": tool_call_id, "name": name}


def _assistant_write_msg(
    file_content: str,
    file_path: str = "count.txt",
    call_id: str = "call_1",
) -> dict:
    """Helper to create an assistant message with a Write tool_call.

    This mirrors the real vCPU message format where the "Count is N" text
    lives in the Write tool_call arguments, not in the tool result.
    """
    return {
        "role": "assistant",
        "content": f"Counting... next is {file_content}",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "Write",
                    "arguments": json.dumps(
                        {"file_path": file_path, "content": file_content}
                    ),
                },
            }
        ],
    }


def _write_tool_result(call_id: str = "call_1", bytes_written: int = 12) -> dict:
    """Helper to create a tool result for a Write call (realistic format).

    The real Write tool returns "Successfully wrote N bytes to count.txt",
    which does NOT contain "Count is N".
    """
    return {
        "role": "tool",
        "content": f"Successfully wrote {bytes_written} bytes to count.txt",
        "tool_call_id": call_id,
        "name": "Write",
    }


# =============================================================================
# Protocol Compliance
# =============================================================================


class TestProtocolCompliance:
    """Verify MockLLMAdapter satisfies the LLMClient protocol."""

    @pytest.mark.asyncio
    async def test_has_chat_method(self, adapter):
        """chat() method exists and is callable."""
        assert callable(getattr(adapter, "chat", None))

    @pytest.mark.asyncio
    async def test_response_has_content_property(self, adapter):
        response = await adapter.chat([_user_msg("hello")])
        assert hasattr(response, "content")
        assert isinstance(response.content, (str, type(None)))

    @pytest.mark.asyncio
    async def test_response_has_tool_calls_property(self, adapter):
        response = await adapter.chat([_user_msg("hello")])
        assert hasattr(response, "tool_calls")
        # tool_calls is None or a list
        assert response.tool_calls is None or isinstance(response.tool_calls, list)

    @pytest.mark.asyncio
    async def test_lifecycle(self, adapter):
        """start/stop lifecycle methods work."""
        await adapter.start()
        assert adapter._started is True
        assert await adapter.health_check() is True
        await adapter.stop()
        assert adapter._started is False

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        async with MockLLMAdapter() as llm:
            assert llm._started is True
        assert llm._started is False


# =============================================================================
# Rule 1: Greeting
# =============================================================================


class TestRuleGreeting:
    @pytest.mark.asyncio
    async def test_hello(self, adapter):
        response = await adapter.chat([_user_msg("hello")])
        assert response.content == "Hello! I'm Nimbus mock agent. How can I help?"
        assert response.tool_calls is None

    @pytest.mark.asyncio
    async def test_hi(self, adapter):
        response = await adapter.chat([_user_msg("Hi there")])
        assert "Hello!" in response.content
        assert response.tool_calls is None

    @pytest.mark.asyncio
    async def test_hey(self, adapter):
        response = await adapter.chat([_user_msg("Hey!")])
        assert "Hello!" in response.content


# =============================================================================
# Rule 2: Echo
# =============================================================================


class TestRuleEcho:
    @pytest.mark.asyncio
    async def test_echo_simple(self, adapter):
        response = await adapter.chat([_user_msg("echo hello world")])
        assert response.tool_calls is not None
        assert len(response.tool_calls) == 1
        tc = response.tool_calls[0]
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "Bash"
        args = json.loads(tc["function"]["arguments"])
        assert args["command"] == "echo hello world"

    @pytest.mark.asyncio
    async def test_echo_has_id(self, adapter):
        response = await adapter.chat([_user_msg("echo test")])
        tc = response.tool_calls[0]
        assert "id" in tc
        assert tc["id"].startswith("mock_")

    @pytest.mark.asyncio
    async def test_echo_continuation_returns_text_only(self, adapter):
        """After Bash tool result, echo rule should return text-only (no tool_calls).

        This prevents the vCPU from looping infinitely on echo commands.
        """
        messages = [
            _user_msg("echo hello"),
            {
                "role": "assistant",
                "content": "I'll echo that for you.",
                "tool_calls": [
                    {
                        "id": "mock_abc123",
                        "type": "function",
                        "function": {
                            "name": "Bash",
                            "arguments": json.dumps({"command": "echo hello"}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "content": "hello\n",
                "tool_call_id": "mock_abc123",
                "name": "Bash",
            },
        ]
        response = await adapter.chat(messages)
        assert response.tool_calls is None
        assert "Done" in response.content

    @pytest.mark.asyncio
    async def test_echo_initial_request_returns_tool_call(self, adapter):
        """Without a preceding tool result, echo should return Bash tool_call."""
        messages = [_user_msg("echo foobar")]
        response = await adapter.chat(messages)
        assert response.tool_calls is not None
        assert response.tool_calls[0]["function"]["name"] == "Bash"


# =============================================================================
# Rule 3: Read
# =============================================================================


class TestRuleRead:
    @pytest.mark.asyncio
    async def test_read_file(self, adapter):
        response = await adapter.chat([_user_msg("read /tmp/test.txt")])
        assert response.tool_calls is not None
        tc = response.tool_calls[0]
        assert tc["function"]["name"] == "Read"
        args = json.loads(tc["function"]["arguments"])
        assert args["file_path"] == "/tmp/test.txt"

    @pytest.mark.asyncio
    async def test_read_continuation_returns_text_only(self, adapter):
        """After Read tool result, read rule should return text-only (no tool_calls).

        This prevents the vCPU from looping infinitely on read commands.
        """
        messages = [
            _user_msg("read /tmp/test.txt"),
            {
                "role": "assistant",
                "content": "I'll read the file for you.",
                "tool_calls": [
                    {
                        "id": "mock_read123",
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "arguments": json.dumps({"file_path": "/tmp/test.txt"}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "content": "file contents here",
                "tool_call_id": "mock_read123",
                "name": "Read",
            },
        ]
        response = await adapter.chat(messages)
        assert response.tool_calls is None
        assert "Done" in response.content

    @pytest.mark.asyncio
    async def test_read_initial_request_returns_tool_call(self, adapter):
        """Without a preceding tool result, read should return Read tool_call."""
        messages = [_user_msg("read /tmp/foo.txt")]
        response = await adapter.chat(messages)
        assert response.tool_calls is not None
        assert response.tool_calls[0]["function"]["name"] == "Read"


# =============================================================================
# Rule 4: Count
# =============================================================================


class TestRuleCount:
    @pytest.mark.asyncio
    async def test_count_first_step(self, adapter):
        """First step of counting should produce Write with 'Count is 1'."""
        response = await adapter.chat([_user_msg("count to 3")])
        assert response.tool_calls is not None
        tc = response.tool_calls[0]
        assert tc["function"]["name"] == "Write"
        args = json.loads(tc["function"]["arguments"])
        assert args["content"] == "Count is 1"
        assert tc["id"] == "call_1"

    @pytest.mark.asyncio
    async def test_count_continuation_via_tool_call_args(self, adapter):
        """Continuation using realistic vCPU message format.

        The real pipeline stores "Count is N" in assistant tool_call arguments
        and returns "Successfully wrote N bytes ..." in the tool result.
        """
        messages = [
            _user_msg("count to 5"),
            # Step 1: assistant emits Write("Count is 1"), tool returns bytes msg
            _assistant_write_msg("Count is 1", call_id="call_1"),
            _write_tool_result(call_id="call_1"),
            # Step 2: assistant emits Write("Count is 2"), tool returns bytes msg
            _assistant_write_msg("Count is 2", call_id="call_2"),
            _write_tool_result(call_id="call_2"),
        ]
        response = await adapter.chat(messages)
        assert response.tool_calls is not None
        args = json.loads(response.tool_calls[0]["function"]["arguments"])
        assert args["content"] == "Count is 3"

    @pytest.mark.asyncio
    async def test_count_continuation_legacy_tool_result(self, adapter):
        """Backward compat: continuation still works with legacy format
        where tool result content literally contains "Count is N"."""
        messages = [
            _user_msg("count to 5"),
            _tool_result_msg("Count is 1", "call_1"),
            _tool_result_msg("Count is 2", "call_2"),
        ]
        response = await adapter.chat(messages)
        assert response.tool_calls is not None
        args = json.loads(response.tool_calls[0]["function"]["arguments"])
        assert args["content"] == "Count is 3"

    @pytest.mark.asyncio
    async def test_count_completion_via_tool_call_args(self, adapter):
        """Count reaches target using realistic vCPU message format."""
        messages = [
            _user_msg("count to 3"),
            _assistant_write_msg("Count is 1", call_id="call_1"),
            _write_tool_result(call_id="call_1"),
            _assistant_write_msg("Count is 2", call_id="call_2"),
            _write_tool_result(call_id="call_2"),
            _assistant_write_msg("Count is 3", call_id="call_3"),
            _write_tool_result(call_id="call_3"),
        ]
        response = await adapter.chat(messages)
        assert response.tool_calls is None
        assert "completed" in response.content.lower()
        assert "3" in response.content

    @pytest.mark.asyncio
    async def test_count_completion_legacy(self, adapter):
        """Count reaches target with legacy tool-result format."""
        messages = [
            _user_msg("count to 3"),
            _tool_result_msg("Count is 1", "call_1"),
            _tool_result_msg("Count is 2", "call_2"),
            _tool_result_msg("Count is 3", "call_3"),
        ]
        response = await adapter.chat(messages)
        assert response.tool_calls is None
        assert "completed" in response.content.lower()
        assert "3" in response.content

    @pytest.mark.asyncio
    async def test_count_default_target(self, adapter):
        """'count to 5' defaults correctly."""
        response = await adapter.chat([_user_msg("count to 5")])
        assert response.tool_calls is not None
        args = json.loads(response.tool_calls[0]["function"]["arguments"])
        assert args["content"] == "Count is 1"

    @pytest.mark.asyncio
    async def test_count_is_continuation_detects_tool_call_args(self, adapter):
        """is_continuation should detect counting state from assistant tool_calls."""
        messages = [
            _user_msg("count to 5"),
            _assistant_write_msg("Count is 1", call_id="call_1"),
            _write_tool_result(call_id="call_1"),
        ]
        # The user text is "count to 5" (which matches is_count_task anyway),
        # but we verify continuation logic by checking the response advances
        # past step 1 even though tool result says "Successfully wrote ...".
        response = await adapter.chat(messages)
        assert response.tool_calls is not None
        args = json.loads(response.tool_calls[0]["function"]["arguments"])
        assert args["content"] == "Count is 2"


# =============================================================================
# Rule 5: Error
# =============================================================================


class TestRuleError:
    @pytest.mark.asyncio
    async def test_error_keyword(self, adapter):
        response = await adapter.chat([_user_msg("trigger an error please")])
        assert "error" in response.content.lower()
        assert response.tool_calls is None


# =============================================================================
# Rule 6: Default
# =============================================================================


class TestRuleDefault:
    @pytest.mark.asyncio
    async def test_default_response(self, adapter):
        response = await adapter.chat([_user_msg("do something random")])
        assert response.content == "I understand. Let me help you with that."
        assert response.tool_calls is None


# =============================================================================
# Streaming (on_chunk)
# =============================================================================


class TestStreaming:
    @pytest.mark.asyncio
    async def test_on_chunk_callback(self, adapter):
        """on_chunk should receive all text content as streaming chunks."""
        chunks = []
        response = await adapter.chat(
            [_user_msg("hello")],
            on_chunk=lambda c: chunks.append(c),
        )
        # Reassembled chunks should equal the full content
        assembled = "".join(chunks)
        assert assembled == response.content

    @pytest.mark.asyncio
    async def test_no_streaming_without_callback(self, adapter):
        """Without on_chunk, chat should still work normally."""
        response = await adapter.chat([_user_msg("hello")])
        assert response.content is not None

    @pytest.mark.asyncio
    async def test_no_streaming_for_tool_only(self, adapter):
        """If response has content + tool_calls, streaming should still fire."""
        chunks = []
        response = await adapter.chat(
            [_user_msg("echo test")],
            on_chunk=lambda c: chunks.append(c),
        )
        assert len(chunks) > 0  # Content was streamed
        assert response.tool_calls is not None  # Tool call also present


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_messages(self, adapter):
        """Empty message list should return default response."""
        response = await adapter.chat([])
        assert response.content is not None

    @pytest.mark.asyncio
    async def test_structured_content(self, adapter):
        """Handle structured content blocks (list format)."""
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello world"},
            ],
        }
        response = await adapter.chat([msg])
        # Should match greeting rule
        assert "Hello!" in response.content

    @pytest.mark.asyncio
    async def test_tools_parameter_accepted(self, adapter):
        """tools parameter should be accepted without error."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "Bash",
                    "description": "Run a command",
                    "parameters": {},
                },
            }
        ]
        response = await adapter.chat([_user_msg("hello")], tools=tools)
        assert response.content is not None

    @pytest.mark.asyncio
    async def test_case_insensitive_rules(self, adapter):
        """Rules should be case-insensitive."""
        r1 = await adapter.chat([_user_msg("HELLO")])
        assert "Hello!" in r1.content

        r2 = await adapter.chat([_user_msg("ECHO test")])
        assert r2.tool_calls is not None

        r3 = await adapter.chat([_user_msg("READ /tmp/x")])
        assert r3.tool_calls is not None
