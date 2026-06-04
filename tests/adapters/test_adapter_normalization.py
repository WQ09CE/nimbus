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
        self.finish_reason = None

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
            
        assert len(events) == 3
        assert events[0].type == "text"
        assert events[0].text == "Hello"
        assert events[1].type == "text"
        assert events[1].text == " world"
        assert events[2].type == "stop"

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
    async def test_ollama_streamed_json_content_stays_buffered(self, mock_acompletion):
        """Gemma4/Ollama can stream JSON tool calls as split content chunks."""
        adapter = _make_adapter("ollama/gemma4:26b")

        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(content='{"')),
            MockChunk(MockDelta(content='name')),
            MockChunk(MockDelta(content='": "Bash", "')),
            MockChunk(MockDelta(content='arguments')),
            MockChunk(MockDelta(content='": {"command": "printf NIMBUS_TOOL_OK"}}')),
        ])

        events = []
        async for event in adapter._stream_litellm([]):
            events.append(event)

        assert len(events) == 2
        assert events[0].type == "tool_call"
        assert events[0].tool_call["name"] == "Bash"
        assert events[0].tool_call["arguments"] == {"command": "printf NIMBUS_TOOL_OK"}
        assert events[1].type == "stop"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_gemma4_keeps_text_tools_after_tool_result(self, mock_acompletion):
        """Post-tool Gemma4 turns can continue, without LiteLLM provider tools."""
        adapter = _make_adapter("ollama/gemma4:26b")
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(content="done")),
        ])
        tools = [{
            "type": "function",
            "function": {
                "name": "Bash",
                "description": "Execute shell",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        messages = [
            {"role": "user", "content": "run command"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "Bash", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "OK"},
        ]

        events = []
        async for event in adapter._stream_litellm(messages, tools=tools):
            events.append(event)

        assert mock_acompletion.call_args.kwargs["tools"] is None
        request_messages = mock_acompletion.call_args.kwargs["messages"]
        assert "Tool calling is available" in request_messages[0]["content"]
        assert "Tool results were just returned" in request_messages[0]["content"]
        assert events[0].type == "text"
        assert events[0].text == "done"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_gemma4_text_tool_prompt_includes_parameters(self, mock_acompletion):
        """Gemma4 text-tool mode must expose parameter names for submit_result."""
        adapter = _make_adapter("ollama/gemma4:26b")
        tools = [{
            "type": "function",
            "function": {
                "name": "submit_result",
                "description": "Submit structured results",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Brief summary"},
                        "findings": {
                            "type": "array",
                            "description": "Key findings",
                            "items": {"type": "string"},
                        },
                        "artifacts": {
                            "type": "array",
                            "description": "Artifact paths",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["summary", "findings"],
                    "additionalProperties": False,
                },
            },
        }]
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(content="done")),
        ])

        events = []
        async for event in adapter._stream_litellm([{"role": "user", "content": "finish"}], tools=tools):
            events.append(event)

        assert mock_acompletion.call_args.kwargs["tools"] is None
        request_messages = mock_acompletion.call_args.kwargs["messages"]
        prompt = request_messages[0]["content"]
        assert "- submit_result: Submit structured results" in prompt
        assert "Use exactly these parameter names" in prompt
        assert "- summary (string, required): Brief summary" in prompt
        assert "- findings (array, required): Key findings" in prompt
        assert "- artifacts (array, optional): Artifact paths" in prompt
        assert events[0].type == "text"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_gemma4_reenables_tools_after_new_user_message(self, mock_acompletion):
        """A later user turn after tool finalization must get tools again."""
        adapter = _make_adapter("ollama/gemma4:26b")
        tools = [{
            "type": "function",
            "function": {
                "name": "Bash",
                "description": "Execute shell",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        messages = [
            {"role": "user", "content": "run command"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "Bash", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "OK"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "run another command"},
        ]
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(content='{"name":"Bash","arguments":{"command":"pwd"}}')),
        ])

        events = []
        async for event in adapter._stream_litellm(messages, tools=tools):
            events.append(event)

        assert mock_acompletion.call_args.kwargs["tools"] is None
        request_messages = mock_acompletion.call_args.kwargs["messages"]
        assert "Tool calling is available" in request_messages[0]["content"]
        assert "Tool results were just returned" not in request_messages[0]["content"]
        tool_events = [e for e in events if e.type == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call["name"] == "Bash"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_gemma4_does_not_pass_tools_to_litellm_transformer(self, mock_acompletion):
        """Gemma4 gets text tool instructions, not LiteLLM's JSON-only tools mode."""
        adapter = _make_adapter("ollama/gemma4:26b")
        tools = [{
            "type": "function",
            "function": {
                "name": "Bash",
                "description": "Execute shell",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        messages = [{"role": "system", "content": "base"}, {"role": "user", "content": "hi"}]
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(content="NIMBUS_CHAT_OK")),
        ])

        events = []
        async for event in adapter._stream_litellm(messages, tools=tools):
            events.append(event)

        assert mock_acompletion.call_args.kwargs["tools"] is None
        request_messages = mock_acompletion.call_args.kwargs["messages"]
        assert request_messages[0]["role"] == "system"
        assert "Tool calling is available" in request_messages[0]["content"]
        assert "For normal conversational replies" in request_messages[0]["content"]
        assert events[0].type == "text"
        assert events[0].text == "NIMBUS_CHAT_OK"
        assert events[-1].type == "stop"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_gemma4_extracts_fenced_json_after_streamed_text(self, mock_acompletion):
        """Gemma4 can explain first, then emit a fenced JSON function call."""
        adapter = _make_adapter("ollama/gemma4:26b")
        tools = [{
            "type": "function",
            "function": {
                "name": "Bash",
                "description": "Execute shell",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(content="我会真正调用工具。\n")),
            MockChunk(MockDelta(content="`")),
            MockChunk(MockDelta(content="``json\n")),
            MockChunk(MockDelta(content='{\n  "id": "real_bash_execution_001",\n')),
            MockChunk(MockDelta(content='  "type": "function",\n')),
            MockChunk(MockDelta(content='  "function": {\n    "name": "Bash",\n')),
            MockChunk(MockDelta(content='    "arguments": {"command": "echo \\"Actual Tool Call Test: $(date)\\" > tool_test.log && cat tool_test.log"}\n')),
            MockChunk(MockDelta(content="  }\n}\n```")),
        ])

        events = []
        async for event in adapter._stream_litellm([], tools=tools):
            events.append(event)

        assert mock_acompletion.call_args.kwargs["tools"] is None
        request_messages = mock_acompletion.call_args.kwargs["messages"]
        assert "Tool calling is available" in request_messages[0]["content"]
        streamed_text = "".join(e.text or "" for e in events if e.type == "text")
        tool_events = [e for e in events if e.type == "tool_call"]

        assert "我会真正调用工具" in streamed_text
        assert "```" not in streamed_text
        assert "tool_test.log" not in streamed_text
        assert len(tool_events) == 1
        assert tool_events[0].tool_call["name"] == "Bash"
        assert "tool_test.log" in tool_events[0].tool_call["arguments"]["command"]
        assert events[-1].type == "stop"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_gemma4_extracts_labeled_tool_calls_after_streamed_text(self, mock_acompletion):
        """Gemma4 may emit a plain Tool Calls: list after explanatory text."""
        adapter = _make_adapter("ollama/gemma4:26b")
        tools = [{
            "type": "function",
            "function": {
                "name": "Write",
                "description": "Write content",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(content="Step 1: Initializing Scratchpad\n\nTo")),
            MockChunk(MockDelta(content="ol Calls: [\n")),
            MockChunk(MockDelta(content='  {"id": "plan_init_0", "type": "function", "function": ')),
            MockChunk(MockDelta(content='{"name": "Write", "arguments": {"path": "scratchpad.md", "content": "TODO"}}}\n')),
            MockChunk(MockDelta(content="]")),
        ])

        events = []
        async for event in adapter._stream_litellm([], tools=tools):
            events.append(event)

        streamed_text = "".join(e.text or "" for e in events if e.type == "text")
        tool_events = [e for e in events if e.type == "tool_call"]
        assert "Step 1" in streamed_text
        assert "Tool Calls" not in streamed_text
        assert len(tool_events) == 1
        assert tool_events[0].tool_call["name"] == "Write"
        assert tool_events[0].tool_call["arguments"]["path"] == "scratchpad.md"
        assert events[-1].type == "stop"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_gemma4_extracts_fenced_json_after_tool_result(self, mock_acompletion):
        """Post-tool Gemma4 turns can call the next required tool."""
        adapter = _make_adapter("ollama/gemma4:26b")
        tools = [{
            "type": "function",
            "function": {
                "name": "Bash",
                "description": "Execute shell",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        messages = [
            {"role": "user", "content": "run command"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "Bash", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "OK"},
        ]
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(content="工具已经执行完成。\n")),
            MockChunk(MockDelta(content='```json\n{"name": "Bash", "arguments": {"command": "echo loop"}}\n```')),
        ])

        events = []
        async for event in adapter._stream_litellm(messages, tools=tools):
            events.append(event)

        assert mock_acompletion.call_args.kwargs["tools"] is None
        request_messages = mock_acompletion.call_args.kwargs["messages"]
        assert "Tool results were just returned" in request_messages[0]["content"]
        tool_events = [e for e in events if e.type == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call["name"] == "Bash"
        assert tool_events[0].tool_call["arguments"]["command"] == "echo loop"
        streamed_text = "".join(e.text or "" for e in events if e.type == "text")
        assert "工具已经执行完成" in streamed_text
        assert "echo loop" not in streamed_text
        assert events[-1].type == "stop"

    @patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock)
    async def test_gemma4_suppresses_json_noop_tool_text(self, mock_acompletion):
        """JSON no-op tool text should not be shown as an assistant reply."""
        adapter = _make_adapter("ollama/gemma4:26b")
        tools = [{
            "type": "function",
            "function": {
                "name": "Bash",
                "description": "Execute shell",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        mock_acompletion.return_value = MockAsyncStream([
            MockChunk(MockDelta(content='{"name": "None", "arguments": {}}')),
        ])

        events = []
        async for event in adapter._stream_litellm([], tools=tools):
            events.append(event)

        assert [e for e in events if e.type == "text"] == []
        assert [e for e in events if e.type == "tool_call"] == []
        assert events[-1].type == "stop"

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
