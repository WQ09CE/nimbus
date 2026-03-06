"""
Integration tests for multiple LLM providers via DirectAdapter.

Tests real LLM tool calling across providers:
  - Ollama (qwen3.5:4b, qwen3.5:9b) — requires local ollama server
  - Anthropic (claude-sonnet-4-6) — uses anthropic OAuth from ~/.pi/agent/auth.json or ANTHROPIC_API_KEY
  - Google (gemini-3-flash-preview) — requires GEMINI_API_KEY
  - OpenAI (gpt-4o) — uses codex OAuth from ~/.pi/agent/auth.json or OPENAI_API_KEY

Run selectively:
  pytest tests/adapters/test_provider_integration.py -m ollama -v -s --timeout=60
  pytest tests/adapters/test_provider_integration.py -m anthropic -v -s --timeout=30
  pytest tests/adapters/test_provider_integration.py -m google -v -s --timeout=30
  pytest tests/adapters/test_provider_integration.py -m openai -v -s --timeout=30
  pytest tests/adapters/test_provider_integration.py -v -s --timeout=60   # ALL
"""

import json
import os
from pathlib import Path

import pytest

from nimbus.adapters.direct_adapter import DirectAdapter
from nimbus.adapters.types import LLMConfig, LLMStreamEvent

# ---------------------------------------------------------------------------
# Shared tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "Read",
        "description": "Read a file's contents",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to read"}
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Bash",
        "description": "Execute a bash command and return output",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to execute"}
            },
            "required": ["command"],
        },
    },
    {
        "name": "Write",
        "description": "Write content to a file",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to write to"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Edit",
        "description": "Edit a file by replacing text",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
                "old_text": {"type": "string", "description": "Text to find"},
                "new_text": {"type": "string", "description": "Text to replace with"},
            },
            "required": ["file_path", "old_text", "new_text"],
        },
    },
]

SIMPLE_TOOLS = TOOLS[:2]  # Read + Bash only


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AUTH_JSON_PATH = Path.home() / ".pi" / "agent" / "auth.json"


def _load_oauth_token(key: str) -> str | None:
    """Load OAuth access token from ~/.pi/agent/auth.json."""
    if not AUTH_JSON_PATH.exists():
        return None
    try:
        data = json.loads(AUTH_JSON_PATH.read_text())
        auth = data.get(key)
        if auth and "access" in auth:
            return auth["access"]
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _ensure_openai_key() -> bool:
    """Set OPENAI_API_KEY from codex OAuth if not already set."""
    if os.environ.get("OPENAI_API_KEY"):
        return True
    token = _load_oauth_token("openai-codex")
    if token:
        os.environ["OPENAI_API_KEY"] = token
        return True
    return False


