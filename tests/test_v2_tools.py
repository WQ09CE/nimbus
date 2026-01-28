"""Tests for V2 native tools.

These tests verify that the v2 tools work correctly without
requiring the v1 adapter layer.
"""

import pytest
import tempfile
import os
from pathlib import Path

from nimbus.v2.tools import (
    read_file,
    glob_files,
    grep_content,
    bash_command,
    write_file,
    edit_file,
    get_all_tools,
    get_tool,
    get_tool_function,
    register_default_tools,
    iterate_tools,
    ALL_TOOLS,
    TOOL_FUNCTIONS,
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
        # Check line numbers are present
        assert "1" in result

    @pytest.mark.asyncio
    async def test_read_file_with_offset(self, temp_file):
        """Test reading with offset."""
        workspace = Path(temp_file).parent
        result = await read_file(temp_file, offset=2, limit=2, workspace=workspace)

        assert "Line 3" in result
        assert "Line 4" in result
        # Lines 1-2 should not be present
        assert "Line 1" not in result

    @pytest.mark.asyncio
    async def test_read_file_not_found(self):
        """Test reading non-existent file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            # File does not exist but path is within workspace
            with pytest.raises(FileNotFoundError):
                await read_file(str(workspace / "nonexistent.txt"), workspace=workspace)

    @pytest.mark.asyncio
    async def test_read_empty_path(self):
        """Test empty file path."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await read_file("", workspace=Path.cwd())


class TestGlobTool:
    """Tests for v2 Glob tool."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory with files."""
        import tempfile
        temp_dir = tempfile.mkdtemp()
        # Create some test files
        (Path(temp_dir) / "file1.txt").touch()
        (Path(temp_dir) / "file2.py").touch()
        (Path(temp_dir) / "subdir").mkdir()
        (Path(temp_dir) / "subdir" / "file3.py").touch()
        yield temp_dir
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_glob_files_basic(self, temp_dir):
        """Test basic glob pattern."""
        workspace = Path(temp_dir)
        result = await glob_files("*.txt", path=".", workspace=workspace)

        assert "file1.txt" in result
        assert "file2.py" not in result

    @pytest.mark.asyncio
    async def test_glob_files_recursive(self, temp_dir):
        """Test recursive glob pattern."""
        workspace = Path(temp_dir)
        result = await glob_files("**/*.py", path=".", workspace=workspace)

        assert "file2.py" in result
        assert "file3.py" in result

    @pytest.mark.asyncio
    async def test_glob_no_matches(self, temp_dir):
        """Test glob with no matches."""
        workspace = Path(temp_dir)
        result = await glob_files("*.xyz", path=".", workspace=workspace)

        assert "No matches found" in result

    @pytest.mark.asyncio
    async def test_glob_empty_pattern(self, temp_dir):
        """Test empty pattern."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await glob_files("", workspace=Path(temp_dir))


class TestGrepTool:
    """Tests for v2 Grep tool."""

    @pytest.fixture
    def temp_dir_with_content(self):
        """Create a directory with files containing searchable content."""
        import tempfile
        temp_dir = tempfile.mkdtemp()
        # Create files with content
        (Path(temp_dir) / "hello.py").write_text("def hello():\n    print('Hello World')\n")
        (Path(temp_dir) / "world.py").write_text("def world():\n    return 'World'\n")
        yield temp_dir
        import shutil
        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_grep_files_with_matches(self, temp_dir_with_content):
        """Test grep returning file names."""
        workspace = Path(temp_dir_with_content)
        result = await grep_content("def", path=".", workspace=workspace)

        assert "hello.py" in result
        assert "world.py" in result

    @pytest.mark.asyncio
    async def test_grep_content_mode(self, temp_dir_with_content):
        """Test grep in content mode."""
        workspace = Path(temp_dir_with_content)
        result = await grep_content(
            "Hello",
            path=".",
            output_mode="content",
            workspace=workspace
        )

        assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, temp_dir_with_content):
        """Test grep with no matches."""
        workspace = Path(temp_dir_with_content)
        result = await grep_content("NONEXISTENT", path=".", workspace=workspace)

        assert "No matches found" in result

    @pytest.mark.asyncio
    async def test_grep_invalid_regex(self, temp_dir_with_content):
        """Test grep with invalid regex pattern."""
        workspace = Path(temp_dir_with_content)
        with pytest.raises(ValueError, match="Invalid regex"):
            await grep_content("[invalid", path=".", workspace=workspace)


class TestBashTool:
    """Tests for v2 Bash tool."""

    @pytest.mark.asyncio
    async def test_bash_simple_command(self):
        """Test simple echo command."""
        result = await bash_command("echo 'Hello World'", workspace=Path.cwd())

        assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_bash_exit_code(self):
        """Test command with non-zero exit code."""
        result = await bash_command("ls /nonexistent_dir_12345", workspace=Path.cwd())

        assert "Exit code:" in result or "No such file" in result

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
            await bash_command("sleep 10", timeout=100, workspace=Path.cwd())


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
        """Test that write creates parent directories."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = str(workspace / "subdir" / "nested" / "test.txt")
            content = "Nested content"

            result = await write_file(file_path, content, workspace=workspace)

            assert "successfully" in result.lower()
            assert Path(file_path).exists()
            assert Path(file_path).read_text() == content

    @pytest.mark.asyncio
    async def test_write_empty_path(self):
        """Test empty file path."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await write_file("", "content", workspace=Path.cwd())


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
                old_string="def hello():",
                new_string="def greet():",
                workspace=workspace,
            )

            assert "updated successfully" in result.lower()
            content = file_path.read_text()
            assert "def greet():" in content
            assert "def hello():" not in content

    @pytest.mark.asyncio
    async def test_edit_batch_mode(self):
        """Test batch edit mode."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = workspace / "test.py"
            file_path.write_text("def foo():\n    pass\ndef bar():\n    pass\n")

            result = await edit_file(
                str(file_path),
                edits=[
                    {"search": "def foo():", "replace": "def baz():"},
                    {"search": "def bar():", "replace": "def qux():"},
                ],
                workspace=workspace,
            )

            assert "updated" in result.lower()
            content = file_path.read_text()
            assert "def baz():" in content
            assert "def qux():" in content

    @pytest.mark.asyncio
    async def test_edit_not_found(self):
        """Test edit with non-existent search text."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = workspace / "test.py"
            file_path.write_text("def hello():\n    pass\n")

            with pytest.raises(ValueError, match="not found"):
                await edit_file(
                    str(file_path),
                    old_string="def nonexistent():",
                    new_string="def replaced():",
                    workspace=workspace,
                )


class TestToolRegistry:
    """Tests for tool registry functions."""

    def test_get_all_tools(self):
        """Test getting all tool definitions."""
        tools = get_all_tools()

        assert len(tools) >= 6  # Read, Glob, Grep, Bash, Write, Edit
        tool_names = [t["name"] for t in tools]
        assert "Read" in tool_names
        assert "Glob" in tool_names
        assert "Grep" in tool_names
        assert "Bash" in tool_names
        assert "Write" in tool_names
        assert "Edit" in tool_names

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

        assert len(tools) >= 6
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
        # Create a mock AgentOS
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
        assert "Glob" in registered
        assert "Grep" in registered
        assert "Bash" in registered
        assert "Write" in registered
        assert "Edit" in registered

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
            tools=["Read", "Glob"],
        )

        assert registered == ["Read", "Glob"]
        assert "Read" in mock_os.registered_tools
        assert "Glob" in mock_os.registered_tools
        assert "Bash" not in mock_os.registered_tools
