"""Write Tool — Create or overwrite files with auto directory creation."""

from pathlib import Path
from typing import Any

from .registry import ToolParameter, tool


@tool(
    name="Write",
    description="Write content to a file. Creates parent directories automatically.",
    parameters=[
        ToolParameter("file_path", "string", "Path to the file to write", required=True),
        ToolParameter("content", "string", "Content to write", required=True),
    ],
)
async def write_file(file_path: str, content: str, **kwargs: Any) -> str:
    path = Path(file_path)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    byte_count = len(content.encode("utf-8"))
    return f"Successfully wrote {byte_count} bytes to {file_path}"
