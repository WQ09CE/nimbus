"""Tests for Nimbus tools: Write, Edit, Bash, Grep, Glob.

This module provides comprehensive tests for the core code tools.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from nimbus.tools import (
    bash_command,
    edit_file,
    glob_files,
    grep_content,
    read_file,
    write_file,
)
from nimbus.tools.sandbox import SandboxError


class TestWriteTool:
    """Tests for the Write tool."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.mark.asyncio
    async def test_write_new_file(self, temp_workspace):
        """Test writing a new file."""
        file_path = temp_workspace / "test.txt"
        content = "Hello, World!"

        result = await write_file(
            str(file_path),
            content,
            workspace=temp_workspace,
        )

        assert "File created successfully at:" in result
        assert file_path.exists()
        assert file_path.read_text() == content

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, temp_workspace):
        """Test that parent directories are created automatically."""
        file_path = temp_workspace / "subdir" / "nested" / "test.txt"
        content = "Nested content"

        result = await write_file(
            str(file_path),
            content,
            workspace=temp_workspace,
        )

        assert "File created successfully at:" in result
        assert file_path.exists()
        assert file_path.read_text() == content

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, temp_workspace):
        """Test that existing files are overwritten."""
        file_path = temp_workspace / "existing.txt"
        file_path.write_text("Old content")

        new_content = "New content"
        await write_file(
            str(file_path),
            new_content,
            workspace=temp_workspace,
        )

        assert file_path.read_text() == new_content

    @pytest.mark.asyncio
    async def test_write_empty_path_raises(self, temp_workspace):
        """Test that empty file path raises ValueError."""
        with pytest.raises(ValueError, match="file_path cannot be empty"):
            await write_file("", "content", workspace=temp_workspace)

    @pytest.mark.asyncio
    async def test_write_to_directory_raises(self, temp_workspace):
        """Test that writing to a directory raises error."""
        subdir = temp_workspace / "subdir"
        subdir.mkdir()

        with pytest.raises(IsADirectoryError):
            await write_file(str(subdir), "content", workspace=temp_workspace)


