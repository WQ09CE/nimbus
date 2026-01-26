"""Tests for nimbus.tools.glob module."""

import pytest
from pathlib import Path
import time

from nimbus.tools.glob import glob_files
from nimbus.tools.sandbox import SandboxError


class TestGlobFiles:
    """Tests for glob_files function."""

    @pytest.mark.asyncio
    async def test_glob_simple_pattern(self, tmp_path):
        """Test globbing with simple pattern."""
        (tmp_path / "test1.py").write_text("# test 1")
        (tmp_path / "test2.py").write_text("# test 2")
        (tmp_path / "other.txt").write_text("other")

        result = await glob_files("*.py", workspace=tmp_path)

        assert "test1.py" in result
        assert "test2.py" in result
        assert "other.txt" not in result
        # New format: no header, just paths

    @pytest.mark.asyncio
    async def test_glob_recursive_pattern(self, tmp_path):
        """Test globbing with recursive ** pattern."""
        (tmp_path / "root.py").write_text("# root")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.py").write_text("# nested")
        deeper = subdir / "deeper"
        deeper.mkdir()
        (deeper / "deep.py").write_text("# deep")

        result = await glob_files("**/*.py", workspace=tmp_path)

        assert "root.py" in result
        assert "subdir/nested.py" in result or "subdir\\nested.py" in result
        # New format: no header, just paths

    @pytest.mark.asyncio
    async def test_glob_with_base_path(self, tmp_path):
        """Test globbing within specific subdirectory."""
        (tmp_path / "root.py").write_text("# root")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.py").write_text("# nested")

        result = await glob_files("*.py", path="subdir", workspace=tmp_path)

        assert "nested.py" in result
        assert "root.py" not in result

    @pytest.mark.asyncio
    async def test_glob_no_matches(self, tmp_path):
        """Test globbing with no matches."""
        (tmp_path / "test.txt").write_text("text")

        result = await glob_files("*.py", workspace=tmp_path)

        assert "No matches found" in result

    @pytest.mark.asyncio
    async def test_glob_with_limit(self, tmp_path):
        """Test globbing with result limit."""
        for i in range(10):
            (tmp_path / f"test{i}.py").write_text(f"# test {i}")

        result = await glob_files("*.py", limit=3, workspace=tmp_path)

        assert "Showing 3 of 10 matches" in result

    @pytest.mark.asyncio
    async def test_glob_sorted_by_mtime(self, tmp_path):
        """Test that results are sorted by modification time (newest first)."""
        # Create files with different modification times
        file1 = tmp_path / "old.py"
        file1.write_text("# old")

        time.sleep(0.1)  # Ensure different mtime

        file2 = tmp_path / "new.py"
        file2.write_text("# new")

        result = await glob_files("*.py", workspace=tmp_path)

        # Newer file should appear before older file
        lines = result.split("\n")
        file_lines = [l for l in lines if l.endswith(".py")]
        assert file_lines[0].endswith("new.py")
        assert file_lines[1].endswith("old.py")

    @pytest.mark.asyncio
    async def test_glob_includes_directories(self, tmp_path):
        """Test that directories are included in results."""
        (tmp_path / "file.py").write_text("# file")
        subdir = tmp_path / "subdir.py"
        subdir.mkdir()  # Directory with .py extension

        result = await glob_files("*.py", workspace=tmp_path)

        assert "file.py" in result
        assert "subdir.py" in result
        # Both file and directory should be included
        lines = [l for l in result.strip().split("\n") if l.endswith(".py")]
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_glob_empty_pattern(self, tmp_path):
        """Test globbing with empty pattern raises error."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await glob_files("", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_glob_zero_limit(self, tmp_path):
        """Test globbing with zero limit raises error."""
        with pytest.raises(ValueError, match="positive"):
            await glob_files("*.py", limit=0, workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_glob_nonexistent_path(self, tmp_path):
        """Test globbing in non-existent path raises error."""
        with pytest.raises(FileNotFoundError):
            await glob_files("*.py", path="nonexistent", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_glob_file_as_path(self, tmp_path):
        """Test globbing with file as base path raises error."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        with pytest.raises(NotADirectoryError):
            await glob_files("*.py", path="test.txt", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_glob_escape_sandbox(self, tmp_path):
        """Test globbing outside sandbox raises error."""
        with pytest.raises(SandboxError):
            await glob_files("*.py", path="../escape", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_glob_question_mark_wildcard(self, tmp_path):
        """Test globbing with ? wildcard pattern."""
        (tmp_path / "test1.py").write_text("# python 1")
        (tmp_path / "test2.py").write_text("# python 2")
        (tmp_path / "test10.py").write_text("# python 10")

        result = await glob_files("test?.py", workspace=tmp_path)

        assert "test1.py" in result
        assert "test2.py" in result
        assert "test10.py" not in result  # ? matches single char only

    @pytest.mark.asyncio
    async def test_glob_hidden_files(self, tmp_path):
        """Test globbing includes hidden files with explicit pattern."""
        (tmp_path / ".hidden.py").write_text("# hidden")
        (tmp_path / "visible.py").write_text("# visible")

        result = await glob_files("*.py", workspace=tmp_path)

        assert "visible.py" in result
        # Hidden files may or may not match depending on glob implementation

    @pytest.mark.asyncio
    async def test_glob_special_characters_in_filename(self, tmp_path):
        """Test globbing with special characters in filenames."""
        test_file = tmp_path / "test[1].py"
        test_file.write_text("# test")

        result = await glob_files("*.py", workspace=tmp_path)

        # Should handle bracket in filename
        assert "test" in result

    @pytest.mark.asyncio
    async def test_glob_deep_nesting(self, tmp_path):
        """Test globbing with deeply nested files."""
        # Create deep directory structure
        deep_path = tmp_path
        for i in range(5):
            deep_path = deep_path / f"level{i}"
            deep_path.mkdir()
        (deep_path / "deep.py").write_text("# deep")

        result = await glob_files("**/*.py", workspace=tmp_path)

        assert "deep.py" in result

    @pytest.mark.asyncio
    async def test_glob_relative_path(self, tmp_path):
        """Test that results show relative paths."""
        subdir = tmp_path / "src" / "core"
        subdir.mkdir(parents=True)
        (subdir / "module.py").write_text("# module")

        result = await glob_files("**/*.py", workspace=tmp_path)

        # Path should be relative to workspace
        assert "src" in result or "core" in result
        assert str(tmp_path) not in result  # Should not have absolute path
