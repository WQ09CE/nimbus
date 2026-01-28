"""V2 Glob tool for pattern-based file searching.

This module provides the Glob tool in v2 format for AgentOS.
"""

from pathlib import Path
from typing import Any, Dict

from nimbus.tools.sandbox import Sandbox, SandboxError

# Default limit for returned files
DEFAULT_LIMIT = 100


async def glob_files(
    pattern: str,
    path: str = ".",
    limit: int = DEFAULT_LIMIT,
    workspace: Path | None = None,
    **kwargs: Any,
) -> str:
    """Find files and directories matching glob pattern.

    Args:
        pattern: Glob pattern to match (e.g., "**/*.py", "src/*.ts", "src/*/").
        path: Base directory to search in. Defaults to ".".
        limit: Maximum number of paths to return. Defaults to 100.
        workspace: Workspace directory for sandbox validation.

    Returns:
        Formatted list of matching paths, sorted by modification time.

    Raises:
        SandboxError: If base path escapes workspace.
        ValueError: If pattern is empty or limit is invalid.
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

    # Validate that all matched paths are within sandbox
    safe_files = []
    for p in matches:
        if sandbox.is_safe(p):
            safe_files.append(p)

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
        return f"No matches found for pattern '{pattern}' in '{path}'"

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
    lines = relative_paths

    if total_count > limit:
        lines.append(f"\n[Showing {limit} of {total_count} matches]")

    return "\n".join(lines)


# V2 Tool Definition
GLOB_TOOL: Dict[str, Any] = {
    "name": "Glob",
    "description": "Find files and directories matching a glob pattern. Returns paths sorted by modification time (newest first).",
    "function": glob_files,
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g., **/*.py, src/**/*.ts, *.json, src/*/)",
            },
            "path": {
                "type": "string",
                "description": "Base directory to search in. Defaults to workspace root.",
                "default": ".",
            },
            "limit": {
                "type": "integer",
                "description": f"Maximum paths to return. Defaults to {DEFAULT_LIMIT}.",
                "default": DEFAULT_LIMIT,
            },
        },
        "required": ["pattern"],
    },
}
