"""Tests for nimbus.tools.read module."""

import pytest
from pathlib import Path

from nimbus.tools.read import read_file, _is_binary_file, _format_line_number
from nimbus.tools.sandbox import SandboxError


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_format_line_number(self):
        """Test line number formatting."""
        assert _format_line_number(1, "hello") == "    1→hello"
        assert _format_line_number(10, "world") == "   10→world"
        assert _format_line_number(100, "test") == "  100→test"

    def test_format_line_number_custom_width(self):
        """Test line number formatting with custom width."""
        assert _format_line_number(1, "hello", max_num_width=3) == "  1→hello"
        assert _format_line_number(99, "hello", max_num_width=3) == " 99→hello"

    def test_is_binary_file_text(self, tmp_path):
        """Test binary detection for text file."""
        text_file = tmp_path / "test.txt"
        text_file.write_text("Hello, World!")

        assert _is_binary_file(text_file) is False

    def test_is_binary_file_binary(self, tmp_path):
        """Test binary detection for binary file."""
        binary_file = tmp_path / "test.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03\xff\xfe")

        assert _is_binary_file(binary_file) is True

    def test_is_binary_file_nonexistent(self, tmp_path):
        """Test binary detection for non-existent file."""
        assert _is_binary_file(tmp_path / "nonexistent.txt") is False


class TestReadFile:
    """Tests for read_file function."""

    @pytest.mark.asyncio
    async def test_read_simple_file(self, tmp_path):
        """Test reading a simple text file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3")

        result = await read_file(str(test_file), workspace=tmp_path)

        assert "1→line 1" in result
        assert "2→line 2" in result
        assert "3→line 3" in result

    @pytest.mark.asyncio
    async def test_read_with_offset(self, tmp_path):
        """Test reading with line offset."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5")

        result = await read_file(str(test_file), offset=2, workspace=tmp_path)

        assert "1→line 1" not in result
        assert "2→line 2" not in result
        assert "3→line 3" in result
        assert "4→line 4" in result
        assert "5→line 5" in result

    @pytest.mark.asyncio
    async def test_read_with_limit(self, tmp_path):
        """Test reading with line limit."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5")

        result = await read_file(str(test_file), limit=2, workspace=tmp_path)

        assert "1→line 1" in result
        assert "2→line 2" in result
        assert "3→line 3" not in result
        assert "Showing lines 1-2 of 5" in result

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, tmp_path):
        """Test reading with both offset and limit."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("\n".join(f"line {i}" for i in range(1, 11)))

        result = await read_file(str(test_file), offset=3, limit=3, workspace=tmp_path)

        assert "3→line 3" not in result
        assert "4→line 4" in result
        assert "5→line 5" in result
        assert "6→line 6" in result
        assert "7→line 7" not in result

    @pytest.mark.asyncio
    async def test_read_empty_file(self, tmp_path):
        """Test reading an empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("")

        result = await read_file(str(test_file), workspace=tmp_path)

        assert "[Empty file]" in result

    @pytest.mark.asyncio
    async def test_read_binary_file(self, tmp_path):
        """Test reading a binary file."""
        binary_file = tmp_path / "test.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03\xff\xfe")

        result = await read_file(str(binary_file), workspace=tmp_path)

        assert "[Binary file:" in result
        assert "test.bin" in result

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, tmp_path):
        """Test reading non-existent file."""
        with pytest.raises(FileNotFoundError):
            await read_file(str(tmp_path / "nonexistent.txt"), workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_read_directory(self, tmp_path):
        """Test reading a directory raises error."""
        with pytest.raises(IsADirectoryError):
            await read_file(str(tmp_path), workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_read_empty_path(self, tmp_path):
        """Test reading with empty path raises error."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await read_file("", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_read_negative_offset(self, tmp_path):
        """Test reading with negative offset raises error."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        with pytest.raises(ValueError, match="non-negative"):
            await read_file(str(test_file), offset=-1, workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_read_zero_limit(self, tmp_path):
        """Test reading with zero limit raises error."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        with pytest.raises(ValueError, match="positive"):
            await read_file(str(test_file), limit=0, workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_read_offset_beyond_file(self, tmp_path):
        """Test reading with offset beyond file length."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line 1\nline 2")

        result = await read_file(str(test_file), offset=100, workspace=tmp_path)

        assert "[No content: offset 100 exceeds file length" in result

    @pytest.mark.asyncio
    async def test_read_escape_sandbox(self, tmp_path):
        """Test reading file outside sandbox raises error."""
        with pytest.raises(SandboxError):
            await read_file("../etc/passwd", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_read_utf8_file(self, tmp_path):
        """Test reading UTF-8 encoded file."""
        test_file = tmp_path / "unicode.txt"
        test_file.write_text("Hello, 世界! 🌍", encoding="utf-8")

        result = await read_file(str(test_file), workspace=tmp_path)

        assert "世界" in result
        assert "🌍" in result

    @pytest.mark.asyncio
    async def test_read_latin1_fallback(self, tmp_path):
        """Test reading with latin-1 fallback."""
        test_file = tmp_path / "latin1.txt"
        # Write bytes that are valid latin-1 but not valid UTF-8
        test_file.write_bytes(b"Caf\xe9 au lait")

        result = await read_file(str(test_file), workspace=tmp_path)

        # Should contain the content (possibly with different encoding interpretation)
        assert "Caf" in result

    @pytest.mark.asyncio
    async def test_read_long_line_truncation(self, tmp_path):
        """Test that long lines are truncated."""
        test_file = tmp_path / "long.txt"
        long_line = "x" * 3000
        test_file.write_text(long_line)

        result = await read_file(str(test_file), workspace=tmp_path)

        # Line should be truncated at 2000 chars
        assert "[truncated]" in result
        assert len(result.split("→")[1].split("\n")[0]) < 2100

    @pytest.mark.asyncio
    async def test_read_relative_path(self, tmp_path):
        """Test reading with relative path."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        test_file = subdir / "test.txt"
        test_file.write_text("content")

        result = await read_file("subdir/test.txt", workspace=tmp_path)

        assert "content" in result

    @pytest.mark.asyncio
    async def test_read_preserves_whitespace(self, tmp_path):
        """Test that whitespace is preserved in output."""
        test_file = tmp_path / "whitespace.txt"
        test_file.write_text("  indented\n\ttabbed")

        result = await read_file(str(test_file), workspace=tmp_path)

        assert "  indented" in result
        assert "\ttabbed" in result

    @pytest.mark.asyncio
    async def test_read_handles_crlf(self, tmp_path):
        """Test handling Windows-style line endings."""
        test_file = tmp_path / "crlf.txt"
        test_file.write_bytes(b"line 1\r\nline 2\r\n")

        result = await read_file(str(test_file), workspace=tmp_path)

        assert "1→line 1" in result
        assert "2→line 2" in result
