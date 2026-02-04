"""Tests for V2 native tools (4 core tools based on pi-coding-agent).

These tests verify that the v2 tools work correctly.
"""

import os
import tempfile
from pathlib import Path

import pytest

from nimbus.tools import (
    bash_command,
    edit_file,
    get_all_tools,
    get_tool,
    get_tool_function,
    iterate_tools,
    read_file,
    register_default_tools,
    write_file,
)


class TestReadTool:
    """Tests for v2 Read tool."""

    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n")
            temp_path = f.name
        yield temp_path
        os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_read_file_basic(self, temp_file):
        """Test basic file reading."""
        workspace = Path(temp_file).parent
        result = await read_file(temp_file, workspace=workspace)

        assert "Line 1" in result
        assert "Line 5" in result

    @pytest.mark.asyncio
    async def test_read_file_with_offset_limit(self, temp_file):
        """Test reading with offset and limit (1-indexed)."""
        workspace = Path(temp_file).parent
        result = await read_file(temp_file, offset=2, limit=2, workspace=workspace)

        assert "Line 2" in result
        assert "Line 3" in result
        # Should not include line 1 or 4+
        assert "Line 1" not in result

    @pytest.mark.asyncio
    async def test_read_file_not_found(self):
        """Test reading non-existent file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with pytest.raises(FileNotFoundError):
                await read_file("nonexistent.txt", workspace=workspace)


class TestBashTool:
    """Tests for v2 Bash tool."""

    @pytest.mark.asyncio
    async def test_bash_simple_command(self):
        """Test simple echo command."""
        result = await bash_command("echo 'Hello World'", workspace=Path.cwd())

        assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_bash_nonzero_exit_returns_output(self):
        """Test command with non-zero exit code returns output."""
        result = await bash_command("ls /nonexistent_dir_12345 2>&1 || true", workspace=Path.cwd())

        assert "No such file" in result or "nonexistent" in result.lower()

    @pytest.mark.asyncio
    async def test_bash_empty_command(self):
        """Test empty command."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await bash_command("", workspace=Path.cwd())

    @pytest.mark.asyncio
    async def test_bash_timeout(self):
        """Test command timeout."""
        import asyncio
        with pytest.raises(asyncio.TimeoutError):
            # Use 1 second timeout - 0.5s may be too fast for process startup
            await bash_command("sleep 10", timeout=1.0, workspace=Path.cwd())

    @pytest.mark.asyncio
    async def test_bash_glob_via_find(self):
        """Test glob functionality via bash find command."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "file1.py").write_text("")
            (workspace / "file2.py").write_text("")
            (workspace / "file3.txt").write_text("")

            result = await bash_command("find . -name '*.py'", workspace=workspace)

            assert "file1.py" in result
            assert "file2.py" in result
            assert "file3.txt" not in result

    @pytest.mark.asyncio
    async def test_bash_grep_via_grep(self):
        """Test grep functionality via bash grep command."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "test.txt").write_text("line with foo\nline without\n")

            result = await bash_command("grep 'foo' test.txt", workspace=workspace)

            assert "foo" in result


class TestWriteTool:
    """Tests for v2 Write tool."""

    @pytest.mark.asyncio
    async def test_write_file_basic(self):
        """Test basic file writing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = str(workspace / "test.txt")
            content = "Hello, World!"

            result = await write_file(file_path, content, workspace=workspace)

            assert "successfully" in result.lower()
            assert (workspace / "test.txt").read_text() == content

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self):
        """Test that write creates parent directories (mkdir -p)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = str(workspace / "subdir" / "nested" / "test.txt")
            content = "Nested content"

            result = await write_file(file_path, content, workspace=workspace)

            assert "successfully" in result.lower()
            assert Path(file_path).exists()
            assert Path(file_path).read_text() == content