class TestEditTool:
    """Tests for the Edit tool."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.mark.asyncio
    async def test_edit_unique_replacement(self, temp_workspace):
        """Test replacing a unique string."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("def hello():\n    pass\n")

        result = await edit_file(
            str(file_path),
            old_string="def hello():",
            new_string="def greet():",
            workspace=temp_workspace,
        )

        assert "has been updated successfully" in result
        assert file_path.read_text() == "def greet():\n    pass\n"

    @pytest.mark.asyncio
    async def test_edit_replace_all(self, temp_workspace):
        """Test replacing all occurrences."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("foo bar foo baz foo\n")

        result = await edit_file(
            str(file_path),
            old_string="foo",
            new_string="qux",
            replace_all=True,
            workspace=temp_workspace,
        )

        assert "has been updated successfully" in result
        assert file_path.read_text() == "qux bar qux baz qux\n"

    @pytest.mark.asyncio
    async def test_edit_non_unique_without_replace_all_raises(self, temp_workspace):
        """Test that non-unique match without replace_all raises error."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("foo bar foo\n")

        with pytest.raises(ValueError, match="appears 2 times"):
            await edit_file(
                str(file_path),
                old_string="foo",
                new_string="baz",
                workspace=temp_workspace,
            )

    @pytest.mark.asyncio
    async def test_edit_string_not_found_raises(self, temp_workspace):
        """Test that missing string raises error."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("hello world\n")

        with pytest.raises(ValueError, match="not found"):
            await edit_file(
                str(file_path),
                old_string="nonexistent",
                new_string="replacement",
                workspace=temp_workspace,
            )

    @pytest.mark.asyncio
    async def test_edit_file_not_found_raises(self, temp_workspace):
        """Test that non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            await edit_file(
                str(temp_workspace / "nonexistent.py"),
                old_string="foo",
                new_string="bar",
                workspace=temp_workspace,
            )

    @pytest.mark.asyncio
    async def test_edit_same_string_raises(self, temp_workspace):
        """Test that same old and new string raises error."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("hello\n")

        with pytest.raises(ValueError, match="cannot be the same"):
            await edit_file(
                str(file_path),
                old_string="hello",
                new_string="hello",
                workspace=temp_workspace,
            )

    @pytest.mark.asyncio
    async def test_edit_with_line_number_prefix(self, temp_workspace):
        """Test that line number prefixes from Read output are cleaned."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("def hello():\n    pass\n")

        # Simulate LLM including line number prefixes from Read output
        result = await edit_file(
            str(file_path),
            old_string="   1→def hello():\n   2→    pass",
            new_string="   1→def greet():\n   2→    return 42",
            workspace=temp_workspace,
        )

        assert "has been updated successfully" in result
        assert file_path.read_text() == "def greet():\n    return 42\n"

    @pytest.mark.asyncio
    async def test_edit_with_escaped_newlines(self, temp_workspace):
        """Test that escaped newlines (\\n) are converted to actual newlines."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("def hello():\n    pass\n")

        # LLM outputs \\n instead of actual newlines
        result = await edit_file(
            str(file_path),
            old_string="def hello():\\n    pass",
            new_string="def greet():\\n    return 42",
            workspace=temp_workspace,
        )

        assert "has been updated successfully" in result
        assert file_path.read_text() == "def greet():\n    return 42\n"

    @pytest.mark.asyncio
    async def test_edit_with_markdown_code_blocks(self, temp_workspace):
        """Test that markdown code blocks are removed."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("def hello():\n    pass\n")

        result = await edit_file(
            str(file_path),
            old_string="```python\ndef hello():\n    pass\n```",
            new_string="```python\ndef greet():\n    return 42\n```",
            workspace=temp_workspace,
        )

        assert "has been updated successfully" in result
        assert file_path.read_text() == "def greet():\n    return 42\n"

    @pytest.mark.asyncio
    async def test_edit_whitespace_normalized_match(self, temp_workspace):
        """Test that trailing whitespace differences are handled."""
        file_path = temp_workspace / "test.py"
        # File has trailing spaces
        file_path.write_text("def hello():   \n    pass   \n")

        # old_string doesn't have trailing spaces
        result = await edit_file(
            str(file_path),
            old_string="def hello():\n    pass",
            new_string="def greet():\n    return 42",
            workspace=temp_workspace,
        )

        assert "has been updated successfully" in result
        # Note: replacement uses original whitespace from file
        content = file_path.read_text()
        assert "def greet()" in content

    @pytest.mark.asyncio
    async def test_edit_fuzzy_match(self, temp_workspace):
        """Test fuzzy matching for slight differences."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("def hello_world():\n    print('Hello World!')\n")

        # old_string has a small typo (< 10% difference)
        result = await edit_file(
            str(file_path),
            old_string="def hello_world():\n    print('Hello World')",  # Missing !
            new_string="def greet():\n    print('Hi')",
            workspace=temp_workspace,
        )

        assert "has been updated successfully" in result
        assert "fuzzy matching" in result
        assert "def greet()" in file_path.read_text()

    @pytest.mark.asyncio
    async def test_edit_fuzzy_match_disabled_for_replace_all(self, temp_workspace):
        """Test that fuzzy matching is disabled for replace_all mode."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("foo bar foo\n")

        # old_string doesn't exist exactly, but would fuzzy match
        with pytest.raises(ValueError, match="not found"):
            await edit_file(
                str(file_path),
                old_string="fo bar",  # Typo, would fuzzy match
                new_string="baz",
                replace_all=True,
                workspace=temp_workspace,
            )

    @pytest.mark.asyncio
    async def test_edit_batch_mode(self, temp_workspace):
        """Test batch edit mode with multiple replacements."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("def foo():\n    pass\n\ndef bar():\n    pass\n")

        result = await edit_file(
            str(file_path),
            edits=[
                {"search": "def foo():", "replace": "def qux():"},
                {"search": "def bar():", "replace": "def baz():"},
            ],
            workspace=temp_workspace,
        )

        assert "2 edits" in result
        content = file_path.read_text()
        assert "def qux():" in content
        assert "def baz():" in content
        assert "def foo():" not in content
        assert "def bar():" not in content

    @pytest.mark.asyncio
    async def test_edit_batch_mode_empty_search_raises(self, temp_workspace):
        """Test that empty search in batch mode raises error."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("hello\n")

        with pytest.raises(ValueError, match="non-empty 'search' field"):
            await edit_file(
                str(file_path),
                edits=[{"search": "", "replace": "world"}],
                workspace=temp_workspace,
            )

    @pytest.mark.asyncio
    async def test_edit_indent_agnostic_match(self, temp_workspace):
        """Test indent-agnostic matching (Tier 2)."""
        file_path = temp_workspace / "test.py"
        # File has 4-space indentation
        file_path.write_text("class Foo:\n    def hello(self):\n        pass\n")

        # old_string has 2-space indentation (wrong), but content matches
        result = await edit_file(
            str(file_path),
            old_string="def hello(self):\n  pass",  # 2-space indent
            new_string="def greet(self):\n  return 42",
            workspace=temp_workspace,
        )

        assert "has been updated successfully" in result
        content = file_path.read_text()
        # Should preserve original 4-space indentation
        assert "    def greet(self):" in content
        assert "        return 42" in content

    @pytest.mark.asyncio
    async def test_edit_no_params_raises(self, temp_workspace):
        """Test that missing both edits and old_string raises error."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("hello\n")

        with pytest.raises(ValueError, match="Either 'edits' array or"):
            await edit_file(
                str(file_path),
                workspace=temp_workspace,
            )

    @pytest.mark.asyncio
    async def test_edit_old_string_without_new_string_raises(self, temp_workspace):
        """Test that old_string without new_string raises error."""
        file_path = temp_workspace / "test.py"
        file_path.write_text("hello\n")

        with pytest.raises(ValueError, match="new_string is required"):
            await edit_file(
                str(file_path),
                old_string="hello",
                workspace=temp_workspace,
            )


class TestBashTool:
    """Tests for the Bash tool."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.mark.asyncio
    async def test_bash_simple_command(self, temp_workspace):
        """Test running a simple command."""
        result = await bash_command(
            "echo 'Hello, World!'",
            workspace=temp_workspace,
        )

        # New format: just stdout for success
        assert "Hello, World!" in result
        # Should not have verbose format for simple success
        assert "Exit code:" not in result

    @pytest.mark.asyncio
    async def test_bash_captures_stderr(self, temp_workspace):
        """Test that stderr is captured."""
        result = await bash_command(
            "ls /nonexistent_directory_12345",
            workspace=temp_workspace,
        )

        # New format shows exit code and stderr for failures
        assert "Exit code:" in result or "stderr:" in result
        # Should have non-zero exit code
        assert "Exit code: 0" not in result

    @pytest.mark.asyncio
    async def test_bash_with_cwd(self, temp_workspace):
        """Test running command in specific directory."""
        subdir = temp_workspace / "subdir"
        subdir.mkdir()

        result = await bash_command(
            "pwd",
            cwd=str(subdir),
            workspace=temp_workspace,
        )

        # New format: just stdout for success
        assert "subdir" in result
        assert "Exit code:" not in result

    @pytest.mark.asyncio
    async def test_bash_empty_command_raises(self, temp_workspace):
        """Test that empty command raises error."""
        with pytest.raises(ValueError, match="command cannot be empty"):
            await bash_command("", workspace=temp_workspace)

    @pytest.mark.asyncio
    async def test_bash_timeout(self, temp_workspace):
        """Test command timeout."""
        with pytest.raises(asyncio.TimeoutError):
            await bash_command(
                "sleep 10",
                timeout=500,  # 500ms timeout - give process time to start
                workspace=temp_workspace,
            )


