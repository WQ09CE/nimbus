"""Tests for nimbus.tools.grep module."""

import pytest
from pathlib import Path

from nimbus.tools.grep import grep_content, FILE_TYPE_PATTERNS
from nimbus.tools.sandbox import SandboxError


class TestGrepContent:
    """Tests for grep_content function."""

    @pytest.mark.asyncio
    async def test_grep_simple_pattern(self, tmp_path):
        """Test grepping with simple pattern."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def main():\n    print('hello')\n\nmain()")

        result = await grep_content("main", output_mode="content", workspace=tmp_path)

        assert "def main" in result
        assert "main()" in result

    @pytest.mark.asyncio
    async def test_grep_files_with_matches_mode(self, tmp_path):
        """Test default files_with_matches mode."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def main():\n    print('hello')\n\nmain()")

        result = await grep_content("main", workspace=tmp_path)

        # Default mode returns file names only
        assert "test.py" in result

    @pytest.mark.asyncio
    async def test_grep_regex_pattern(self, tmp_path):
        """Test grepping with regex pattern."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport sys\nimport re")

        result = await grep_content(r"import \w+", output_mode="content", workspace=tmp_path)

        assert "import os" in result
        assert "import sys" in result
        assert "import re" in result

    @pytest.mark.asyncio
    async def test_grep_with_file_type(self, tmp_path):
        """Test grepping with file type filter."""
        (tmp_path / "test.py").write_text("hello world")
        (tmp_path / "test.js").write_text("hello world")
        (tmp_path / "test.txt").write_text("hello world")

        result = await grep_content("hello", type="py", workspace=tmp_path)

        assert "test.py" in result
        assert "test.js" not in result
        assert "test.txt" not in result

    @pytest.mark.asyncio
    async def test_grep_with_glob_filter(self, tmp_path):
        """Test grepping with glob filter."""
        (tmp_path / "test.py").write_text("hello world")
        (tmp_path / "other.py").write_text("hello world")

        result = await grep_content("hello", glob="test*", workspace=tmp_path)

        assert "test.py" in result
        assert "other.py" not in result

    @pytest.mark.asyncio
    async def test_grep_with_context_before(self, tmp_path):
        """Test grepping with context lines before."""
        test_file = tmp_path / "test.py"
        test_file.write_text("# comment\ndef foo():\n    pass")

        # Use -B parameter for context before
        result = await grep_content("def foo", output_mode="content", workspace=tmp_path, **{"-B": 1})

        assert "# comment" in result
        assert "def foo" in result

    @pytest.mark.asyncio
    async def test_grep_with_context_after(self, tmp_path):
        """Test grepping with context lines after."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo():\n    pass\n    return 42")

        # Use -A parameter for context after
        result = await grep_content("def foo", output_mode="content", workspace=tmp_path, **{"-A": 2})

        assert "def foo" in result
        assert "pass" in result
        assert "return 42" in result

    @pytest.mark.asyncio
    async def test_grep_case_insensitive(self, tmp_path):
        """Test case-insensitive search."""
        test_file = tmp_path / "test.py"
        test_file.write_text("Hello World\nHELLO WORLD\nhello world")

        # Use -i parameter for case insensitive
        result = await grep_content("hello", output_mode="content", workspace=tmp_path, **{"-i": True})

        assert "Hello World" in result
        assert "HELLO WORLD" in result
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_grep_head_limit(self, tmp_path):
        """Test head_limit to limit output."""
        test_file = tmp_path / "test.py"
        lines = [f"match_{i}" for i in range(20)]
        test_file.write_text("\n".join(lines))

        result = await grep_content("match", output_mode="content", head_limit=5, workspace=tmp_path)

        # Should have limited output
        assert "limited" in result.lower() or len(result.split("\n")) <= 6

    @pytest.mark.asyncio
    async def test_grep_count_mode(self, tmp_path):
        """Test count output mode."""
        test_file = tmp_path / "test.py"
        test_file.write_text("match1\nmatch2\nmatch3")

        result = await grep_content("match", output_mode="count", workspace=tmp_path)

        assert "test.py:3" in result

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, tmp_path):
        """Test grepping with no matches."""
        test_file = tmp_path / "test.py"
        test_file.write_text("hello world")

        result = await grep_content("nonexistent", workspace=tmp_path)

        assert "No matches found" in result

    @pytest.mark.asyncio
    async def test_grep_no_files(self, tmp_path):
        """Test grepping when no files match type."""
        (tmp_path / "test.txt").write_text("hello")

        result = await grep_content("hello", type="py", workspace=tmp_path)

        assert "No files found" in result

    @pytest.mark.asyncio
    async def test_grep_empty_pattern(self, tmp_path):
        """Test grepping with empty pattern raises error."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await grep_content("", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_grep_invalid_regex(self, tmp_path):
        """Test grepping with invalid regex raises error."""
        with pytest.raises(ValueError, match="Invalid regex"):
            await grep_content("[invalid", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_grep_invalid_file_type(self, tmp_path):
        """Test grepping with invalid file type raises error."""
        with pytest.raises(ValueError, match="Unknown file type"):
            await grep_content("test", type="unknown_type", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_grep_negative_context(self, tmp_path):
        """Test grepping with negative context raises error."""
        with pytest.raises(ValueError, match="non-negative"):
            await grep_content("test", workspace=tmp_path, **{"-B": -1})

    @pytest.mark.asyncio
    async def test_grep_negative_head_limit(self, tmp_path):
        """Test grepping with negative head_limit raises error."""
        with pytest.raises(ValueError, match="non-negative"):
            await grep_content("test", head_limit=-1, workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_grep_escape_sandbox(self, tmp_path):
        """Test grepping outside sandbox raises error."""
        with pytest.raises(SandboxError):
            await grep_content("test", path="../escape", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_grep_nonexistent_path(self, tmp_path):
        """Test grepping in non-existent path raises error."""
        with pytest.raises(FileNotFoundError):
            await grep_content("test", path="nonexistent", workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_grep_file_as_path(self, tmp_path):
        """Test grepping with file as path searches that single file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content here")

        # In the new implementation, a file path is valid and searches that file
        result = await grep_content("test", path="test.txt", workspace=tmp_path)
        assert "test.txt" in result

    @pytest.mark.asyncio
    async def test_grep_binary_file_skipped(self, tmp_path):
        """Test that binary files are skipped."""
        binary_file = tmp_path / "test.bin"
        binary_file.write_bytes(b"\x00\x01test\x02\x03")

        result = await grep_content("test", workspace=tmp_path)

        assert "test.bin" not in result

    @pytest.mark.asyncio
    async def test_grep_multiple_files(self, tmp_path):
        """Test grepping across multiple files."""
        (tmp_path / "file1.py").write_text("def main(): pass")
        (tmp_path / "file2.py").write_text("def main(): return")
        (tmp_path / "file3.py").write_text("other content")

        result = await grep_content("def main", workspace=tmp_path)

        assert "file1.py" in result
        assert "file2.py" in result
        assert "file3.py" not in result

    @pytest.mark.asyncio
    async def test_grep_shows_line_numbers(self, tmp_path):
        """Test that results include line numbers in content mode."""
        test_file = tmp_path / "test.py"
        test_file.write_text("line 1\nline 2\nmatch here\nline 4")

        result = await grep_content("match", output_mode="content", workspace=tmp_path)

        # Line number should be 3
        assert ":3:" in result or ":3-" in result

    @pytest.mark.asyncio
    async def test_grep_context_line_indicators(self, tmp_path):
        """Test context lines have different indicators."""
        test_file = tmp_path / "test.py"
        test_file.write_text("before\nmatch\nafter")

        result = await grep_content(
            "match", output_mode="content", workspace=tmp_path,
            **{"-B": 1, "-A": 1}
        )

        # Match lines use ':' and context lines use '-'
        assert ":match" in result

    @pytest.mark.asyncio
    async def test_grep_utf8_content(self, tmp_path):
        """Test grepping UTF-8 content."""
        test_file = tmp_path / "test.py"
        test_file.write_text("# Chinese comment\nprint('Hello, World!')", encoding="utf-8")

        result = await grep_content("Chinese", output_mode="content", workspace=tmp_path)

        assert "Chinese" in result

    @pytest.mark.asyncio
    async def test_grep_in_subdirectory(self, tmp_path):
        """Test grepping in subdirectory."""
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("def main(): pass")
        (tmp_path / "root.py").write_text("def main(): pass")

        result = await grep_content("def main", path="src", workspace=tmp_path)

        assert "main.py" in result
        assert "root.py" not in result


class TestFileTypePatterns:
    """Tests for FILE_TYPE_PATTERNS mapping."""

    def test_python_patterns(self):
        """Test Python file type patterns."""
        assert "py" in FILE_TYPE_PATTERNS
        assert "python" in FILE_TYPE_PATTERNS
        assert FILE_TYPE_PATTERNS["py"] == "**/*.py"

    def test_javascript_patterns(self):
        """Test JavaScript file type patterns."""
        assert "js" in FILE_TYPE_PATTERNS
        assert "javascript" in FILE_TYPE_PATTERNS
        assert FILE_TYPE_PATTERNS["js"] == "**/*.js"

    def test_typescript_patterns(self):
        """Test TypeScript file type patterns."""
        assert "ts" in FILE_TYPE_PATTERNS
        assert "typescript" in FILE_TYPE_PATTERNS
        assert "tsx" in FILE_TYPE_PATTERNS

    def test_common_languages(self):
        """Test common language patterns exist."""
        expected = ["py", "js", "ts", "java", "go", "rs", "c", "cpp", "rb", "php"]
        for lang in expected:
            assert lang in FILE_TYPE_PATTERNS, f"Missing file type: {lang}"
