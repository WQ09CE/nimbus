"""Test CodeAgent to AgentOS integration.

This test verifies that CodeAgent correctly uses v2 AgentOS runtime.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Check if API key is available
HAS_GEMINI_KEY = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


class TestCodeAgentV2Integration:
    """Integration tests for CodeAgent with v2 AgentOS."""

    @pytest.fixture
    def mock_v1_llm_client(self):
        """Create a mock v1 LLM client that returns tool calls."""
        from nimbus.llm.base import CompletionResponse, ToolCall

        mock = MagicMock()
        call_count = [0]

        async def mock_complete_with_tools(messages, tools=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: request Glob tool
                return CompletionResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="Glob",
                            arguments={"pattern": "*.py", "path": "."},  # Dict, not string!
                        )
                    ],
                    finish_reason="tool_calls"
                )
            else:
                # Second call: return result
                return CompletionResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call_2",
                            name="return_result",
                            arguments={"result": "Found 10 Python files"},  # Dict!
                        )
                    ],
                    finish_reason="tool_calls"
                )

        mock.complete_with_tools = AsyncMock(side_effect=mock_complete_with_tools)
        # Ensure mock doesn't have 'chat' method so adapter is created
        del mock.chat
        return mock

    @pytest.mark.asyncio
    async def test_v2llm_adapter_converts_arguments_to_json(self, mock_v1_llm_client):
        """Test that V2LLMAdapter correctly converts dict arguments to JSON string."""
        from nimbus.core.agent import CodeAgent

        agent = CodeAgent(
            llm_client=mock_v1_llm_client,
            load_yaml_config=False,
            system_prompt="Test agent",
        )

        # Get the adapter
        adapter = agent._create_v2_llm_adapter()

        # Call chat
        messages = [{"role": "user", "content": "Find Python files"}]
        response = await adapter.chat(messages, tools=[])

        # Verify tool_calls format
        assert response.tool_calls is not None
        assert len(response.tool_calls) == 1

        tc = response.tool_calls[0]
        assert tc["function"]["name"] == "Glob"

        # Critical: arguments should be a JSON string, not dict
        args = tc["function"]["arguments"]
        assert isinstance(args, str), f"Expected str, got {type(args)}"

        import json
        parsed = json.loads(args)
        assert parsed["pattern"] == "*.py"
        print("✓ V2LLMAdapter correctly converts dict arguments to JSON string")

    @pytest.mark.asyncio
    async def test_codeagent_init_v2_runtime(self, mock_v1_llm_client):
        """Test that CodeAgent correctly initializes v2 runtime."""
        from nimbus.core.agent import CodeAgent

        agent = CodeAgent(
            llm_client=mock_v1_llm_client,
            load_yaml_config=False,
            system_prompt="Test agent",
        )

        # Verify v2 AgentOS is initialized
        assert agent._v2_agentos is not None

        # Verify tools are registered
        tools = agent._v2_agentos.list_tools()
        assert "Read" in tools
        assert "Glob" in tools
        assert "Grep" in tools
        print(f"✓ AgentOS initialized with tools: {tools}")


@pytest.mark.slow
class TestCodeAgentWithGemini:
    """Integration tests with real Gemini API."""

    @pytest.fixture
    def api_key_required(self):
        """Skip test if GEMINI_API_KEY is not set."""
        if not HAS_GEMINI_KEY:
            pytest.skip("GEMINI_API_KEY not set - skipping integration test")

    @pytest.mark.asyncio
    async def test_codeagent_simple_task(self, api_key_required, tmp_path):
        """Test CodeAgent executing a simple task with Gemini."""
        from nimbus.v2.llm import GeminiV2Client
        from nimbus.core.agent import CodeAgent

        # Create a test file
        test_file = tmp_path / "hello.txt"
        test_file.write_text("Hello, World!")

        # Create CodeAgent with GeminiV2Client (v2 native)
        async with GeminiV2Client() as gemini:
            agent = CodeAgent(
                llm_client=gemini,
                workspace=tmp_path,
                load_yaml_config=False,
                system_prompt="""You are a code assistant with access to tools.

IMPORTANT RULES:
1. You MUST use the provided tools to complete tasks.
2. When you have completed the task, you MUST call the return_result tool with the final answer.
3. Be concise and direct.""",
            )

            # Run a simple task
            result = await agent.run(f"Read the file {test_file} and tell me what it says.")

            print(f"Result: {result.text}")

            # Should complete without BUDGET_EXCEEDED
            assert "BUDGET_EXCEEDED" not in result.text
            assert "Hello" in result.text.lower() or "world" in result.text.lower()


if __name__ == "__main__":
    import asyncio

    async def run_tests():
        """Run tests manually."""
        print("Testing V2LLMAdapter...")

        from nimbus.llm.base import CompletionResponse, ToolCall

        # Create mock
        mock = MagicMock()
        call_count = [0]

        async def mock_complete_with_tools(messages, tools=None):
            call_count[0] += 1
            return CompletionResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="Glob",
                        arguments={"pattern": "*.py"},
                    )
                ],
                finish_reason="tool_calls"
            )

        mock.complete_with_tools = mock_complete_with_tools

        from nimbus.core.agent import CodeAgent

        agent = CodeAgent(
            llm_client=mock,
            load_yaml_config=False,
            system_prompt="Test",
        )

        adapter = agent._create_v2_llm_adapter()
        response = await adapter.chat([{"role": "user", "content": "test"}], tools=[])

        tc = response.tool_calls[0]
        args = tc["function"]["arguments"]

        print(f"Arguments type: {type(args)}")
        print(f"Arguments value: {args}")

        import json
        if isinstance(args, str):
            parsed = json.loads(args)
            print(f"✓ SUCCESS: Arguments correctly converted to JSON string")
            print(f"  Parsed: {parsed}")
        else:
            print(f"✗ FAILURE: Arguments should be string, got {type(args)}")

    asyncio.run(run_tests())
