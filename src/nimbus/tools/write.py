"""Write Tool - File writing with auto directory creation

Based on pi-coding-agent implementation.
Automatically creates parent directories using mkdir -p logic.

Example:
    >>> result = await write_file("new/path/file.txt", "content")
    >>> print(result)
    Successfully wrote 7 bytes to new/path/file.txt
"""

from pathlib import Path
from typing import Any, Optional

from .sandbox import Sandbox, SandboxError


async def write_file(
    file_path: str,
    content: str,
    workspace: Optional[Path] = None,
    **kwargs: Any,
) -> str:
    """
    Write content to a file. Creates if doesn't exist, overwrites if does.
    Automatically creates parent directories.

    Args:
        file_path: Path to file (relative or absolute)
        content: Content to write
        workspace: Workspace root for relative paths

    Returns:
        Success message with byte count

    Raises:
        SandboxError: If path escapes workspace
        PermissionError: If cannot write to path
    """
    # Resolve path
    path_obj = Path(file_path)
    if workspace is None:
        workspace = path_obj.parent if path_obj.is_absolute() else Path.cwd()

    # Validate with sandbox (allow non-existing files)
    Sandbox(workspace)
    try:
        # For write, we allow non-existing paths as long as they're within workspace
        if path_obj.is_absolute():
            resolved_path = path_obj
            # Check if it's within workspace
            try:
                resolved_path.relative_to(workspace)
            except ValueError:
                raise SandboxError(file_path, workspace, f"Path outside workspace: {file_path}")
        else:
            resolved_path = workspace / file_path
    except SandboxError:
        raise

    try:
        # Create parent directories
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file
        with open(resolved_path, "w", encoding="utf-8") as f:
            f.write(content)

        bytes_count = len(content.encode("utf-8"))

        return f"Successfully wrote {bytes_count} bytes to {file_path}"

    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}")
    except Exception as e:
        raise OSError(f"Failed to write file: {str(e)}")
