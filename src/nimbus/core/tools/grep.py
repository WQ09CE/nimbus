"""Grep Tool — Search file contents by pattern."""

import os
import re
from pathlib import Path
from typing import Any, Optional

from .registry import ToolParameter, tool

MAX_MATCHES = 200
MAX_OUTPUT_BYTES = 50 * 1024  # 50KB total output limit (aligned with pi-coding-agent)
MAX_LINE_LENGTH = 500  # Per-line truncation (aligned with pi's GREP_MAX_LINE_LENGTH)


@tool(
    name="Grep",
    description="Search file contents for a pattern. Returns matching lines with file paths and line numbers.",
    parameters=[
        ToolParameter("pattern", "string", "Regex pattern to search for", required=True),
        ToolParameter("path", "string", "File or directory to search in (default: cwd)", required=False),
        ToolParameter("glob", "string", "File glob filter, e.g. '*.py'", required=False),
    ],
)
async def grep_search(
    pattern: str,
    path: Optional[str] = None,
    glob: Optional[str] = None,
    **kwargs: Any,
) -> str:
    search_path = Path(path) if path else Path.cwd()
    if not search_path.is_absolute():
        search_path = (Path.cwd() / search_path).resolve()

    if not search_path.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    try:
        regex = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid regex: {e}")

    matches = []
    total_bytes = 0

    if search_path.is_file():
        files = [search_path]
    else:
        glob_pattern = glob or "**/*"
        # Ensure recursive matching
        if not glob_pattern.startswith("**/") and "/" not in glob_pattern:
            glob_pattern = f"**/{glob_pattern}"
        files = sorted(search_path.glob(glob_pattern))

    for file in files:
        if not file.is_file():
            continue
        # Skip binary/hidden/large files
        if file.name.startswith(".") or file.stat().st_size > 1024 * 1024:
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError):
            continue

        for i, line in enumerate(text.split("\n"), 1):
            if regex.search(line):
                rel = file.relative_to(search_path) if search_path.is_dir() else file.name
                # Per-line truncation (pi-style: GREP_MAX_LINE_LENGTH = 500)
                display_line = line.rstrip()
                if len(display_line) > MAX_LINE_LENGTH:
                    display_line = display_line[:MAX_LINE_LENGTH] + "…"
                match_str = f"{rel}:{i}: {display_line}"
                match_bytes = len(match_str.encode("utf-8")) + 1  # +1 for newline

                # Byte limit check
                if total_bytes + match_bytes > MAX_OUTPUT_BYTES:
                    matches.append(f"\n[Output truncated at {MAX_OUTPUT_BYTES // 1024}KB. {len(matches)} matches shown.]")
                    return "\n".join(matches)

                matches.append(match_str)
                total_bytes += match_bytes

                if len(matches) >= MAX_MATCHES:
                    matches.append(f"\n[Stopped at {MAX_MATCHES} matches]")
                    return "\n".join(matches)

    if not matches:
        return f"No matches found for pattern: {pattern}"
    return "\n".join(matches)
