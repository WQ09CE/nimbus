"""Tests for Multi-Agent Delegation functionality.

This module tests the enhanced multi-agent architecture as specified in
docs/multi-agent-integration-design.md v2.0:

1. System prompt enhancement with delegation guidance
2. SubagentExecutor integration with SubagentRegistry
3. spawn_subagent_and_verify method for result verification
4. End-to-end delegation flow

Following the wukong protocol test patterns with proper mocking.
"""

import asyncio
import tempfile
import pytest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from nimbus.tools.subagent import (
    SubagentExecutor,
    SUBAGENT_TOOL_PERMISSIONS,
    reset_executor,
    get_executor,
)
from nimbus.core.agent_config import (
    SubagentConfig,
    SubagentRegistry,
)
from nimbus.tools.base import ToolRegistry, ToolParameter, tool


# =============================================================================
# Mock Helpers
# =============================================================================


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, responses: Optional[List[str]] = None):
        self.responses = responses or ["Test response"]
        self._call_count = 0
        self.prompts: List[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        response = self.responses[self._call_count % len(self.responses)]
        self._call_count += 1
        return response


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client."""
    return MockLLMClient()


@pytest.fixture
def mock_tool_registry():
    """Create a mock tool registry with test tools."""
    registry = ToolRegistry()

    @tool(
        name="Read",
        description="Read a file",
        parameters=[
            ToolParameter("file_path", "string", "Path to file", required=True),
        ],
    )
    async def read_tool(file_path: str, **kwargs) -> str:
        return f"Content of {file_path}"

    @tool(
        name="Glob",
        description="Find files by pattern",
        parameters=[
            ToolParameter("pattern", "string", "Glob pattern", required=True),
        ],
    )
    async def glob_tool(pattern: str, **kwargs) -> str:
        return f"Found files matching {pattern}"

    @tool(
        name="Grep",
        description="Search in files",
        parameters=[
            ToolParameter("pattern", "string", "Search pattern", required=True),
        ],
    )
    async def grep_tool(pattern: str, **kwargs) -> str:
        return f"Found matches for {pattern}"

    @tool(
        name="Write",
        description="Write to a file",
        parameters=[
            ToolParameter("file_path", "string", "Path to file", required=True),
            ToolParameter("content", "string", "Content to write", required=True),
        ],
    )
    async def write_tool(file_path: str, content: str, **kwargs) -> str:
        return f"Wrote to {file_path}"

    @tool(
        name="Bash",
        description="Run shell command",
        parameters=[
            ToolParameter("command", "string", "Command to run", required=True),
        ],
    )
    async def bash_tool(command: str, **kwargs) -> str:
        return f"Ran: {command}"

    registry.register_decorated(read_tool)
    registry.register_decorated(glob_tool)
    registry.register_decorated(grep_tool)
    registry.register_decorated(write_tool)
    registry.register_decorated(bash_tool)

    return registry


@pytest.fixture(autouse=True)
def reset_subagent_state():
    """Reset subagent executor state before each test."""
    reset_executor()
    yield
    reset_executor()


# =============================================================================
# Phase 1: Test Delegation Guidance in System Prompt
# =============================================================================


class TestDelegationGuidanceInPrompt:
    """Tests for delegation guidance in system prompt."""

    def test_delegation_guidance_in_default_yaml(self):
        """Verify that default.yaml system_prompt contains delegation guidance."""
        yaml_path = Path(__file__).parent.parent / "src" / "nimbus" / "agents" / "default.yaml"

        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        system_prompt = config.get("system_prompt", "")

        # Check for key delegation guidance sections
        assert "Subagent Delegation" in system_prompt
        assert "coder" in system_prompt
        assert "explorer" in system_prompt
        assert "reviewer" in system_prompt

        # Check for when to delegate guidance
        assert "When to Delegate" in system_prompt
        assert "Multi-file" in system_prompt

        # Check for when to handle directly guidance
        assert "When to Handle Directly" in system_prompt
        assert "Simple questions" in system_prompt

        # Check for delegation example
        assert "Delegation Example" in system_prompt
        assert "Subagent" in system_prompt

    def test_delegation_guidance_contains_tool_info(self):
        """Verify that delegation guidance includes tool information."""
        yaml_path = Path(__file__).parent.parent / "src" / "nimbus" / "agents" / "default.yaml"

        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        system_prompt = config.get("system_prompt", "")

        # Check for tool listings
        assert "Read" in system_prompt
        assert "Write" in system_prompt
        assert "Glob" in system_prompt
        assert "Grep" in system_prompt


# =============================================================================
# Phase 2: Test SubagentExecutor Uses Registry
# =============================================================================


class TestSubagentExecutorUsesRegistry:
    """Tests for SubagentExecutor integration with SubagentRegistry."""

    def test_executor_accepts_registry_parameter(self):
        """Test that SubagentExecutor accepts registry parameter."""
        registry = SubagentRegistry()
        executor = SubagentExecutor(registry=registry)

        assert executor._registry is registry

    def test_validate_tools_uses_registry_first(self, mock_tool_registry):
        """Test that _validate_tools reads from registry first."""
        # Create a registry with custom tool permissions
        registry = SubagentRegistry()
        custom_config = SubagentConfig(
            name="custom-explorer",
            description="Custom explorer with extra tools",
            mode="subagent",
            allowed_tools=["Read", "Glob", "Grep", "WebSearch"],  # Extra tool
        )
        registry.register(custom_config)

        executor = SubagentExecutor(
            parent_tools={"Read", "Glob", "Grep", "WebSearch", "Write"},
            tool_registry=mock_tool_registry,
            registry=registry,
        )

        # Validate tools for custom-explorer type
        validated = executor._validate_tools(
            requested_tools={"Read", "Glob", "Grep", "WebSearch"},
            subagent_type="custom-explorer",
        )

        # Should include WebSearch from custom config
        assert validated == {"Read", "Glob", "Grep", "WebSearch"}

    def test_validate_tools_falls_back_to_hardcoded(self, mock_tool_registry):
        """Test that _validate_tools falls back to hardcoded permissions."""
        # Create registry without the requested subagent type
        registry = SubagentRegistry()

        executor = SubagentExecutor(
            parent_tools={"Read", "Glob", "Grep", "Write", "Edit", "Bash"},
            tool_registry=mock_tool_registry,
            registry=registry,
        )

        # Validate tools for standard explorer type (not in registry)
        validated = executor._validate_tools(
            requested_tools={"Read", "Glob", "Grep"},
            subagent_type="explorer",
        )

        # Should use hardcoded SUBAGENT_TOOL_PERMISSIONS
        assert validated == SUBAGENT_TOOL_PERMISSIONS["explorer"]

    def test_validate_tools_without_registry(self, mock_tool_registry):
        """Test that _validate_tools works without registry."""
        executor = SubagentExecutor(
            parent_tools={"Read", "Glob", "Grep"},
            tool_registry=mock_tool_registry,
            registry=None,  # No registry
        )

        validated = executor._validate_tools(
            requested_tools={"Read", "Glob", "Grep"},
            subagent_type="explorer",
        )

        # Should use hardcoded permissions
        assert validated == {"Read", "Glob", "Grep"}

    def test_get_executor_accepts_registry(self, mock_tool_registry, mock_llm_client):
        """Test that get_executor function accepts registry parameter."""
        registry = SubagentRegistry()

        executor = get_executor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
            parent_tools={"Read", "Glob"},
            registry=registry,
        )

        assert executor._registry is registry

    def test_registry_config_with_empty_tools(self, mock_tool_registry):
        """Test that empty allowed_tools in config falls back to hardcoded."""
        registry = SubagentRegistry()
        empty_tools_config = SubagentConfig(
            name="empty-tools-agent",
            description="Agent with empty tools list",
            mode="subagent",
            allowed_tools=[],  # Empty - means all tools allowed
        )
        registry.register(empty_tools_config)

        executor = SubagentExecutor(
            parent_tools={"Read", "Glob", "Grep"},
            tool_registry=mock_tool_registry,
            registry=registry,
        )

        # When config has empty tools, should fall back to hardcoded
        validated = executor._validate_tools(
            requested_tools={"Read", "Glob"},
            subagent_type="empty-tools-agent",
        )

        # Should fall back to explorer (default) permissions
        expected = {"Read", "Glob"} & SUBAGENT_TOOL_PERMISSIONS["explorer"]
        assert validated == expected


# =============================================================================
# Phase 3: Test spawn_subagent_and_verify
# =============================================================================


class TestSpawnSubagentAndVerify:
    """Tests for spawn_subagent_and_verify method."""

    @pytest.fixture
    def mock_code_agent(self, mock_llm_client, mock_tool_registry):
        """Create a mock CodeAgent for testing."""
        from nimbus.core.agent import CodeAgent

        agent = CodeAgent(
            llm_client=mock_llm_client,
            system_prompt="Test agent",
            memory_type="simple",
            planner_type="simple",
            enable_logging=False,
            tool_registry=mock_tool_registry,
        )
        return agent

    @pytest.mark.asyncio
    async def test_spawn_subagent_and_verify_success(self, mock_code_agent):
        """Test successful verification when files exist."""
        # Create a temporary file to simulate modified file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("# Test file")
            temp_file = f.name

        try:
            # Mock spawn_subagent to return a result with files_modified
            mock_result = {
                "agent_id": "test_agent_123",
                "status": "completed",
                "summary": "Task completed successfully",
                "files_modified": [temp_file],
            }
            mock_code_agent.spawn_subagent = AsyncMock(return_value=mock_result)

            result = await mock_code_agent.spawn_subagent_and_verify(
                prompt="Test task",
                subagent_type="coder",
                verify=True,
            )

            assert result["verification"] == "PASSED"
            assert result["status"] == "completed"
        finally:
            # Clean up
            Path(temp_file).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_spawn_subagent_and_verify_file_not_found(self, mock_code_agent):
        """Test verification failure when file does not exist."""
        # Mock spawn_subagent to return a result with non-existent file
        mock_result = {
            "agent_id": "test_agent_456",
            "status": "completed",
            "summary": "Task completed",
            "files_modified": ["/nonexistent/path/file.py"],
        }
        mock_code_agent.spawn_subagent = AsyncMock(return_value=mock_result)

        result = await mock_code_agent.spawn_subagent_and_verify(
            prompt="Test task",
            subagent_type="coder",
            verify=True,
        )

        assert "FAILED" in result["verification"]
        assert "File not found" in result["verification"]
        assert "/nonexistent/path/file.py" in result["verification"]

    @pytest.mark.asyncio
    async def test_spawn_subagent_and_verify_skipped_when_disabled(self, mock_code_agent):
        """Test that verification is skipped when verify=False."""
        mock_result = {
            "agent_id": "test_agent_789",
            "status": "completed",
            "summary": "Task completed",
            "files_modified": ["/some/file.py"],
        }
        mock_code_agent.spawn_subagent = AsyncMock(return_value=mock_result)

        result = await mock_code_agent.spawn_subagent_and_verify(
            prompt="Test task",
            subagent_type="coder",
            verify=False,
        )

        assert result["verification"] == "SKIPPED"

    @pytest.mark.asyncio
    async def test_spawn_subagent_and_verify_skipped_on_failure(self, mock_code_agent):
        """Test that verification is skipped when task failed."""
        mock_result = {
            "agent_id": "test_agent_failed",
            "status": "failed",
            "summary": "Task failed",
            "error": "Some error",
            "files_modified": [],
        }
        mock_code_agent.spawn_subagent = AsyncMock(return_value=mock_result)

        result = await mock_code_agent.spawn_subagent_and_verify(
            prompt="Test task",
            subagent_type="coder",
            verify=True,
        )

        assert result["verification"] == "SKIPPED"
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_spawn_subagent_and_verify_no_files_modified(self, mock_code_agent):
        """Test verification passes when no files were modified."""
        mock_result = {
            "agent_id": "test_agent_nofiles",
            "status": "completed",
            "summary": "Task completed (no files changed)",
            "files_modified": [],
        }
        mock_code_agent.spawn_subagent = AsyncMock(return_value=mock_result)

        result = await mock_code_agent.spawn_subagent_and_verify(
            prompt="Test task",
            subagent_type="explorer",  # Explorer typically doesn't modify files
            verify=True,
        )

        assert result["verification"] == "PASSED"

    @pytest.mark.asyncio
    async def test_spawn_subagent_and_verify_multiple_files(self, mock_code_agent):
        """Test verification with multiple modified files."""
        # Create temporary files
        temp_files = []
        for i in range(3):
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
            f.write(f"# Test file {i}")
            f.close()
            temp_files.append(f.name)

        try:
            mock_result = {
                "agent_id": "test_agent_multi",
                "status": "completed",
                "summary": "Modified multiple files",
                "files_modified": temp_files,
            }
            mock_code_agent.spawn_subagent = AsyncMock(return_value=mock_result)

            result = await mock_code_agent.spawn_subagent_and_verify(
                prompt="Test task",
                subagent_type="coder",
                verify=True,
            )

            assert result["verification"] == "PASSED"
        finally:
            # Clean up
            for f in temp_files:
                Path(f).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_spawn_subagent_and_verify_partial_files_exist(self, mock_code_agent):
        """Test verification fails if any file is missing."""
        # Create one real file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("# Real file")
            real_file = f.name

        try:
            mock_result = {
                "agent_id": "test_agent_partial",
                "status": "completed",
                "summary": "Modified files",
                "files_modified": [real_file, "/nonexistent/missing.py"],
            }
            mock_code_agent.spawn_subagent = AsyncMock(return_value=mock_result)

            result = await mock_code_agent.spawn_subagent_and_verify(
                prompt="Test task",
                subagent_type="coder",
                verify=True,
            )

            assert "FAILED" in result["verification"]
            assert "missing.py" in result["verification"]
        finally:
            Path(real_file).unlink(missing_ok=True)


# =============================================================================
# End-to-End Integration Tests
# =============================================================================


class TestMultiAgentDelegationE2E:
    """End-to-end integration tests for multi-agent delegation."""

    @pytest.mark.asyncio
    async def test_delegation_flow_with_registry(self, mock_llm_client, mock_tool_registry):
        """Test complete delegation flow using registry configuration."""
        # Create registry with custom coder config
        registry = SubagentRegistry()
        coder_config = SubagentConfig(
            name="coder",
            description="Code implementation expert",
            mode="subagent",
            allowed_tools=["Read", "Write", "Glob", "Grep", "Bash"],
            max_turns=30,
        )
        registry.register(coder_config)

        # Create executor with registry
        executor = SubagentExecutor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
            parent_tools={"Read", "Write", "Glob", "Grep", "Bash", "Edit"},
            registry=registry,
        )

        # Spawn a coder subagent
        result = await executor.spawn(
            prompt="Refactor the API module",
            subagent_type="coder",
            description="Refactor API",
            run_in_background=False,
        )

        assert result["status"] == "completed"
        assert "agent_id" in result

    @pytest.mark.asyncio
    async def test_delegation_with_custom_subagent_type(self, mock_llm_client, mock_tool_registry):
        """Test delegation with a custom subagent type defined in registry."""
        # Create registry with custom subagent type
        registry = SubagentRegistry()
        custom_config = SubagentConfig(
            name="security-reviewer",
            description="Security-focused code reviewer",
            mode="subagent",
            allowed_tools=["Read", "Glob", "Grep"],
            prompt="You are a security expert. Review code for vulnerabilities.",
            max_turns=20,
        )
        registry.register(custom_config)

        # Update SUBAGENT_TOOL_PERMISSIONS to include custom type
        # (In real usage, registry takes precedence)

        executor = SubagentExecutor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
            parent_tools={"Read", "Glob", "Grep", "Write"},
            registry=registry,
        )

        # Validate tools for custom type
        validated = executor._validate_tools(
            requested_tools={"Read", "Glob", "Grep"},
            subagent_type="security-reviewer",
        )

        # Should match config's allowed_tools
        assert validated == {"Read", "Glob", "Grep"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
