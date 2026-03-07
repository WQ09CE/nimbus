"""Read Tool — Read file contents with smart truncation."""

from pathlib import Path
from typing import Any, Optional

from .registry import ToolParameter, tool

MAX_LINES = 2000
MAX_BYTES = 100 * 1024  # 100KB


@tool(
    name="Read",
    description="Read file contents. Supports offset/limit for large files.",
    parameters=[
        ToolParameter("file_path", "string", "Path to the file to read", required=True),
        ToolParameter("offset", "integer", "Line number to start from (1-indexed)", required=False),
        ToolParameter("limit", "integer", "Maximum number of lines to read", required=False),
    ],
)
async def read_file(file_path: str, offset: Optional[int] = None, limit: Optional[int] = None, **kwargs: Any) -> str:
    path = Path(file_path)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if path.is_dir():
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))[:50]
        lines = [f"[Directory: {file_path}]"]
        for e in entries:
            prefix = "d " if e.is_dir() else "  "
            lines.append(f"{prefix}{e.name}")
        return "\n".join(lines)

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.split("\n")
    total = len(lines)

    # Apply offset (1-indexed)
    start = (offset - 1) if offset else 0
    start = max(0, min(start, total - 1))

    # Apply limit
    max_lines = limit if limit else MAX_LINES
    end = min(start + max_lines, total)
    selected = lines[start:end]

    # Byte truncation
    result_lines = []
    byte_count = 0
    for line in selected:
        line_bytes = len(line.encode("utf-8")) + 1
        if byte_count + line_bytes > MAX_BYTES and result_lines:
            result_lines.append(f"\n[Truncated at {MAX_BYTES // 1024}KB. Use offset={start + len(result_lines) + 1} to continue.]")
            break
        result_lines.append(line)
        byte_count += line_bytes

    output = "\n".join(result_lines)

    if end < total:
        output += f"\n\n[Showing lines {start + 1}-{end} of {total}. Use offset={end + 1} to continue.]"

    return output
