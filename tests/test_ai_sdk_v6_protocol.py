"""Tests for AI SDK v6 UI Message Stream Protocol.

This module tests the AI SDK v6 protocol implementation:
- SSE event format: data: {JSON}\n\n
- Event types: start, text-start, text-delta, text-end, tool-*, finish
- Stream sequence validation
- Frontend simulation tests

Protocol Reference: https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol
"""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

# =============================================================================
# Helper Functions for Testing
# =============================================================================


def parse_sse_events(response_text: str) -> list[dict[str, Any]]:
    """Parse SSE response text into a list of event dictionaries.

    Args:
        response_text: Raw SSE response text with data: lines.

    Returns:
        List of parsed event dictionaries. [DONE] marker is represented
        as {"type": "[DONE]"}.
    """
    events = []
    for line in response_text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            data = line[6:]  # Remove "data: " prefix
            if data == "[DONE]":
                events.append({"type": "[DONE]"})
            else:
                try:
                    events.append(json.loads(data))
                except json.JSONDecodeError:
                    # Skip malformed lines
                    pass
    return events


def collect_text_from_events(events: list[dict[str, Any]]) -> str:
    """Collect complete text from text-delta events.

    This simulates how the frontend accumulates text deltas.

    Args:
        events: List of parsed SSE events.

    Returns:
        Concatenated text from all text-delta events.
    """
    return "".join(
        e.get("delta", "")
        for e in events
        if e.get("type") == "text-delta"
    )


