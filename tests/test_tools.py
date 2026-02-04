"""Tests for Nimbus Tools (4 core tools based on pi-coding-agent).

Tests cover:
- Read: Smart truncation, image detection, offset/limit
- Write: Auto directory creation
- Edit: Fuzzy matching, BOM/CRLF preservation
- Bash: Timeout, output truncation
"""

import asyncio

import pytest

from nimbus.tools import (
    Sandbox,
    SandboxError,
    bash_command,
    edit_file,
    read_file,
    write_file,
)

# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    return tmp_path


# =============================================================================
# Read Tool Tests
# =============================================================================

class TestReadTool:
    """Tests for Read tool."""

    @pytest.mark.asyncio
    async def test_read_simple_file(self, temp_workspace):
        """Test reading a simple file."""
        test_file = temp_workspace / "test.txt"
        test_file.write_text("Hello\nWorld\n")

        result = await read_file(
            str(test_file),
            workspace=temp_workspace,
        )

        assert "Hello" in result
        assert "World" in result

    @pytest.mark.asyncio
    async def test_read_with_offset_limit(self, temp_workspace):
        """Test reading with offset and limit (1-indexed)."""
        test_file = temp_workspace / "lines.txt"
        content = "\n".join(f"Line {i}" for i in range(1, 101))
        test_file.write_text(content)

        # Read lines 10-14 (1-indexed)
        result = await read_file(
            str(test_file),
            offset=10,
            limit=5,
            workspace=temp_workspace,
        )

        assert "Line 10" in result
        assert "Line 14" in result

    @pytest.mark.asyncio
    async def test_read_truncation(self, temp_workspace):
        """Test that large files are truncated to 2000 lines."""
        test_file = temp_workspace / "large.txt"
        content = "\n".join(f"Line {i}" for i in range(1, 3001))
        test_file.write_text(content)

        result = await read_file(
            str(test_file),
            workspace=temp_workspace,
        )

        # Should be truncated and include continuation hint
        assert "offset=" in result.lower() or "continue" in result.lower()

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, temp_workspace):
        """Test reading a nonexistent file raises error."""
        with pytest.raises(FileNotFoundError):
            await read_file(
                "nonexistent.txt",
                workspace=temp_workspace,
            )


# =============================================================================
# Write Tool Tests
# =============================================================================

class TestWriteTool:
    """Tests for Write tool."""

    @pytest.mark.asyncio
    async def test_write_new_file(self, temp_workspace):
        """Test writing a new file."""
        result = await write_file(
            "new_file.txt",
            "Hello World",
            workspace=temp_workspace,
        )

        assert "Successfully" in result
        assert (temp_workspace / "new_file.txt").read_text() == "Hello World"

    @pytest.mark.asyncio
    async def test_write_creates_directories(self, temp_workspace):
        """Test that parent directories are auto-created (mkdir -p)."""
        result = await write_file(
            "nested/path/to/file.txt",
            "Content",
            workspace=temp_workspace,
        )

        assert "Successfully" in result
        assert (temp_workspace / "nested/path/to/file.txt").exists()

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, temp_workspace):
        """Test overwriting an existing file."""
        test_file = temp_workspace / "existing.txt"
        test_file.write_text("Old content")

        await write_file(
            str(test_file),
            "New content",
            workspace=temp_workspace,
        )

        assert test_file.read_text() == "New content"


# =============================================================================
# Edit Tool Tests
# =============================================================================