class TestGrepTool:
    """Tests for the Grep tool."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace with test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)

            # Create test files
            (workspace / "main.py").write_text(
                "def main():\n    print('hello')\n\ndef helper():\n    pass\n"
            )
            (workspace / "utils.py").write_text(
                "def util_func():\n    return 42\n"
            )
            (workspace / "config.json").write_text(
                '{"name": "test", "version": "1.0"}\n'
            )

            yield workspace

    @pytest.mark.asyncio
    async def test_grep_files_with_matches(self, temp_workspace):
        """Test default mode returns file paths."""
        result = await grep_content(
            "def",
            workspace=temp_workspace,
        )

        assert "main.py" in result
        assert "utils.py" in result
        assert "config.json" not in result

    @pytest.mark.asyncio
    async def test_grep_content_mode(self, temp_workspace):
        """Test content mode shows matching lines."""
        result = await grep_content(
            "def main",
            output_mode="content",
            workspace=temp_workspace,
        )

        assert "main.py" in result
        assert "def main():" in result
        assert ":1:" in result  # Line number

    @pytest.mark.asyncio
    async def test_grep_count_mode(self, temp_workspace):
        """Test count mode shows match counts."""
        result = await grep_content(
            "def",
            output_mode="count",
            workspace=temp_workspace,
        )

        assert "main.py:2" in result  # 2 defs in main.py
        assert "utils.py:1" in result  # 1 def in utils.py

    @pytest.mark.asyncio
    async def test_grep_with_context(self, temp_workspace):
        """Test context lines around matches."""
        result = await grep_content(
            "print",
            output_mode="content",
            workspace=temp_workspace,
            **{"-B": 1, "-A": 1},
        )

        assert "def main():" in result  # Line before
        assert "print" in result  # Match
        # Context line indicator
        assert "-" in result or ":" in result

    @pytest.mark.asyncio
    async def test_grep_case_insensitive(self, temp_workspace):
        """Test case insensitive search."""
        result = await grep_content(
            "DEF",
            output_mode="files_with_matches",
            workspace=temp_workspace,
            **{"-i": True},
        )

        assert "main.py" in result
        assert "utils.py" in result

    @pytest.mark.asyncio
    async def test_grep_file_type_filter(self, temp_workspace):
        """Test filtering by file type."""
        result = await grep_content(
            "test",
            type="json",
            workspace=temp_workspace,
        )

        assert "config.json" in result
        assert "main.py" not in result

    @pytest.mark.asyncio
    async def test_grep_glob_filter(self, temp_workspace):
        """Test filtering by glob pattern."""
        result = await grep_content(
            "def",
            glob="main*.py",
            workspace=temp_workspace,
        )

        assert "main.py" in result
        assert "utils.py" not in result

    @pytest.mark.asyncio
    async def test_grep_head_limit(self, temp_workspace):
        """Test limiting output."""
        result = await grep_content(
            "def",
            output_mode="files_with_matches",
            head_limit=1,
            workspace=temp_workspace,
        )

        # Should only have one file
        lines = result.strip().split("\n")
        assert len(lines) == 1


class TestGlobTool:
    """Tests for the Glob tool."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace with test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)

            # Create test files
            (workspace / "main.py").write_text("main")
            (workspace / "utils.py").write_text("utils")
            (workspace / "config.json").write_text("{}")

            subdir = workspace / "src"
            subdir.mkdir()
            (subdir / "app.py").write_text("app")
            (subdir / "models.py").write_text("models")

            yield workspace

    @pytest.mark.asyncio
    async def test_glob_pattern(self, temp_workspace):
        """Test basic glob pattern matching."""
        result = await glob_files(
            "*.py",
            workspace=temp_workspace,
        )

        assert "main.py" in result
        assert "utils.py" in result
        assert "config.json" not in result

    @pytest.mark.asyncio
    async def test_glob_recursive(self, temp_workspace):
        """Test recursive glob pattern."""
        result = await glob_files(
            "**/*.py",
            workspace=temp_workspace,
        )

        assert "main.py" in result
        assert "src/app.py" in result or "src\\app.py" in result

    @pytest.mark.asyncio
    async def test_glob_with_path(self, temp_workspace):
        """Test glob in specific directory."""
        result = await glob_files(
            "*.py",
            path="src",
            workspace=temp_workspace,
        )

        assert "app.py" in result
        assert "main.py" not in result

    @pytest.mark.asyncio
    async def test_glob_limit(self, temp_workspace):
        """Test limiting results."""
        result = await glob_files(
            "**/*.py",
            limit=2,
            workspace=temp_workspace,
        )

        # Count Python files in output (excluding the header and footer lines)
        lines = [l for l in result.split("\n") if l.endswith(".py")]
        assert len(lines) <= 2

    @pytest.mark.asyncio
    async def test_glob_no_matches(self, temp_workspace):
        """Test when no files match."""
        result = await glob_files(
            "*.nonexistent",
            workspace=temp_workspace,
        )

        assert "No matches found" in result

    @pytest.mark.asyncio
    async def test_glob_empty_pattern_raises(self, temp_workspace):
        """Test that empty pattern raises error."""
        with pytest.raises(ValueError, match="pattern cannot be empty"):
            await glob_files("", workspace=temp_workspace)


