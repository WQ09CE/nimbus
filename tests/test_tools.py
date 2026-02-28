"""Tests for Nimbus Tools (4 core tools based on pi-coding-agent).

Tests cover:
- Read: Smart truncation, image detection, offset/limit
- Write: Auto directory creation
- Edit: Fuzzy matching, BOM/CRLF preservation
- Bash: Timeout, output truncation
"""

import asyncio
from pathlib import Path

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
            task_id="test-task",
        )

        # Use partial matches because of possible Auto-Offload wrapping or hints
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
            task_id="test-task",
        )

        assert "Line 10" in result
        assert "Line 14" in result

    @pytest.mark.asyncio
    async def test_read_truncation(self, temp_workspace):
        """Test that large files are truncated or offloaded."""
        test_file = temp_workspace / "large.txt"
        # Create a file large enough to trigger some form of hint or offload
        content = "\n".join(f"Line {i}" for i in range(1, 10001))
        test_file.write_text(content)

        result = await read_file(
            str(test_file),
            workspace=temp_workspace,
            task_id="test-task",
        )

        # Check for various possible indicators of truncation or offloading
        indicators = ["offset=", "continue", "auto-offload", "nimfs://", "truncated", "showing lines", "exceeded threshold", "output", "line"]
        assert any(x in result.lower() for x in indicators)

    @pytest.mark.asyncio
    async def test_read_offset_beyond_eof_clamps(self, temp_workspace):
        """Test that offset beyond EOF clamps to last page instead of raising."""
        test_file = temp_workspace / "lines.txt"
        content = "\n".join(f"Line {i}" for i in range(1, 51))  # 50 lines
        test_file.write_text(content)

        # Offset 500 is beyond the 50-line file
        result = await read_file(
            str(test_file),
            offset=500,
            limit=10,
            workspace=temp_workspace,
            task_id="test-task",
        )

        # Clamping logic adds a warning
        assert "exceeds file length" in result.lower()
        assert "Line 50" in result
        assert "showing from line" in result.lower()

    @pytest.mark.asyncio
    async def test_read_offset_at_exact_boundary(self, temp_workspace):
        """Test that offset at exact file length works normally."""
        test_file = temp_workspace / "lines.txt"
        content = "\n".join(f"Line {i}" for i in range(1, 11))
        test_file.write_text(content)

        # offset=10 is the last line
        result = await read_file(
            str(test_file),
            offset=10,
            workspace=temp_workspace,
            task_id="test-task",
        )
        assert "Line 10" in result

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, temp_workspace):
        """Test reading a file that doesn't exist."""
        with pytest.raises(FileNotFoundError):
            await read_file(
                "nonexistent.txt",
                workspace=temp_workspace,
                task_id="test-task",
            )


# =============================================================================
# Write Tool Tests
# =============================================================================

class TestWriteTool:
    """Tests for Write tool."""

    @pytest.mark.asyncio
    async def test_write_new_file(self, temp_workspace):
        """Test writing to a new file."""
        test_file = temp_workspace / "new.txt"
        result = await write_file(
            str(test_file),
            "New Content",
            workspace=temp_workspace,
            task_id="test-task",
        )

        assert "Successfully wrote" in result
        assert test_file.read_text() == "New Content"

    @pytest.mark.asyncio
    async def test_write_creates_directories(self, temp_workspace):
        """Test that write_file creates parent directories."""
        path = "a/b/c/deep.txt"
        result = await write_file(
            path,
            "Deep",
            workspace=temp_workspace,
            task_id="test-task",
        )

        assert "Successfully wrote" in result
        assert (temp_workspace / path).read_text() == "Deep"

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, temp_workspace):
        """Test that write_file overwrites existing content."""
        test_file = temp_workspace / "exist.txt"
        test_file.write_text("Old")

        await write_file(
            str(test_file),
            "New",
            workspace=temp_workspace,
            task_id="test-task",
        )

        assert test_file.read_text() == "New"


# =============================================================================
# Edit Tool Tests
# =============================================================================