def collect_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect tool calls from events.

    Groups tool-input-start, tool-input-available, and tool-output-available
    events by toolCallId.

    Args:
        events: List of parsed SSE events.

    Returns:
        List of tool call dictionaries with input and output.
    """
    tool_calls: dict[str, dict[str, Any]] = {}

    for event in events:
        event_type = event.get("type", "")
        tool_call_id = event.get("toolCallId")

        if not tool_call_id:
            continue

        if tool_call_id not in tool_calls:
            tool_calls[tool_call_id] = {
                "toolCallId": tool_call_id,
                "toolName": None,
                "input": None,
                "output": None,
            }

        if event_type == "tool-input-start":
            tool_calls[tool_call_id]["toolName"] = event.get("toolName")
        elif event_type == "tool-input-available":
            tool_calls[tool_call_id]["toolName"] = event.get("toolName")
            tool_calls[tool_call_id]["input"] = event.get("input")
        elif event_type == "tool-output-available":
            tool_calls[tool_call_id]["output"] = event.get("output")

    return list(tool_calls.values())


def get_event_types(events: list[dict[str, Any]]) -> list[str]:
    """Extract event types from a list of events.

    Args:
        events: List of parsed SSE events.

    Returns:
        List of event type strings.
    """
    return [e.get("type", "unknown") for e in events]


# =============================================================================
# Test V6 Protocol Format Functions
# =============================================================================


class TestV6ProtocolFormatters:
    """Test v6 protocol format functions from api_ai_sdk module."""

    def test_sse_event_dict(self):
        """Test sse_event handles dictionary input correctly."""
        from nimbus.server.api_ai_sdk import sse_event

        result = sse_event({"type": "start", "messageId": "msg_123"})
        assert result == 'data: {"type": "start", "messageId": "msg_123"}\n\n'

    def test_sse_event_string(self):
        """Test sse_event handles string input (for [DONE])."""
        from nimbus.server.api_ai_sdk import sse_event

        result = sse_event("[DONE]")
        assert result == "data: [DONE]\n\n"

    def test_sse_event_unicode(self):
        """Test sse_event preserves Unicode characters (ensure_ascii=False)."""
        from nimbus.server.api_ai_sdk import sse_event

        result = sse_event({"type": "text-delta", "delta": "Hello World"})
        # Chinese should be preserved, not escaped
        assert "Hello World" in result
        assert "\\u" not in result

    def test_format_start(self):
        """Test format_start creates correct start event."""
        from nimbus.server.api_ai_sdk import format_start

        result = format_start("msg_abc123")
        events = parse_sse_events(result)

        assert len(events) == 1
        assert events[0]["type"] == "start"
        assert events[0]["messageId"] == "msg_abc123"

    def test_format_text_start(self):
        """Test format_text_start creates correct text-start event."""
        from nimbus.server.api_ai_sdk import format_text_start

        result = format_text_start("text_xyz789")
        events = parse_sse_events(result)

        assert len(events) == 1
        assert events[0]["type"] == "text-start"
        assert events[0]["id"] == "text_xyz789"

    def test_format_text_delta_simple(self):
        """Test format_text_delta with simple ASCII text."""
        from nimbus.server.api_ai_sdk import format_text_delta

        result = format_text_delta("text_001", "Hello")
        events = parse_sse_events(result)

        assert len(events) == 1
        assert events[0]["type"] == "text-delta"
        assert events[0]["id"] == "text_001"
        assert events[0]["delta"] == "Hello"

    def test_format_text_delta_chinese(self):
        """Test format_text_delta with Chinese characters."""
        from nimbus.server.api_ai_sdk import format_text_delta

        result = format_text_delta("text_002", "Hello World")
        events = parse_sse_events(result)

        assert events[0]["delta"] == "Hello World"

    def test_format_text_delta_special_chars(self):
        """Test format_text_delta with special characters."""
        from nimbus.server.api_ai_sdk import format_text_delta

        # Test with quotes, newlines, backslashes
        result = format_text_delta("text_003", 'Say "hello"\nWorld\\n')
        events = parse_sse_events(result)

        assert events[0]["delta"] == 'Say "hello"\nWorld\\n'

    def test_format_text_end(self):
        """Test format_text_end creates correct text-end event."""
        from nimbus.server.api_ai_sdk import format_text_end

        result = format_text_end("text_001")
        events = parse_sse_events(result)

        assert len(events) == 1
        assert events[0]["type"] == "text-end"
        assert events[0]["id"] == "text_001"

    def test_format_tool_input_start(self):
        """Test format_tool_input_start creates correct event."""
        from nimbus.server.api_ai_sdk import format_tool_input_start

        result = format_tool_input_start("call_123", "getWeather")
        events = parse_sse_events(result)

        assert len(events) == 1
        assert events[0]["type"] == "tool-input-start"
        assert events[0]["toolCallId"] == "call_123"
        assert events[0]["toolName"] == "getWeather"

    def test_format_tool_input_available(self):
        """Test format_tool_input_available creates correct event."""
        from nimbus.server.api_ai_sdk import format_tool_input_available

        input_data = {"city": "Beijing", "unit": "celsius"}
        result = format_tool_input_available("call_123", "getWeather", input_data)
        events = parse_sse_events(result)

        assert len(events) == 1
        assert events[0]["type"] == "tool-input-available"
        assert events[0]["toolCallId"] == "call_123"
        assert events[0]["toolName"] == "getWeather"
        assert events[0]["input"] == input_data

    def test_format_tool_output_available(self):
        """Test format_tool_output_available creates correct event."""
        from nimbus.server.api_ai_sdk import format_tool_output_available

        result = format_tool_output_available("call_123", "Temperature: 25C, Sunny")
        events = parse_sse_events(result)

        assert len(events) == 1
        assert events[0]["type"] == "tool-output-available"
        assert events[0]["toolCallId"] == "call_123"
        assert events[0]["output"] == "Temperature: 25C, Sunny"

    def test_format_finish(self):
        """Test format_finish creates correct finish event."""
        from nimbus.server.api_ai_sdk import format_finish

        result = format_finish()
        events = parse_sse_events(result)

        assert len(events) == 1
        assert events[0]["type"] == "finish"

    def test_format_done(self):
        """Test format_done creates correct [DONE] marker."""
        from nimbus.server.api_ai_sdk import format_done

        result = format_done()
        assert result == "data: [DONE]\n\n"


# =============================================================================
# Test SSE Parser
# =============================================================================


class TestSSEParser:
    """Test SSE parsing helper functions."""

    def test_parse_single_event(self):
        """Parse a single SSE event."""
        raw = 'data: {"type": "start", "messageId": "msg_001"}\n\n'
        events = parse_sse_events(raw)

        assert len(events) == 1
        assert events[0]["type"] == "start"
        assert events[0]["messageId"] == "msg_001"

    def test_parse_multiple_events(self):
        """Parse multiple SSE events."""
        raw = (
            'data: {"type": "start", "messageId": "msg_001"}\n\n'
            'data: {"type": "text-start", "id": "text_001"}\n\n'
            'data: {"type": "text-delta", "id": "text_001", "delta": "Hi"}\n\n'
        )
        events = parse_sse_events(raw)

        assert len(events) == 3
        assert events[0]["type"] == "start"
        assert events[1]["type"] == "text-start"
        assert events[2]["type"] == "text-delta"

    def test_parse_done_marker(self):
        """Parse [DONE] marker."""
        raw = (
            'data: {"type": "finish"}\n\n'
            'data: [DONE]\n\n'
        )
        events = parse_sse_events(raw)

        assert len(events) == 2
        assert events[0]["type"] == "finish"
        assert events[1]["type"] == "[DONE]"

    def test_parse_empty_string(self):
        """Parse empty response."""
        events = parse_sse_events("")
        assert events == []

    def test_parse_malformed_json(self):
        """Handle malformed JSON gracefully."""
        raw = 'data: {invalid json}\n\ndata: {"type": "valid"}\n\n'
        events = parse_sse_events(raw)

        # Should skip the malformed line and parse the valid one
        assert len(events) == 1
        assert events[0]["type"] == "valid"

    def test_parse_with_extra_whitespace(self):
        """Handle extra whitespace in response."""
        raw = '  data: {"type": "start", "messageId": "m1"}  \n\n  '
        events = parse_sse_events(raw)

        assert len(events) == 1
        assert events[0]["type"] == "start"


# =============================================================================
# Test Complete Stream Sequences
# =============================================================================


class TestV6StreamSequence:
    """Test complete v6 stream event sequences."""

    def test_simple_text_stream_sequence(self):
        """Test simple text stream follows correct sequence:
        start -> text-start -> text-delta(s) -> text-end -> finish -> [DONE]
        """
        from nimbus.server.api_ai_sdk import (
            format_done,
            format_finish,
            format_start,
            format_text_delta,
            format_text_end,
            format_text_start,
        )

        # Build a complete stream
        stream = ""
        stream += format_start("msg_001")
        stream += format_text_start("text_001")
        stream += format_text_delta("text_001", "Hello ")
        stream += format_text_delta("text_001", "World")
        stream += format_text_end("text_001")
        stream += format_finish()
        stream += format_done()

        events = parse_sse_events(stream)
        event_types = get_event_types(events)

        # Verify sequence
        assert event_types == [
            "start",
            "text-start",
            "text-delta",
            "text-delta",
            "text-end",
            "finish",
            "[DONE]"
        ]

        # Verify text accumulation
        text = collect_text_from_events(events)
        assert text == "Hello World"

    def test_tool_call_stream_sequence(self):
        """Test tool call stream follows correct sequence."""
        from nimbus.server.api_ai_sdk import (
            format_done,
            format_finish,
            format_start,
            format_text_delta,
            format_text_end,
            format_text_start,
            format_tool_input_available,
            format_tool_input_start,
            format_tool_output_available,
        )

        stream = ""
        stream += format_start("msg_002")
        stream += format_tool_input_start("call_001", "read_file")
        stream += format_tool_input_available("call_001", "read_file", {"path": "/test.txt"})
        stream += format_tool_output_available("call_001", "File content here")
        stream += format_text_start("text_002")
        stream += format_text_delta("text_002", "The file contains: File content here")
        stream += format_text_end("text_002")
        stream += format_finish()
        stream += format_done()

        events = parse_sse_events(stream)
        event_types = get_event_types(events)

        assert event_types == [
            "start",
            "tool-input-start",
            "tool-input-available",
            "tool-output-available",
            "text-start",
            "text-delta",
            "text-end",
            "finish",
            "[DONE]"
        ]

        # Verify tool call extraction
        tool_calls = collect_tool_calls(events)
        assert len(tool_calls) == 1
        assert tool_calls[0]["toolCallId"] == "call_001"
        assert tool_calls[0]["toolName"] == "read_file"
        assert tool_calls[0]["input"] == {"path": "/test.txt"}
        assert tool_calls[0]["output"] == "File content here"

    def test_multiple_tool_calls_sequence(self):
        """Test stream with multiple tool calls."""
        from nimbus.server.api_ai_sdk import (
            format_done,
            format_finish,
            format_start,
            format_text_delta,
            format_text_end,
            format_text_start,
            format_tool_input_available,
            format_tool_input_start,
            format_tool_output_available,
        )

        stream = ""
        stream += format_start("msg_003")

        # First tool call
        stream += format_tool_input_start("call_001", "search")
        stream += format_tool_input_available("call_001", "search", {"query": "weather"})
        stream += format_tool_output_available("call_001", "Search results...")

        # Second tool call
        stream += format_tool_input_start("call_002", "fetch")
        stream += format_tool_input_available("call_002", "fetch", {"url": "http://example.com"})
        stream += format_tool_output_available("call_002", "Page content...")

        stream += format_text_start("text_003")
        stream += format_text_delta("text_003", "Based on my research...")
        stream += format_text_end("text_003")
        stream += format_finish()
        stream += format_done()

        events = parse_sse_events(stream)
        tool_calls = collect_tool_calls(events)

        assert len(tool_calls) == 2
        assert tool_calls[0]["toolName"] == "search"
        assert tool_calls[1]["toolName"] == "fetch"

    def test_stream_always_ends_with_done(self):
        """Verify stream always ends with [DONE] marker."""
        from nimbus.server.api_ai_sdk import (
            format_done,
            format_finish,
            format_start,
            format_text_delta,
            format_text_end,
            format_text_start,
        )

        stream = ""
        stream += format_start("msg_004")
        stream += format_text_start("text_004")
        stream += format_text_delta("text_004", "Test")
        stream += format_text_end("text_004")
        stream += format_finish()
        stream += format_done()

        events = parse_sse_events(stream)

        # Last event should be [DONE]
        assert events[-1]["type"] == "[DONE]"
        # Second to last should be finish
        assert events[-2]["type"] == "finish"

    def test_stream_always_starts_with_start(self):
        """Verify stream always starts with start event."""
        from nimbus.server.api_ai_sdk import (
            format_done,
            format_finish,
            format_start,
            format_text_delta,
            format_text_end,
            format_text_start,
        )

        stream = ""
        stream += format_start("msg_005")
        stream += format_text_start("text_005")
        stream += format_text_delta("text_005", "Test")
        stream += format_text_end("text_005")
        stream += format_finish()
        stream += format_done()

        events = parse_sse_events(stream)

        assert events[0]["type"] == "start"
        assert "messageId" in events[0]


# =============================================================================
# Test Frontend Simulation
# =============================================================================


class TestFrontendSimulation:
    """Simulate frontend useChat behavior with v6 protocol."""

    def test_accumulate_text_deltas(self):
        """Test text delta accumulation (simulating frontend text building)."""
        events = [
            {"type": "text-delta", "id": "t1", "delta": "Hello"},
            {"type": "text-delta", "id": "t1", "delta": " "},
            {"type": "text-delta", "id": "t1", "delta": "World"},
            {"type": "text-delta", "id": "t1", "delta": "!"},
        ]

        text = collect_text_from_events(events)
        assert text == "Hello World!"

    def test_accumulate_chinese_text(self):
        """Test Chinese text accumulation (character by character)."""
        # Simulating character-by-character streaming
        events = [
            {"type": "text-delta", "id": "t1", "delta": "Hello"},
            {"type": "text-delta", "id": "t1", "delta": "World"},
            {"type": "text-delta", "id": "t1", "delta": "!"},
        ]

        text = collect_text_from_events(events)
        assert text == "HelloWorld!"

    def test_handle_tool_call_sequence(self):
        """Test handling tool call events as frontend would."""
        events = [
            {"type": "start", "messageId": "msg_001"},
            {"type": "tool-input-start", "toolCallId": "call_001", "toolName": "calculator"},
            {"type": "tool-input-available", "toolCallId": "call_001", "toolName": "calculator",
             "input": {"expression": "2+2"}},
            {"type": "tool-output-available", "toolCallId": "call_001", "output": "4"},
            {"type": "text-start", "id": "text_001"},
            {"type": "text-delta", "id": "text_001", "delta": "The result is 4"},
            {"type": "text-end", "id": "text_001"},
            {"type": "finish"},
            {"type": "[DONE]"},
        ]

        # Simulate frontend state machine
        message_id = None
        current_tool_calls = {}
        final_text = ""
        is_complete = False

        for event in events:
            event_type = event.get("type")

            if event_type == "start":
                message_id = event.get("messageId")
            elif event_type == "tool-input-start":
                tc_id = event["toolCallId"]
                current_tool_calls[tc_id] = {
                    "name": event["toolName"],
                    "input": None,
                    "output": None,
                    "status": "pending"
                }
            elif event_type == "tool-input-available":
                tc_id = event["toolCallId"]
                current_tool_calls[tc_id]["input"] = event["input"]
                current_tool_calls[tc_id]["status"] = "running"
            elif event_type == "tool-output-available":
                tc_id = event["toolCallId"]
                current_tool_calls[tc_id]["output"] = event["output"]
                current_tool_calls[tc_id]["status"] = "complete"
            elif event_type == "text-delta":
                final_text += event.get("delta", "")
            elif event_type == "finish":
                is_complete = True

        # Verify final state
        assert message_id == "msg_001"
        assert len(current_tool_calls) == 1
        assert current_tool_calls["call_001"]["status"] == "complete"
        assert current_tool_calls["call_001"]["output"] == "4"
        assert final_text == "The result is 4"
        assert is_complete

    def test_handle_multiple_text_blocks(self):
        """Test handling multiple separate text blocks."""
        events = [
            {"type": "start", "messageId": "msg_001"},
            {"type": "text-start", "id": "text_001"},
            {"type": "text-delta", "id": "text_001", "delta": "First block. "},
            {"type": "text-end", "id": "text_001"},
            {"type": "text-start", "id": "text_002"},
            {"type": "text-delta", "id": "text_002", "delta": "Second block."},
            {"type": "text-end", "id": "text_002"},
            {"type": "finish"},
            {"type": "[DONE]"},
        ]

        # Collect all text
        text = collect_text_from_events(events)
        assert text == "First block. Second block."

    def test_track_event_ids(self):
        """Test that text events maintain consistent IDs."""
        from nimbus.server.api_ai_sdk import (
            format_text_delta,
            format_text_end,
            format_text_start,
        )

        text_id = "text_consistent_001"

        stream = ""
        stream += format_text_start(text_id)
        stream += format_text_delta(text_id, "Hello")
        stream += format_text_delta(text_id, " World")
        stream += format_text_end(text_id)

        events = parse_sse_events(stream)

        # All text events should have the same ID
        for event in events:
            assert event.get("id") == text_id


# =============================================================================
# Test Request/Response Models
# =============================================================================


class TestV6RequestModels:
    """Test v6 request model handling."""

    def test_message_v5_format(self):
        """Test Message with v5 content format."""
        from nimbus.server.api_ai_sdk import Message

        msg = Message(role="user", content="Hello world")
        assert msg.get_text_content() == "Hello world"

    def test_message_v6_parts_format(self):
        """Test Message with v6 parts format."""
        from nimbus.server.api_ai_sdk import Message

        msg = Message(
            role="user",
            parts=[
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": " Part 2"}
            ],
            id="msg_001"
        )
        assert msg.get_text_content() == "Part 1 Part 2"

    def test_message_v6_mixed_parts(self):
        """Test Message with mixed part types (only text extracted)."""
        from nimbus.server.api_ai_sdk import Message

        msg = Message(
            role="user",
            parts=[
                {"type": "text", "text": "Hello "},
                {"type": "image", "url": "http://example.com/img.png"},
                {"type": "text", "text": "World"}
            ]
        )
        # Should only extract text parts
        assert msg.get_text_content() == "Hello World"

    def test_message_empty_content(self):
        """Test Message with no content."""
        from nimbus.server.api_ai_sdk import Message

        msg = Message(role="user")
        assert msg.get_text_content() == ""

    def test_chat_request_basic(self):
        """Test basic AISdkChatRequest."""
        from nimbus.server.api_ai_sdk import AISdkChatRequest, Message

        request = AISdkChatRequest(
            messages=[
                Message(role="user", content="Hi"),
                Message(role="assistant", content="Hello!"),
                Message(role="user", content="How are you?")
            ],
            sessionId="session_001"
        )

        assert len(request.messages) == 3
        assert request.sessionId == "session_001"

    def test_chat_request_with_workspace(self):
        """Test AISdkChatRequest with workspace path."""
        from nimbus.server.api_ai_sdk import AISdkChatRequest, Message

        request = AISdkChatRequest(
            messages=[Message(role="user", content="Hi")],
            workspacePath="/home/user/project"
        )

        assert request.workspacePath == "/home/user/project"


# =============================================================================
# Test API Endpoint with Mocked Agent
# =============================================================================


class TestChatEndpoint:
    """Test /api/chat endpoint with mocked dependencies."""

    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage."""
        storage = AsyncMock()
        storage.add_message = AsyncMock()
        return storage

    @pytest.fixture
    def mock_session_manager(self):
        """Create a mock session manager."""
        manager = AsyncMock()
        manager.create_session = AsyncMock(return_value={"id": "test_session_001"})
        manager.get_session = AsyncMock(return_value={"id": "test_session_001"})
        return manager

    @pytest.fixture
    def mock_agent(self):
        """Create a mock agent that yields streaming events."""
        agent = AsyncMock()

        async def mock_run_stream(message, history=None):
            # Simulate agent streaming response
            yield {"type": "direct", "content": "Hello from mock agent!"}

        agent.run_stream = mock_run_stream
        return agent

    @pytest.fixture
    def mock_message_cache(self):
        """Create a mock message cache."""
        cache = AsyncMock()
        cache.get_history = AsyncMock(return_value=[])
        cache.add_message = AsyncMock()
        return cache

    @pytest.fixture
    def app_with_mocks(self, mock_storage, mock_session_manager, mock_agent, mock_message_cache):
        """Create FastAPI app with mocked dependencies."""
        from fastapi import FastAPI

        from nimbus.server.api_ai_sdk import router

        app = FastAPI()
        app.include_router(router)

        # Set up app state
        app.state.storage = mock_storage
        app.state.session_manager = mock_session_manager
        app.state.message_cache = mock_message_cache

        # Make session manager return mock agent
        mock_session_manager.get_or_create_agent = AsyncMock(return_value=mock_agent)

        return app

    def test_response_headers(self, app_with_mocks):
        """Verify response has correct headers for v6 protocol."""
        from fastapi.testclient import TestClient

        client = TestClient(app_with_mocks)

        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}]
            }
        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        assert response.headers.get("x-vercel-ai-ui-message-stream") == "v1"

    def test_stream_starts_with_start_event(self, app_with_mocks):
        """Verify stream starts with start event."""
        from fastapi.testclient import TestClient

        client = TestClient(app_with_mocks)

        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}]
            }
        )

        events = parse_sse_events(response.text)
        assert len(events) > 0
        assert events[0]["type"] == "start"
        assert "messageId" in events[0]

    def test_stream_ends_with_done(self, app_with_mocks):
        """Verify stream ends with [DONE] marker."""
        from fastapi.testclient import TestClient

        client = TestClient(app_with_mocks)

        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}]
            }
        )

        events = parse_sse_events(response.text)
        assert len(events) > 0
        assert events[-1]["type"] == "[DONE]"

    def test_stream_includes_text_content(self, app_with_mocks):
        """Verify stream includes text content from agent."""
        from fastapi.testclient import TestClient

        client = TestClient(app_with_mocks)

        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}]
            }
        )

        events = parse_sse_events(response.text)
        text = collect_text_from_events(events)

        assert "Hello from mock agent!" in text

    def test_no_user_message_returns_error(self, app_with_mocks):
        """Test error handling when no user message provided."""
        from fastapi.testclient import TestClient

        client = TestClient(app_with_mocks)

        # Send only assistant messages
        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "assistant", "content": "I am assistant"}]
            }
        )

        events = parse_sse_events(response.text)
        text = collect_text_from_events(events)

        assert "Error" in text or "error" in text.lower()

    def test_empty_messages_returns_error(self, app_with_mocks):
        """Test error handling with empty messages list."""
        from fastapi.testclient import TestClient

        client = TestClient(app_with_mocks)

        response = client.post(
            "/api/chat",
            json={
                "messages": []
            }
        )

        events = parse_sse_events(response.text)
        text = collect_text_from_events(events)

        # Should return error about no user message
        assert "Error" in text or "error" in text.lower()


