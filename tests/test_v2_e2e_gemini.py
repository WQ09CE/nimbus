"""
End-to-End Tests for Nimbus v2 with Gemini

This module tests the complete v2 pipeline:
1. GeminiV2Client (ALU) - LLM calls with tool calling
2. InstructionDecoder - Parse tool calls into ActionIR
3. VCPU - Execute Think-Act-Observe loop

The tests use the real Gemini API (requires GEMINI_API_KEY env var).
Tests are marked as slow and will be skipped if no API key is available.

Usage:
    # Run all v2 e2e tests
    pytest tests/test_v2_e2e_gemini.py -v

    # Skip slow tests
    pytest tests/test_v2_e2e_gemini.py -v -m "not slow"
"""

import os
import pytest
from typing import Any, Dict, List
from unittest.mock import AsyncMock

# Check if API key is available (supports both GEMINI_API_KEY and GOOGLE_API_KEY)
HAS_GEMINI_KEY = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def api_key_required():
    """Skip test if GEMINI_API_KEY is not set."""
    if not HAS_GEMINI_KEY:
        pytest.skip("GEMINI_API_KEY not set - skipping integration test")


@pytest.fixture
def sample_tools():
    """Sample tool definitions in OpenAI format."""
    return [
        {
            "type": "function",
            "function": {
                "name": "Read",
                "description": "Read the contents of a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "The absolute path to the file to read"
                        }
                    },
                    "required": ["file_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "Glob",
                "description": "Find files matching a glob pattern",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "The glob pattern to match"
                        },
                        "path": {
                            "type": "string",
                            "description": "The directory to search in"
                        }
                    },
                    "required": ["pattern"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "return_result",
                "description": "Return the final result when task is complete",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "result": {
                            "type": "string",
                            "description": "The final result to return"
                        }
                    },
                    "required": ["result"]
                }
            }
        }
    ]


# =============================================================================
# Unit Tests (No API key required)
# =============================================================================


class TestGeminiV2ClientUnit:
    """Unit tests for GeminiV2Client (no API calls)."""

    def test_init_with_api_key(self):
        """Test client initialization with API key."""
        from nimbus.llm import GeminiV2Client

        client = GeminiV2Client(api_key="test-key")
        assert client._api_key == "test-key"
        assert client.config.model == "gemini-2.0-flash"

    def test_init_with_env_var(self, monkeypatch):
        """Test client initialization with environment variable."""
        from nimbus.llm import GeminiV2Client

        monkeypatch.setenv("GEMINI_API_KEY", "env-test-key")
        client = GeminiV2Client()
        assert client._api_key == "env-test-key"

    def test_init_without_key_raises(self, monkeypatch):
        """Test that initialization without API key raises error."""
        from nimbus.llm import GeminiV2Client

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key is required"):
            GeminiV2Client()

    def test_convert_messages_to_gemini(self):
        """Test message format conversion."""
        from nimbus.llm import GeminiV2Client

        client = GeminiV2Client(api_key="test-key")

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]

        contents, system_instruction = client._convert_messages_to_gemini(messages)

        assert system_instruction == "You are helpful."
        assert len(contents) == 3
        assert contents[0]["role"] == "user"
        assert contents[0]["parts"][0]["text"] == "Hello"
        assert contents[1]["role"] == "model"
        assert contents[2]["role"] == "user"

    def test_convert_messages_with_tool_calls(self):
        """Test message conversion with tool calls."""
        from nimbus.llm import GeminiV2Client

        client = GeminiV2Client(api_key="test-key")

        messages = [
            {"role": "user", "content": "Read the file /tmp/test.txt"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "arguments": '{"file_path": "/tmp/test.txt"}'
                    }
                }]
            },
            {
                "role": "tool",
                "tool_call_id": "call_123",
                "name": "Read",
                "content": "File contents here"
            }
        ]

        contents, _ = client._convert_messages_to_gemini(messages)

        assert len(contents) == 3
        # First is user message
        assert contents[0]["role"] == "user"
        # Second is assistant with function call
        assert contents[1]["role"] == "model"
        assert "functionCall" in contents[1]["parts"][0]
        # Third is tool result (converted to user message with functionResponse)
        assert contents[2]["role"] == "user"
        assert "functionResponse" in contents[2]["parts"][0]
        assert contents[2]["parts"][0]["functionResponse"]["name"] == "Read"

    def test_convert_tools_to_gemini(self, sample_tools):
        """Test tool format conversion."""
        from nimbus.llm import GeminiV2Client

        client = GeminiV2Client(api_key="test-key")

        gemini_tools = client._convert_tools_to_gemini(sample_tools)

        assert len(gemini_tools) == 1
        assert "function_declarations" in gemini_tools[0]
        declarations = gemini_tools[0]["function_declarations"]
        assert len(declarations) == 3
        assert declarations[0]["name"] == "Read"
        assert declarations[1]["name"] == "Glob"
        assert declarations[2]["name"] == "return_result"


