"""V2 Grep tool for regex-based file content searching.

This module provides the Grep tool in v2 format for AgentOS.
Reuses core logic from v1 tools.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nimbus.tools.sandbox import Sandbox, SandboxError
from nimbus.tools.grep import (
    FILE_TYPE_PATTERNS,
    _is_binary_file,
    _read_file_lines,
    _get_files_to_search,
    _search_file,
    _count_matches_in_file,
    _format_content_output,
    _format_files_output,
    _format_count_output,
)


async def grep_content(
    pattern: str,
    path: str = ".",
    glob: Optional[str] = None,
    type: Optional[str] = None,
    output_mode: str = "files_with_matches",
    workspace: Path | None = None,
    multiline: bool = False,
    head_limit: int = 0,
    offset: int = 0,
    **kwargs: Any,
) -> str:
    """Search file contents with regex pattern.

    Args:
        pattern: Regex pattern to search for.
        path: Directory or file to search in. Defaults to ".".
        glob: Optional glob pattern to filter files (e.g., "*.py").
        type: Optional file type to filter (e.g., "py", "js").
        output_mode: Output format - "content", "files_with_matches", or "count".
        workspace: Workspace directory for sandbox validation.
        multiline: Enable multiline pattern matching.
        head_limit: Limit output entries. 0 means unlimited.
        offset: Skip first N entries.
        **kwargs: Additional parameters (-A, -B, -C, -n, -i).

    Returns:
        Search results in the specified output format.

    Raises:
        SandboxError: If search path escapes workspace.
        ValueError: If pattern is invalid regex or parameters are invalid.
    """
    # Extract context parameters from kwargs
    context_after = kwargs.get("-A", kwargs.get("A", kwargs.get("context_after", 0)))
    context_before = kwargs.get("-B", kwargs.get("B", kwargs.get("context_before", 0)))
    context_both = kwargs.get("-C", kwargs.get("C", 0))
    show_line_numbers = kwargs.get("-n", kwargs.get("n", True))
    ignore_case = kwargs.get("-i", kwargs.get("i", kwargs.get("ignore_case", False)))

    # -C overrides -A and -B if provided
    if context_both > 0:
        context_after = context_both
        context_before = context_both

    # Validate parameters
    if not pattern:
        raise ValueError("pattern cannot be empty")

    if context_before < 0:
        raise ValueError(f"context_before must be non-negative, got {context_before}")

    if context_after < 0:
        raise ValueError(f"context_after must be non-negative, got {context_after}")

    if head_limit < 0:
        raise ValueError(f"head_limit must be non-negative, got {head_limit}")

    if offset < 0:
        raise ValueError(f"offset must be non-negative, got {offset}")

    if output_mode not in ("content", "files_with_matches", "count"):
        raise ValueError(
            f"output_mode must be 'content', 'files_with_matches', or 'count', got '{output_mode}'"
        )

    # Compile regex pattern
    try:
        flags = re.IGNORECASE if ignore_case else 0
        if multiline:
            flags |= re.DOTALL | re.MULTILINE
        regex = re.compile(pattern, flags)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern '{pattern}': {e}")

    # Determine workspace
    if workspace is None:
        workspace = Path.cwd()

    # Validate search path with sandbox
    sandbox = Sandbox(workspace)
    try:
        base_path = sandbox.validate(path, must_exist=True)
    except SandboxError:
        raise
    except FileNotFoundError:
        raise FileNotFoundError(f"Search path not found: {path}")

    # Handle single file search
    if base_path.is_file():
        files = [base_path]
    else:
        # Get files to search
        try:
            files = _get_files_to_search(base_path, glob, type, sandbox)
        except ValueError:
            raise

        if not files:
            return f"No files found to search in '{path}'"

    # Search files based on output mode
    if output_mode == "count":
        file_counts: Dict[Path, int] = {}
        for file_path in files:
            count = _count_matches_in_file(file_path, regex)
            if count > 0:
                file_counts[file_path] = count
        return _format_count_output(file_counts, workspace, head_limit, offset)

    elif output_mode == "files_with_matches":
        file_matches: Dict[Path, List[Tuple[int, str, bool]]] = {}
        for file_path in files:
            matches = _search_file(file_path, regex, 0, 0, multiline)
            if matches:
                file_matches[file_path] = matches
        return _format_files_output(file_matches, workspace, head_limit, offset)

    else:  # content mode
        file_matches = {}
        for file_path in files:
            matches = _search_file(
                file_path, regex, context_before, context_after, multiline
            )
            if matches:
                file_matches[file_path] = matches
        return _format_content_output(
            file_matches, workspace, show_line_numbers, head_limit, offset
        )


# V2 Tool Definition
GREP_TOOL: Dict[str, Any] = {
    "name": "Grep",
    "description": (
        "Search file contents with regex pattern. "
        "Output modes: 'files_with_matches' (default) shows file paths, "
        "'content' shows matching lines, 'count' shows match counts."
    ),
    "function": grep_content,
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to search for (e.g., 'log.*Error', 'function\\s+\\w+')",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in. Defaults to current working directory.",
                "default": ".",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g., '*.js', '**/*.tsx')",
            },
            "type": {
                "type": "string",
                "description": "File type to search (py, js, ts, go, java, etc.).",
            },
            "output_mode": {
                "type": "string",
                "description": "Output mode: 'content', 'files_with_matches' (default), 'count'.",
                "enum": ["content", "files_with_matches", "count"],
                "default": "files_with_matches",
            },
            "-A": {
                "type": "integer",
                "description": "Number of lines to show after each match.",
                "default": 0,
            },
            "-B": {
                "type": "integer",
                "description": "Number of lines to show before each match.",
                "default": 0,
            },
            "-C": {
                "type": "integer",
                "description": "Number of lines to show before and after each match.",
                "default": 0,
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers in output.",
                "default": True,
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search.",
                "default": False,
            },
            "head_limit": {
                "type": "integer",
                "description": "Limit output to first N lines/entries. 0 means unlimited.",
                "default": 0,
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N lines/entries.",
                "default": 0,
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode where patterns can span across lines.",
                "default": False,
            },
        },
        "required": ["pattern"],
    },
}