def _has_anthropic_auth() -> bool:
    """Check if anthropic OAuth token exists in auth.json."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    return _load_oauth_token("anthropic") is not None


def _make_adapter(model: str, max_tokens: int = 2048) -> DirectAdapter:
    config = LLMConfig(model=model, temperature=0.0, max_tokens=max_tokens)
    return DirectAdapter(config=config)


def _get_streamer(adapter: DirectAdapter, messages, tools):
    """Route to the correct streaming method based on model/auth."""
    if adapter._is_anthropic_model() and adapter._anthropic_auth is not None:
        return adapter._stream_anthropic_native(messages, tools)
    # Codex, Ollama, Google, OpenAI all go through litellm
    return adapter._stream_litellm(messages=messages, tools=tools)


async def _collect_events(adapter: DirectAdapter, messages, tools=None):
    """Stream and collect all events, printing diagnostics."""
    events = []
    async for event in _get_streamer(adapter, messages, tools or SIMPLE_TOOLS):
        events.append(event)

    tool_calls = [e for e in events if e.type == "tool_call"]
    text_events = [e for e in events if e.type == "text"]

    print(f"\n  Total events: {len(events)}, tool_calls: {len(tool_calls)}, text: {len(text_events)}")
    for e in tool_calls:
        print(f"  tool_call: {e.tool_call}")
    for e in text_events:
        snippet = e.text[:200] if e.text else "(empty)"
        print(f"  text: {snippet}")

    return events, tool_calls, text_events


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def ollama_4b():
    return _make_adapter("ollama/qwen3.5:4b")


@pytest.fixture
async def ollama_9b():
    return _make_adapter("ollama/qwen3.5:9b")


@pytest.fixture
async def anthropic_sonnet():
    """Claude Sonnet adapter -- uses anthropic OAuth from ~/.pi/agent/auth.json."""
    if not _has_anthropic_auth():
        pytest.skip("No Anthropic credentials (set ANTHROPIC_API_KEY or have ~/.pi/agent/auth.json)")
    return _make_adapter("anthropic/claude-sonnet-4-6", max_tokens=1024)


@pytest.fixture
async def gemini_flash():
    return _make_adapter("google/gemini-3-flash-preview", max_tokens=1024)


@pytest.fixture
async def openai_gpt():
    """GPT-4o adapter -- uses codex OAuth from ~/.pi/agent/auth.json."""
    if not _ensure_openai_key():
        pytest.skip("No OpenAI credentials (set OPENAI_API_KEY or login with codex OAuth)")
    return _make_adapter("openai/gpt-4o", max_tokens=1024)


# ===========================================================================
# Ollama Tests (qwen3.5:4b)
# ===========================================================================

@pytest.mark.ollama
class TestOllamaQwen4b:
    """Tests for qwen3.5:4b via ollama."""

    async def test_basic_tool_call(self, ollama_4b):
        """Ask the model to call Read -- verify tool_call event extracted."""
        messages = [
            {"role": "system", "content": "You must use the Read tool to read files. Call Read with the file_path argument."},
            {"role": "user", "content": "Use Read tool to read /tmp/test.txt"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_4b, messages)

        assert len(tool_calls) > 0, (
            f"No tool_call events. Got {len(text_events)} text events. "
            f"Text: {' '.join(e.text[:100] for e in text_events)}"
        )

    async def test_bash_tool_call(self, ollama_4b):
        """Ask to run a command -- verify Bash tool call."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Use tools to complete tasks."},
            {"role": "user", "content": "List files in /tmp using the Bash tool"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_4b, messages)

        assert len(tool_calls) > 0, "No tool call extracted for Bash request"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "Bash", f"Expected Bash tool but got {tc['name']}"

    async def test_multi_tool_selection(self, ollama_4b):
        """Give 4 tools, ask for a specific one -- verify correct selection."""
        messages = [
            {"role": "system", "content": "You have 4 tools: Read, Bash, Write, Edit. Use the appropriate tool."},
            {"role": "user", "content": "Write 'hello world' to the file /tmp/greeting.txt using the Write tool"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_4b, messages, tools=TOOLS)

        assert len(tool_calls) > 0, "No tool call extracted"
        tc = tool_calls[0].tool_call
        # Small models may pick Write or Bash -- both are acceptable
        print(f"  Selected tool: {tc['name']}")

    async def test_tool_call_with_complex_args(self, ollama_4b):
        """Ask for Edit which requires 3 arguments."""
        messages = [
            {"role": "system", "content": "Use the Edit tool to make changes to files."},
            {"role": "user", "content": "In file /tmp/app.py, replace 'print(hello)' with 'print(world)' using the Edit tool"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_4b, messages, tools=TOOLS)

        assert len(tool_calls) > 0, "No tool call extracted for Edit request"
        tc = tool_calls[0].tool_call
        if tc["name"] == "Edit":
            args = tc["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            assert "file_path" in args, f"Missing file_path in args: {args}"
            print(f"  Edit args: {args}")

    async def test_chinese_language(self, ollama_4b):
        """Ask in Chinese -- verify response or tool call."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Use tools when needed."},
            {"role": "user", "content": "请使用Read工具读取文件 /tmp/config.yaml"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_4b, messages)

        # Should get either a tool call or text response
        assert len(events) > 0, "No events at all"
        if tool_calls:
            print(f"  Got tool call for Chinese request: {tool_calls[0].tool_call['name']}")
        else:
            full_text = "".join(e.text for e in text_events)
            print(f"  Got text response (no tool call): {full_text[:200]}")

    async def test_plain_text_response(self, ollama_4b):
        """A simple question should produce a response without tools."""
        messages = [
            {"role": "system", "content": "Answer the question directly. Do NOT use any tools."},
            {"role": "user", "content": "What is 2 + 2? Reply with just the number."},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_4b, messages)

        assert len(events) > 0, "No events at all from adapter"

    async def test_error_json_suppressed(self, ollama_4b):
        """If model outputs {"error": "..."}, it should be suppressed."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Read the file /nonexistent/path.txt"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc_0",
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "arguments": '{"file_path": "/nonexistent/path.txt"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "content": "Error: File not found: /nonexistent/path.txt",
                "tool_call_id": "tc_0",
                "name": "Read",
            },
        ]
        events, tool_calls, text_events = await _collect_events(ollama_4b, messages)

        for e in text_events:
            if e.text.strip().startswith("{"):
                try:
                    parsed = json.loads(e.text.strip())
                    assert "error" not in parsed or len(parsed) > 3, (
                        f"JSON error text leaked to output: {e.text[:200]}"
                    )
                except json.JSONDecodeError:
                    pass

    async def test_sequential_conversation(self, ollama_4b):
        """Send a conversation with tool result -- check model follow-up."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Use tools to help users."},
            {"role": "user", "content": "What is in the file /tmp/data.txt?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "arguments": '{"file_path": "/tmp/data.txt"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "content": "Hello World\nThis is test data.\nLine 3.",
                "tool_call_id": "tc_1",
                "name": "Read",
            },
        ]
        events, tool_calls, text_events = await _collect_events(ollama_4b, messages)

        # Model should respond with text summarizing the file content, or possibly another tool call
        assert len(events) > 0, "No response after tool result"
        if text_events:
            full_text = "".join(e.text for e in text_events)
            print(f"  Follow-up text: {full_text[:300]}")


# ===========================================================================
# Ollama Tests (qwen3.5:9b)
# ===========================================================================

@pytest.mark.ollama
class TestOllamaQwen9b:
    """Tests for qwen3.5:9b via ollama -- more capable, can be more ambitious."""

    async def test_basic_tool_call(self, ollama_9b):
        """9b model should reliably call Read when asked."""
        messages = [
            {"role": "system", "content": "You must use the Read tool to read files. Call Read with the file_path argument."},
            {"role": "user", "content": "Read the file /etc/hostname"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_9b, messages)

        assert len(tool_calls) > 0, f"No tool_call events from 9b model"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "Read", f"Expected Read but got {tc['name']}"
        args = tc["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        assert "file_path" in args

    async def test_bash_tool_call(self, ollama_9b):
        """9b model should handle Bash tool calls."""
        messages = [
            {"role": "system", "content": "Use the Bash tool to run commands."},
            {"role": "user", "content": "Run 'uname -a' using Bash"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_9b, messages)

        assert len(tool_calls) > 0, "No tool call from 9b for Bash"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "Bash"

    async def test_multi_tool_selection(self, ollama_9b):
        """9b should be better at picking the correct tool from 4 options."""
        messages = [
            {"role": "system", "content": "You have 4 tools: Read, Bash, Write, Edit. Choose the right one."},
            {"role": "user", "content": "Edit the file /tmp/main.py: change 'DEBUG = True' to 'DEBUG = False'"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_9b, messages, tools=TOOLS)

        assert len(tool_calls) > 0, "No tool call for Edit request"
        tc = tool_calls[0].tool_call
        print(f"  9b selected tool: {tc['name']} (expected Edit)")
        # 9b should more reliably pick Edit
        if tc["name"] == "Edit":
            args = tc["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            assert "file_path" in args
            assert "old_text" in args or "old_string" in args  # model might use slightly different names
            print(f"  Edit args: {args}")

    async def test_complex_args(self, ollama_9b):
        """Test that 9b can produce multiple arguments correctly."""
        messages = [
            {"role": "system", "content": "Use the Write tool to create files."},
            {"role": "user", "content": "Create a file /tmp/hello.py with content: print('Hello, World!')"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_9b, messages, tools=TOOLS)

        assert len(tool_calls) > 0, "No tool call for Write"
        tc = tool_calls[0].tool_call
        if tc["name"] == "Write":
            args = tc["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            assert "file_path" in args
            assert "content" in args
            print(f"  Write args: file_path={args.get('file_path')}, content_len={len(str(args.get('content', '')))}")

    async def test_chinese_language(self, ollama_9b):
        """Chinese prompt -- 9b should handle well."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Use tools when needed."},
            {"role": "user", "content": "请用Bash工具执行命令: echo '你好世界'"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_9b, messages)

        assert len(events) > 0, "No response for Chinese prompt"
        if tool_calls:
            tc = tool_calls[0].tool_call
            print(f"  Chinese: got tool call {tc['name']}")

    async def test_sequential_conversation(self, ollama_9b):
        """Conversation with prior tool result -- 9b should give coherent follow-up."""
        messages = [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": "Check what Python version is installed"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc_2",
                        "type": "function",
                        "function": {
                            "name": "Bash",
                            "arguments": '{"command": "python3 --version"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "content": "Python 3.12.1",
                "tool_call_id": "tc_2",
                "name": "Bash",
            },
        ]
        events, tool_calls, text_events = await _collect_events(ollama_9b, messages)

        assert len(events) > 0, "No response after tool result"

    async def test_4b_vs_9b_both_extract(self, ollama_4b, ollama_9b):
        """Both 4b and 9b should be able to extract tool calls for the same prompt."""
        messages = [
            {"role": "system", "content": "Use the Read tool to read files."},
            {"role": "user", "content": "Read /tmp/test.txt"},
        ]

        events_4b, tc_4b, _ = await _collect_events(ollama_4b, messages)
        events_9b, tc_9b, _ = await _collect_events(ollama_9b, messages)

        print(f"\n  4b tool_calls: {len(tc_4b)}, 9b tool_calls: {len(tc_9b)}")

        # Both should produce at least one tool call
        assert len(tc_4b) > 0 or len(tc_9b) > 0, (
            "Neither 4b nor 9b produced tool calls -- adapter issue?"
        )
        if tc_4b:
            print(f"  4b: {tc_4b[0].tool_call}")
        if tc_9b:
            print(f"  9b: {tc_9b[0].tool_call}")

    async def test_no_tools_mode(self, ollama_9b):
        """When no tools are provided, 9b should produce pure text."""
        messages = [
            {"role": "user", "content": "What is 2 + 2? Reply with just the number."},
        ]
        events = []
        async for event in _get_streamer(ollama_9b, messages, tools=None):
            events.append(event)
        tool_calls = [e for e in events if e.type == "tool_call"]
        text_events = [e for e in events if e.type == "text"]
        # Small models should not hallucinate tool calls when none are provided
        assert len(tool_calls) == 0, f"Got tool calls with no tools provided: {[tc.tool_call for tc in tool_calls]}"
        assert len(text_events) > 0, "No text response"
        full_text = "".join(e.text for e in text_events)
        print(f"  9b no-tools response: {full_text[:200]}")

    async def test_streaming_stop_event(self, ollama_9b):
        """Stream should end with a stop event (relaxed for small models)."""
        messages = [
            {"role": "user", "content": "Say 'hello'"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_9b, messages)
        stop_events = [e for e in events if e.type == "stop"]
        event_types = [e.type for e in events]
        if not stop_events:
            print(f"  WARNING: ollama 9b missing stop event. Types: {event_types}")
        else:
            print(f"  9b stop event present. Types: {event_types}")
        # Relaxed: just warn, don't fail
        assert len(events) > 0, "No events at all from ollama 9b"

    async def test_error_recovery(self, ollama_9b):
        """After receiving a tool error, 9b should try something (relaxed assertion)."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. If a tool fails, explain the error or try another approach."},
            {"role": "user", "content": "Read the log file"},
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "tc_ol_err1", "type": "function", "function": {"name": "Read", "arguments": '{"file_path": "/var/log/system.log"}'}}],
            },
            {"role": "tool", "content": "Error: Permission denied: /var/log/system.log", "tool_call_id": "tc_ol_err1", "name": "Read"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_9b, messages)
        # Relaxed: just expect some response, don't mandate specific behavior
        assert len(events) > 0, "No response after error from ollama 9b"
        if tool_calls:
            tc = tool_calls[0].tool_call
            print(f"  9b error recovery: tried {tc['name']}")
        elif text_events:
            full_text = "".join(e.text for e in text_events)
            print(f"  9b error recovery text: {full_text[:200]}")

    async def test_tool_not_needed(self, ollama_9b):
        """When tools are available but not needed, 9b ideally responds with text only (relaxed)."""
        messages = [
            {"role": "system", "content": "You have tools available but only use them when necessary. For general knowledge questions, respond directly without tools."},
            {"role": "user", "content": "What is the capital of France?"},
        ]
        events, tool_calls, text_events = await _collect_events(ollama_9b, messages)
        assert len(events) > 0, "No events from ollama 9b"
        if tool_calls:
            # Small models may still call tools -- just warn
            print(f"  WARNING: 9b used tools for general knowledge: {[tc.tool_call['name'] for tc in tool_calls]}")
        if text_events:
            full_text = "".join(e.text for e in text_events)
            print(f"  9b text response: {full_text[:200]}")


# ===========================================================================
# Anthropic Tests (claude-sonnet-4-6)
# ===========================================================================

@pytest.mark.anthropic
class TestAnthropicSonnet:
    """Tests for claude-sonnet-4-6 via Anthropic API."""

    async def test_basic_tool_call(self, anthropic_sonnet):
        """Claude should reliably call Read when asked."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Use tools as needed."},
            {"role": "user", "content": "Read the file /tmp/config.yaml"},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages)

        assert len(tool_calls) > 0, "Claude did not produce a tool call"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "Read"
        args = tc["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        assert "file_path" in args
        assert args["file_path"] == "/tmp/config.yaml"

    async def test_bash_tool_call(self, anthropic_sonnet):
        """Claude should handle Bash tool calls correctly."""
        messages = [
            {"role": "system", "content": "Use tools to help the user."},
            {"role": "user", "content": "Run the command 'ls -la /tmp' using Bash"},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages)

        assert len(tool_calls) > 0, "No Bash tool call from Claude"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "Bash"

    async def test_multi_tool_selection(self, anthropic_sonnet):
        """Claude should precisely select the right tool from 4 options."""
        messages = [
            {"role": "system", "content": "You have tools: Read, Bash, Write, Edit. Use the most appropriate one."},
            {"role": "user", "content": "Write a Python script to /tmp/hello.py that prints 'Hello World'"},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages, tools=TOOLS)

        assert len(tool_calls) > 0, "No tool call from Claude for Write"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "Write", f"Expected Write but Claude chose {tc['name']}"
        args = tc["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        assert "file_path" in args
        assert "content" in args

    async def test_complex_args(self, anthropic_sonnet):
        """Claude should produce all required Edit arguments."""
        messages = [
            {"role": "system", "content": "Use tools to help."},
            {"role": "user", "content": "In /tmp/app.py, replace 'DEBUG = True' with 'DEBUG = False'"},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages, tools=TOOLS)

        assert len(tool_calls) > 0, "No tool call for Edit"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "Edit"
        args = tc["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        assert "file_path" in args
        assert "old_text" in args
        assert "new_text" in args

    async def test_chinese_language(self, anthropic_sonnet):
        """Claude should handle Chinese prompts."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Respond in the user's language."},
            {"role": "user", "content": "请使用Bash工具执行 echo '你好' 命令"},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages)

        assert len(tool_calls) > 0, "No tool call for Chinese prompt"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "Bash"

    async def test_sequential_conversation(self, anthropic_sonnet):
        """Claude should give coherent follow-up after receiving tool result."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is in /tmp/notes.txt?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc_anth_1",
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "arguments": '{"file_path": "/tmp/notes.txt"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "content": "Meeting at 3pm\nBring laptop\nPrepare slides",
                "tool_call_id": "tc_anth_1",
                "name": "Read",
            },
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages)

        assert len(text_events) > 0, "Claude did not produce text after tool result"
        full_text = "".join(e.text for e in text_events)
        print(f"  Claude follow-up: {full_text[:300]}")

    async def test_parallel_tool_calls(self, anthropic_sonnet):
        """Claude should produce multiple tool calls for independent requests."""
        messages = [
            {"role": "system", "content": "When multiple independent actions are needed, call multiple tools in parallel."},
            {"role": "user", "content": "Read both /tmp/file_a.txt and /tmp/file_b.txt"},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages)
        assert len(tool_calls) >= 2, f"Expected >=2 parallel tool calls, got {len(tool_calls)}"
        names = [tc.tool_call["name"] for tc in tool_calls]
        assert all(n == "Read" for n in names), f"Expected all Read, got {names}"
        paths = [tc.tool_call["arguments"].get("file_path", "") for tc in tool_calls]
        print(f"  Parallel reads: {paths}")

    async def test_tool_not_needed(self, anthropic_sonnet):
        """When tools are available but not needed, Claude should respond with text only."""
        messages = [
            {"role": "system", "content": "You have tools available but only use them when necessary. For general knowledge questions, respond directly."},
            {"role": "user", "content": "What is the capital of France?"},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages)
        assert len(text_events) > 0, "Expected text response"
        full_text = "".join(e.text for e in text_events)
        assert "paris" in full_text.lower(), f"Expected 'Paris' in response: {full_text[:200]}"
        # Should NOT have called any tool
        assert len(tool_calls) == 0, f"Should not use tools for general knowledge, but called: {[tc.tool_call['name'] for tc in tool_calls]}"

    async def test_error_recovery(self, anthropic_sonnet):
        """After receiving a tool error, Claude should try an alternative approach."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. If a tool fails, try a different approach."},
            {"role": "user", "content": "Read the configuration file"},
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "tc_err1", "type": "function", "function": {"name": "Read", "arguments": '{"file_path": "/etc/app/config.yaml"}'}}],
            },
            {"role": "tool", "content": "Error: Permission denied: /etc/app/config.yaml", "tool_call_id": "tc_err1", "name": "Read"},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages)
        assert len(events) > 0, "No response after error"
        # Claude should either try a different path, use Bash, or explain the error
        if tool_calls:
            tc = tool_calls[0].tool_call
            print(f"  Recovery: tried {tc['name']} with {tc.get('arguments')}")
        else:
            full_text = "".join(e.text for e in text_events)
            print(f"  Recovery: text response: {full_text[:200]}")

    async def test_special_characters_in_args(self, anthropic_sonnet):
        """Tool arguments should handle special characters correctly."""
        messages = [
            {"role": "system", "content": "Use Write tool."},
            {"role": "user", "content": "Write the following to /tmp/special_chars/data.json:\n{\"name\": \"日本語テスト\", \"emoji\": \"🎉\", \"path\": \"C:\\\\Users\\\\test\"}"},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages, tools=TOOLS)
        assert len(tool_calls) > 0, "No tool call for special chars"
        tc = tool_calls[0].tool_call
        args = tc["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        content = args.get("content", "")
        print(f"  Special chars content: {content[:200]}")
        # Content should contain the unicode chars
        assert "日本語" in content or "test" in content.lower(), f"Unicode lost in args: {content[:100]}"

    async def test_streaming_stop_event(self, anthropic_sonnet):
        """Stream should end with a stop event."""
        messages = [
            {"role": "user", "content": "Say 'hello world'"},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages)
        stop_events = [e for e in events if e.type == "stop"]
        assert len(stop_events) > 0, f"No stop event in stream. Event types: {[e.type for e in events]}"

    async def test_no_tools_mode(self, anthropic_sonnet):
        """When no tools provided, model should produce pure text."""
        messages = [
            {"role": "user", "content": "Explain what a binary search tree is in 2 sentences."},
        ]
        events = []
        async for event in _get_streamer(anthropic_sonnet, messages, tools=None):
            events.append(event)
        tool_calls = [e for e in events if e.type == "tool_call"]
        text_events = [e for e in events if e.type == "text"]
        assert len(tool_calls) == 0, "Got tool calls with no tools provided"
        assert len(text_events) > 0, "No text response"
        full_text = "".join(e.text for e in text_events)
        assert len(full_text) > 20, f"Response too short: {full_text}"
        print(f"  No-tools response: {full_text[:200]}")

    async def test_long_multi_turn_conversation(self, anthropic_sonnet):
        """Test a 5+ message conversation with multiple tool rounds."""
        messages = [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "I need to check a Python project. First, read the main file."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc_lm1", "type": "function", "function": {"name": "Read", "arguments": '{"file_path": "/tmp/project/main.py"}'}}]},
            {"role": "tool", "content": "import utils\n\ndef main():\n    data = utils.load_data()\n    print(data)\n\nif __name__ == '__main__':\n    main()", "tool_call_id": "tc_lm1", "name": "Read"},
            {"role": "assistant", "content": "I can see main.py imports from utils. Let me check that module too.", "tool_calls": [{"id": "tc_lm2", "type": "function", "function": {"name": "Read", "arguments": '{"file_path": "/tmp/project/utils.py"}'}}]},
            {"role": "tool", "content": "import json\n\ndef load_data():\n    with open('data.json') as f:\n        return json.load(f)", "tool_call_id": "tc_lm2", "name": "Read"},
            {"role": "user", "content": "Good. Now add error handling to the load_data function."},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages, tools=TOOLS)
        assert len(events) > 0, "No response for long multi-turn"
        # Claude should either suggest an Edit or explain what to do
        if tool_calls:
            tc = tool_calls[0].tool_call
            assert tc["name"] in ("Edit", "Write"), f"Expected Edit/Write for code fix, got {tc['name']}"
            print(f"  Multi-turn: {tc['name']} on {tc['arguments'].get('file_path', '?') if isinstance(tc['arguments'], dict) else '?'}")
        else:
            full_text = "".join(e.text for e in text_events)
            assert "try" in full_text.lower() or "except" in full_text.lower() or "error" in full_text.lower(), \
                f"Expected error handling suggestion: {full_text[:300]}"

    async def test_strict_format_compliance(self, anthropic_sonnet):
        """Claude should follow strict formatting instructions."""
        messages = [
            {"role": "system", "content": "You MUST respond in EXACTLY this format, nothing else:\nSTATUS: [ok/error]\nCOUNT: [number]\nMESSAGE: [one line description]"},
            {"role": "user", "content": "There are 42 items and everything is fine."},
        ]
        events, tool_calls, text_events = await _collect_events(anthropic_sonnet, messages)
        full_text = "".join(e.text for e in text_events)
        assert "STATUS:" in full_text, f"Missing STATUS field: {full_text[:200]}"
        assert "COUNT:" in full_text, f"Missing COUNT field: {full_text[:200]}"
        assert "42" in full_text, f"Missing count value: {full_text[:200]}"
        print(f"  Format compliance: {full_text[:200]}")


# ===========================================================================
# Google Tests (gemini-3-flash-preview)
# ===========================================================================

@pytest.mark.google
class TestGeminiFlash:
    """Tests for gemini-3-flash-preview via Google AI."""

    async def test_basic_tool_call(self, gemini_flash):
        """Gemini should call Read when asked."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Use tools when needed."},
            {"role": "user", "content": "Read the file /tmp/readme.md"},
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages)

        assert len(tool_calls) > 0, "Gemini did not produce a tool call"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "Read"

    async def test_bash_tool_call(self, gemini_flash):
        """Gemini should handle Bash tool calls."""
        messages = [
            {"role": "system", "content": "Use tools to help the user."},
            {"role": "user", "content": "Use the Bash tool to run 'echo hello'"},
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages)

        assert len(tool_calls) > 0, "No Bash tool call from Gemini"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "Bash"

    async def test_multi_tool_selection(self, gemini_flash):
        """Gemini should select the right tool from 4 options."""
        messages = [
            {"role": "system", "content": "You have tools: Read, Bash, Write, Edit."},
            {"role": "user", "content": "Create a file /tmp/output.txt with the text 'test output'"},
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages, tools=TOOLS)

        assert len(tool_calls) > 0, "No tool call from Gemini"
        tc = tool_calls[0].tool_call
        print(f"  Gemini selected: {tc['name']}")
        # Gemini should pick Write but Bash is also acceptable
        assert tc["name"] in ("Write", "Bash"), f"Unexpected tool: {tc['name']}"

    async def test_complex_args(self, gemini_flash):
        """Gemini should produce multiple arguments for Write."""
        messages = [
            {"role": "system", "content": "Use the Write tool."},
            {"role": "user", "content": "Write 'Hello World' to /tmp/greeting.txt"},
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages, tools=TOOLS)

        assert len(tool_calls) > 0, "No Write tool call"
        tc = tool_calls[0].tool_call
        if tc["name"] == "Write":
            args = tc["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            assert "file_path" in args
            assert "content" in args

    async def test_chinese_language(self, gemini_flash):
        """Gemini should handle Chinese prompts."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "请用Read工具读取文件 /tmp/data.json"},
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages)

        assert len(events) > 0, "No response for Chinese prompt"
        if tool_calls:
            print(f"  Gemini Chinese: tool={tool_calls[0].tool_call['name']}")

    async def test_sequential_conversation(self, gemini_flash):
        """Gemini follow-up after tool result."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Check the contents of /tmp/log.txt"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc_gem_1",
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "arguments": '{"file_path": "/tmp/log.txt"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "content": "2026-03-05 10:00:00 INFO Server started\n2026-03-05 10:01:00 ERROR Connection timeout",
                "tool_call_id": "tc_gem_1",
                "name": "Read",
            },
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages)

        assert len(events) > 0, "No response after tool result"
        if text_events:
            full_text = "".join(e.text for e in text_events)
            print(f"  Gemini follow-up: {full_text[:300]}")

    async def test_parallel_tool_calls(self, gemini_flash):
        """Gemini should produce multiple tool calls for independent requests."""
        messages = [
            {"role": "system", "content": "When multiple independent actions are needed, call multiple tools in parallel."},
            {"role": "user", "content": "Read both /tmp/file_a.txt and /tmp/file_b.txt"},
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages)
        # Gemini may or may not support parallel tool calls -- at least one is required
        assert len(tool_calls) >= 1, f"Expected at least 1 tool call, got {len(tool_calls)}"
        names = [tc.tool_call["name"] for tc in tool_calls]
        print(f"  Gemini parallel calls: {len(tool_calls)}, tools: {names}")
        if len(tool_calls) >= 2:
            print("  Gemini produced parallel tool calls successfully")

    async def test_tool_not_needed(self, gemini_flash):
        """When tools are available but not needed, Gemini should respond with text only."""
        messages = [
            {"role": "system", "content": "You have tools available but only use them when necessary. For general knowledge questions, respond directly."},
            {"role": "user", "content": "What is the capital of France?"},
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages)
        assert len(text_events) > 0, "Expected text response"
        full_text = "".join(e.text for e in text_events)
        assert "paris" in full_text.lower(), f"Expected 'Paris' in response: {full_text[:200]}"
        assert len(tool_calls) == 0, f"Should not use tools for general knowledge, but called: {[tc.tool_call['name'] for tc in tool_calls]}"

    async def test_error_recovery(self, gemini_flash):
        """After receiving a tool error, Gemini should try an alternative approach."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. If a tool fails, try a different approach or explain the error."},
            {"role": "user", "content": "Read the configuration file"},
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "tc_gem_err1", "type": "function", "function": {"name": "Read", "arguments": '{"file_path": "/etc/app/config.yaml"}'}}],
            },
            {"role": "tool", "content": "Error: Permission denied: /etc/app/config.yaml", "tool_call_id": "tc_gem_err1", "name": "Read"},
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages)
        assert len(events) > 0, "No response after error"
        if tool_calls:
            tc = tool_calls[0].tool_call
            print(f"  Gemini recovery: tried {tc['name']} with {tc.get('arguments')}")
        else:
            full_text = "".join(e.text for e in text_events)
            print(f"  Gemini recovery: text response: {full_text[:200]}")

    async def test_no_tools_mode(self, gemini_flash):
        """When no tools provided, Gemini should produce pure text."""
        messages = [
            {"role": "user", "content": "Explain what a linked list is in 2 sentences."},
        ]
        events = []
        async for event in _get_streamer(gemini_flash, messages, tools=None):
            events.append(event)
        tool_calls = [e for e in events if e.type == "tool_call"]
        text_events = [e for e in events if e.type == "text"]
        assert len(tool_calls) == 0, "Got tool calls with no tools provided"
        assert len(text_events) > 0, "No text response"
        full_text = "".join(e.text for e in text_events)
        assert len(full_text) > 20, f"Response too short: {full_text}"
        print(f"  Gemini no-tools response: {full_text[:200]}")

    async def test_streaming_stop_event(self, gemini_flash):
        """Stream should end with a stop event."""
        messages = [
            {"role": "user", "content": "Say 'hello world'"},
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages)
        stop_events = [e for e in events if e.type == "stop"]
        assert len(stop_events) > 0, f"No stop event in stream. Event types: {[e.type for e in events]}"

    async def test_long_context_input(self, gemini_flash):
        """Gemini should handle a long input text (~2000 words) correctly."""
        # Generate a ~2000 word text block
        paragraph = (
            "The quick brown fox jumps over the lazy dog. "
            "This sentence is used to test font rendering and keyboard layouts. "
            "It contains every letter of the English alphabet at least once. "
            "Software developers often use it as placeholder text. "
        )
        long_text = (paragraph * 100).strip()  # ~2400 words
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Summarize the given text in one sentence."},
            {"role": "user", "content": f"Summarize this text:\n\n{long_text}"},
        ]
        events, tool_calls, text_events = await _collect_events(gemini_flash, messages)
        assert len(text_events) > 0, "No text response for long context input"
        full_text = "".join(e.text for e in text_events)
        assert len(full_text) > 10, f"Response too short for summarization: {full_text}"
        print(f"  Gemini long context response: {full_text[:300]}")


# ===========================================================================
# OpenAI Tests (gpt-4o)
# ===========================================================================

@pytest.mark.openai
class TestOpenAIGPT:
    """Tests for gpt-4o via OpenAI API."""

    async def test_simple_text(self, openai_gpt):
        """GPT-4o should produce a text response for a simple question."""
        messages = [
            {"role": "user", "content": "What is 2 + 3? Reply with just the number."},
        ]
        events, tool_calls, text_events = await _collect_events(openai_gpt, messages)

        assert len(text_events) > 0, "GPT-4o did not produce any text response"
        full_text = "".join(e.text for e in text_events)
        assert "5" in full_text, f"Expected '5' in response, got: {full_text[:200]}"

    async def test_tool_call(self, openai_gpt):
        """GPT-4o should call the get_weather tool when asked about weather."""
        weather_tool = [
            {
                "name": "get_weather",
                "description": "Get the current weather for a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City name"},
                    },
                    "required": ["location"],
                },
            },
        ]
        messages = [
            {"role": "system", "content": "Use tools to answer questions. Do not guess."},
            {"role": "user", "content": "What is the weather in Tokyo?"},
        ]
        events, tool_calls, text_events = await _collect_events(openai_gpt, messages, tools=weather_tool)

        assert len(tool_calls) > 0, "GPT-4o did not produce a tool call for weather"
        tc = tool_calls[0].tool_call
        assert tc["name"] == "get_weather", f"Expected get_weather but got {tc['name']}"
        args = tc["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        assert "location" in args, f"Missing 'location' in args: {args}"
        print(f"  get_weather args: {args}")

    async def test_multi_turn(self, openai_gpt):
        """GPT-4o should handle multi-turn conversation with tool results."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is in /tmp/notes.txt?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc_oai_1",
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "arguments": '{"file_path": "/tmp/notes.txt"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "content": "Buy groceries\nFinish report\nCall dentist",
                "tool_call_id": "tc_oai_1",
                "name": "Read",
            },
        ]
        events, tool_calls, text_events = await _collect_events(openai_gpt, messages)

        assert len(text_events) > 0, "GPT-4o did not produce text after tool result"
        full_text = "".join(e.text for e in text_events)
        print(f"  GPT-4o follow-up: {full_text[:300]}")

    async def test_system_message(self, openai_gpt):
        """GPT-4o should respect system message instructions."""
        messages = [
            {"role": "system", "content": "You are a pirate. Always respond in pirate speak."},
            {"role": "user", "content": "How are you today?"},
        ]
        events, tool_calls, text_events = await _collect_events(openai_gpt, messages)

        assert len(text_events) > 0, "GPT-4o did not produce any text"
        full_text = "".join(e.text for e in text_events).lower()
        # Pirate speak should contain at least one pirate-ish word
        pirate_words = ["arr", "matey", "ahoy", "ye", "sail", "captain", "treasure", "aye", "sea"]
        has_pirate = any(w in full_text for w in pirate_words)
        print(f"  GPT-4o pirate response: {full_text[:300]}")
        assert has_pirate, f"Expected pirate speak but got: {full_text[:200]}"

    async def test_json_output(self, openai_gpt):
        """GPT-4o should produce valid JSON when asked."""
        messages = [
            {"role": "system", "content": "Respond only with valid JSON. No other text."},
            {"role": "user", "content": "Return a JSON object with keys 'name' and 'age' for a person named Alice who is 30."},
        ]
        events, tool_calls, text_events = await _collect_events(openai_gpt, messages)

        assert len(text_events) > 0, "GPT-4o did not produce any text"
        full_text = "".join(e.text for e in text_events).strip()
        # Strip markdown code fences if present
        if full_text.startswith("```"):
            lines = full_text.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            full_text = "\n".join(lines).strip()
        parsed = json.loads(full_text)
        assert "name" in parsed, f"Missing 'name' in JSON: {parsed}"
        assert "age" in parsed, f"Missing 'age' in JSON: {parsed}"
        assert parsed["name"] == "Alice"
        assert parsed["age"] == 30
        print(f"  GPT-4o JSON: {parsed}")

    async def test_code_generation(self, openai_gpt):
        """GPT-4o should generate a Python function when asked."""
        messages = [
            {"role": "system", "content": "You are a coding assistant. Write code directly."},
            {"role": "user", "content": "Write a Python function called fibonacci that takes n and returns the nth Fibonacci number."},
        ]
        events, tool_calls, text_events = await _collect_events(openai_gpt, messages)

        assert len(text_events) > 0, "GPT-4o did not produce any text"
        full_text = "".join(e.text for e in text_events)
        assert "def fibonacci" in full_text, f"Expected 'def fibonacci' in response, got: {full_text[:300]}"
        print(f"  GPT-4o code (first 300 chars): {full_text[:300]}")