class TestChatEndpointToolCalls:
    """Test /api/chat endpoint with tool call scenarios."""

    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage."""
        storage = AsyncMock()
        storage.add_message = AsyncMock()
        return storage

    @pytest.fixture
    def mock_session_manager(self):
        """Create a mock session manager."""
        manager = AsyncMock()
        manager.create_session = AsyncMock(return_value={"id": "test_session_002"})
        manager.get_session = AsyncMock(return_value={"id": "test_session_002"})
        return manager

    @pytest.fixture
    def mock_agent_with_tool(self):
        """Create a mock agent that simulates tool calls."""
        agent = AsyncMock()

        async def mock_run_stream(message, history=None):
            # Simulate tool call
            yield {
                "type": "task_start",
                "task_id": "tc_mock_001",
                "skill": "search_web",
                "params": {"query": "test query"}
            }
            yield {
                "type": "task_done",
                "task_id": "tc_mock_001",
                "result": "Search results for: test query"
            }
            # Then text response
            yield {"type": "direct", "content": "Based on my search, I found relevant information."}

        agent.run_stream = mock_run_stream
        return agent

    @pytest.fixture
    def mock_message_cache(self):
        """Create a mock message cache."""
        cache = AsyncMock()
        cache.get_history = AsyncMock(return_value=[])
        cache.add_message = AsyncMock()
        return cache

    @pytest.fixture
    def app_with_tool_mock(self, mock_storage, mock_session_manager, mock_agent_with_tool, mock_message_cache):
        """Create FastAPI app with tool-enabled mock agent."""
        from fastapi import FastAPI

        from nimbus.server.api_ai_sdk import router

        app = FastAPI()
        app.include_router(router)

        app.state.storage = mock_storage
        app.state.session_manager = mock_session_manager
        app.state.message_cache = mock_message_cache
        mock_session_manager.get_or_create_agent = AsyncMock(return_value=mock_agent_with_tool)

        return app

    def test_tool_call_events_emitted(self, app_with_tool_mock):
        """Verify tool call events are properly emitted."""
        from fastapi.testclient import TestClient

        client = TestClient(app_with_tool_mock)

        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Search for test"}]
            }
        )

        events = parse_sse_events(response.text)
        event_types = get_event_types(events)

        # Should contain tool events
        assert "tool-input-start" in event_types
        assert "tool-input-available" in event_types
        assert "tool-output-available" in event_types

    def test_tool_call_data_correct(self, app_with_tool_mock):
        """Verify tool call data is correctly formatted."""
        from fastapi.testclient import TestClient

        client = TestClient(app_with_tool_mock)

        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Search for test"}]
            }
        )

        events = parse_sse_events(response.text)
        tool_calls = collect_tool_calls(events)

        assert len(tool_calls) == 1
        assert tool_calls[0]["toolName"] == "search_web"
        assert tool_calls[0]["input"] == {"query": "test query"}
        assert "Search results" in tool_calls[0]["output"]


class TestChatEndpointErrorHandling:
    """Test /api/chat endpoint error handling."""

    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage."""
        storage = AsyncMock()
        storage.add_message = AsyncMock()
        return storage

    @pytest.fixture
    def mock_session_manager(self):
        """Create a mock session manager."""
        manager = AsyncMock()
        manager.create_session = AsyncMock(return_value={"id": "test_session_003"})
        manager.get_session = AsyncMock(return_value={"id": "test_session_003"})
        return manager

    @pytest.fixture
    def mock_agent_with_error(self):
        """Create a mock agent that raises an error."""
        agent = AsyncMock()

        async def mock_run_stream(message, history=None):
            yield {"type": "direct", "content": "Starting..."}
            raise Exception("Mock agent error")

        agent.run_stream = mock_run_stream
        return agent

    @pytest.fixture
    def mock_message_cache(self):
        """Create a mock message cache."""
        cache = AsyncMock()
        cache.get_history = AsyncMock(return_value=[])
        cache.add_message = AsyncMock()
        return cache

    @pytest.fixture
    def app_with_error_mock(self, mock_storage, mock_session_manager, mock_agent_with_error, mock_message_cache):
        """Create FastAPI app with error-throwing mock agent."""
        from fastapi import FastAPI

        from nimbus.server.api_ai_sdk import router

        app = FastAPI()
        app.include_router(router)

        app.state.storage = mock_storage
        app.state.session_manager = mock_session_manager
        app.state.message_cache = mock_message_cache
        mock_session_manager.get_or_create_agent = AsyncMock(return_value=mock_agent_with_error)

        return app

    def test_error_gracefully_handled(self, app_with_error_mock):
        """Verify errors are gracefully handled in stream."""
        from fastapi.testclient import TestClient

        client = TestClient(app_with_error_mock)

        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Trigger error"}]
            }
        )

        # Should still return 200 (error is in stream)
        assert response.status_code == 200

        events = parse_sse_events(response.text)
        text = collect_text_from_events(events)

        # Error should be in the text
        assert "Error" in text or "error" in text.lower()

        # Stream should still end properly
        assert events[-1]["type"] == "[DONE]"


