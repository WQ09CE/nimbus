"""Tests for nimbus.tools.batch module."""

import asyncio
import json
import pytest
from pathlib import Path

from nimbus.tools.base import ToolParameter, ToolRegistry, tool
from nimbus.tools.batch import (
    BatchExecutionError,
    batch_tool,
    MAX_CONCURRENT_CALLS,
    DEFAULT_TIMEOUT,
    _execute_single_tool,
)
from nimbus.tools.read import read_file
from nimbus.tools.glob import glob_files


@pytest.fixture
def sample_registry():
    """Create a registry with some sample tools."""
    registry = ToolRegistry()

    @tool(
        name="Echo",
        description="Echo the input message",
        parameters=[
            ToolParameter("message", "string", "Message to echo", required=True),
        ],
    )
    async def echo_tool(message: str, **kwargs) -> str:
        return f"Echo: {message}"

    @tool(
        name="Add",
        description="Add two numbers",
        parameters=[
            ToolParameter("a", "number", "First number", required=True),
            ToolParameter("b", "number", "Second number", required=True),
        ],
    )
    async def add_tool(a: float, b: float, **kwargs) -> str:
        return str(a + b)

    @tool(
        name="SlowTool",
        description="A slow tool that takes time",
        parameters=[
            ToolParameter("delay", "number", "Delay in seconds", required=True),
        ],
    )
    async def slow_tool(delay: float, **kwargs) -> str:
        await asyncio.sleep(delay)
        return f"Completed after {delay}s"

    @tool(
        name="FailingTool",
        description="A tool that always fails",
        parameters=[
            ToolParameter("message", "string", "Error message", required=True),
        ],
    )
    async def failing_tool(message: str, **kwargs) -> str:
        raise ValueError(message)

    registry.register_decorated(echo_tool)
    registry.register_decorated(add_tool)
    registry.register_decorated(slow_tool)
    registry.register_decorated(failing_tool)

    return registry


