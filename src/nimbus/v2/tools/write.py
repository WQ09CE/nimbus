"""V2 Write tool for secure file writing.

This module provides the Write tool in v2 format for AgentOS.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from nimbus.tools.sandbox import Sandbox, SandboxError


async def write_file(
    file_path: str,
    content: str,
    mode: str = "write",
    workspace: Path | None = None,
    **kwargs: Any,
) -> str:
    """Write content to a file.

    Creates or overwrites a file with the specified content. Parent directories
    are created automatically if they don't exist.

    Args:
        file_path: Absolute path to the file to write.
        content: Content to write to the file.
        mode: 'write' (overwrite) or 'append' (add to end). Defaults to 'write'.
        workspace: Optional workspace directory for sandbox validation.

    Returns:
        Success message with file path.

    Raises:
        SandboxError: If path escapes workspace.
        ValueError: If file_path is empty.
        IsADirectoryError: If path points to a directory.
        OSError: If file cannot be written.
    """
    # Validate parameters
    if not file_path:
        raise ValueError("file_path cannot be empty")

    if content is None:
        content = ""

    if mode not in ("write", "append"):
        raise ValueError(f"Invalid mode '{mode}'. Must be 'write' or 'append'.")

    # Determine workspace
    path_obj = Path(file_path)
    if workspace is None:
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
        file_mode = "ab" if mode == "append" else "wb"
        with open(resolved_path, file_mode) as f:
            f.write(content_bytes)
    except OSError as e:
        raise OSError(f"Cannot write to file '{file_path}': {e}")

    action = "appended to" if mode == "append" else "created"
    return f"File {action} successfully at: {resolved_path}"


# V2 Tool Definition
WRITE_TOOL: Dict[str, Any] = {
    "name": "Write",
    "description": "Write or append content to a file. Creates parent directories if needed.",
    "function": write_file,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to write",
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file",
            },
            "mode": {
                "type": "string",
                "description": "Mode: 'write' (overwrite) or 'append'. Defaults to 'write'.",
                "enum": ["write", "append"],
                "default": "write",
            },
        },
        "required": ["file_path", "content"],
    },
}
