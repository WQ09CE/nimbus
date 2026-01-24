"""Sandbox utilities for secure file access.

This module provides a Sandbox class that restricts file access to a specified
workspace directory, preventing directory traversal attacks and unauthorized
file access.

Example:
    >>> sandbox = Sandbox(Path("/home/user/project"))
    >>> sandbox.validate("src/main.py")  # OK - returns absolute path
    Path('/home/user/project/src/main.py')
    >>> sandbox.validate("../etc/passwd")  # Raises SandboxError
    SandboxError: Path escapes sandbox
"""

from pathlib import Path
from typing import Union


class SandboxError(Exception):
    """Raised when a path escapes the sandbox.

    This exception is raised when a file access attempt would leave the
    designated workspace directory, indicating a potential security violation.

    Attributes:
        path: The path that caused the violation.
        workspace: The sandbox workspace directory.
        message: Description of the violation.
    """

    def __init__(self, path: Union[str, Path], workspace: Path, message: str = ""):
        self.path = path
        self.workspace = workspace
        self.message = message or f"Path '{path}' escapes sandbox '{workspace}'"
        super().__init__(self.message)


class Sandbox:
    """Sandbox for restricting file access to workspace.

    The Sandbox class provides secure file path validation that prevents
    directory traversal attacks by ensuring all paths resolve within a
    designated workspace directory.

    Features:
        - Resolves relative paths against workspace
        - Follows and validates symlinks
        - Detects directory traversal attempts (../)
        - Validates file existence (when required)

    Attributes:
        workspace: The root directory for all file operations.

    Example:
        >>> sandbox = Sandbox(Path("/project"))
        >>> path = sandbox.validate("src/utils.py")  # Safe path
        >>> sandbox.validate("../../etc/passwd")  # Raises SandboxError
    """

    def __init__(self, workspace: Path) -> None:
        """Initialize sandbox with workspace root.

        Args:
            workspace: The root directory to use as the sandbox boundary.
                       Will be resolved to an absolute path.

        Raises:
            ValueError: If workspace doesn't exist or isn't a directory.
        """
        self.workspace = workspace.resolve()
        if not self.workspace.exists():
            raise ValueError(f"Workspace does not exist: {workspace}")
        if not self.workspace.is_dir():
            raise ValueError(f"Workspace is not a directory: {workspace}")

    def validate(self, path: Union[str, Path], must_exist: bool = True) -> Path:
        """Validate and normalize path within workspace.

        Resolves the given path to an absolute path and ensures it falls
        within the sandbox workspace. Optionally checks for file existence.

        Args:
            path: Relative or absolute path to validate.
            must_exist: If True, raises FileNotFoundError if path doesn't exist.
                       If False, only validates that path would be within sandbox.

        Returns:
            Resolved absolute path within workspace.

        Raises:
            SandboxError: If the resolved path escapes the workspace.
            FileNotFoundError: If must_exist=True and the path doesn't exist.
            ValueError: If path is empty.

        Example:
            >>> sandbox = Sandbox(Path("/project"))
            >>> sandbox.validate("src/main.py")
            Path('/project/src/main.py')
            >>> sandbox.validate("../secret")  # Raises SandboxError
        """
        if not path:
            raise ValueError("Path cannot be empty")

        # Convert to Path object
        path_obj = Path(path) if isinstance(path, str) else path

        # Resolve relative paths against workspace
        if not path_obj.is_absolute():
            path_obj = self.workspace / path_obj

        # Resolve to handle .. and symlinks
        try:
            resolved = path_obj.resolve()
        except OSError as e:
            raise SandboxError(
                path,
                self.workspace,
                f"Cannot resolve path '{path}': {e}",
            )

        # Check if within workspace
        # Use try/except for relative_to check
        try:
            resolved.relative_to(self.workspace)
        except ValueError:
            raise SandboxError(
                path,
                self.workspace,
                f"Path '{path}' resolves to '{resolved}' which escapes workspace '{self.workspace}'",
            )

        # Check existence if required
        if must_exist and not resolved.exists():
            raise FileNotFoundError(
                f"Path does not exist: {resolved}"
            )

        return resolved

    def is_safe(self, path: Union[str, Path]) -> bool:
        """Check if path is within workspace (doesn't check existence).

        A convenience method that returns a boolean instead of raising
        exceptions. Useful for quick safety checks.

        Args:
            path: Relative or absolute path to check.

        Returns:
            True if the path would resolve within the workspace, False otherwise.

        Example:
            >>> sandbox = Sandbox(Path("/project"))
            >>> sandbox.is_safe("src/main.py")
            True
            >>> sandbox.is_safe("../secret")
            False
        """
        try:
            self.validate(path, must_exist=False)
            return True
        except (SandboxError, ValueError, OSError):
            return False

    def normalize(self, path: Union[str, Path]) -> Path:
        """Normalize path relative to workspace without full validation.

        Resolves relative paths against workspace without checking
        workspace boundaries or file existence. Useful for constructing
        paths that will be validated later.

        Args:
            path: Relative or absolute path to normalize.

        Returns:
            Resolved absolute path (may be outside workspace).

        Warning:
            This method does NOT validate security constraints.
            Use validate() for secure path handling.

        Example:
            >>> sandbox = Sandbox(Path("/project"))
            >>> sandbox.normalize("src/main.py")
            Path('/project/src/main.py')
        """
        path_obj = Path(path) if isinstance(path, str) else path

        if path_obj.is_absolute():
            return path_obj.resolve()

        return (self.workspace / path_obj).resolve()

    def relative_path(self, path: Union[str, Path]) -> Path:
        """Get path relative to workspace.

        Converts an absolute path to a path relative to the workspace.
        Validates that the path is within the workspace.

        Args:
            path: Absolute or relative path.

        Returns:
            Path relative to workspace.

        Raises:
            SandboxError: If path escapes workspace.

        Example:
            >>> sandbox = Sandbox(Path("/project"))
            >>> sandbox.relative_path("/project/src/main.py")
            Path('src/main.py')
        """
        validated = self.validate(path, must_exist=False)
        return validated.relative_to(self.workspace)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"Sandbox(workspace={self.workspace!r})"