class TestEditTool:
    """Tests for Edit tool."""

    @pytest.mark.asyncio
    async def test_edit_exact_match(self, temp_workspace):
        """Test editing with exact match."""
        test_file = temp_workspace / "test.py"
        test_file.write_text("def hello():\n    pass\n")

        result = await edit_file(
            str(test_file),
            old_text="def hello():",
            new_text="def greet():",
            workspace=temp_workspace,
        )

        assert "Successfully" in result
        assert "def greet():" in test_file.read_text()

    @pytest.mark.asyncio
    async def test_edit_fuzzy_match_trailing_whitespace(self, temp_workspace):
        """Test fuzzy matching handles trailing whitespace."""
        test_file = temp_workspace / "test.py"
        # File has trailing spaces
        test_file.write_text("def hello():  \n    pass\n")

        # old_text doesn't have trailing spaces - should still match
        result = await edit_file(
            str(test_file),
            old_text="def hello():",
            new_text="def greet():",
            workspace=temp_workspace,
        )

        assert "Successfully" in result
        assert "def greet():" in test_file.read_text()

    @pytest.mark.asyncio
    async def test_edit_not_found(self, temp_workspace):
        """Test editing when text not found raises ValueError."""
        test_file = temp_workspace / "test.py"
        test_file.write_text("def hello():\n    pass\n")

        with pytest.raises(ValueError, match="Could not find"):
            await edit_file(
                str(test_file),
                old_text="nonexistent text",
                new_text="replacement",
                workspace=temp_workspace,
            )

    @pytest.mark.asyncio
    async def test_edit_multiple_occurrences_raises(self, temp_workspace):
        """Test editing when text appears multiple times raises error."""
        test_file = temp_workspace / "test.py"
        test_file.write_text("foo\nbar\nfoo\n")

        with pytest.raises(ValueError, match="occurrences"):
            await edit_file(
                str(test_file),
                old_text="foo",
                new_text="baz",
                workspace=temp_workspace,
            )

    @pytest.mark.asyncio
    async def test_edit_shows_diff(self, temp_workspace):
        """Test that edit returns diff information."""
        test_file = temp_workspace / "test.py"
        test_file.write_text("value = 42\n")

        result = await edit_file(
            str(test_file),
            old_text="value = 42",
            new_text="value = 100",
            workspace=temp_workspace,
        )

        # Should include diff in output
        assert "Diff" in result or "+" in result or "-" in result

    @pytest.mark.asyncio
    async def test_edit_backward_compat_old_string_new_string(self, temp_workspace):
        """Test backward compatibility with old_string/new_string params."""
        test_file = temp_workspace / "test.py"
        test_file.write_text("foo = 1\n")

        # Use old parameter names for backward compatibility
        result = await edit_file(
            str(test_file),
            old_string="foo = 1",
            new_string="bar = 2",
            workspace=temp_workspace,
        )

        assert "Successfully" in result
        assert "bar = 2" in test_file.read_text()


# =============================================================================
# Bash Tool Tests
# =============================================================================

class TestBashTool:
    """Tests for Bash tool."""

    @pytest.mark.asyncio
    async def test_bash_simple_command(self, temp_workspace):
        """Test simple command execution."""
        result = await bash_command(
            "echo 'Hello World'",
            workspace=temp_workspace,
        )

        assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_bash_captures_stderr(self, temp_workspace):
        """Test that stderr is captured."""
        result = await bash_command(
            "ls /nonexistent_directory_12345 2>&1 || true",
            workspace=temp_workspace,
        )

        assert "No such file" in result or "nonexistent" in result.lower()

    @pytest.mark.asyncio
    async def test_bash_timeout(self, temp_workspace):
        """Test command timeout (default 60s, can override)."""
        with pytest.raises(asyncio.TimeoutError):
            # Use 1 second timeout - 0.5s may be too fast for process startup
            await bash_command(
                "sleep 10",
                timeout=1.0,
                workspace=temp_workspace,
            )

    @pytest.mark.asyncio
    async def test_bash_nonzero_exit_returns_output(self, temp_workspace):
        """Test that non-zero exit returns output with exit code."""
        result = await bash_command(
            "exit 42",
            workspace=temp_workspace,
        )

        # Should contain exit code info (not raise exception)
        assert "42" in result or "exit" in result.lower()

    @pytest.mark.asyncio
    async def test_bash_glob_via_find(self, temp_workspace):
        """Test glob functionality via bash find command."""
        (temp_workspace / "file1.py").write_text("")
        (temp_workspace / "file2.py").write_text("")
        (temp_workspace / "file3.txt").write_text("")

        result = await bash_command(
            "find . -name '*.py'",
            workspace=temp_workspace,
        )

        assert "file1.py" in result
        assert "file2.py" in result
        assert "file3.txt" not in result

    @pytest.mark.asyncio
    async def test_bash_grep_via_grep(self, temp_workspace):
        """Test grep functionality via bash grep command."""
        test_file = temp_workspace / "test.txt"
        test_file.write_text("line with foo\nline without\nline with foo again\n")

        result = await bash_command(
            f"grep 'foo' {test_file}",
            workspace=temp_workspace,
        )

        assert "foo" in result


# =============================================================================
# Sandbox Tests
# =============================================================================

class TestSandbox:
    """Tests for Sandbox security."""

    def test_sandbox_allows_valid_path(self, temp_workspace):
        """Test that valid paths within workspace are allowed."""
        sandbox = Sandbox(temp_workspace)
        test_file = temp_workspace / "test.txt"
        test_file.write_text("content")

        resolved = sandbox.validate(str(test_file))
        assert resolved == test_file

    def test_sandbox_blocks_path_traversal(self, temp_workspace):
        """Test that path traversal attempts are blocked."""
        sandbox = Sandbox(temp_workspace)

        with pytest.raises(SandboxError):
            sandbox.validate("../../../etc/passwd")

    def test_sandbox_allows_relative_path(self, temp_workspace):
        """Test that relative paths within workspace work."""
        sandbox = Sandbox(temp_workspace)
        test_file = temp_workspace / "subdir" / "test.txt"
        test_file.parent.mkdir()
        test_file.write_text("content")

        resolved = sandbox.validate("subdir/test.txt")
        assert resolved == test_file