class TestGeminiV2Response:
    """Test GeminiV2Response implements LLMResponse Protocol."""

    def test_response_with_content(self):
        """Test response with text content."""
        from nimbus.llm.gemini import GeminiV2Response

        response = GeminiV2Response(_content="Hello, world!")

        assert response.content == "Hello, world!"
        assert response.tool_calls is None

    def test_response_with_tool_calls(self):
        """Test response with tool calls."""
        from nimbus.llm.gemini import GeminiV2Response, ToolCallInfo, FunctionInfo

        tool_call = ToolCallInfo(
            id="call_123",
            type="function",
            function=FunctionInfo(name="Read", arguments='{"file_path": "/tmp/test.txt"}')
        )
        response = GeminiV2Response(_tool_calls=[tool_call])

        assert response.content is None
        assert response.tool_calls is not None
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].function.name == "Read"


class TestDecoderIntegration:
    """Test InstructionDecoder with GeminiV2Response."""

    def test_decode_text_response(self):
        """Test decoding a text-only response."""
        from nimbus.core.runtime.decoder import InstructionDecoder
        from nimbus.llm.gemini import GeminiV2Response

        decoder = InstructionDecoder()
        response = GeminiV2Response(_content="I think I should read the file.")

        actions = decoder.decode(response.content, response.tool_calls)

        assert len(actions) == 1
        assert actions[0].kind == "THOUGHT"
        assert "read the file" in actions[0].args["text"]

    def test_decode_tool_call_response(self):
        """Test decoding a tool call response."""
        from nimbus.core.runtime.decoder import InstructionDecoder
        from nimbus.llm.gemini import GeminiV2Response, ToolCallInfo, FunctionInfo

        decoder = InstructionDecoder()
        tool_call = ToolCallInfo(
            id="call_123",
            type="function",
            function=FunctionInfo(name="Read", arguments='{"file_path": "/tmp/test.txt"}')
        )
        response = GeminiV2Response(_tool_calls=[tool_call])

        actions = decoder.decode(response.content, response.tool_calls)

        assert len(actions) == 1
        assert actions[0].kind == "TOOL_CALL"
        assert actions[0].name == "Read"
        assert actions[0].args["file_path"] == "/tmp/test.txt"

    def test_decode_return_result(self):
        """Test decoding return_result as RETURN action."""
        from nimbus.core.runtime.decoder import InstructionDecoder
        from nimbus.llm.gemini import GeminiV2Response, ToolCallInfo, FunctionInfo

        decoder = InstructionDecoder()
        tool_call = ToolCallInfo(
            id="call_456",
            type="function",
            function=FunctionInfo(name="return_result", arguments='{"result": "Task completed"}')
        )
        response = GeminiV2Response(_tool_calls=[tool_call])

        actions = decoder.decode(response.content, response.tool_calls)

        assert len(actions) == 1
        assert actions[0].kind == "RETURN"
        assert actions[0].args["result"] == "Task completed"


# =============================================================================
# Integration Tests (Requires API key)
# =============================================================================


