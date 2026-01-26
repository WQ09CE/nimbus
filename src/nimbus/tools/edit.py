"""File edit tool for precise string replacement editing.

This module provides a tool for editing files by replacing exact string matches,
with support for unique match validation and replace-all functionality.

Example:
    >>> from pathlib import Path
    >>> result = await edit_file(
    ...     "/project/src/main.py",
    ...     old_string="def hello():",
    ...     new_string="def greet():",
    ...     workspace=Path("/project")
    ... )
    >>> print(result)
    Successfully replaced 1 occurrence in /project/src/main.py
"""

from pathlib import Path
from typing import Any, Optional

from .base import ToolParameter, tool
from .sandbox import Sandbox, SandboxError


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
    name="Edit",
    description="Edit a file by replacing exact string matches. Requires old_string to be unique unless replace_all is True.",
    parameters=[
        ToolParameter(
            "file_path",
            "string",
            "Absolute path to the file to edit",
            required=True,
        ),
        ToolParameter(
            "old_string",
            "string",
            "The exact text to replace",
            required=True,
        ),
        ToolParameter(
            "new_string",
            "string",
            "The text to replace with",
            required=True,
        ),
        ToolParameter(
            "replace_all",
            "boolean",
            "Replace all occurrences instead of requiring unique match. Defaults to False.",
            required=False,
            default=False,
        ),
    ],
)
async def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    workspace: Optional[Path] = None,
    **kwargs: Any,
) -> str:
    """Edit a file by replacing exact string matches.

    Performs precise string replacement in a file. By default, requires
    the old_string to appear exactly once in the file (uniqueness check).
    Use replace_all=True to replace all occurrences.

    Features:
        - Exact string matching (no regex)
        - Uniqueness validation (unless replace_all=True)
        - Preserves file encoding (UTF-8 preferred, latin-1 fallback)
        - Atomic write operation

    Args:
        file_path: Absolute path to the file to edit.
        old_string: The exact text to find and replace.
        new_string: The replacement text.
        replace_all: If True, replace all occurrences. If False (default),
                     old_string must appear exactly once in the file.
        workspace: Optional workspace directory for sandbox validation.

    Returns:
        Success message with replacement count.

    Raises:
        SandboxError: If path escapes workspace.
        FileNotFoundError: If file doesn't exist.
        ValueError: If old_string is empty, not found, or not unique (when replace_all=False).
        IsADirectoryError: If path points to a directory.

    Example:
        >>> # Unique replacement (default)
        >>> result = await edit_file("/project/main.py", "def foo():", "def bar():")
        >>> print(result)
        Successfully replaced 1 occurrence in /project/main.py

        >>> # Replace all occurrences
        >>> result = await edit_file("/project/main.py", "old_name", "new_name", replace_all=True)
        >>> print(result)
        Successfully replaced 5 occurrences in /project/main.py
    """
    # Validate parameters
    if not file_path:
        raise ValueError("file_path cannot be empty")

    if not old_string:
        raise ValueError("old_string cannot be empty")

    if old_string == new_string:
        raise ValueError("old_string and new_string cannot be the same")

    # Determine workspace
    path_obj = Path(file_path)
    if workspace is None:
        workspace = path_obj.parent if path_obj.is_absolute() else Path.cwd()

    # Validate path with sandbox
    sandbox = Sandbox(workspace)
    try:
        resolved_path = sandbox.validate(file_path, must_exist=True)
    except SandboxError:
        raise
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")

    # Check if it's a directory
    if resolved_path.is_dir():
        raise IsADirectoryError(f"Path is a directory, not a file: {file_path}")

    # Read file content
    try:
        content, encoding = _read_file_with_encoding(resolved_path)
    except OSError as e:
        raise OSError(f"Cannot read file '{file_path}': {e}")

    # Count occurrences
    occurrence_count = content.count(old_string)

    if occurrence_count == 0:
        raise ValueError(
            f"old_string not found in file '{file_path}'. "
            "Make sure the text matches exactly including whitespace and indentation."
        )

    if not replace_all and occurrence_count > 1:
        # Find line numbers of occurrences for helpful error message
        lines = content.splitlines(keepends=True)
        occurrence_lines = []
        char_pos = 0
        for line_num, line in enumerate(lines, 1):
            if old_string in line:
                occurrence_lines.append(line_num)
            char_pos += len(line)

        raise ValueError(
            f"old_string appears {occurrence_count} times in '{file_path}' "
            f"(lines: {', '.join(map(str, occurrence_lines[:5]))}{'...' if len(occurrence_lines) > 5 else ''}). "
            "Either provide more surrounding context to make it unique, "
            "or use replace_all=True to replace all occurrences."
        )

    # Perform replacement
    new_content = content.replace(old_string, new_string)

    # Write back with same encoding
    try:
        with open(resolved_path, "w", encoding=encoding) as f:
            f.write(new_content)
    except OSError as e:
        raise OSError(f"Cannot write to file '{file_path}': {e}")

    return f"The file {resolved_path} has been updated successfully."
