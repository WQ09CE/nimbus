"""Read tool for reading file contents.

This module provides the Read tool for AgentOS.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from nimbus.tools.sandbox import Sandbox, SandboxError

# Maximum line length before truncation
MAX_LINE_LENGTH = 2000

# Default limit for lines to read
DEFAULT_LIMIT = 2000

# Binary detection: check for null bytes in first N bytes
BINARY_CHECK_BYTES = 8192


def _is_binary_file(file_path: Path) -> bool:
    """Check if a file is binary by looking for null bytes."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(BINARY_CHECK_BYTES)
            return b"\x00" in chunk
    except OSError:
        return False


def _format_line_number(line_num: int, content: str, max_num_width: int = 5) -> str:
    """Format a line with line number prefix."""
    return f"{line_num:>{max_num_width}} | {content}"


def _read_file_with_encoding(file_path: Path) -> tuple[str, str]:
    """Read file content with encoding fallback."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read(), "utf-8"
    except UnicodeDecodeError:
        pass
    with open(file_path, "r", encoding="latin-1") as f:
        return f.read(), "latin-1"


async def read_file(
    file_path: str,
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    start_line: Optional[int] = None,
    workspace: Path | None = None,
    **kwargs: Any,
) -> str:
    """Read file contents with line numbers.

    Args:
        file_path: Absolute or relative path to the file to read.
        offset: Starting line number (0-based). Defaults to 0.
        limit: Maximum number of lines to read. Defaults to 2000.
        start_line: Alternative to offset (1-based). If provided, overrides offset.
        workspace: Optional workspace directory for sandbox validation.

    Returns:
        Formatted file contents with line numbers.

    Raises:
        SandboxError: If path escapes workspace.
        FileNotFoundError: If file doesn't exist.
        IsADirectoryError: If path points to a directory.
        ValueError: If path is empty or offset/limit are invalid.
    """
    # Validate parameters
    if not file_path:
        raise ValueError("file_path cannot be empty")

    # Handle start_line alias (1-based -> 0-based offset)
    if start_line is not None:
        if start_line < 1:
            raise ValueError(f"start_line must be positive, got {start_line}")
        offset = start_line - 1

    if offset < 0:
        raise ValueError(f"offset must be non-negative, got {offset}")

    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    # Determine workspace
    path_obj = Path(file_path)
    if workspace is None:
        workspace = path_obj.parent if path_obj.is_absolute() else Path.cwd()

    # Validate path with sandbox
    sandbox = Sandbox(workspace)
    try:
        resolved_path = sandbox.validate(file_path)
    except SandboxError:
        raise
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")

    # Check if it's a directory
    if resolved_path.is_dir():
        raise IsADirectoryError(f"Path is a directory, not a file: {file_path}")

    # Check if binary
    if _is_binary_file(resolved_path):
        return f"[Binary file: {resolved_path.name}]"

    # Read file content
    try:
        content, encoding = _read_file_with_encoding(resolved_path)
    except OSError as e:
        raise OSError(f"Cannot read file '{file_path}': {e}")

    # Handle empty file
    if not content:
        return "[Empty file]"

    # Split into lines
    lines = content.splitlines()
    total_lines = len(lines)

    # Apply offset and limit
    if offset >= total_lines:
        return f"[No content: offset {offset} exceeds file length {total_lines}]"

    selected_lines = lines[offset : offset + limit]
    end_line = min(offset + limit, total_lines)

    # Calculate line number width for alignment
    num_width = 5

    # Format lines with numbers
    formatted_lines = []
    for i, line in enumerate(selected_lines):
        line_num = offset + i + 1  # 1-based line numbers

        # Truncate long lines
        if len(line) > MAX_LINE_LENGTH:
            line = line[:MAX_LINE_LENGTH] + "... [truncated]"

        formatted_lines.append(_format_line_number(line_num, line, num_width))

    result = "\n".join(formatted_lines)

    # Add footer if file was truncated
    if end_line < total_lines:
        result += f"\n\n[Showing lines {offset + 1}-{end_line} of {total_lines} total]"

    return result


# Tool Definition
READ_TOOL: Dict[str, Any] = {
    "name": "Read",
    "description": "Read file contents with optional line range. Returns content with line numbers.",
    "function": read_file,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "description": "Starting line number (0-based). Defaults to 0.",
                "default": 0,
            },
            "start_line": {
                "type": "integer",
                "description": "Starting line number (1-based). Alternative to offset.",
            },
            "limit": {
                "type": "integer",
                "description": f"Maximum lines to read. Defaults to {DEFAULT_LIMIT}.",
                "default": DEFAULT_LIMIT,
            },
        },
        "required": ["file_path"],
    },
}
