"""Comprehensive tests for the Subagent system.

This module tests all components of the subagent system:
- SubagentTool: The tool for spawning subagents
- AgentConfig: Configuration loading and validation
- BatchTool: Parallel tool execution
- Permission: Permission enforcement
- CodeAgent integration: Subagent lifecycle management

Following the wukong protocol test patterns with proper mocking.
"""

import asyncio
import json
import pytest
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from unittest.mock import AsyncMock, MagicMock, patch

from nimbus.tools.subagent import (
    SubagentType,
    SubagentStatus,
    SubagentContext,
    SubagentResult,
    SubagentExecutor,
    subagent_task,
    get_subagent_result,
    cancel_subagent,
    list_subagents,
    reset_executor,
    get_executor,
    MAX_DEPTH,
    MAX_CONCURRENT,
    DEFAULT_MAX_TURNS,
    SUBAGENT_TOOL_PERMISSIONS,
)
from nimbus.core.agent_config import (
    SubagentConfig,
    SubagentConfigLoader,
    SubagentRegistry,
    get_default_registry,
    reset_default_registry,
)
from nimbus.core.permission import (
    PermissionRule,
    PermissionSet,
    PermissionManager,
    create_permission_manager,
    create_subagent_permissions,
    READONLY_PERMISSIONS,
    CODER_PERMISSIONS,
    EXPLORER_PERMISSIONS,
)
from nimbus.tools.base import ToolRegistry, ToolParameter, tool
from nimbus.tools.batch import batch_tool, MAX_CONCURRENT_CALLS


# =============================================================================
# Mock Helpers
# =============================================================================


class MockLLMClient:
    """Mock LLM client for testing subagent execution."""

    def __init__(self, responses: Optional[List[str]] = None):
        """Initialize with optional predefined responses.

        Args:
            responses: List of response strings. Will cycle through if needed.
        """
        self.responses = responses or ["Test response"]
        self._call_count = 0
        self.prompts: List[str] = []

    async def complete(self, prompt: str) -> str:
        """Mock completion call.

        Args:
            prompt: The prompt text.

        Returns:
            Next response from the responses list.
        """
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
# TestSubagentTool - Tests for the Subagent tool
# =============================================================================


