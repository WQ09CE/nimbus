"""File read tool for secure file reading with sandbox validation.

This module provides a tool for reading file contents with line numbers,
encoding detection, and sandbox-based security validation.

Example:
    >>> from pathlib import Path
    >>> result = await read_file("/project/src/main.py", workspace=Path("/project"))
    >>> print(result)
       1→import sys
       2→
       3→def main():
       4→    print("Hello, World!")
"""

from pathlib import Path
from typing import Any, Optional

from .base import ToolParameter, tool
from .sandbox import Sandbox, SandboxError

# Maximum line length before truncation
MAX_LINE_LENGTH = 2000

# Default limit for lines to read
DEFAULT_LIMIT = 2000

# Binary detection: check for null bytes in first N bytes
BINARY_CHECK_BYTES = 8192


def _is_binary_file(file_path: Path) -> bool:
    """Check if a file is binary by looking for null bytes.

    Reads the first BINARY_CHECK_BYTES of the file and checks for
    null bytes, which indicate binary content.

    Args:
        file_path: Path to the file to check.

    Returns:
        True if file appears to be binary, False otherwise.
    """
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(BINARY_CHECK_BYTES)
            return b"\x00" in chunk
    except OSError:
        return False


def _format_line_number(line_num: int, content: str, max_num_width: int = 5) -> str:
    """Format a line with line number prefix.

    Creates output similar to 'cat -n' with right-aligned line numbers.

    Args:
        line_num: The line number (1-based).
        content: The line content.
        max_num_width: Width for line number padding.

    Returns:
        Formatted line with number prefix.

    Example:
        >>> _format_line_number(1, "hello")
        '    1→hello'
        >>> _format_line_number(100, "world")
        '  100→world'
    """
    return f"{line_num:>{max_num_width}}→{content}"


def _read_file_with_encoding(file_path: Path) -> tuple[str, str]:
    """Read file content with encoding fallback.

    Attempts to read file as UTF-8 first, falling back to latin-1
    if UTF-8 decoding fails.

    Args:
        file_path: Path to the file to read.

    Returns:
        Tuple of (content, encoding_used).

    Raises:
        OSError: If file cannot be read.
    """
    # Try UTF-8 first
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read(), "utf-8"
    except UnicodeDecodeError:
        pass

    # Fall back to latin-1 (which accepts any byte sequence)
    with open(file_path, "r", encoding="latin-1") as f:
        return f.read(), "latin-1"


@tool(
    name="Read",
    description="Read file contents with optional line range. Returns content with line numbers.",
    parameters=[
        ToolParameter(
            "file_path",
            "string",
            "Absolute path to the file to read",
            required=True,
        ),
        ToolParameter(
            "offset",
            "integer",
            "Starting line number (0-based). Defaults to 0 (start of file).",
            required=False,
            default=0,
        ),
        ToolParameter(
            "limit",
            "integer",
            f"Maximum lines to read. Defaults to {DEFAULT_LIMIT}.",
            required=False,
            default=DEFAULT_LIMIT,
        ),
    ],
)
async def read_file(
    file_path: str,
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    workspace: Optional[Path] = None,
    **kwargs: Any,
) -> str:
    """Read file contents with line numbers.

    Reads a file and returns its contents formatted with line numbers,
    similar to 'cat -n'. Supports offset and limit for reading specific
    sections of large files.

    Features:
        - Line number display (cat -n style): "   1→content"
        - Binary file detection
        - UTF-8 with fallback to latin-1
        - Line truncation at 2000 chars
        - Handles empty files

    Args:
        file_path: Absolute or relative path to the file to read.
        offset: Starting line number (0-based). Defaults to 0.
        limit: Maximum number of lines to read. Defaults to 2000.
        workspace: Optional workspace directory for sandbox validation.
                   If not provided, uses parent directory of file_path.

    Returns:
        Formatted file contents with line numbers.

    Raises:
        SandboxError: If path escapes workspace.
        FileNotFoundError: If file doesn't exist.
        IsADirectoryError: If path points to a directory.
        ValueError: If path is empty or offset/limit are invalid.

    Example:
        >>> result = await read_file("/project/main.py", offset=0, limit=10)
        >>> print(result)
           1→import sys
           2→
           3→def main():
           4→    pass
    """
    # Validate parameters
    if not file_path:
        raise ValueError("file_path cannot be empty")

    if offset < 0:
        raise ValueError(f"offset must be non-negative, got {offset}")

    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    # Determine workspace
    path_obj = Path(file_path)
    if workspace is None:
        # Use the file's parent directory as workspace (no restriction)
        # In practice, workspace should always be provided for security
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
    # Always use fixed 5-digit width for Claude Code compatibility
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