class TestToolRegistry:
    """Tests for tool registration."""

    def test_register_all_tools(self):
        """Test that all tools can be registered via get_all_tools."""
        from nimbus.tools import get_all_tools, TOOL_FUNCTIONS

        tools = get_all_tools()
        
        # Should have at least 6 tools
        assert len(tools) >= 6
        
        # Verify expected tools are present
        tool_names = [t["name"] for t in tools]
        assert "Read" in tool_names
        assert "Write" in tool_names
        assert "Edit" in tool_names
        assert "Bash" in tool_names
        assert "Glob" in tool_names
        assert "Grep" in tool_names
        
        # Verify each tool has a corresponding function
        for name in tool_names:
            assert name in TOOL_FUNCTIONS or name == "return_result"

    def test_tool_definitions_format(self):
        """Test that tool definitions are valid."""
        from nimbus.tools import ToolRegistry

        registry = ToolRegistry()
        registry.register_decorated(write_file)
        registry.register_decorated(edit_file)
        registry.register_decorated(bash_command)

        definitions = registry.get_definitions(format="claude")

        for defn in definitions:
            assert "name" in defn
            assert "description" in defn
            assert "input_schema" in defn
            assert "type" in defn["input_schema"]
            assert defn["input_schema"]["type"] == "object"

    def test_dangerous_tool_flagged(self):
        """Test that Bash is marked as dangerous."""
        from nimbus.tools import ToolRegistry

        registry = ToolRegistry()
        registry.register_decorated(bash_command)

        assert registry.is_dangerous("Bash")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
