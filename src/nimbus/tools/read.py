"""Read Tool - Enhanced file reading with image support

Based on pi-coding-agent implementation with:
- Text file truncation (2000 lines or 50KB limit)
- Image file support (base64 attachment)
- Smart offset/limit for large files
- 1-indexed line numbers (user-friendly)

Example:
    >>> result = await read_file("README.md")
    >>> print(result)
    Read README.md (150 lines):
    ...

    >>> result = await read_file("large.log", offset=1000, limit=100)
    >>> print(result)
    Read large.log (lines 1000-1100 of 5000):
    ...
"""

import mimetypes
from pathlib import Path
from typing import Any, Optional

from .sandbox import Sandbox, SandboxError
from .utils import DEFAULT_MAX_BYTES, format_size, truncate_head


async def read_file(
    file_path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
    workspace: Optional[Path] = None,
    **kwargs: Any,
) -> str:
    """
    Read the contents of a file. Supports text files and images.

    For text files:
    - Output is truncated to 2000 lines or 50KB (whichever is hit first)
    - Use offset/limit for large files
    - Lines are 1-indexed (user-friendly)

    For images (jpg, png, gif, webp):
    - Returned as base64 attachments

    Args:
        file_path: Path to file (relative or absolute)
        offset: Line number to start from (1-indexed)
        limit: Maximum lines to read
        workspace: Workspace root for relative paths

    Returns:
        File content or image description

    Raises:
        SandboxError: If path escapes workspace
        FileNotFoundError: If file doesn't exist
        ValueError: If offset is out of bounds
    """
    # Resolve path
    path_obj = Path(file_path)
    if workspace is None:
        workspace = path_obj.parent if path_obj.is_absolute() else Path.cwd()

    # Validate with sandbox
    sandbox = Sandbox(workspace)
    try:
        resolved_path = sandbox.validate(file_path)
    except SandboxError:
        raise
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")

    if not resolved_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Ensure types (LLM might pass strings)
    if offset is not None:
        try:
            offset = int(offset)
        except (ValueError, TypeError):
            raise ValueError(f"offset must be an integer, got {offset}")

    if limit is not None:
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            raise ValueError(f"limit must be an integer, got {limit}")

    # Check if it's an image
    mime_type, _ = mimetypes.guess_type(str(resolved_path))
    if mime_type and mime_type.startswith("image/"):
        return await _read_image(resolved_path, mime_type)

    # Read text file
    return await _read_text(resolved_path, offset, limit, file_path)


async def _read_image(file_path: Path, mime_type: str) -> str:
    """Read image file and return description."""
    try:
        size_kb = file_path.stat().st_size / 1024
        return f"Read image file [{mime_type}] ({size_kb:.1f}KB)\n[Image content not shown in text output - would be sent as base64 attachment in full implementation]"
    except Exception as e:
        raise OSError(f"Failed to read image: {str(e)}")


async def _read_text(
    file_path: Path, offset: Optional[int], limit: Optional[int], original_path: str
) -> str:
    """Read text file with optional offset/limit."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8 text: {original_path}")
    except Exception as e:
        raise OSError(f"Failed to read file: {str(e)}")

    lines = content.split("\n")
    total_lines = len(lines)

    # Apply offset (1-indexed → 0-indexed)
    start_line = (offset - 1) if offset else 0
    start_line = max(0, start_line)

    # Check if offset is out of bounds
    if start_line >= total_lines:
        raise ValueError(f"Offset {offset} is beyond end of file ({total_lines} lines total)")

    # Select lines
    if limit is not None:
        end_line = min(start_line + limit, total_lines)
        selected_content = "\n".join(lines[start_line:end_line])
        user_limited_lines = end_line - start_line
    else:
        selected_content = "\n".join(lines[start_line:])
        user_limited_lines = None

    # Apply truncation
    truncation = truncate_head(selected_content)

    # Build output text
    if truncation["first_line_exceeds_limit"]:
        first_line_size = format_size(len(lines[start_line].encode("utf-8")))
        max_size = format_size(DEFAULT_MAX_BYTES)
        return (
            f"[Line {start_line + 1} is {first_line_size}, exceeds {max_size} limit. "
            f"Use bash: sed -n '{start_line + 1}p' {file_path.name} | head -c {DEFAULT_MAX_BYTES}]"
        )

    output_text = truncation["content"]

    if truncation["truncated"]:
        end_line_display = start_line + truncation["output_lines"]
        next_offset = end_line_display + 1

        if truncation["truncated_by"] == "lines":
            output_text += (
                f"\n\n[Showing lines {start_line + 1}-{end_line_display} of {total_lines}. "
                f"Use offset={next_offset} to continue.]"
            )
        else:
            max_size = format_size(DEFAULT_MAX_BYTES)
            output_text += (
                f"\n\n[Showing lines {start_line + 1}-{end_line_display} of {total_lines} "
                f"({max_size} limit). Use offset={next_offset} to continue.]"
            )
    elif user_limited_lines is not None and start_line + user_limited_lines < total_lines:
        remaining = total_lines - (start_line + user_limited_lines)
        next_offset = start_line + user_limited_lines + 1
        output_text += (
            f"\n\n[{remaining} more lines in file. Use offset={next_offset} to continue.]"
        )

    return output_text