class TestSubagentTool:
    """Tests for the Subagent tool."""

    def test_subagent_types_enum(self):
        """Verify all SubagentType enum values."""
        assert SubagentType.EXPLORER.value == "explorer"
        assert SubagentType.RESEARCHER.value == "researcher"
        assert SubagentType.CODER.value == "coder"
        assert SubagentType.REVIEWER.value == "reviewer"

        # Verify all types exist
        all_types = [t.value for t in SubagentType]
        assert len(all_types) == 4
        assert "explorer" in all_types
        assert "researcher" in all_types
        assert "coder" in all_types
        assert "reviewer" in all_types

    def test_subagent_status_enum(self):
        """Verify all SubagentStatus enum values."""
        assert SubagentStatus.PENDING.value == "pending"
        assert SubagentStatus.RUNNING.value == "running"
        assert SubagentStatus.COMPLETED.value == "completed"
        assert SubagentStatus.FAILED.value == "failed"
        assert SubagentStatus.CANCELLED.value == "cancelled"

    def test_subagent_tool_permissions_mapping(self):
        """Verify subagent type to tool permissions mapping."""
        # Explorer has read-only tools
        assert SUBAGENT_TOOL_PERMISSIONS["explorer"] == {"Read", "Glob", "Grep"}

        # Researcher has read + web tools
        assert SUBAGENT_TOOL_PERMISSIONS["researcher"] == {
            "Read", "Glob", "Grep", "WebSearch", "WebFetch"
        }

        # Coder has full file operations
        assert SUBAGENT_TOOL_PERMISSIONS["coder"] == {
            "Read", "Write", "Edit", "Bash", "Glob", "Grep"
        }

        # Reviewer has read-only tools
        assert SUBAGENT_TOOL_PERMISSIONS["reviewer"] == {"Read", "Glob", "Grep"}

    @pytest.mark.asyncio
    async def test_subagent_task_parameters(self, mock_llm_client, mock_tool_registry):
        """Verify subagent_task tool parameters."""
        # Call with required parameters only
        result = await subagent_task(
            prompt="Explore the src directory",
            subagent_type="explorer",
            description="Explore src",
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        assert "agent_id" in result
        assert result["status"] in ["completed", "failed", "running"]

    @pytest.mark.asyncio
    async def test_invalid_subagent_type(self, mock_llm_client, mock_tool_registry):
        """Test that invalid subagent type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid subagent_type"):
            await subagent_task(
                prompt="Test task",
                subagent_type="invalid_type",
                description="Test",
                workspace=Path("/tmp"),
                tool_registry=mock_tool_registry,
                llm_client=mock_llm_client,
            )

    @pytest.mark.asyncio
    async def test_max_depth_enforcement(self, mock_llm_client, mock_tool_registry):
        """Test recursive depth limiting."""
        executor = SubagentExecutor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
            current_depth=MAX_DEPTH,  # Already at max depth
        )

        with pytest.raises(ValueError, match=f"Maximum subagent depth"):
            await executor.spawn(
                prompt="Test task",
                subagent_type="explorer",
                description="Test",
            )

    @pytest.mark.asyncio
    async def test_concurrent_limiting(self, mock_llm_client, mock_tool_registry):
        """Test concurrent subagent limiting via semaphore."""
        # Create executor
        executor = SubagentExecutor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=MockLLMClient(["Slow response"]),
        )

        # Spawn MAX_CONCURRENT + 1 background subagents
        tasks = []
        for i in range(MAX_CONCURRENT + 1):
            task = asyncio.create_task(
                executor.spawn(
                    prompt=f"Task {i}",
                    subagent_type="explorer",
                    description=f"Task {i}",
                    run_in_background=False,  # Foreground to test semaphore
                )
            )
            tasks.append(task)

        # All should eventually complete (semaphore manages concurrency)
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All tasks should succeed (semaphore queues excess)
        successful = [r for r in results if isinstance(r, dict)]
        assert len(successful) == MAX_CONCURRENT + 1

    @pytest.mark.asyncio
    async def test_foreground_execution(self, mock_llm_client, mock_tool_registry):
        """Test foreground (blocking) subagent execution."""
        result = await subagent_task(
            prompt="Analyze the codebase",
            subagent_type="explorer",
            description="Code analysis",
            run_in_background=False,
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        assert result["status"] == "completed"
        assert "agent_id" in result
        assert "summary" in result
        assert "turn_count" in result
        assert result["turn_count"] >= 1

    @pytest.mark.asyncio
    async def test_background_execution(self, mock_llm_client, mock_tool_registry):
        """Test background (async) subagent execution."""
        result = await subagent_task(
            prompt="Research async patterns",
            subagent_type="researcher",
            description="Research",
            run_in_background=True,
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        assert result["status"] == "running"
        assert "agent_id" in result
        assert "message" in result

        # Wait a bit for background task to complete
        await asyncio.sleep(0.1)

        # Should be able to get result
        executor = get_executor()
        final_result = await executor.get_result(result["agent_id"])
        # Result may still be running or completed
        assert final_result is not None

    @pytest.mark.asyncio
    async def test_tool_permission_inheritance(self, mock_llm_client, mock_tool_registry):
        """Test that subagent tools are subset of parent tools."""
        parent_tools = {"Read", "Glob"}  # Limited parent tools

        executor = SubagentExecutor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
            parent_tools=parent_tools,
        )

        context = executor._create_context(
            parent_context="",
            subagent_type="coder",  # Coder normally has Write, Edit, Bash
            allowed_tools=None,
        )

        # Coder's tools should be intersected with parent's tools
        # Coder wants {Read, Write, Edit, Bash, Glob, Grep}
        # Parent has {Read, Glob}
        # Result should be {Read, Glob}
        assert context.allowed_tools == {"Read", "Glob"}


# =============================================================================
# TestSubagentContext - Tests for SubagentContext
# =============================================================================


class TestSubagentContext:
    """Tests for SubagentContext dataclass."""

    def test_create_context(self):
        """Test creating a subagent context."""
        context = SubagentContext.create(
            parent_context="Parent context here",
            parent_id="parent_123",
            depth=1,
            allowed_tools={"Read", "Glob"},
        )

        assert context.agent_id.startswith("subagent_")
        assert context.parent_id == "parent_123"
        assert context.depth == 1
        assert context.context_snapshot == "Parent context here"
        assert context.allowed_tools == {"Read", "Glob"}
        assert isinstance(context.created_at, datetime)

    def test_create_context_max_depth_exceeded(self):
        """Test that creating context beyond max depth fails."""
        with pytest.raises(ValueError, match="Maximum subagent depth"):
            SubagentContext.create(
                parent_context="",
                parent_id=None,
                depth=MAX_DEPTH + 1,
                allowed_tools=set(),
            )

    def test_working_memory(self):
        """Test working memory operations."""
        context = SubagentContext.create(
            parent_context="",
            parent_id=None,
            depth=1,
            allowed_tools=set(),
        )

        context.set_working("key1", "value1")
        context.set_working("key2", {"nested": "data"})

        assert context.get_working("key1") == "value1"
        assert context.get_working("key2") == {"nested": "data"}
        assert context.get_working("nonexistent") is None
        assert context.get_working("nonexistent", "default") == "default"

    def test_can_use_tool(self):
        """Test tool permission checking."""
        context = SubagentContext.create(
            parent_context="",
            parent_id=None,
            depth=1,
            allowed_tools={"Read", "Glob", "Grep"},
        )

        assert context.can_use_tool("Read") is True
        assert context.can_use_tool("Glob") is True
        assert context.can_use_tool("Write") is False
        assert context.can_use_tool("Bash") is False

    def test_get_full_context(self):
        """Test full context generation."""
        context = SubagentContext.create(
            parent_context="Parent snapshot",
            parent_id="parent_1",
            depth=2,
            allowed_tools={"Read", "Glob"},
        )
        context.set_working("current_task", "exploration")

        full = context.get_full_context()

        assert "Parent Context" in full
        assert "Parent snapshot" in full
        assert "Working Memory" in full
        assert "current_task" in full
        assert "Subagent Info" in full
        assert context.agent_id in full
        assert f"Depth: 2/{MAX_DEPTH}" in full
        assert "Read" in full

    def test_to_dict(self):
        """Test serialization to dictionary."""
        context = SubagentContext.create(
            parent_context="Test context",
            parent_id="parent_1",
            depth=1,
            allowed_tools={"Read"},
        )
        context.set_working("key", "value")

        data = context.to_dict()

        assert data["agent_id"] == context.agent_id
        assert data["parent_id"] == "parent_1"
        assert data["depth"] == 1
        assert data["context_snapshot"] == "Test context"
        assert data["working_memory"] == {"key": "value"}
        assert "Read" in data["allowed_tools"]
        assert "created_at" in data


# =============================================================================
# TestSubagentResult - Tests for SubagentResult
# =============================================================================


class TestSubagentResult:
    """Tests for SubagentResult dataclass."""

    def test_create_result(self):
        """Test creating a subagent result."""
        result = SubagentResult(
            agent_id="agent_123",
            status=SubagentStatus.COMPLETED,
            summary="Task completed successfully",
            result={"data": "test"},
            turn_count=5,
            duration_ms=1500,
            files_accessed=["/path/to/file.py"],
            files_modified=["/path/to/output.py"],
        )

        assert result.agent_id == "agent_123"
        assert result.status == SubagentStatus.COMPLETED
        assert result.summary == "Task completed successfully"
        assert result.result == {"data": "test"}
        assert result.error is None
        assert result.turn_count == 5
        assert result.duration_ms == 1500
        assert result.files_accessed == ["/path/to/file.py"]
        assert result.files_modified == ["/path/to/output.py"]

    def test_create_failed_result(self):
        """Test creating a failed result."""
        result = SubagentResult(
            agent_id="agent_456",
            status=SubagentStatus.FAILED,
            summary="Task failed with error",
            error="Connection timeout",
        )

        assert result.status == SubagentStatus.FAILED
        assert result.error == "Connection timeout"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        result = SubagentResult(
            agent_id="agent_789",
            status=SubagentStatus.COMPLETED,
            summary="Done",
            result="test output",
            turn_count=3,
            duration_ms=500,
        )

        data = result.to_dict()

        assert data["agent_id"] == "agent_789"
        assert data["status"] == "completed"
        assert data["summary"] == "Done"
        assert data["result"] == "test output"
        assert data["turn_count"] == 3
        assert data["duration_ms"] == 500


# =============================================================================
# TestSubagentExecutor - Tests for SubagentExecutor
# =============================================================================


class TestSubagentExecutor:
    """Tests for SubagentExecutor class."""

    def test_init_defaults(self):
        """Test default initialization."""
        executor = SubagentExecutor()

        assert executor.parent_tools == set()
        assert executor.workspace == Path.cwd()
        assert executor.current_depth == 0
        assert executor.tool_registry is None
        assert executor.llm_client is None
        assert len(executor.running_subagents) == 0
        assert len(executor.completed_subagents) == 0

    def test_init_with_params(self, mock_llm_client, mock_tool_registry):
        """Test initialization with parameters."""
        executor = SubagentExecutor(
            parent_tools={"Read", "Write"},
            workspace=Path("/test/workspace"),
            current_depth=1,
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        assert executor.parent_tools == {"Read", "Write"}
        assert executor.workspace == Path("/test/workspace")
        assert executor.current_depth == 1
        assert executor.tool_registry is mock_tool_registry
        assert executor.llm_client is mock_llm_client

    def test_validate_tools(self, mock_tool_registry):
        """Test tool validation logic."""
        executor = SubagentExecutor(
            parent_tools={"Read", "Glob", "Write", "Bash"},
            tool_registry=mock_tool_registry,
        )

        # Explorer type wants Read, Glob, Grep
        # Parent has Read, Glob, Write, Bash
        # Result should be Read, Glob (intersection)
        validated = executor._validate_tools(
            requested_tools={"Read", "Glob", "Grep"},
            subagent_type="explorer",
        )

        assert validated == {"Read", "Glob"}

    @pytest.mark.asyncio
    async def test_spawn_foreground(self, mock_llm_client, mock_tool_registry):
        """Test spawning foreground subagent."""
        executor = SubagentExecutor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        result = await executor.spawn(
            prompt="Test task",
            subagent_type="explorer",
            description="Test",
            run_in_background=False,
        )

        assert result["status"] == "completed"
        assert "agent_id" in result
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_spawn_background(self, mock_llm_client, mock_tool_registry):
        """Test spawning background subagent."""
        executor = SubagentExecutor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        result = await executor.spawn(
            prompt="Background task",
            subagent_type="researcher",
            description="Research",
            run_in_background=True,
        )

        assert result["status"] == "running"
        assert "agent_id" in result

        # Wait for completion
        await asyncio.sleep(0.1)

        # Check running/completed lists
        running = executor.list_running()
        completed = executor.list_completed()

        # Should be completed by now
        assert result["agent_id"] in running or result["agent_id"] in completed

    @pytest.mark.asyncio
    async def test_cancel_subagent(self, mock_llm_client, mock_tool_registry):
        """Test cancelling a running subagent."""
        # Use slow LLM responses
        slow_llm = MockLLMClient(["[TOOL: Read]\nSlow operation..."])

        executor = SubagentExecutor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=slow_llm,
        )

        # Spawn background task
        result = await executor.spawn(
            prompt="Long task",
            subagent_type="explorer",
            description="Long",
            run_in_background=True,
        )

        agent_id = result["agent_id"]

        # Cancel immediately
        cancelled = await executor.cancel(agent_id)

        # Either cancellation succeeded or task completed quickly
        assert cancelled is True or agent_id in executor.completed_subagents

    @pytest.mark.asyncio
    async def test_get_result(self, mock_llm_client, mock_tool_registry):
        """Test getting subagent result for background tasks."""
        executor = SubagentExecutor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        # Spawn background task
        spawn_result = await executor.spawn(
            prompt="Quick task",
            subagent_type="explorer",
            description="Quick",
            run_in_background=True,  # Background so it goes into tracking
        )

        agent_id = spawn_result["agent_id"]

        # Wait for task to complete
        await asyncio.sleep(0.1)

        # Result should be available
        result = await executor.get_result(agent_id)
        assert result is not None
        # Background tasks go into completed_subagents after completion

    @pytest.mark.asyncio
    async def test_list_running_and_completed(self, mock_llm_client, mock_tool_registry):
        """Test listing running and completed subagents."""
        executor = SubagentExecutor(
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        # Initially empty
        assert executor.list_running() == []
        assert executor.list_completed() == []

        # Spawn background task
        result = await executor.spawn(
            prompt="Task 1",
            subagent_type="explorer",
            description="Task 1",
            run_in_background=True,
        )

        agent_id = result["agent_id"]

        # Wait for completion
        await asyncio.sleep(0.1)

        # Should be in one of the lists
        all_agents = executor.list_running() + executor.list_completed()
        assert agent_id in all_agents


# =============================================================================
# TestAgentConfig - Tests for SubagentConfig system
# =============================================================================


class TestSubagentConfigValidation:
    """Tests for SubagentConfig validation."""

    def test_valid_config(self):
        """Test creating a valid configuration."""
        config = SubagentConfig(
            name="test_agent",
            description="Test agent",
            mode="subagent",
            allowed_tools=["Read", "Glob"],
            max_turns=30,
        )

        assert config.name == "test_agent"
        assert config.description == "Test agent"
        assert config.mode == "subagent"
        assert config.allowed_tools == ["Read", "Glob"]
        assert config.max_turns == 30

    def test_empty_name_raises(self):
        """Test that empty name raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            SubagentConfig(name="", description="Test")

    def test_invalid_name_characters(self):
        """Test that invalid characters raise ValueError."""
        with pytest.raises(ValueError, match="invalid characters"):
            SubagentConfig(name="test@agent!", description="Test")

    def test_valid_name_with_hyphens(self):
        """Test that hyphens are allowed in names."""
        config = SubagentConfig(name="code-explorer", description="Test")
        assert config.name == "code-explorer"

    def test_valid_name_with_underscores(self):
        """Test that underscores are allowed in names."""
        config = SubagentConfig(name="code_explorer", description="Test")
        assert config.name == "code_explorer"

    def test_invalid_mode_raises(self):
        """Test that invalid mode raises ValueError."""
        with pytest.raises(ValueError, match="Invalid mode"):
            SubagentConfig(name="test", description="Test", mode="invalid")

    def test_invalid_max_turns_raises(self):
        """Test that max_turns < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_turns must be at least 1"):
            SubagentConfig(name="test", description="Test", max_turns=0)


class TestSubagentConfigLoader:
    """Tests for SubagentConfigLoader."""

    def test_load_from_yaml(self):
        """Test loading from YAML file."""
        yaml_content = """
name: explorer
description: "Code exploration expert"
mode: subagent
allowed_tools:
  - Read
  - Glob
  - Grep
max_turns: 30
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()

            loader = SubagentConfigLoader()
            config = loader.load_from_yaml(f.name)

            assert config.name == "explorer"
            assert config.description == "Code exploration expert"
            assert config.allowed_tools == ["Read", "Glob", "Grep"]
            assert config.max_turns == 30

    def test_load_from_yaml_uses_filename(self):
        """Test that filename is used as name if not specified."""
        yaml_content = """
description: "Test agent"
mode: subagent
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, prefix="myagent_"
        ) as f:
            f.write(yaml_content)
            f.flush()

            loader = SubagentConfigLoader()
            config = loader.load_from_yaml(f.name)

            assert config.name.startswith("myagent_")

    def test_load_nonexistent_file_raises(self):
        """Test that loading non-existent file raises error."""
        loader = SubagentConfigLoader()
        with pytest.raises(FileNotFoundError):
            loader.load_from_yaml("/nonexistent/path.yaml")

    def test_load_empty_file_raises(self):
        """Test that loading empty file raises error."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("")
            f.flush()

            loader = SubagentConfigLoader()
            with pytest.raises(ValueError, match="Empty configuration"):
                loader.load_from_yaml(f.name)

    def test_discover_agents(self):
        """Test discovering agents from directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create YAML files
            (Path(tmpdir) / "explorer.yaml").write_text("""
name: explorer
description: Explorer agent
allowed_tools: [Read, Glob]
""")
            (Path(tmpdir) / "coder.yml").write_text("""
name: coder
description: Coder agent
allowed_tools: [Read, Write, Bash]
""")

            loader = SubagentConfigLoader()
            configs = loader.discover_agents([tmpdir], include_builtin=False)

            assert len(configs) == 2
            assert "explorer" in configs
            assert "coder" in configs


class TestSubagentRegistry:
    """Tests for SubagentRegistry."""

    def test_register_and_get(self):
        """Test registering and retrieving configs."""
        registry = SubagentRegistry()

        config = SubagentConfig(
            name="explorer",
            description="Explorer agent",
        )
        registry.register(config)

        retrieved = registry.get("explorer")
        assert retrieved is not None
        assert retrieved.name == "explorer"

    def test_get_nonexistent(self):
        """Test getting non-existent config returns None."""
        registry = SubagentRegistry()
        assert registry.get("nonexistent") is None

    def test_get_or_raise(self):
        """Test get_or_raise raises for non-existent config."""
        registry = SubagentRegistry()
        registry.register(SubagentConfig(name="exists", description="Exists"))

        # Should work for existing
        config = registry.get_or_raise("exists")
        assert config.name == "exists"

        # Should raise for non-existent
        with pytest.raises(KeyError, match="not found"):
            registry.get_or_raise("nonexistent")

    def test_unregister(self):
        """Test unregistering configs."""
        registry = SubagentRegistry()
        registry.register(SubagentConfig(name="temp", description="Temporary"))

        assert "temp" in registry
        assert registry.unregister("temp") is True
        assert "temp" not in registry
        assert registry.unregister("temp") is False

    def test_list_agents_by_mode(self):
        """Test filtering agents by mode."""
        registry = SubagentRegistry()
        registry.register(SubagentConfig(name="a", description="A", mode="primary"))
        registry.register(SubagentConfig(name="b", description="B", mode="subagent"))
        registry.register(SubagentConfig(name="c", description="C", mode="all"))

        subagents = registry.list_agents(mode="subagent")
        names = [a.name for a in subagents]

        assert "b" in names
        assert "c" in names  # "all" mode matches any filter
        assert "a" not in names

    def test_validate_tools(self):
        """Test tool validation."""
        registry = SubagentRegistry()

        config = SubagentConfig(
            name="test",
            description="Test",
            allowed_tools=["Read", "Write", "UnknownTool"],
        )

        available = ["Read", "Write", "Bash", "Glob"]
        invalid = registry.validate_tools(config, available)

        assert invalid == ["UnknownTool"]


class TestGlobalRegistry:
    """Tests for global registry functions."""

    def teardown_method(self):
        """Reset global registry after each test."""
        reset_default_registry()

    def test_get_default_registry(self):
        """Test getting default registry."""
        registry = get_default_registry()
        assert isinstance(registry, SubagentRegistry)

    def test_singleton_pattern(self):
        """Test that default registry is singleton."""
        registry1 = get_default_registry()
        registry2 = get_default_registry()
        assert registry1 is registry2

    def test_reset_default_registry(self):
        """Test resetting default registry."""
        registry1 = get_default_registry()
        reset_default_registry()
        registry2 = get_default_registry()
        assert registry1 is not registry2


# =============================================================================
# TestBatchTool - Tests for batch tool execution
# =============================================================================


class TestBatchToolBasic:
    """Basic tests for batch_tool."""

    @pytest.mark.asyncio
    async def test_single_tool_call(self, mock_tool_registry):
        """Test batch with single tool call."""
        result = await batch_tool(
            tool_calls=[{"name": "Read", "params": {"file_path": "/test.py"}}],
            tool_registry=mock_tool_registry,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 1/1 tool calls"
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self, mock_tool_registry):
        """Test batch with multiple tool calls."""
        result = await batch_tool(
            tool_calls=[
                {"name": "Read", "params": {"file_path": "/file1.py"}},
                {"name": "Glob", "params": {"pattern": "*.py"}},
                {"name": "Grep", "params": {"pattern": "def main"}},
            ],
            tool_registry=mock_tool_registry,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 3/3 tool calls"
        assert len(data["results"]) == 3


class TestBatchToolErrorIsolation:
    """Tests for error isolation in batch_tool."""

    @pytest.mark.asyncio
    async def test_single_failure_isolated(self, mock_tool_registry):
        """Test that single tool failure doesn't affect others."""
        result = await batch_tool(
            tool_calls=[
                {"name": "Read", "params": {"file_path": "/before.py"}},
                {"name": "NonExistent", "params": {}},
                {"name": "Read", "params": {"file_path": "/after.py"}},
            ],
            tool_registry=mock_tool_registry,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 2/3 tool calls"
        assert data["results"][0]["status"] == "success"
        assert data["results"][1]["status"] == "error"
        assert data["results"][2]["status"] == "success"

    @pytest.mark.asyncio
    async def test_max_concurrent_limit(self, mock_tool_registry):
        """Test maximum concurrent calls limit (25)."""
        too_many = [{"name": "Read", "params": {"file_path": f"/file{i}.py"}}
                    for i in range(30)]

        with pytest.raises(ValueError, match=f"exceeds limit of {MAX_CONCURRENT_CALLS}"):
            await batch_tool(
                tool_calls=too_many,
                tool_registry=mock_tool_registry,
            )


class TestBatchToolParallelExecution:
    """Tests for parallel execution in batch_tool."""

    @pytest.mark.asyncio
    async def test_parallel_execution(self):
        """Test that tools execute in parallel."""
        import time

        registry = ToolRegistry()

        @tool(
            name="SlowTool",
            description="Slow tool",
            parameters=[
                ToolParameter("delay", "number", "Delay in seconds", required=True),
            ],
        )
        async def slow_tool(delay: float, **kwargs) -> str:
            await asyncio.sleep(delay)
            return f"Done after {delay}s"

        registry.register_decorated(slow_tool)

        start = time.time()

        result = await batch_tool(
            tool_calls=[
                {"name": "SlowTool", "params": {"delay": 0.1}},
                {"name": "SlowTool", "params": {"delay": 0.1}},
                {"name": "SlowTool", "params": {"delay": 0.1}},
            ],
            tool_registry=registry,
            timeout=5.0,
        )

        elapsed = time.time() - start

        data = json.loads(result)
        assert data["summary"] == "Completed 3/3 tool calls"
        # If parallel, should complete in ~0.1s, not ~0.3s
        assert elapsed < 0.25


# =============================================================================
# TestPermission - Tests for permission system
# =============================================================================


class TestPermissionEvaluation:
    """Tests for permission evaluation."""

    def test_basic_allow(self):
        """Test basic allow rule."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        assert manager.evaluate("Read") == "allow"

    def test_basic_deny(self):
        """Test basic deny rule."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Bash", "*", "deny"))
        assert manager.evaluate("Bash") == "deny"

    def test_deny_priority_over_allow(self):
        """Test that deny takes priority over allow at same priority level."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow", priority=10))
        manager.add_rule(PermissionRule("Read", "*", "deny", priority=10))
        assert manager.evaluate("Read") == "deny"

    def test_higher_priority_wins(self):
        """Test that higher priority rules win."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow", priority=0))
        manager.add_rule(PermissionRule("Read", "/etc/**", "deny", priority=10))

        assert manager.evaluate("Read", "/home/file.txt") == "allow"
        assert manager.evaluate("Read", "/etc/passwd") == "deny"