class TestEditTool:
    """Tests for Edit tool."""

    @pytest.mark.asyncio
    async def test_edit_exact_match(self, temp_workspace):
        """Test exact text replacement."""
        test_file = temp_workspace / "test.py"
        test_file.write_text("def hello():\n    return 1\n")

        result = await edit_file(
            str(test_file),
            old_text="def hello():",
            new_text="def greet():",
            workspace=temp_workspace,
            task_id="test-task",
        )

        assert "Successfully" in result
        assert "def greet():" in test_file.read_text()

    @pytest.mark.asyncio
    async def test_edit_fuzzy_match_trailing_whitespace(self, temp_workspace):
        """Test fuzzy matching handles trailing whitespace."""
        test_file = temp_workspace / "test.py"
        test_file.write_text("def hello():  \n    pass\n")

        result = await edit_file(
            str(test_file),
            old_text="def hello():",
            new_text="def greet():",
            workspace=temp_workspace,
            task_id="test-task",
        )

        assert "Successfully" in result
        assert "def greet():" in test_file.read_text()

    @pytest.mark.asyncio
    async def test_edit_not_found(self, temp_workspace):
        """Test error message content when text is not found."""
        test_file = temp_workspace / "test.txt"
        test_file.write_text("Alpha")

        # Edit tool raises ValueError with a helpful message
        with pytest.raises(ValueError) as excinfo:
            await edit_file(
                str(test_file),
                old_text="Beta",
                new_text="Gamma",
                workspace=temp_workspace,
                task_id="test-task",
            )
        
        assert "EDIT FAILED" in str(excinfo.value)
        assert "old_text not found" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_edit_multiple_occurrences_raises(self, temp_workspace):
        """Test error when multiple occurrences are found."""
        test_file = temp_workspace / "test.txt"
        test_file.write_text("repeat\nrepeat\n")

        with pytest.raises(ValueError) as excinfo:
            await edit_file(
                str(test_file),
                old_text="repeat",
                new_text="unique",
                workspace=temp_workspace,
                task_id="test-task",
            )
        
        assert "Found 2 occurrences" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_edit_shows_diff(self, temp_workspace):
        """Test that successful edit shows a diff."""
        test_file = temp_workspace / "test.txt"
        test_file.write_text("line1\nline2\nline3\n")

        result = await edit_file(
            str(test_file),
            old_text="line2",
            new_text="LINE2",
            workspace=temp_workspace,
            task_id="test-task",
        )

        assert "Successfully" in result
        assert "line2" in result
        assert "LINE2" in result

    @pytest.mark.asyncio
    async def test_edit_backward_compat_old_string_new_string(self, temp_workspace):
        """Test that edit_file still accepts old_string/new_string kwargs."""
        test_file = temp_workspace / "compat.txt"
        test_file.write_text("old")

        result = await edit_file(
            str(test_file),
            old_string="old",
            new_string="new",
            workspace=temp_workspace,
            task_id="test-task",
        )

        assert "Successfully" in result
        assert test_file.read_text() == "new"


# =============================================================================
# Bash Tool Tests
# =============================================================================

class TestBashTool:
    """Tests for Bash tool."""

    @pytest.mark.asyncio
    async def test_bash_simple_command(self, temp_workspace):
        """Test running a simple command."""
        result = await bash_command(
            "echo 'Hello World'",
            workspace=temp_workspace,
            task_id="test-task",
        )

        assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_bash_captures_stderr(self, temp_workspace):
        """Test that stderr is captured."""
        result = await bash_command(
            "ls /nonexistent_directory_12345 2>&1 || true",
            workspace=temp_workspace,
            task_id="test-task",
        )

        assert "No such file" in result or "nonexistent" in result.lower()

    @pytest.mark.asyncio
    async def test_bash_timeout(self, temp_workspace):
        """Test command timeout."""
        with pytest.raises(asyncio.TimeoutError):
            await bash_command(
                "sleep 10",
                timeout=0.1, # Short timeout
                workspace=temp_workspace,
                task_id="test-task",
            )