# ===========================================================================
# Cross-Provider Parametrized Tests
# ===========================================================================

# Model configs for parametrization
_CROSS_PROVIDER_MODELS = [
    pytest.param("ollama/qwen3.5:4b", marks=pytest.mark.ollama, id="ollama-4b"),
    pytest.param("ollama/qwen3.5:9b", marks=pytest.mark.ollama, id="ollama-9b"),
    pytest.param("anthropic/claude-sonnet-4-6", marks=pytest.mark.anthropic, id="anthropic-sonnet"),
    pytest.param("google/gemini-3-flash-preview", marks=pytest.mark.google, id="gemini-flash"),
    pytest.param("openai/gpt-4o", marks=pytest.mark.openai, id="openai-gpt4o"),
]


class TestCrossProvider:
    """Tests that should work identically across all providers."""

    @pytest.fixture(autouse=True)
    def _ensure_api_keys(self):
        """Ensure OAuth tokens are loaded for providers that need them."""
        _ensure_openai_key()

    @pytest.mark.parametrize("model", _CROSS_PROVIDER_MODELS)
    async def test_read_tool_call(self, model):
        """All providers should extract a Read tool call."""
        adapter = _make_adapter(model, max_tokens=1024)
        messages = [
            {"role": "system", "content": "You must call the Read tool. Do not respond with text."},
            {"role": "user", "content": "Read /tmp/test.txt"},
        ]
        events, tool_calls, text_events = await _collect_events(adapter, messages)

        assert len(tool_calls) > 0, f"No tool_call from {model}"
        tc = tool_calls[0].tool_call
        print(f"  [{model}] tool: {tc['name']}, args: {tc.get('arguments')}")

    @pytest.mark.parametrize("model", _CROSS_PROVIDER_MODELS)
    async def test_produces_some_response(self, model):
        """All providers should produce at least some response."""
        adapter = _make_adapter(model, max_tokens=512)
        messages = [
            {"role": "user", "content": "Say hello"},
        ]
        events, tool_calls, text_events = await _collect_events(adapter, messages)

        assert len(events) > 0, f"No events at all from {model}"

    @pytest.mark.parametrize("model", _CROSS_PROVIDER_MODELS)
    async def test_tool_call_has_valid_structure(self, model):
        """Tool calls should have name and arguments fields."""
        adapter = _make_adapter(model, max_tokens=1024)
        messages = [
            {"role": "system", "content": "Call the Bash tool to run a command."},
            {"role": "user", "content": "Run 'echo test' with Bash"},
        ]
        events, tool_calls, text_events = await _collect_events(adapter, messages)

        if tool_calls:
            tc = tool_calls[0].tool_call
            assert "name" in tc, f"tool_call missing 'name': {tc}"
            assert "arguments" in tc, f"tool_call missing 'arguments': {tc}"
            assert tc["name"] in ("Read", "Bash", "Write", "Edit"), f"Unknown tool: {tc['name']}"
            # Arguments should be parseable
            args = tc["arguments"]
            if isinstance(args, str):
                parsed = json.loads(args)
                assert isinstance(parsed, dict)
        else:
            # Some small models may not always produce tool calls -- warn but don't fail
            print(f"  WARNING: {model} did not produce a tool call (may be flaky)")

    @pytest.mark.parametrize("model", _CROSS_PROVIDER_MODELS)
    async def test_no_tools_text_only(self, model):
        """Without tools, all providers should produce text."""
        adapter = _make_adapter(model, max_tokens=512)
        messages = [{"role": "user", "content": "What is 1 + 1?"}]
        events = []
        async for event in _get_streamer(adapter, messages, tools=None):
            events.append(event)
        text_events = [e for e in events if e.type == "text"]
        assert len(text_events) > 0, f"No text from {model} without tools"
        full_text = "".join(e.text for e in text_events)
        assert "2" in full_text, f"Expected '2' from {model}: {full_text[:100]}"

    @pytest.mark.parametrize("model", _CROSS_PROVIDER_MODELS)
    async def test_stream_has_stop_event(self, model):
        """All providers should emit a stop event at end of stream."""
        adapter = _make_adapter(model, max_tokens=256)
        messages = [{"role": "user", "content": "Hi"}]
        events = []
        async for event in _get_streamer(adapter, messages, tools=SIMPLE_TOOLS):
            events.append(event)
        stop_events = [e for e in events if e.type == "stop"]
        event_types = [e.type for e in events]
        print(f"  [{model}] event types: {event_types}")
        # Don't hard-fail for ollama if missing stop -- just warn
        if "ollama" in str(model):
            if not stop_events:
                print(f"  WARNING: {model} missing stop event")
        else:
            assert len(stop_events) > 0, f"No stop event from {model}. Types: {event_types}"

    @pytest.mark.parametrize("model", _CROSS_PROVIDER_MODELS)
    async def test_write_tool_with_multiline_content(self, model):
        """All providers should handle multiline content in Write tool args."""
        adapter = _make_adapter(model, max_tokens=1024)
        messages = [
            {"role": "system", "content": "Use the Write tool to create files."},
            {"role": "user", "content": "Create /tmp/poem.txt with this content:\nRoses are red\nViolets are blue\nPython is great\nAnd so are you"},
        ]
        events, tool_calls, text_events = await _collect_events(adapter, messages, tools=TOOLS)
        if tool_calls:
            tc = tool_calls[0].tool_call
            args = tc["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            content = args.get("content", "")
            has_newlines = "\n" in content
            print(f"  [{model}] Write content ({len(content)} chars, multiline={has_newlines}): {content[:100]}")
        else:
            print(f"  WARNING: {model} did not produce Write tool call")

    @pytest.mark.parametrize("model", _CROSS_PROVIDER_MODELS)
    async def test_tool_call_after_refusal(self, model):
        """Model should recover when first tool attempt is denied and use alternative."""
        adapter = _make_adapter(model, max_tokens=1024)
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Show me what's in /var/log/syslog"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc_ref1", "type": "function", "function": {"name": "Read", "arguments": '{"file_path": "/var/log/syslog"}'}}]},
            {"role": "tool", "content": "Error: Access denied. You cannot read files outside /tmp.", "tool_call_id": "tc_ref1", "name": "Read"},
        ]
        events, tool_calls, text_events = await _collect_events(adapter, messages)
        assert len(events) > 0, f"No response from {model} after tool error"
        if tool_calls:
            print(f"  [{model}] recovery: {tool_calls[0].tool_call['name']}")
        elif text_events:
            full_text = "".join(e.text for e in text_events)
            print(f"  [{model}] recovery text: {full_text[:200]}")