class TestSubagentPermissionSubset:
    """Tests for subagent permission subsetting."""

    def test_create_subset_permissions(self):
        """Test creating subset permissions for subagent."""
        parent = create_permission_manager(CODER_PERMISSIONS)

        # Create readonly subset
        subset = parent.create_subset(["Read", "Glob", "Grep"])

        assert subset.is_allowed("Read") is True
        assert subset.is_allowed("Glob") is True
        assert subset.is_allowed("Grep") is True
        assert subset.is_denied("Write") is True
        assert subset.is_denied("Bash") is True

    def test_create_subagent_permissions(self):
        """Test factory function for subagent permissions."""
        perms = create_subagent_permissions(["Read", "Glob"])

        assert perms.is_allowed("Read") is True
        assert perms.is_allowed("Glob") is True
        assert perms.is_denied("Write") is True
        assert perms.is_denied("Bash") is True

    def test_multi_level_subsetting(self):
        """Test creating subagents from subagents."""
        level1 = create_subagent_permissions(["Read", "Glob", "Grep", "Write"])
        level2 = level1.create_subset(["Read", "Glob"])

        assert level2.is_allowed("Read") is True
        assert level2.is_allowed("Glob") is True
        assert level2.is_denied("Grep") is True
        assert level2.is_denied("Write") is True