# =============================================================================
# Test Protocol Edge Cases
# =============================================================================


class TestProtocolEdgeCases:
    """Test edge cases in v6 protocol handling."""

    def test_empty_text_delta(self):
        """Test handling of empty text delta."""
        from nimbus.server.api_ai_sdk import format_text_delta

        result = format_text_delta("text_001", "")
        events = parse_sse_events(result)

        assert events[0]["delta"] == ""

    def test_very_long_text_delta(self):
        """Test handling of very long text delta."""
        from nimbus.server.api_ai_sdk import format_text_delta

        long_text = "A" * 10000
        result = format_text_delta("text_001", long_text)
        events = parse_sse_events(result)

        assert events[0]["delta"] == long_text

    def test_special_json_characters(self):
        """Test handling of special JSON characters in text."""
        from nimbus.server.api_ai_sdk import format_text_delta

        special_text = '{"key": "value"}\n\t\\backslash'
        result = format_text_delta("text_001", special_text)
        events = parse_sse_events(result)

        assert events[0]["delta"] == special_text

    def test_unicode_in_tool_output(self):
        """Test Unicode in tool output."""
        from nimbus.server.api_ai_sdk import format_tool_output_available

        result = format_tool_output_available("call_001", "Result")
        events = parse_sse_events(result)

        assert events[0]["output"] == "Result"

    def test_nested_json_in_tool_input(self):
        """Test nested JSON in tool input."""
        from nimbus.server.api_ai_sdk import format_tool_input_available

        nested_input = {
            "config": {
                "nested": {
                    "deep": ["value1", "value2"]
                }
            },
            "array": [1, 2, 3]
        }

        result = format_tool_input_available("call_001", "complex_tool", nested_input)
        events = parse_sse_events(result)

        assert events[0]["input"] == nested_input
