"""Glob Tool — List files matching a glob pattern (read-only).

nimbus had no file-listing primitive: grep searches *contents*, so a
read-only agent (the `reader` sub-agent role) had no way to answer
"what files are here". This fills that gap without granting Bash.
"""

from pathlib import Path
from typing import Any, Optional

from nimbus.core.path_context import AgentPathContext, PathResolver

from .registry import ToolParameter, tool

MAX_RESULTS = 1000
MAX_OUTPUT_BYTES = 50 * 1024  # 50KB total output limit (aligned with Grep)


@tool(
    name="Glob",
    description=(
        "List files matching a glob pattern (read-only). Returns absolute file "
        "paths, one per line. Use this to discover/enumerate files, e.g. find all "
        "Python files with pattern '**/*.py' or '*.py'."
    ),
    parameters=[
        ToolParameter("pattern", "string", "Glob pattern, e.g. '**/*.py' or '*.md'", required=True),
        ToolParameter("path", "string", "Directory to search in (default: workspace root)", required=False),
    ],
)
async def glob_search(
    pattern: str,
    path: Optional[str] = None,
    **kwargs: Any,
) -> str:
    _path_context: AgentPathContext = kwargs.get("_path_context") or AgentPathContext.from_cwd()

    if path:
        base = Path(PathResolver.validate_read(path, _path_context))
    else:
        base = Path(_path_context.target_root)

    if not base.exists():
        raise FileNotFoundError(f"Path not found: {path or base}")

    # Bare patterns (no path separator) match recursively, like Grep's glob filter.
    pat = pattern
    if not pat.startswith("**/") and "/" not in pat:
        pat = f"**/{pat}"

    matches = []
    total_bytes = 0
    for p in sorted(base.glob(pat)):
        if not p.is_file():
            continue
        line = str(p)
        b = len(line.encode("utf-8")) + 1
        if total_bytes + b > MAX_OUTPUT_BYTES:
            matches.append(f"\n[Output truncated at {MAX_OUTPUT_BYTES // 1024}KB. {len(matches)} files shown.]")
            break
        matches.append(line)
        total_bytes += b
        if len(matches) >= MAX_RESULTS:
            matches.append(f"\n[Stopped at {MAX_RESULTS} files]")
            break

    if not matches:
        return f"No files match pattern: {pattern}"
    return "\n".join(matches)