class TestPredefinedPermissionSets:
    """Tests for predefined permission sets."""

    def test_readonly_permissions(self):
        """Test READONLY_PERMISSIONS set."""
        manager = create_permission_manager(READONLY_PERMISSIONS)

        assert manager.is_allowed("Read") is True
        assert manager.is_allowed("Glob") is True
        assert manager.is_allowed("Grep") is True
        assert manager.is_denied("Write") is True
        assert manager.is_denied("Bash") is True

    def test_coder_permissions(self):
        """Test CODER_PERMISSIONS set."""
        manager = create_permission_manager(CODER_PERMISSIONS)

        assert manager.is_allowed("Read") is True
        assert manager.is_allowed("Write") is True
        assert manager.is_allowed("Edit") is True
        assert manager.requires_ask("Bash") is True

    def test_explorer_permissions(self):
        """Test EXPLORER_PERMISSIONS set."""
        manager = create_permission_manager(EXPLORER_PERMISSIONS)

        assert manager.is_allowed("Read") is True
        assert manager.is_allowed("Glob") is True
        assert manager.is_allowed("Grep") is True
        assert manager.is_allowed("WebSearch") is True
        assert manager.is_denied("Write") is True
        assert manager.is_denied("Bash") is True


# =============================================================================
# TestCodeAgentSubagent - Integration tests with CodeAgent
# =============================================================================


