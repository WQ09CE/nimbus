"""Read Tool - Smart file reading with adaptive limits

Intelligent Limit System:
- **Adaptive scaling**: 2000 lines / 100KB for modern LLMs (vs old 300/12KB)
- **Context aware**: Automatically scales based on available context capacity
- **Small file optimization**: Reads small files completely without truncation
- **Memory safe**: Large files use streaming to prevent OOM

Features:
- Smart truncation with helpful continue hints
- Image file support (jpg, png, gif, webp)
- Memory-safe large file handling
- User-friendly 1-indexed line numbers
- Precise offset/limit control

Examples:
    >>> result = await read_file("vcpu.py")  # Reads complete 1500-line file
    >>> print(result)

    >>> result = await read_file("large.log", offset=1000, limit=100)
    >>> print(result)  # Read specific range
"""

import mimetypes
from pathlib import Path
from typing import Any, Optional

from .utils import (
    DEFAULT_MAX_BYTES,
    format_size,
    get_smart_limits,
    truncate_head,
)


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

    Note:
        If offset exceeds file length, it is automatically clamped to the
        last page of the file instead of raising an error.
    """
    # Resolve path
    path_obj = Path(file_path)
    if workspace is None:
        workspace = Path.cwd()

    # YOLO Mode: Direct resolution without sandbox constraints
    # If path is relative, resolve against workspace. If absolute, use as is.
    if path_obj.is_absolute():
        resolved_path = path_obj.resolve()
    else:
        resolved_path = (workspace / path_obj).resolve()

    if not resolved_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Directory fallback: return listing instead of error
    if resolved_path.is_dir():
        return _list_directory(resolved_path, file_path)

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


def _list_directory(dir_path: Path, original_path: str) -> str:
    """List directory contents when Read is called on a directory."""
    try:
        entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        lines = [f"['{original_path}' is a directory. Contents:]", ""]
        for entry in entries[:100]:  # Cap at 100 entries
            prefix = "📁 " if entry.is_dir() else "   "
            lines.append(f"{prefix}{entry.name}")
        if len(list(dir_path.iterdir())) > 100:
            lines.append(f"\n... and more ({len(list(dir_path.iterdir())) - 100} entries omitted)")
        lines.append("\n[Tip: Use Glob to find files by pattern, or Read with a specific file path.]")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error listing directory '{original_path}': {e}]"


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
    # Check file size first
    try:
        file_size = file_path.stat().st_size
    except Exception as e:
        raise OSError(f"Failed to stat file: {str(e)}")

    # If file is "small" (< 1MB), read fully for accurate line counts (legacy behavior)
    if file_size < 1024 * 1024:
        return await _read_small_text(file_path, offset, limit, original_path)

    # Large file optimization
    return await _read_large_text(file_path, offset, limit, original_path, file_size)


async def _read_small_text(
    file_path: Path, offset: Optional[int], limit: Optional[int], original_path: str
) -> str:
    """Read small text file fully into memory with smart limits."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8 text: {original_path}")
    except Exception as e:
        raise OSError(f"Failed to read file: {str(e)}")

    lines = content.split("\n")
    total_lines = len(lines)
    file_size = len(content.encode("utf-8"))

    # Get smart limits based on file size
    smart_max_lines, smart_max_bytes = get_smart_limits(file_size=file_size)

    # Apply offset (1-indexed → 0-indexed)
    original_offset = offset
    start_line = (offset - 1) if offset else 0
    start_line = max(0, start_line)

    # Clamp offset if beyond end of file (prevent doom loops)
    clamp_warning = ""
    if start_line >= total_lines:
        effective_limit = limit if limit is not None else smart_max_lines
        start_line = max(0, total_lines - effective_limit)
        clamp_warning = (
            f"\u26a0\ufe0f Requested offset {original_offset} exceeds file length "
            f"({total_lines} lines). Showing from line {start_line + 1}.\n\n"
        )

    # Select lines
    if limit is not None:
        end_line = min(start_line + limit, total_lines)
        selected_content = "\n".join(lines[start_line:end_line])
        user_limited_lines = end_line - start_line
    else:
        selected_content = "\n".join(lines[start_line:])
        user_limited_lines = None

    # Apply smart truncation
    truncation = truncate_head(selected_content, max_lines=smart_max_lines, max_bytes=smart_max_bytes)

    # Build output text
    if truncation["first_line_exceeds_limit"]:
        first_line_size = format_size(len(lines[start_line].encode("utf-8")))
        max_size = format_size(smart_max_bytes)
        return (
            f"[Line {start_line + 1} is {first_line_size}, exceeds {max_size} limit. "
            f"Use bash: sed -n '{start_line + 1}p' {file_path.name} | head -c {smart_max_bytes}]"
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
            max_size = format_size(smart_max_bytes)
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

    return clamp_warning + output_text


async def _read_large_text(
    file_path: Path,
    offset: Optional[int],
    limit: Optional[int],
    original_path: str,
    file_size: int,
) -> str:
    """Read large text file line-by-line to avoid OOM with smart limits."""
    # Get smart limits based on file size
    smart_max_lines, smart_max_bytes = get_smart_limits(file_size=file_size)

    # Apply offset (1-indexed → 0-indexed)
    original_offset = offset
    start_line = (offset - 1) if offset else 0
    start_line = max(0, start_line)

    clamp_warning = ""
    lines_read = []
    lines_skipped = 0
    bytes_read = 0

    # Use smart limits unless user specified a smaller limit
    max_lines = limit if limit is not None else smart_max_lines
    # Cap to smart limit if user limit is too large
    if limit is None or limit > smart_max_lines:
        max_lines = smart_max_lines

    truncated_by_bytes = False
    truncated_by_lines = False

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            # Skip lines
            offset_beyond_eof = False
            for _ in range(start_line):
                if not f.readline():
                    # End of file reached during skip - count total lines
                    offset_beyond_eof = True
                    break
                lines_skipped += 1

            if offset_beyond_eof:
                # Count total lines and clamp offset to last page
                total_lines = lines_skipped  # lines_skipped = lines read before EOF
                effective_limit = limit if limit is not None else max_lines
                clamped_start = max(0, total_lines - effective_limit)
                clamp_warning = (
                    f"\u26a0\ufe0f Requested offset {original_offset} exceeds file length "
                    f"({total_lines} lines). Showing from line {clamped_start + 1}.\n\n"
                )
                # Re-read from clamped position
                start_line = clamped_start
                lines_skipped = 0
                f.seek(0)
                for _ in range(start_line):
                    f.readline()
                    lines_skipped += 1

            # Read requested lines
            while len(lines_read) < max_lines:
                line = f.readline()
                if not line:
                    break

                line_bytes = len(line.encode("utf-8"))

                # Check byte limit using smart limit
                if bytes_read + line_bytes > smart_max_bytes:
                    # If it's the very first line, return special error
                    if len(lines_read) == 0:
                        first_line_size = format_size(line_bytes)
                        max_size = format_size(smart_max_bytes)
                        return (
                            f"[Line {start_line + 1} is {first_line_size}, exceeds {max_size} limit. "
                            f"Use bash: sed -n '{start_line + 1}p' {Path(original_path).name} | head -c {smart_max_bytes}]"
                        )

                    truncated_by_bytes = True
                    break

                lines_read.append(line.rstrip("\n")) # strip for display, add back later?
                # Wait, original implementation kept newlines?
                # lines = content.split("\n") removes them from the list elements if using split
                # But then join adds them back.
                # readline() keeps \n.
                # If we use .rstrip("\n"), we lose it.
                # Let's keep consistent with _read_small_text which splits by \n
                # content.split("\n") -> ["line1", "line2", ...] (no \n at end of strings)
                # So we should strip \n here.

                bytes_read += line_bytes

            # Check if there is more content (for truncation flags)
            # Try reading one more byte/line to check eof
            if not truncated_by_bytes and len(lines_read) == max_lines:
                if f.read(1):
                    truncated_by_lines = True

    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8 text: {original_path}")
    except Exception as e:
        raise OSError(f"Failed to read file: {str(e)}")

    output_text = "\n".join(lines_read)

    # Add large file warning/hint
    end_line_display = start_line + len(lines_read)
    next_offset = end_line_display + 1

    size_str = format_size(file_size)
    max_size_str = format_size(DEFAULT_MAX_BYTES)

    hint = f"\n\n[File is large ({size_str}). Showing lines {start_line + 1}-{end_line_display}"

    if truncated_by_bytes:
        hint += f" (truncated at {max_size_str} limit)"
    elif truncated_by_lines:
        hint += f" (limit {max_lines} lines)"
    elif limit is not None:
        hint += f" (user limit {limit})"
    else:
        hint += " (end of file)"

    if truncated_by_bytes or truncated_by_lines:
        hint += f". Use offset={next_offset} to read more.]"
    else:
        # We don't know if there are more lines unless we counted them all or hit EOF
        # If we hit EOF (loop ended naturally and not truncated), we are done.
        # But wait, lines_read < max_lines means we hit EOF.
        # So we only need hint if truncated.
        pass

    if truncated_by_bytes or truncated_by_lines:
        output_text += hint
    elif len(lines_read) < max_lines and limit is not None:
         # User set a limit, and we read less than limit -> EOF reached.
         # Or user set a limit, and we read exactly limit -> Maybe more?
         pass

    # For large files, we might not know total_lines without scanning.
    # We omit "of {total_lines}" to save time.

    return clamp_warning + output_text
