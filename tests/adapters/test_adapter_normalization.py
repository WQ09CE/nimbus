"""Tests for cross-provider normalization in DirectAdapter.

This test suite ensures that differences between providers (like Ollama's
JSON-in-text tool calls vs native tool calls) are properly normalized into
standard LLMStreamEvents by the time they exit the adapter layer.
"""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from nimbus.adapters.types import LLMStreamEvent


# ---------------------------------------------------------------------------
# Helpers to get a DirectAdapter without real credentials
# ---------------------------------------------------------------------------

def _make_adapter(model_id="test-model"):
    """Create a DirectAdapter with mocked config."""
    with patch("nimbus.adapters.direct_adapter.get_config") as mock_cfg, \
         patch("nimbus.adapters.direct_adapter.DirectAdapter._init_anthropic_oauth"), \
         patch("nimbus.adapters.direct_adapter.DirectAdapter._init_openai_codex_oauth"):
        cfg = MagicMock()
        cfg.gemini_api_key = None
        mock_cfg.return_value = cfg

        from nimbus.adapters.direct_adapter import DirectAdapter, LLMConfig
        adapter = DirectAdapter.__new__(DirectAdapter)
        adapter.config = LLMConfig()
        adapter._model = model_id
        adapter._anthropic_auth = None
        adapter._codex_auth = None
        adapter._anthropic_client = None
        adapter._anthropic_client_token = None
        adapter._codex_client = None
        adapter._codex_client_token = None
        return adapter


# ---------------------------------------------------------------------------
# Mock Async Stream for LiteLLM
# ---------------------------------------------------------------------------

class MockDelta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls

class MockChoice:
    def __init__(self, delta):
        self.delta = delta

class MockChunk:
    def __init__(self, delta):
        self.choices = [MockChoice(delta)]

class MockAsyncStream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        self.iter = iter(self.chunks)
        return self

    async def __anext__(self):
        try:
            return next(self.iter)
        except StopIteration:
            raise StopAsyncIteration


# ---------------------------------------------------------------------------
# Normalization Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAdapterNormalization:

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_native_text_streaming(self, mock_acompletion):
        """Test that plain text from LiteLLM is yielded as text events."""
        adapter = _make_adapter("openai/gpt-4")
        
        # Mock acompletion to return an async stream generator
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(content="Hello")),
            MockChunk(MockDelta(content=" world")),
        ])

        events = []
        async for event in adapter._stream_litellm([]):
            events.append(event)
            
        assert len(events) == 2
        assert events[0].type == "text"
        assert events[0].text == "Hello world"
        assert events[1].type == "stop"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_native_tool_call_streaming(self, mock_acompletion):
        """Test that native tool calls are properly assembled and yielded."""
        adapter = _make_adapter("openai/gpt-4")
        
        # Mock native LiteLLM tool calls (chunked)
        tc1 = MagicMock()
        tc1.index = 0
        tc1.id = "call_123"
        tc1.function = MagicMock(name="get_weather", arguments='{"loc')
        tc1.function.name = "get_weather"
        tc1.function.arguments = '{"loc'
        
        tc2 = MagicMock()
        tc2.index = 0
        tc2.id = None
        tc2.function = MagicMock(name=None, arguments='ation": "NY"}')
        tc2.function.name = None
        tc2.function.arguments = 'ation": "NY"}'

        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(tool_calls=[tc1])),
            MockChunk(MockDelta(tool_calls=[tc2])),
        ])

        events = []
        async for event in adapter._stream_litellm([]):
            events.append(event)
            
        assert len(events) == 2
        assert events[0].type == "tool_call"
        assert events[0].tool_call["name"] == "get_weather"
        assert events[0].tool_call["arguments"] == {"location": "NY"}
        assert events[1].type == "stop"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_ollama_reasoning_fallback_to_text(self, mock_acompletion):
        """Test that Ollama yielding only reasoning_content falls back to text."""
        # Using ollama prefix
        adapter = _make_adapter("ollama/qwen3.5:9b")
        
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(reasoning_content="Let me think...")),
            MockChunk(MockDelta(reasoning_content=" Yes, done.")),
        ])

        events = []
        async for event in adapter._stream_litellm([]):
            events.append(event)
            
        assert len(events) == 2
        assert events[0].type == "text"
        assert events[0].text == "Let me think... Yes, done."
        assert events[1].type == "stop"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_ollama_json_tool_extraction(self, mock_acompletion):
        """Test that Ollama yielding JSON in reasoning_content gets extracted to a tool_call."""
        adapter = _make_adapter("ollama/qwen3.5:9b")
        
        # Simulate Ollama dumping JSON tool call into the reasoning block
        json_str = '```json\n{"name": "run_bash", "arguments": {"cmd": "ls -l"}}\n```'
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(reasoning_content=json_str)),
        ])

        events = []
        async for event in adapter._stream_litellm([]):
            events.append(event)
            
        assert len(events) == 2
        assert events[0].type == "tool_call"
        assert events[0].tool_call["name"] == "run_bash"
        assert events[0].tool_call["arguments"] == {"cmd": "ls -l"}
        assert events[1].type == "stop"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_ollama_json_tool_extraction_array(self, mock_acompletion):
        """Test that Ollama yielding JSON array gets extracted to multiple tool_calls."""
        adapter = _make_adapter("ollama/qwen3.5:9b")
        
        json_arr = '[{"name": "t1", "arguments": {"k": "v1"}}, {"name": "t2", "arguments": {"k": "v2"}}]'
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(reasoning_content=json_arr)),
        ])

        events = []
        async for event in adapter._stream_litellm([]):
            events.append(event)
            
        assert len(events) == 3
        assert events[0].type == "tool_call"
        assert events[0].tool_call["name"] == "t1"
        assert events[1].type == "tool_call"
        assert events[1].tool_call["name"] == "t2"
        assert events[2].type == "stop"
