"""Tests for nimbus.tools.search module."""

import pytest
from pathlib import Path

from nimbus.tools.search import code_search
from nimbus.tools.sandbox import SandboxError


class TestCodeSearch:
    """Tests for code_search function."""

    @pytest.mark.asyncio
    async def test_search_basic(self, tmp_path):
        """Test basic text search."""
        (tmp_path / "test.py").write_text("def hello():\n    return 'Hello World'")

        result = await code_search("hello", workspace=tmp_path)

        assert "test.py" in result
        assert "def hello" in result

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self, tmp_path):
        """Test case-insensitive search (default)."""
        (tmp_path / "test.py").write_text("def Hello():\n    pass")

        result = await code_search("hello", workspace=tmp_path)

        assert "Hello" in result

    @pytest.mark.asyncio
    async def test_search_case_sensitive(self, tmp_path):
        """Test case-sensitive search."""
        (tmp_path / "test.py").write_text("def Hello():\n    pass")

        result = await code_search("hello", case_sensitive=True, workspace=tmp_path)

        assert "No matches found" in result

    @pytest.mark.asyncio
    async def test_search_with_type_filter(self, tmp_path):
        """Test search with file type filter."""
        (tmp_path / "test.py").write_text("hello world")
        (tmp_path / "test.js").write_text("hello world")

        result = await code_search("hello", type="py", workspace=tmp_path)

        assert "test.py" in result
        assert "test.js" not in result

    @pytest.mark.asyncio
    async def test_search_with_glob_filter(self, tmp_path):
        """Test search with glob pattern filter."""
        (tmp_path / "test.py").write_text("hello world")
        (tmp_path / "other.py").write_text("hello world")

        result = await code_search("hello", glob="test*", workspace=tmp_path)

        assert "test.py" in result
        assert "other.py" not in result

    @pytest.mark.asyncio
    async def test_search_with_path(self, tmp_path):
        """Test search within specific directory."""
        (tmp_path / "test.py").write_text("hello root")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "test.py").write_text("hello subdir")

        result = await code_search("hello", path="subdir", workspace=tmp_path)

        assert "subdir" in result
        # Root file should not be in results when searching subdir

    @pytest.mark.asyncio
    async def test_search_with_context_lines(self, tmp_path):
        """Test search with context lines."""
        (tmp_path / "test.py").write_text("line1\nline2\nmatch\nline4\nline5")

        result = await code_search("match", context_lines=1, workspace=tmp_path)

        assert "line2" in result
        assert "match" in result
        assert "line4" in result

    @pytest.mark.asyncio
    async def test_search_with_limit(self, tmp_path):
        """Test search with result limit."""
        for i in range(10):
            (tmp_path / f"test{i}.py").write_text(f"match{i}")

        result = await code_search("match", limit=3, workspace=tmp_path)

        # Should have limited results
        assert result.count("test") <= 3

    @pytest.mark.asyncio
    async def test_search_no_matches(self, tmp_path):
        """Test search with no matches."""
        (tmp_path / "test.py").write_text("hello world")

        result = await code_search("nonexistent", workspace=tmp_path)

        assert "No matches found" in result

    @pytest.mark.asyncio
    async def test_search_empty_query_raises(self, tmp_path):
        """Test that empty query raises error."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await code_search("", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_search_invalid_path_raises(self, tmp_path):
        """Test that invalid path raises error."""
        with pytest.raises(FileNotFoundError):
            await code_search("test", path="nonexistent", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_search_escape_sandbox_raises(self, tmp_path):
        """Test that escaping sandbox raises error."""
        with pytest.raises(SandboxError):
            await code_search("test", path="../escape", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_search_regex_pattern(self, tmp_path):
        """Test search with regex pattern."""
        (tmp_path / "test.py").write_text("def foo():\ndef bar():\ndef baz():")

        result = await code_search("def \\w+\\(\\)", workspace=tmp_path)

        assert "foo" in result
        assert "bar" in result
        assert "baz" in result

    @pytest.mark.asyncio
    async def test_search_semantic_mode_not_implemented(self, tmp_path):
        """Test that semantic mode returns not implemented message."""
        (tmp_path / "test.py").write_text("hello world")

        result = await code_search("hello", mode="semantic", workspace=tmp_path)

        assert "not yet implemented" in result.lower()

    @pytest.mark.asyncio
    async def test_search_hybrid_mode(self, tmp_path):
        """Test hybrid mode returns text search results."""
        (tmp_path / "test.py").write_text("hello world")

        result = await code_search("hello", mode="hybrid", workspace=tmp_path)

        assert "test.py" in result
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_search_zero_limit_raises(self, tmp_path):
        """Test that zero limit raises error."""
        with pytest.raises(ValueError, match="positive"):
            await code_search("test", limit=0, workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_search_multiple_files(self, tmp_path):
        """Test search across multiple files."""
        (tmp_path / "a.py").write_text("def foo(): pass")
        (tmp_path / "b.py").write_text("def bar(): pass")
        (tmp_path / "c.py").write_text("something else")

        result = await code_search("def", workspace=tmp_path)

        assert "a.py" in result
        assert "b.py" in result
        # c.py should not be in results (no "def")

    @pytest.mark.asyncio
    async def test_search_nested_directories(self, tmp_path):
        """Test search in nested directories."""
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "deep.py").write_text("deep match")

        result = await code_search("deep match", workspace=tmp_path)

        assert "deep.py" in result