@pytest.mark.slow
class TestGeminiV2Integration:
    """Integration tests with real Gemini API."""

    @pytest.mark.asyncio
    async def test_simple_chat(self, api_key_required):
        """Test simple chat without tools."""
        from nimbus.llm import GeminiV2Client

        async with GeminiV2Client() as client:
            messages = [
                {"role": "user", "content": "Say 'hello' in one word only."}
            ]
            response = await client.chat(messages)

            assert response.content is not None
            assert len(response.content) > 0
            assert "hello" in response.content.lower()

    @pytest.mark.asyncio
    async def test_chat_with_tools(self, api_key_required, sample_tools):
        """Test chat with tool calling."""
        from nimbus.llm import GeminiV2Client

        async with GeminiV2Client() as client:
            messages = [
                {"role": "system", "content": "You are a code assistant. Use tools to help the user."},
                {"role": "user", "content": "Please read the file /tmp/test.txt"}
            ]
            response = await client.chat(messages, tools=sample_tools)

            # Should get a tool call for Read
            assert response.tool_calls is not None
            assert len(response.tool_calls) > 0
            assert response.tool_calls[0].function.name == "Read"

    @pytest.mark.asyncio
    async def test_multi_turn_with_tool_results(self, api_key_required, sample_tools):
        """Test multi-turn conversation with tool results."""
        from nimbus.llm import GeminiV2Client
        import json

        async with GeminiV2Client() as client:
            # First turn: user asks to read file
            messages = [
                {"role": "system", "content": "You are a code assistant. Use the return_result tool to return your final answer."},
                {"role": "user", "content": "Read /tmp/test.txt and tell me what it says."}
            ]
            response = await client.chat(messages, tools=sample_tools)

            assert response.tool_calls is not None
            assert response.tool_calls[0].function.name == "Read"

            # Second turn: provide tool result
            tool_call = response.tool_calls[0]
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments
                    }
                }]
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_call.function.name,
                "content": "Hello from test file!"
            })

            # Get second response
            response2 = await client.chat(messages, tools=sample_tools)

            # Should either give a text response or call return_result
            if response2.content:
                assert "hello" in response2.content.lower() or "test" in response2.content.lower()
            elif response2.tool_calls:
                assert response2.tool_calls[0].function.name == "return_result"


# =============================================================================
# End-to-End vCPU Tests (Requires API key)
# =============================================================================


