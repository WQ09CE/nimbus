"""File glob tool for pattern-based file searching.

This module provides a tool for finding files matching glob patterns,
with sandbox-based security validation and modification time sorting.

Example:
    >>> from pathlib import Path
    >>> result = await glob_files("**/*.py", workspace=Path("/project"))
    >>> print(result)
    Found 15 files:
    src/main.py
    src/utils.py
    tests/test_main.py
    ...
"""

from pathlib import Path
from typing import Optional

from .base import ToolParameter, tool
from .sandbox import Sandbox, SandboxError

# Default limit for returned files
DEFAULT_LIMIT = 100


@tool(
    name="Glob",
    description="Find files matching a glob pattern. Returns file paths sorted by modification time (newest first).",
    parameters=[
        ToolParameter(
            "pattern",
            "string",
            "Glob pattern (e.g., **/*.py, src/**/*.ts, *.json)",
            required=True,
        ),
        ToolParameter(
            "path",
            "string",
            "Base directory to search in. Defaults to workspace root.",
            required=False,
            default=".",
        ),
        ToolParameter(
            "limit",
            "integer",
            f"Maximum files to return. Defaults to {DEFAULT_LIMIT}.",
            required=False,
            default=DEFAULT_LIMIT,
        ),
    ],
)
async def glob_files(
    pattern: str,
    path: str = ".",
    limit: int = DEFAULT_LIMIT,
    workspace: Optional[Path] = None,
    **kwargs,
) -> str:
    """Find files matching glob pattern.

    Searches for files matching a glob pattern within a directory,
    returning results sorted by modification time (newest first).

    Features:
        - Standard glob patterns (**/*.py, *.ts, etc.)
        - Recursive matching with **
        - Sort by modification time (newest first)
        - Result limiting
        - Returns relative paths

    Args:
        pattern: Glob pattern to match (e.g., "**/*.py", "src/*.ts").
        path: Base directory to search in. Defaults to ".".
        limit: Maximum number of files to return. Defaults to 100.
        workspace: Workspace directory for sandbox validation.
                   Required for security.

    Returns:
        Formatted list of matching file paths, sorted by modification time.

    Raises:
        SandboxError: If base path escapes workspace.
        ValueError: If pattern is empty or limit is invalid.

    Example:
        >>> result = await glob_files("**/*.py", path="src", limit=10)
        >>> print(result)
        Found 5 files:
        src/main.py
        src/utils.py
        src/core/engine.py
        ...
    """
    # Validate parameters
    if not pattern:
        raise ValueError("pattern cannot be empty")

    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    # Handle empty path - default to current directory
    if not path or path.strip() == "":
        path = "."

    # Determine workspace
    if workspace is None:
        workspace = Path.cwd()

    # Validate base path with sandbox
    sandbox = Sandbox(workspace)
    try:
        base_path = sandbox.validate(path, must_exist=True)
    except SandboxError:
        raise
    except FileNotFoundError:
        raise FileNotFoundError(f"Base path not found: {path}")

    # Ensure base path is a directory
    if not base_path.is_dir():
        raise NotADirectoryError(f"Base path is not a directory: {path}")

    # Execute glob
    try:
        matches = list(base_path.glob(pattern))
    except OSError as e:
        raise OSError(f"Glob error: {e}")

    # Filter to only files (exclude directories)
    files = [p for p in matches if p.is_file()]

    # Also validate that all matched files are within sandbox
    # (glob shouldn't escape, but symlinks could)
    safe_files = []
    for f in files:
        if sandbox.is_safe(f):
            safe_files.append(f)

    # Sort by modification time (newest first)
    try:
        safe_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        # If we can't stat some files, just use path sorting as fallback
        safe_files.sort(key=lambda p: str(p))

    # Apply limit
    total_count = len(safe_files)
    limited_files = safe_files[:limit]

    # Format output with relative paths
    if not limited_files:
        return f"No files found matching pattern '{pattern}' in '{path}'"

    # Convert to relative paths for cleaner output
    relative_paths = []
    for f in limited_files:
        try:
            rel_path = f.relative_to(workspace)
            relative_paths.append(str(rel_path))
        except ValueError:
            # Fallback to absolute path if relative_to fails
            relative_paths.append(str(f))

    # Build output
    lines = [f"Found {total_count} file(s) matching '{pattern}':"]
    lines.extend(relative_paths)

    if total_count > limit:
        lines.append(f"\n[Showing {limit} of {total_count} files]")

    return "\n".join(lines)