class TestCodeAgentSubagentIntegration:
    """Integration tests for CodeAgent subagent functionality."""

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

    def test_subagent_registry_initialized(self, mock_code_agent):
        """Test that subagent registry is initialized."""
        assert mock_code_agent._subagent_registry is not None
        assert isinstance(mock_code_agent._subagent_registry, SubagentRegistry)

    def test_get_subagent_types(self, mock_code_agent):
        """Test getting available subagent types."""
        types = mock_code_agent.get_subagent_types()
        assert isinstance(types, list)
        # Should include built-in types if available

    def test_permission_manager_initialized(self, mock_code_agent):
        """Test that permission manager is initialized."""
        assert mock_code_agent._permission_manager is not None
        assert isinstance(mock_code_agent._permission_manager, PermissionManager)

    @pytest.mark.asyncio
    async def test_spawn_subagent_invalid_type(self, mock_code_agent):
        """Test spawning with invalid subagent type."""
        with pytest.raises(ValueError, match="Unknown subagent type"):
            await mock_code_agent.spawn_subagent(
                prompt="Test task",
                subagent_type="invalid_type",
            )

    def test_list_running_subagents_empty(self, mock_code_agent):
        """Test listing running subagents when none exist."""
        running = mock_code_agent.list_running_subagents()
        assert running == []

    def test_list_completed_subagents_empty(self, mock_code_agent):
        """Test listing completed subagents when none exist."""
        completed = mock_code_agent.list_completed_subagents()
        assert completed == []


