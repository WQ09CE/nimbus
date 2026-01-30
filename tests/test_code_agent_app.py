"""Test Code Agent Application.

Tests the CodeAgent built on Agent OS kernel.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from nimbus.apps.code_agent import CodeAgent, CODE_AGENT_SYSTEM_PROMPT
from nimbus.tools.base import ToolRegistry


class TestCodeAgent:
    """Test CodeAgent class."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM client."""
        mock = AsyncMock()
        mock.complete_with_tools.return_value = {
            "text": "Task completed successfully.",
            "function_calls": []
        }
        return mock

    def test_init_defaults(self):
        """Test CodeAgent initialization with defaults."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            mock_create.return_value = MagicMock()

            agent = CodeAgent(workspace="/tmp/test")

            # Use resolve() to handle macOS /tmp -> /private/tmp symlink
            assert agent.workspace == Path("/tmp/test").resolve()
            assert agent.system_prompt == CODE_AGENT_SYSTEM_PROMPT
            assert agent.max_iterations == 50
            assert len(agent.tools) == 6  # Read, Glob, Grep, Write, Edit, Bash

    def test_tool_registration(self):
        """Test that all tools are registered correctly."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            mock_create.return_value = MagicMock()

            agent = CodeAgent()

            expected_tools = {"Read", "Glob", "Grep", "Write", "Edit", "Bash"}
            registered = set(agent.tools.list_tools())

            assert registered == expected_tools

    def test_tool_categories(self):
        """Test tool category constants."""
        assert CodeAgent.READONLY_TOOLS == {"Read", "Glob", "Grep"}
        assert CodeAgent.EXECUTE_TOOLS == {"Bash"}
        assert CodeAgent.WRITE_TOOLS == {"Write", "Edit"}
        assert CodeAgent.ALL_TOOLS == {"Read", "Glob", "Grep", "Bash", "Write", "Edit"}

    @pytest.mark.asyncio
    async def test_run_with_mock_kernel(self):
        """Test run method with mocked kernel."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            mock_llm = MagicMock()
            mock_create.return_value = mock_llm

            agent = CodeAgent()

            # Mock kernel methods
            agent.kernel.spawn = AsyncMock(return_value="test_pid_123")
            agent.kernel.wait = AsyncMock(return_value={
                "pid": "test_pid_123",
                "exit_code": 0,
                "result": {"text": "Found 10 Python files"},
                "token_usage": 1500,
            })

            result = await agent.run(
                goal="Find Python files",
                allowed_tools={"Glob", "Read"}
            )

            assert result["status"] == "success"
            assert result["exit_code"] == 0
            assert result["output"] == "Found 10 Python files"
            assert result["pid"] == "test_pid_123"

            # Verify spawn was called with correct arguments
            agent.kernel.spawn.assert_called_once()
            call_kwargs = agent.kernel.spawn.call_args.kwargs
            assert call_kwargs["role"] == "CodeAgent"
            assert call_kwargs["allowed_tools"] == {"Glob", "Read"}

    @pytest.mark.asyncio
    async def test_run_with_default_tools(self):
        """Test run uses readonly tools by default."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            mock_create.return_value = MagicMock()

            agent = CodeAgent()
            agent.kernel.spawn = AsyncMock(return_value="pid")
            agent.kernel.wait = AsyncMock(return_value={
                "exit_code": 0,
                "result": {"text": "Done"},
            })

            await agent.run(goal="Search code")

            # Should use readonly tools by default
            call_kwargs = agent.kernel.spawn.call_args.kwargs
            assert call_kwargs["allowed_tools"] == {"Read", "Glob", "Grep"}

    @pytest.mark.asyncio
    async def test_run_handles_failure(self):
        """Test run handles task failure."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            mock_create.return_value = MagicMock()

            agent = CodeAgent()
            agent.kernel.spawn = AsyncMock(return_value="pid")
            agent.kernel.wait = AsyncMock(return_value={
                "exit_code": 1,
                "result": {},
                "error": "Task failed",
            })

            result = await agent.run(goal="Do something")

            assert result["status"] == "failed"
            assert result["exit_code"] == 1
            assert result["error"] == "Task failed"

    @pytest.mark.asyncio
    async def test_run_handles_exception(self):
        """Test run handles exceptions gracefully."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            mock_create.return_value = MagicMock()

            agent = CodeAgent()
            agent.kernel.spawn = AsyncMock(side_effect=Exception("Spawn failed"))

            result = await agent.run(goal="Do something")

            assert result["status"] == "failed"
            assert result["exit_code"] == 1
            assert "Spawn failed" in result["error"]

    @pytest.mark.asyncio
    async def test_search_code_convenience(self):
        """Test search_code convenience method."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            mock_create.return_value = MagicMock()

            agent = CodeAgent()
            agent.run = AsyncMock(return_value={"output": "Found matches"})

            result = await agent.search_code(
                pattern="def main",
                file_type="py",
                path="src"
            )

            agent.run.assert_called_once()
            call_kwargs = agent.run.call_args.kwargs
            assert "def main" in call_kwargs["goal"]
            assert "py" in call_kwargs["goal"]
            assert "src" in call_kwargs["goal"]
            assert call_kwargs["allowed_tools"] == {"Grep", "Glob", "Read"}

    @pytest.mark.asyncio
    async def test_read_file_convenience(self):
        """Test read_file convenience method."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            mock_create.return_value = MagicMock()

            agent = CodeAgent()
            agent.run = AsyncMock(return_value={"output": "File contents..."})

            await agent.read_file("/path/to/file.py")

            agent.run.assert_called_once()
            call_kwargs = agent.run.call_args.kwargs
            assert "/path/to/file.py" in call_kwargs["goal"]
            assert call_kwargs["allowed_tools"] == {"Read"}

    @pytest.mark.asyncio
    async def test_analyze_codebase_convenience(self):
        """Test analyze_codebase convenience method."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            mock_create.return_value = MagicMock()

            agent = CodeAgent()
            agent.run = AsyncMock(return_value={"output": "Analysis..."})

            await agent.analyze_codebase()

            agent.run.assert_called_once()
            call_kwargs = agent.run.call_args.kwargs
            assert "Analyze" in call_kwargs["goal"]
            assert call_kwargs["allowed_tools"] == {"Glob", "Grep", "Read", "Bash"}

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            mock_llm = AsyncMock()
            mock_create.return_value = mock_llm

            async with CodeAgent() as agent:
                assert agent is not None

            # close should be called
            mock_llm.close.assert_called_once()


class TestCodeAgentIntegration:
    """Integration tests for CodeAgent (requires mocked kernel)."""

    @pytest.mark.asyncio
    async def test_full_flow_with_tool_calls(self):
        """Test full flow with multiple tool calls."""
        with patch('nimbus.apps.code_agent.create_llm_client') as mock_create:
            # Create mock LLM that simulates tool calling
            mock_llm = AsyncMock()

            # First call: request Glob tool
            # Second call: return final result
            mock_llm.complete_with_tools.side_effect = [
                {"text": "", "function_calls": [{"name": "Glob", "arguments": {"pattern": "**/*.py"}}]},
                {"text": "Found 5 Python files:\n- main.py\n- utils.py\n- test_main.py", "function_calls": []},
            ]

            mock_create.return_value = mock_llm

            agent = CodeAgent()

            # The kernel would handle the tool execution
            # For this test, we mock the full kernel flow
            agent.kernel.spawn = AsyncMock(return_value="pid_001")
            agent.kernel.wait = AsyncMock(return_value={
                "exit_code": 0,
                "result": {"text": "Found 5 Python files"},
                "token_usage": 2000,
                "turns": 2,
            })

            result = await agent.run(
                goal="Find all Python files",
                allowed_tools={"Glob", "Read"}
            )

            assert result["status"] == "success"
            assert result["turns"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
