"""Tests for nimbus.tools.sandbox module."""

import os
import tempfile
from pathlib import Path

import pytest

from nimbus.tools.sandbox import Sandbox, SandboxError


class TestSandbox:
    """Tests for Sandbox class."""

    def test_init_with_valid_directory(self, tmp_path):
        """Test creating sandbox with valid directory."""
        sandbox = Sandbox(tmp_path)
        assert sandbox.workspace == tmp_path.resolve()

    def test_init_with_nonexistent_directory(self):
        """Test that non-existent directory raises ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            Sandbox(Path("/nonexistent/directory/path"))

    def test_init_with_file(self, tmp_path):
        """Test that file path raises ValueError."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("content")

        with pytest.raises(ValueError, match="not a directory"):
            Sandbox(file_path)

    def test_validate_relative_path(self, tmp_path):
        """Test validating a relative path within workspace."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        sandbox = Sandbox(tmp_path)
        result = sandbox.validate("test.txt")

        assert result == test_file.resolve()

    def test_validate_absolute_path_within_workspace(self, tmp_path):
        """Test validating an absolute path within workspace."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        sandbox = Sandbox(tmp_path)
        result = sandbox.validate(str(test_file))

        assert result == test_file.resolve()

    def test_validate_nested_path(self, tmp_path):
        """Test validating a nested path."""
        nested_dir = tmp_path / "subdir"
        nested_dir.mkdir()
        test_file = nested_dir / "test.txt"
        test_file.write_text("content")

        sandbox = Sandbox(tmp_path)
        result = sandbox.validate("subdir/test.txt")

        assert result == test_file.resolve()

    def test_validate_path_escape_with_dotdot(self, tmp_path):
        """Test that .. escaping workspace raises SandboxError."""
        sandbox = Sandbox(tmp_path)

        with pytest.raises(SandboxError, match="escapes"):
            sandbox.validate("../etc/passwd")

    def test_validate_path_escape_absolute(self, tmp_path):
        """Test that absolute path outside workspace raises SandboxError."""
        sandbox = Sandbox(tmp_path)

        with pytest.raises(SandboxError, match="escapes"):
            sandbox.validate("/etc/passwd")

    def test_validate_nonexistent_file(self, tmp_path):
        """Test that non-existent file raises FileNotFoundError."""
        sandbox = Sandbox(tmp_path)

        with pytest.raises(FileNotFoundError, match="does not exist"):
            sandbox.validate("nonexistent.txt")

    def test_validate_nonexistent_file_must_exist_false(self, tmp_path):
        """Test that non-existent file is allowed with must_exist=False."""
        sandbox = Sandbox(tmp_path)
        result = sandbox.validate("nonexistent.txt", must_exist=False)

        assert result == (tmp_path / "nonexistent.txt").resolve()

    def test_validate_empty_path(self, tmp_path):
        """Test that empty path raises ValueError."""
        sandbox = Sandbox(tmp_path)

        with pytest.raises(ValueError, match="cannot be empty"):
            sandbox.validate("")

    def test_is_safe_valid_path(self, tmp_path):
        """Test is_safe returns True for valid path."""
        sandbox = Sandbox(tmp_path)

        assert sandbox.is_safe("valid_path.txt") is True
        assert sandbox.is_safe("subdir/file.txt") is True

    def test_is_safe_escaping_path(self, tmp_path):
        """Test is_safe returns False for escaping path."""
        sandbox = Sandbox(tmp_path)

        assert sandbox.is_safe("../escape.txt") is False
        assert sandbox.is_safe("/etc/passwd") is False

    def test_is_safe_empty_path(self, tmp_path):
        """Test is_safe returns False for empty path."""
        sandbox = Sandbox(tmp_path)
        assert sandbox.is_safe("") is False

    def test_normalize_relative_path(self, tmp_path):
        """Test normalize with relative path."""
        sandbox = Sandbox(tmp_path)
        result = sandbox.normalize("test.txt")

        assert result == (tmp_path / "test.txt").resolve()

    def test_normalize_absolute_path(self, tmp_path):
        """Test normalize with absolute path."""
        sandbox = Sandbox(tmp_path)
        result = sandbox.normalize("/some/absolute/path")

        assert result == Path("/some/absolute/path").resolve()

    def test_normalize_with_dotdot(self, tmp_path):
        """Test normalize resolves .. (without blocking)."""
        sandbox = Sandbox(tmp_path)
        # normalize doesn't validate, just resolves
        result = sandbox.normalize("subdir/../file.txt")

        assert result == (tmp_path / "file.txt").resolve()

    def test_relative_path_from_absolute(self, tmp_path):
        """Test getting relative path from absolute path."""
        test_file = tmp_path / "subdir" / "test.txt"

        sandbox = Sandbox(tmp_path)
        result = sandbox.relative_path(str(test_file))

        assert result == Path("subdir/test.txt")

    def test_relative_path_from_relative(self, tmp_path):
        """Test getting relative path from relative path."""
        sandbox = Sandbox(tmp_path)
        result = sandbox.relative_path("subdir/test.txt")

        assert result == Path("subdir/test.txt")

    def test_relative_path_escape_raises(self, tmp_path):
        """Test that relative_path raises for escaping paths."""
        sandbox = Sandbox(tmp_path)

        with pytest.raises(SandboxError):
            sandbox.relative_path("../escape.txt")

    def test_repr(self, tmp_path):
        """Test string representation."""
        sandbox = Sandbox(tmp_path)
        repr_str = repr(sandbox)

        assert "Sandbox" in repr_str
        assert str(tmp_path) in repr_str

    @pytest.mark.skipif(os.name == 'nt', reason="Symlinks require admin on Windows")
    def test_symlink_within_workspace(self, tmp_path):
        """Test that symlinks within workspace are allowed."""
        real_file = tmp_path / "real.txt"
        real_file.write_text("content")
        symlink = tmp_path / "link.txt"
        symlink.symlink_to(real_file)

        sandbox = Sandbox(tmp_path)
        result = sandbox.validate("link.txt")

        # Should resolve to the real file
        assert result == real_file.resolve()

    @pytest.mark.skipif(os.name == 'nt', reason="Symlinks require admin on Windows")
    def test_symlink_escaping_workspace(self, tmp_path):
        """Test that symlinks escaping workspace are blocked."""
        # Create a file outside workspace
        with tempfile.NamedTemporaryFile(delete=False) as f:
            external_file = Path(f.name)
            external_file.write_text("external content")

        try:
            symlink = tmp_path / "escape_link.txt"
            symlink.symlink_to(external_file)

            sandbox = Sandbox(tmp_path)

            with pytest.raises(SandboxError, match="escapes"):
                sandbox.validate("escape_link.txt")
        finally:
            external_file.unlink()


class TestSandboxError:
    """Tests for SandboxError class."""

    def test_error_attributes(self, tmp_path):
        """Test error has correct attributes."""
        error = SandboxError("../escape", tmp_path, "Custom message")

        assert error.path == "../escape"
        assert error.workspace == tmp_path
        assert error.message == "Custom message"

    def test_error_default_message(self, tmp_path):
        """Test error generates default message."""
        error = SandboxError("../escape", tmp_path)

        assert "../escape" in str(error)
        assert str(tmp_path) in str(error)
        assert "escapes" in str(error)