class TestEditTool:
    """Tests for v2 Edit tool."""

    @pytest.mark.asyncio
    async def test_edit_simple_replacement(self):
        """Test simple string replacement."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = workspace / "test.py"
            file_path.write_text("def hello():\n    print('Hello')\n")

            result = await edit_file(
                str(file_path),
                old_text="def hello():",
                new_text="def greet():",
                workspace=workspace,
            )

            assert "Successfully" in result
            content = file_path.read_text()
            assert "def greet():" in content
            assert "def hello():" not in content

    @pytest.mark.asyncio
    async def test_edit_not_found(self):
        """Test edit with non-existent search text."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = workspace / "test.py"
            file_path.write_text("def hello():\n    pass\n")

            with pytest.raises(ValueError, match="Could not find"):
                await edit_file(
                    str(file_path),
                    old_text="def nonexistent():",
                    new_text="def replaced():",
                    workspace=workspace,
                )

    @pytest.mark.asyncio
    async def test_edit_backward_compat(self):
        """Test backward compatibility with old_string/new_string."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = workspace / "test.py"
            file_path.write_text("foo = 1\n")

            result = await edit_file(
                str(file_path),
                old_string="foo = 1",
                new_string="bar = 2",
                workspace=workspace,
            )

            assert "Successfully" in result
            assert "bar = 2" in file_path.read_text()


class TestToolRegistry:
    """Tests for tool registry functions."""

    def test_get_all_tools(self):
        """Test getting all tool definitions."""
        tools = get_all_tools()

        # 4 core tools + return_result
        assert len(tools) == 5
        tool_names = [t["name"] for t in tools]
        assert "Read" in tool_names
        assert "Write" in tool_names
        assert "Edit" in tool_names
        assert "Bash" in tool_names
        assert "return_result" in tool_names

    def test_get_tool(self):
        """Test getting a single tool definition."""
        read_tool = get_tool("Read")

        assert read_tool is not None
        assert read_tool["name"] == "Read"
        assert "function" in read_tool
        assert "parameters" in read_tool

    def test_get_tool_not_found(self):
        """Test getting non-existent tool."""
        result = get_tool("NonExistent")
        assert result is None

    def test_get_tool_function(self):
        """Test getting a tool function."""
        func = get_tool_function("Read")

        assert func is not None
        assert callable(func)
        assert func.__name__ == "read_file"

    def test_iterate_tools(self):
        """Test iterating over tools."""
        tools = iterate_tools(workspace=Path.cwd())

        assert len(tools) == 5  # 4 core tools + return_result
        for name, func, desc, params in tools:
            assert isinstance(name, str)
            assert callable(func)
            assert isinstance(desc, str)
            assert isinstance(params, dict)


class TestAgentOSIntegration:
    """Tests for AgentOS integration with v2 tools."""

    @pytest.mark.asyncio
    async def test_register_default_tools(self):
        """Test registering default tools with mock AgentOS."""
        class MockAgentOS:
            def __init__(self):
                self.registered_tools = {}

            def register_tool(self, name, func, description="", parameters=None):
                self.registered_tools[name] = {
                    "func": func,
                    "description": description,
                    "parameters": parameters,
                }

        mock_os = MockAgentOS()
        registered = register_default_tools(mock_os, workspace=Path.cwd())

        assert "Read" in registered
        assert "Write" in registered
        assert "Edit" in registered
        assert "Bash" in registered
        assert "return_result" in registered

        # Verify tools were actually registered
        assert "Read" in mock_os.registered_tools
        assert callable(mock_os.registered_tools["Read"]["func"])

    @pytest.mark.asyncio
    async def test_register_specific_tools(self):
        """Test registering specific tools only."""
        class MockAgentOS:
            def __init__(self):
                self.registered_tools = {}

            def register_tool(self, name, func, description="", parameters=None):
                self.registered_tools[name] = func

        mock_os = MockAgentOS()
        registered = register_default_tools(
            mock_os,
            workspace=Path.cwd(),
            tools=["Read", "Bash"],
        )

        assert registered == ["Read", "Bash"]
        assert "Read" in mock_os.registered_tools
        assert "Bash" in mock_os.registered_tools
        assert "Write" not in mock_os.registered_tools