class TestBatchToolBasic:
    """Basic functionality tests for batch_tool."""

    @pytest.mark.asyncio
    async def test_single_tool_call(self, sample_registry):
        """Test batch with a single tool call."""
        result = await batch_tool(
            tool_calls=[{"name": "Echo", "params": {"message": "Hello"}}],
            tool_registry=sample_registry,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 1/1 tool calls"
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "success"
        assert data["results"][0]["name"] == "Echo"
        assert "Echo: Hello" in data["results"][0]["result"]

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self, sample_registry):
        """Test batch with multiple tool calls."""
        result = await batch_tool(
            tool_calls=[
                {"name": "Echo", "params": {"message": "Hello"}},
                {"name": "Add", "params": {"a": 1, "b": 2}},
                {"name": "Echo", "params": {"message": "World"}},
            ],
            tool_registry=sample_registry,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 3/3 tool calls"
        assert len(data["results"]) == 3

        # Results should be ordered by index
        assert data["results"][0]["index"] == 0
        assert data["results"][1]["index"] == 1
        assert data["results"][2]["index"] == 2

    @pytest.mark.asyncio
    async def test_tool_without_params(self, sample_registry):
        """Test tool call without params field."""

        @tool(
            name="NoParams",
            description="A tool without required params",
            parameters=[],
        )
        async def no_params_tool(**kwargs) -> str:
            return "No params needed"

        sample_registry.register_decorated(no_params_tool)

        result = await batch_tool(
            tool_calls=[{"name": "NoParams"}],
            tool_registry=sample_registry,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 1/1 tool calls"
        assert data["results"][0]["status"] == "success"


class TestBatchToolErrorHandling:
    """Error handling tests for batch_tool."""

    @pytest.mark.asyncio
    async def test_empty_tool_calls(self, sample_registry):
        """Test with empty tool_calls list."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await batch_tool(tool_calls=[], tool_registry=sample_registry)

    @pytest.mark.asyncio
    async def test_invalid_tool_calls_type(self, sample_registry):
        """Test with non-list tool_calls."""
        with pytest.raises(ValueError, match="must be a list"):
            await batch_tool(tool_calls="not a list", tool_registry=sample_registry)

    @pytest.mark.asyncio
    async def test_exceeds_max_concurrent_calls(self, sample_registry):
        """Test exceeding maximum concurrent calls."""
        too_many = [{"name": "Echo", "params": {"message": "x"}} for _ in range(30)]

        with pytest.raises(ValueError, match=f"exceeds limit of {MAX_CONCURRENT_CALLS}"):
            await batch_tool(tool_calls=too_many, tool_registry=sample_registry)

    @pytest.mark.asyncio
    async def test_missing_registry(self):
        """Test with missing tool_registry."""
        with pytest.raises(BatchExecutionError, match="tool_registry not provided"):
            await batch_tool(
                tool_calls=[{"name": "Echo", "params": {"message": "Hello"}}]
            )

    @pytest.mark.asyncio
    async def test_invalid_registry_type(self):
        """Test with invalid registry type."""
        with pytest.raises(BatchExecutionError, match="must be a ToolRegistry"):
            await batch_tool(
                tool_calls=[{"name": "Echo", "params": {"message": "Hello"}}],
                tool_registry="not a registry",
            )

    @pytest.mark.asyncio
    async def test_invalid_timeout(self, sample_registry):
        """Test with invalid timeout value."""
        with pytest.raises(ValueError, match="must be positive"):
            await batch_tool(
                tool_calls=[{"name": "Echo", "params": {"message": "Hello"}}],
                tool_registry=sample_registry,
                timeout=0,
            )

        with pytest.raises(ValueError, match="must be positive"):
            await batch_tool(
                tool_calls=[{"name": "Echo", "params": {"message": "Hello"}}],
                tool_registry=sample_registry,
                timeout=-1,
            )


class TestBatchToolErrorIsolation:
    """Tests for error isolation in batch_tool."""

    @pytest.mark.asyncio
    async def test_single_failure_isolated(self, sample_registry):
        """Test that a single tool failure doesn't affect others."""
        result = await batch_tool(
            tool_calls=[
                {"name": "Echo", "params": {"message": "Before"}},
                {"name": "FailingTool", "params": {"message": "Expected error"}},
                {"name": "Echo", "params": {"message": "After"}},
            ],
            tool_registry=sample_registry,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 2/3 tool calls"

        # First and last should succeed
        assert data["results"][0]["status"] == "success"
        assert data["results"][2]["status"] == "success"

        # Middle should fail
        assert data["results"][1]["status"] == "error"
        assert "Expected error" in data["results"][1]["error"]

    @pytest.mark.asyncio
    async def test_nonexistent_tool(self, sample_registry):
        """Test calling a non-existent tool."""
        result = await batch_tool(
            tool_calls=[
                {"name": "Echo", "params": {"message": "Hello"}},
                {"name": "NonExistentTool", "params": {}},
            ],
            tool_registry=sample_registry,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 1/2 tool calls"

        assert data["results"][0]["status"] == "success"
        assert data["results"][1]["status"] == "error"
        assert "not found" in data["results"][1]["error"]

    @pytest.mark.asyncio
    async def test_invalid_tool_call_format(self, sample_registry):
        """Test with invalid tool_call format in the list."""
        result = await batch_tool(
            tool_calls=[
                {"name": "Echo", "params": {"message": "Valid"}},
                "not a dict",  # Invalid format
                {"params": {}},  # Missing name
            ],
            tool_registry=sample_registry,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 1/3 tool calls"

        assert data["results"][0]["status"] == "success"
        assert data["results"][1]["status"] == "error"
        assert "Invalid tool_call format" in data["results"][1]["error"]
        assert data["results"][2]["status"] == "error"
        assert "Missing required field 'name'" in data["results"][2]["error"]

    @pytest.mark.asyncio
    async def test_invalid_params_format(self, sample_registry):
        """Test with invalid params format."""
        result = await batch_tool(
            tool_calls=[
                {"name": "Echo", "params": "not a dict"},
            ],
            tool_registry=sample_registry,
        )

        data = json.loads(result)
        assert data["results"][0]["status"] == "error"
        assert "Invalid params format" in data["results"][0]["error"]


class TestBatchToolTimeout:
    """Timeout handling tests for batch_tool."""

    @pytest.mark.asyncio
    async def test_tool_timeout(self, sample_registry):
        """Test that slow tools timeout correctly."""
        result = await batch_tool(
            tool_calls=[
                {"name": "SlowTool", "params": {"delay": 5.0}},  # Will timeout
                {"name": "Echo", "params": {"message": "Fast"}},  # Should complete
            ],
            tool_registry=sample_registry,
            timeout=0.1,  # 100ms timeout
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 1/2 tool calls"

        assert data["results"][0]["status"] == "error"
        assert "timed out" in data["results"][0]["error"]

        assert data["results"][1]["status"] == "success"

    @pytest.mark.asyncio
    async def test_custom_timeout(self, sample_registry):
        """Test custom timeout value."""
        result = await batch_tool(
            tool_calls=[
                {"name": "SlowTool", "params": {"delay": 0.05}},  # 50ms
            ],
            tool_registry=sample_registry,
            timeout=1.0,  # 1s timeout - plenty of time
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 1/1 tool calls"
        assert data["results"][0]["status"] == "success"


class TestBatchToolParallelExecution:
    """Tests for parallel execution behavior."""

    @pytest.mark.asyncio
    async def test_parallel_execution_timing(self, sample_registry):
        """Test that tools execute in parallel, not sequentially."""
        import time

        start = time.time()

        result = await batch_tool(
            tool_calls=[
                {"name": "SlowTool", "params": {"delay": 0.1}},
                {"name": "SlowTool", "params": {"delay": 0.1}},
                {"name": "SlowTool", "params": {"delay": 0.1}},
            ],
            tool_registry=sample_registry,
            timeout=5.0,
        )

        elapsed = time.time() - start

        data = json.loads(result)
        assert data["summary"] == "Completed 3/3 tool calls"

        # If parallel, should complete in ~0.1s
        # If sequential, would take ~0.3s
        assert elapsed < 0.25, f"Expected parallel execution, but took {elapsed}s"


class TestBatchToolContext:
    """Tests for context passing."""

    @pytest.mark.asyncio
    async def test_context_passed_to_tools(self):
        """Test that context is passed to each tool."""
        registry = ToolRegistry()

        @tool(
            name="CheckContext",
            description="Check context values",
            parameters=[],
        )
        async def check_context_tool(workspace=None, custom_value=None, **kwargs) -> str:
            return f"workspace={workspace}, custom={custom_value}"

        registry.register_decorated(check_context_tool)

        result = await batch_tool(
            tool_calls=[{"name": "CheckContext", "params": {}}],
            tool_registry=registry,
            workspace=Path("/test/workspace"),
            custom_value="test123",
        )

        data = json.loads(result)
        assert "workspace=/test/workspace" in data["results"][0]["result"]
        assert "custom=test123" in data["results"][0]["result"]

    @pytest.mark.asyncio
    async def test_registry_from_context(self):
        """Test getting registry from context."""
        registry = ToolRegistry()

        @tool(
            name="ContextEcho",
            description="Echo from context registry",
            parameters=[],
        )
        async def context_echo_tool(**kwargs) -> str:
            return "Context works"

        registry.register_decorated(context_echo_tool)

        result = await batch_tool(
            tool_calls=[{"name": "ContextEcho", "params": {}}],
            tool_registry=registry,  # Passed as context kwarg
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 1/1 tool calls"


class TestExecuteSingleTool:
    """Tests for the _execute_single_tool helper function."""

    @pytest.mark.asyncio
    async def test_execute_single_success(self, sample_registry):
        """Test successful single tool execution."""
        result = await _execute_single_tool(
            index=0,
            tool_call={"name": "Echo", "params": {"message": "Test"}},
            registry=sample_registry,
            timeout=5.0,
            context={},
        )

        assert result["index"] == 0
        assert result["name"] == "Echo"
        assert result["status"] == "success"
        assert "Echo: Test" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_single_failure(self, sample_registry):
        """Test failed single tool execution."""
        result = await _execute_single_tool(
            index=5,
            tool_call={"name": "FailingTool", "params": {"message": "Boom"}},
            registry=sample_registry,
            timeout=5.0,
            context={},
        )

        assert result["index"] == 5
        assert result["name"] == "FailingTool"
        assert result["status"] == "error"
        assert "Boom" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_single_timeout(self, sample_registry):
        """Test timeout in single tool execution."""
        result = await _execute_single_tool(
            index=0,
            tool_call={"name": "SlowTool", "params": {"delay": 5.0}},
            registry=sample_registry,
            timeout=0.1,
            context={},
        )

        assert result["status"] == "error"
        assert "timed out" in result["error"]


class TestBatchToolWithRealTools:
    """Integration tests with real tools."""

    @pytest.mark.asyncio
    async def test_with_read_tool(self, tmp_path):
        """Test batch with real Read tool."""
        # Create test files
        file1 = tmp_path / "test1.txt"
        file2 = tmp_path / "test2.txt"
        file1.write_text("Content 1")
        file2.write_text("Content 2")

        registry = ToolRegistry()
        registry.register_decorated(read_file)

        result = await batch_tool(
            tool_calls=[
                {"name": "Read", "params": {"file_path": str(file1)}},
                {"name": "Read", "params": {"file_path": str(file2)}},
            ],
            tool_registry=registry,
            workspace=tmp_path,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 2/2 tool calls"
        assert "Content 1" in data["results"][0]["result"]
        assert "Content 2" in data["results"][1]["result"]

    @pytest.mark.asyncio
    async def test_with_glob_tool(self, tmp_path):
        """Test batch with real Glob tool."""
        # Create test files
        (tmp_path / "file1.py").write_text("python 1")
        (tmp_path / "file2.py").write_text("python 2")
        (tmp_path / "file.txt").write_text("text")

        registry = ToolRegistry()
        registry.register_decorated(glob_files)

        result = await batch_tool(
            tool_calls=[
                {"name": "Glob", "params": {"pattern": "*.py"}},
                {"name": "Glob", "params": {"pattern": "*.txt"}},
            ],
            tool_registry=registry,
            workspace=tmp_path,
        )

        data = json.loads(result)
        assert data["summary"] == "Completed 2/2 tool calls"

        # Check Python files result
        py_result = data["results"][0]["result"]
        assert "file1.py" in py_result
        assert "file2.py" in py_result

        # Check text files result
        txt_result = data["results"][1]["result"]
        assert "file.txt" in txt_result


class TestBatchToolOutputFormat:
    """Tests for output format consistency."""

    @pytest.mark.asyncio
    async def test_json_output_format(self, sample_registry):
        """Test that output is valid JSON with expected structure."""
        result = await batch_tool(
            tool_calls=[
                {"name": "Echo", "params": {"message": "Test"}},
                {"name": "FailingTool", "params": {"message": "Error"}},
            ],
            tool_registry=sample_registry,
        )

        # Should be valid JSON
        data = json.loads(result)

        # Required fields
        assert "results" in data
        assert "summary" in data
        assert isinstance(data["results"], list)
        assert isinstance(data["summary"], str)

        # Each result should have required fields
        for r in data["results"]:
            assert "index" in r
            assert "name" in r
            assert "status" in r
            assert r["status"] in ("success", "error")

            if r["status"] == "success":
                assert "result" in r
            else:
                assert "error" in r

    @pytest.mark.asyncio
    async def test_results_ordered_by_index(self, sample_registry):
        """Test that results are ordered by index."""
        result = await batch_tool(
            tool_calls=[
                {"name": "Echo", "params": {"message": "0"}},
                {"name": "Echo", "params": {"message": "1"}},
                {"name": "Echo", "params": {"message": "2"}},
                {"name": "Echo", "params": {"message": "3"}},
            ],
            tool_registry=sample_registry,
        )

        data = json.loads(result)

        for i, r in enumerate(data["results"]):
            assert r["index"] == i

    @pytest.mark.asyncio
    async def test_dict_result_serialization(self, sample_registry):
        """Test that dict results are properly serialized."""

        @tool(
            name="DictTool",
            description="Returns a dict",
            parameters=[],
        )
        async def dict_tool(**kwargs) -> dict:
            return {"key": "value", "nested": {"a": 1}}

        sample_registry.register_decorated(dict_tool)

        result = await batch_tool(
            tool_calls=[{"name": "DictTool", "params": {}}],
            tool_registry=sample_registry,
        )

        data = json.loads(result)
        # The result should be a JSON string of the dict
        inner_data = json.loads(data["results"][0]["result"])
        assert inner_data["key"] == "value"
        assert inner_data["nested"]["a"] == 1