# =============================================================================
# TestEndToEnd - End-to-end integration tests
# =============================================================================


class TestSubagentE2E:
    """End-to-end tests for subagent system."""

    @pytest.mark.asyncio
    async def test_explorer_subagent_flow(self, mock_llm_client, mock_tool_registry):
        """Test complete explorer subagent flow."""
        # Configure LLM to return exploration result
        mock_llm_client.responses = [
            "Found 5 Python files in the src directory:\n"
            "- src/main.py\n"
            "- src/utils.py\n"
            "- src/config.py\n"
            "- src/models.py\n"
            "- src/api.py"
        ]

        result = await subagent_task(
            prompt="Find all Python files in the src directory",
            subagent_type="explorer",
            description="Explore src",
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        assert result["status"] == "completed"
        assert "agent_id" in result
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_reviewer_subagent_flow(self, mock_llm_client, mock_tool_registry):
        """Test complete reviewer subagent flow."""
        mock_llm_client.responses = [
            "Code review complete:\n"
            "- No security issues found\n"
            "- Suggest adding docstrings\n"
            "- Consider using type hints"
        ]

        result = await subagent_task(
            prompt="Review the auth.py module for security issues",
            subagent_type="reviewer",
            description="Review auth",
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        assert result["status"] == "completed"
        assert "agent_id" in result

    @pytest.mark.asyncio
    async def test_background_subagent_lifecycle(self, mock_llm_client, mock_tool_registry):
        """Test background subagent complete lifecycle."""
        # Spawn background subagent
        result = await subagent_task(
            prompt="Research best practices",
            subagent_type="researcher",
            description="Research",
            run_in_background=True,
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        assert result["status"] == "running"
        agent_id = result["agent_id"]

        # Wait for completion
        await asyncio.sleep(0.2)

        # Check result
        final_result = await get_subagent_result(agent_id=agent_id)
        # Result should be available (either still running or completed)
        assert final_result is not None

    @pytest.mark.asyncio
    async def test_subagent_cancellation_lifecycle(
        self, mock_tool_registry
    ):
        """Test subagent cancellation lifecycle."""
        # Use slow LLM
        slow_llm = MockLLMClient(["Slow processing..."])

        # Spawn background subagent
        result = await subagent_task(
            prompt="Long running task",
            subagent_type="coder",
            description="Long task",
            run_in_background=True,
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=slow_llm,
        )

        agent_id = result["agent_id"]

        # Cancel it
        cancel_result = await cancel_subagent(agent_id=agent_id)

        # Should be cancelled or not found (if completed quickly)
        assert cancel_result["status"] in ["cancelled", "not_found"]

    @pytest.mark.asyncio
    async def test_list_subagents_operation(self, mock_llm_client, mock_tool_registry):
        """Test listing all subagents."""
        # Spawn a few subagents
        await subagent_task(
            prompt="Task 1",
            subagent_type="explorer",
            description="Task 1",
            run_in_background=True,
            workspace=Path("/tmp"),
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client,
        )

        await asyncio.sleep(0.1)

        # List subagents
        result = await list_subagents()

        assert "running" in result
        assert "completed" in result
        assert "running_count" in result
        assert "completed_count" in result
        assert isinstance(result["running"], list)
        assert isinstance(result["completed"], list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
