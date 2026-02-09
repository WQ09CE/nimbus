"""Write Tool - File writing with auto directory creation

Based on pi-coding-agent implementation.
Automatically creates parent directories using mkdir -p logic.

Example:
    >>> result = await write_file("new/path/file.txt", "content")
    >>> print(result)
    Successfully wrote 7 bytes to new/path/file.txt
"""

from pathlib import Path
from typing import Any, List, Optional

from .sandbox import Sandbox, SandboxError


async def write_file(
    file_path: str,
    content: str,
    workspace: Optional[Path] = None,
    allowed_paths: Optional[List[Path]] = None,
    **kwargs: Any,
) -> str:
    """
    Write content to a file. Creates if doesn't exist, overwrites if does.
    Automatically creates parent directories.

    Args:
        file_path: Path to file (relative or absolute)
        content: Content to write
        workspace: Workspace root for relative paths
        allowed_paths: Additional allowed paths outside workspace (e.g. ~/.nimbus/)

    Returns:
        Success message with byte count

    Raises:
        SandboxError: If path escapes workspace and allowed_paths
        PermissionError: If cannot write to path
    """
    path_obj = Path(file_path)
    if workspace is None:
        workspace = path_obj.parent if path_obj.is_absolute() else Path.cwd()

    if path_obj.is_absolute():
        resolved_path = path_obj.resolve()

        in_workspace = False
        try:
            resolved_path.relative_to(workspace.resolve())
            in_workspace = True
        except ValueError:
            pass

        in_allowed = False
        if not in_workspace and allowed_paths:
            for ap in allowed_paths:
                try:
                    resolved_path.relative_to(ap.resolve())
                    in_allowed = True
                    break
                except ValueError:
                    continue

        if not in_workspace and not in_allowed:
            raise SandboxError(file_path, workspace, f"Path outside workspace: {file_path}")
    else:
        resolved_path = (workspace / file_path).resolve()

    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        with open(resolved_path, "w", encoding="utf-8") as f:
            f.write(content)

        bytes_count = len(content.encode("utf-8"))

        return f"Successfully wrote {bytes_count} bytes to {file_path}"

    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}")
    except Exception as e:
        raise OSError(f"Failed to write file: {str(e)}")
