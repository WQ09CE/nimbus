"""V2 Glob tool for pattern-based file searching.

This module provides the Glob tool in v2 format for AgentOS.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from nimbus.tools.sandbox import Sandbox, SandboxError

# Default limit for returned files
DEFAULT_LIMIT = 100

# Default exclude patterns for common noise directories
DEFAULT_EXCLUDES = [
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "*.pyc",
    ".DS_Store",
    "dist",
    "build",
    "*.egg-info",
]


async def glob_files(
    pattern: str,
    path: str = ".",
    limit: int = DEFAULT_LIMIT,
    exclude: Optional[List[str]] = None,
    workspace: Path | None = None,
    **kwargs: Any,
) -> str:
    """Find files and directories matching glob pattern.

    Args:
        pattern: Glob pattern to match (e.g., "**/*.py", "src/*.ts", "src/*/").
        path: Base directory to search in. Defaults to ".".
        limit: Maximum number of paths to return. Defaults to 100.
        exclude: Optional list of glob patterns to exclude.
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

    # Validate that all matched paths are within sandbox and not excluded
    safe_files = []
    
    # Handle exclude patterns - merge default excludes with user-provided ones
    from fnmatch import fnmatch
    
    # Combine default excludes with user-provided excludes
    all_excludes = list(DEFAULT_EXCLUDES)
    if exclude:
        all_excludes.extend(exclude)
    
    for p in matches:
        if not sandbox.is_safe(p):
            continue
            
        # Check exclusions against all path parts
        should_skip = False
        rel_path = p.relative_to(workspace) if p.is_relative_to(workspace) else p
        rel_path_str = str(rel_path)
        
        for ex in all_excludes:
            # Check against full relative path
            if fnmatch(rel_path_str, ex) or fnmatch(rel_path_str, f"*/{ex}/*") or fnmatch(rel_path_str, f"*/{ex}"):
                should_skip = True
                break
            # Check against filename
            if fnmatch(p.name, ex):
                should_skip = True
                break
            # Check if any part of the path matches the exclude pattern
            for part in rel_path.parts:
                if fnmatch(part, ex):
                    should_skip = True
                    break
            if should_skip:
                break
                
        if should_skip:
            continue
                
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
        # Smart Fallback: If no matches, try to find helpful hints
        hints = []
        
        # 1. If pattern is not recursive, check if a recursive search would find something
        if "**" not in pattern:
            recursive_pattern = f"**/{pattern}"
            try:
                rec_matches = list(base_path.glob(recursive_pattern))
                # Filter safe
                rec_safe = [p for p in rec_matches if sandbox.is_safe(p)]
                if rec_safe:
                    # Found matches deeply nested!
                    count = len(rec_safe)
                    example = rec_safe[0].relative_to(workspace)
                    hints.append(f"Found {count} matches in subdirectories (e.g., '{example}'). Try pattern '{recursive_pattern}'.")
            except Exception:
                pass
        
        # 2. List top-level directories to give context
        try:
            subdirs = [p.name for p in base_path.iterdir() if p.is_dir() and not p.name.startswith(".")]
            if subdirs:
                hints.append(f"Available directories: {', '.join(subdirs[:5])}...")
        except Exception:
            pass

        msg = f"No matches found for pattern '{pattern}' in '{path}'"
        if hints:
            msg += "\n\nHINTS:\n" + "\n".join(f"- {h}" for h in hints)
        return msg

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
    "description": (
        "Find files and directories matching a glob pattern. "
        "Returns paths sorted by modification time (newest first). "
        "Automatically excludes: .venv, node_modules, __pycache__, .git, .mypy_cache, etc."
    ),
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
            "exclude": {
                "type": "array",
                "description": "Additional glob patterns to exclude (on top of default excludes like .venv, node_modules)",
                "items": {"type": "string"},
            },
        },
        "required": ["pattern"],
    },
}
