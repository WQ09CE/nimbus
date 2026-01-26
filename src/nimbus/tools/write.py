"""File write tool for secure file writing with sandbox validation.

This module provides a tool for writing file contents with automatic
parent directory creation and sandbox-based security validation.

Example:
    >>> from pathlib import Path
    >>> result = await write_file("/project/src/main.py", "print('hello')", workspace=Path("/project"))
    >>> print(result)
    Successfully wrote 14 bytes to /project/src/main.py
"""

from pathlib import Path
from typing import Any, Optional

from .base import ToolParameter, tool
from .sandbox import Sandbox, SandboxError


@tool(
    name="Write",
    description="Write content to a file. Creates parent directories if needed. Overwrites existing files.",
    parameters=[
        ToolParameter(
            "file_path",
            "string",
            "Absolute path to the file to write",
            required=True,
        ),
        ToolParameter(
            "content",
            "string",
            "Content to write to the file",
            required=True,
        ),
    ],
)
async def write_file(
    file_path: str,
    content: str,
    workspace: Optional[Path] = None,
    **kwargs: Any,
) -> str:
    """Write content to a file.

    Creates or overwrites a file with the specified content. Parent directories
    are created automatically if they don't exist.

    Features:
        - Creates parent directories automatically
        - Overwrites existing files
        - UTF-8 encoding
        - Returns byte count confirmation

    Args:
        file_path: Absolute path to the file to write.
        content: Content to write to the file.
        workspace: Optional workspace directory for sandbox validation.
                   If not provided, uses parent directory of file_path.

    Returns:
        Success message with file path and bytes written.

    Raises:
        SandboxError: If path escapes workspace.
        ValueError: If file_path is empty.
        IsADirectoryError: If path points to a directory.
        OSError: If file cannot be written.

    Example:
        >>> result = await write_file("/project/src/new.py", "print('hello')")
        >>> print(result)
        Successfully wrote 14 bytes to /project/src/new.py
    """
    # Validate parameters
    if not file_path:
        raise ValueError("file_path cannot be empty")

    if content is None:
        content = ""

    # Determine workspace
    path_obj = Path(file_path)
    if workspace is None:
        # Use the file's parent directory as workspace (no restriction)
        # In practice, workspace should always be provided for security
        workspace = path_obj.parent if path_obj.is_absolute() else Path.cwd()

    # Validate path with sandbox (allow non-existent paths for writing)
    sandbox = Sandbox(workspace)
    try:
        resolved_path = sandbox.validate(file_path, must_exist=False)
    except SandboxError:
        raise

    # Check if target is a directory
    if resolved_path.exists() and resolved_path.is_dir():
        raise IsADirectoryError(f"Cannot write to directory: {file_path}")

    # Create parent directories if needed
    parent_dir = resolved_path.parent
    if not parent_dir.exists():
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise OSError(f"Cannot create parent directories for '{file_path}': {e}")

    # Write content
    try:
        content_bytes = content.encode("utf-8")
        with open(resolved_path, "wb") as f:
            f.write(content_bytes)
    except OSError as e:
        raise OSError(f"Cannot write to file '{file_path}': {e}")

    return f"File created successfully at: {resolved_path}"