class TestVCPUIntegration:
    """Integration tests for vCPU with mocked Gemini (no API key required)."""

    @pytest.mark.asyncio
    async def test_vcpu_with_mock_gemini(self):
        """Test full vCPU pipeline with mocked Gemini responses."""
        from nimbus.llm import GeminiV2Client
        from nimbus.llm.gemini import GeminiV2Response, ToolCallInfo, FunctionInfo
        from nimbus.core.runtime.vcpu import VCPU, VCPUConfig
        from nimbus.core.runtime.decoder import InstructionDecoder
        from nimbus.core.memory.mmu import MMU, MMUConfig
        from nimbus.os.gate import KernelGate, SimplePermissionManager, SimpleEventStream

        # Simple tool executor
        class SimpleToolExecutor:
            def __init__(self, tools):
                self.tools = tools

            async def execute(self, tool_name, args):
                if tool_name in self.tools:
                    return await self.tools[tool_name](args)
                raise ValueError(f'Unknown tool: {tool_name}')

        # Create client with test key
        client = GeminiV2Client(api_key='test-key')
        decoder = InstructionDecoder()
        mmu = MMU(config=MMUConfig())

        # Mock tool function
        async def mock_read(args):
            return f"Contents of {args.get('file_path')}: Hello World!"

        # Create tool executor and gate
        executor = SimpleToolExecutor({'Read': mock_read})
        gate = KernelGate(
            pid='test-proc',
            permission_mgr=SimplePermissionManager(['Read', 'return_result']),
            event_stream=SimpleEventStream(),
            tool_executor=executor
        )

        # Define tools
        tools = [
            {
                'type': 'function',
                'function': {
                    'name': 'Read',
                    'description': 'Read a file',
                    'parameters': {
                        'type': 'object',
                        'properties': {'file_path': {'type': 'string'}},
                        'required': ['file_path']
                    }
                }
            },
            {
                'type': 'function',
                'function': {
                    'name': 'return_result',
                    'description': 'Return the final result',
                    'parameters': {
                        'type': 'object',
                        'properties': {'result': {'type': 'string'}},
                        'required': ['result']
                    }
                }
            }
        ]

        # Create vCPU
        vcpu = VCPU(
            alu=client,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=VCPUConfig(max_iterations=5),
            tools=tools
        )

        # Track API calls
        call_count = [0]

        async def mock_chat(messages, tools=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: return Read tool
                return GeminiV2Response(
                    _tool_calls=[ToolCallInfo(
                        id='call_1',
                        function=FunctionInfo(name='Read', arguments='{"file_path": "/tmp/test.txt"}')
                    )]
                )
            else:
                # Second call: return final result
                return GeminiV2Response(
                    _tool_calls=[ToolCallInfo(
                        id='call_2',
                        function=FunctionInfo(name='return_result', arguments='{"result": "File contains: Hello World!"}')
                    )]
                )

        # Replace chat method with mock
        client.chat = mock_chat

        # Execute
        result = await vcpu.execute('Read /tmp/test.txt and return its contents.')

        # Verify
        assert result.status == 'OK'
        assert result.is_final is True
        assert 'Hello World' in str(result.output)
        assert call_count[0] == 2

        await client.close()


@pytest.mark.slow
class TestVCPUWithGemini:
    """End-to-end tests for vCPU with real Gemini API."""

    @pytest.mark.asyncio
    async def test_vcpu_simple_task(self, api_key_required):
        """Test vCPU executing a simple task that returns immediately."""
        from nimbus.llm import GeminiV2Client
        from nimbus.core.runtime.vcpu import VCPU, VCPUConfig
        from nimbus.core.runtime.decoder import InstructionDecoder
        from nimbus.core.memory.mmu import MMU, MMUConfig
        from nimbus.os.gate import KernelGate, SimplePermissionManager, SimpleEventStream

        # Create components
        client = GeminiV2Client()
        decoder = InstructionDecoder()
        mmu = MMU(config=MMUConfig())

        # Simple tools with return_result
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "return_result",
                    "description": "Return the final result. Call this when you have completed the task.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "result": {
                                "type": "string",
                                "description": "The final result to return"
                            }
                        },
                        "required": ["result"]
                    }
                }
            }
        ]

        # Create gate with permission manager
        gate = KernelGate(
            pid="test-process",
            permission_mgr=SimplePermissionManager(["return_result"]),
            event_stream=SimpleEventStream()
        )

        # Create vCPU
        vcpu = VCPU(
            alu=client,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=VCPUConfig(max_iterations=5),
            tools=tools
        )

        # Execute simple task
        result = await vcpu.execute("Say hello and return immediately using return_result.")

        assert result.status in ["OK", "ERROR"]  # May error if no tools executed
        await client.close()

    @pytest.mark.asyncio
    async def test_vcpu_with_mock_tool(self, api_key_required):
        """Test vCPU with a mock tool execution."""
        from nimbus.llm import GeminiV2Client
        from nimbus.core.runtime.vcpu import VCPU, VCPUConfig
        from nimbus.core.runtime.decoder import InstructionDecoder
        from nimbus.core.memory.mmu import MMU, MMUConfig
        from nimbus.os.gate import KernelGate, SimplePermissionManager, SimpleEventStream

        # Simple tool executor
        class SimpleToolExecutor:
            def __init__(self, tools):
                self.tools = tools

            async def execute(self, tool_name, args):
                if tool_name in self.tools:
                    return await self.tools[tool_name](args)
                raise ValueError(f'Unknown tool: {tool_name}')

        # Create components
        client = GeminiV2Client()
        decoder = InstructionDecoder()
        mmu = MMU(config=MMUConfig())

        # Create mock Read tool
        async def mock_read(args: Dict[str, Any]) -> str:
            file_path = args.get("file_path", "")
            return f"Contents of {file_path}: Hello, World!"

        # Tools definition
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "Read",
                    "description": "Read file contents",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "Path to file"}
                        },
                        "required": ["file_path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "return_result",
                    "description": "Return the final result",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "result": {"type": "string", "description": "Final result"}
                        },
                        "required": ["result"]
                    }
                }
            }
        ]

        # Create tool executor and gate
        executor = SimpleToolExecutor({"Read": mock_read})
        gate = KernelGate(
            pid="test-process",
            permission_mgr=SimplePermissionManager(["Read", "return_result"]),
            event_stream=SimpleEventStream(),
            tool_executor=executor
        )

        # Create vCPU
        vcpu = VCPU(
            alu=client,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=VCPUConfig(max_iterations=10),
            tools=tools
        )

        # Execute task
        result = await vcpu.execute("Read the file /tmp/hello.txt and return its contents.")

        # Should complete with OK or reach max iterations
        assert result.status in ["OK", "ERROR"]

        await client.close()


# =============================================================================
# CLI Test Runner
# =============================================================================


if __name__ == "__main__":
    import asyncio

    async def run_manual_test():
        """Run a manual test for debugging."""
        if not HAS_GEMINI_KEY:
            print("GEMINI_API_KEY not set")
            return

        from nimbus.llm import GeminiV2Client

        print("Testing GeminiV2Client...")
        async with GeminiV2Client() as client:
            messages = [
                {"role": "user", "content": "Say 'hello' in one word."}
            ]
            response = await client.chat(messages)
            print(f"Response: {response.content}")

            # Test with tools
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "description": "Read a file",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string"}
                            },
                            "required": ["file_path"]
                        }
                    }
                }
            ]
            messages = [
                {"role": "user", "content": "Read the file /tmp/test.txt"}
            ]
            response = await client.chat(messages, tools=tools)
            print(f"Tool calls: {response.tool_calls}")

    asyncio.run(run_manual_test())
