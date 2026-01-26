"""Content grep tool for regex-based file content searching.

This module provides a powerful tool for searching file contents with regex patterns,
supporting multiple output modes, context lines, and pagination.

Example:
    >>> from pathlib import Path
    >>> result = await grep_content("def main", path="src", workspace=Path("/project"))
    >>> print(result)
    src/main.py
    src/cli.py

    >>> result = await grep_content("def main", output_mode="content", workspace=Path("/project"))
    >>> print(result)
    src/main.py:10:def main():
    src/cli.py:25:def main():
"""

import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

from .base import ToolParameter, tool
from .sandbox import Sandbox, SandboxError

# File type to glob pattern mapping
FILE_TYPE_PATTERNS: dict[str, str] = {
    "py": "**/*.py",
    "python": "**/*.py",
    "js": "**/*.js",
    "javascript": "**/*.js",
    "ts": "**/*.ts",
    "typescript": "**/*.ts",
    "tsx": "**/*.tsx",
    "jsx": "**/*.jsx",
    "java": "**/*.java",
    "go": "**/*.go",
    "rs": "**/*.rs",
    "rust": "**/*.rs",
    "c": "**/*.c",
    "cpp": "**/*.cpp",
    "cc": "**/*.cc",
    "cxx": "**/*.cxx",
    "h": "**/*.h",
    "hpp": "**/*.hpp",
    "md": "**/*.md",
    "markdown": "**/*.md",
    "json": "**/*.json",
    "yaml": "**/*.yaml",
    "yml": "**/*.yml",
    "toml": "**/*.toml",
    "xml": "**/*.xml",
    "html": "**/*.html",
    "css": "**/*.css",
    "sql": "**/*.sql",
    "sh": "**/*.sh",
    "bash": "**/*.sh",
    "rb": "**/*.rb",
    "ruby": "**/*.rb",
    "php": "**/*.php",
    "swift": "**/*.swift",
    "kt": "**/*.kt",
    "kotlin": "**/*.kt",
    "scala": "**/*.scala",
}

# Default values
DEFAULT_HEAD_LIMIT = 0  # 0 means unlimited
BINARY_CHECK_BYTES = 8192


def _is_binary_file(file_path: Path) -> bool:
    """Check if a file is binary by looking for null bytes."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(BINARY_CHECK_BYTES)
            return b"\x00" in chunk
    except OSError:
        return True


def _read_file_lines(file_path: Path) -> Optional[List[str]]:
    """Read file lines with encoding fallback.

    Returns None if file cannot be read or is binary.
    """
    if _is_binary_file(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.readlines()
    except UnicodeDecodeError:
        try:
            with open(file_path, "r", encoding="latin-1") as f:
                return f.readlines()
        except OSError:
            return None
    except OSError:
        return None


def _get_files_to_search(
    base_path: Path,
    glob_pattern: Optional[str],
    file_type: Optional[str],
    sandbox: Sandbox,
) -> List[Path]:
    """Get list of files to search based on glob or file type."""
    # Determine pattern
    if glob_pattern:
        pattern = glob_pattern
    elif file_type:
        file_type_lower = file_type.lower()
        if file_type_lower not in FILE_TYPE_PATTERNS:
            valid_types = ", ".join(sorted(set(FILE_TYPE_PATTERNS.keys())))
            raise ValueError(
                f"Unknown file type '{file_type}'. Valid types: {valid_types}"
            )
        pattern = FILE_TYPE_PATTERNS[file_type_lower]
    else:
        # Search all files
        pattern = "**/*"

    # Execute glob
    matches = list(base_path.glob(pattern))

    # Filter to files only, validate with sandbox
    files = []
    for p in matches:
        if p.is_file() and sandbox.is_safe(p):
            files.append(p)

    return files


def _search_file(
    file_path: Path,
    pattern: re.Pattern,
    context_before: int,
    context_after: int,
    multiline: bool = False,
) -> List[Tuple[int, str, bool]]:
    """Search a file for pattern matches with context.

    Returns list of (line_number, line_content, is_match) tuples.
    """
    lines = _read_file_lines(file_path)
    if lines is None:
        return []

    results: List[Tuple[int, str, bool]] = []
    match_indices: set[int] = set()

    if multiline:
        # For multiline mode, join lines and find match positions
        full_content = "".join(lines)
        # Track which lines have matches
        for match in pattern.finditer(full_content):
            # Find line numbers for this match
            start_pos = match.start()
            end_pos = match.end()
            char_count = 0
            for i, line in enumerate(lines):
                line_start = char_count
                line_end = char_count + len(line)
                if line_start < end_pos and line_end > start_pos:
                    match_indices.add(i)
                char_count = line_end
    else:
        # Standard line-by-line search
        for i, line in enumerate(lines):
            if pattern.search(line):
                match_indices.add(i)

    # Collect matches with context
    context_indices: set[int] = set()
    for match_idx in match_indices:
        # Add context before
        for i in range(max(0, match_idx - context_before), match_idx):
            context_indices.add(i)
        # Add context after
        for i in range(match_idx + 1, min(len(lines), match_idx + context_after + 1)):
            context_indices.add(i)

    # Build result tuples
    all_indices = sorted(match_indices | context_indices)
    for i in all_indices:
        line_content = lines[i].rstrip("\n\r")
        is_match = i in match_indices
        results.append((i + 1, line_content, is_match))  # 1-based line numbers

    return results


def _count_matches_in_file(file_path: Path, pattern: re.Pattern) -> int:
    """Count total pattern matches in a file."""
    lines = _read_file_lines(file_path)
    if lines is None:
        return 0

    count = 0
    for line in lines:
        count += len(pattern.findall(line))
    return count


def _format_content_output(
    file_matches: dict[Path, List[Tuple[int, str, bool]]],
    workspace: Path,
    show_line_numbers: bool,
    head_limit: int,
    offset: int,
) -> str:
    """Format search results in content mode (matching lines)."""
    output_lines: List[str] = []
    total_lines = 0

    for file_path, matches in file_matches.items():
        if not matches:
            continue

        # Get relative path
        try:
            rel_path = file_path.relative_to(workspace)
        except ValueError:
            rel_path = file_path

        prev_line_num = -2  # Track for gap detection
        for line_num, content, is_match in matches:
            total_lines += 1

            # Apply offset
            if total_lines <= offset:
                prev_line_num = line_num
                continue

            # Apply head_limit
            if head_limit > 0 and len(output_lines) >= head_limit:
                output_lines.append(f"\n[Output limited to {head_limit} lines]")
                return "\n".join(output_lines)

            # Add gap indicator if lines are not consecutive
            if line_num > prev_line_num + 1 and prev_line_num > 0 and output_lines:
                output_lines.append("--")

            # Format line
            prefix = ":" if is_match else "-"
            if show_line_numbers:
                output_lines.append(f"{rel_path}:{line_num}{prefix}{content}")
            else:
                output_lines.append(f"{rel_path}{prefix}{content}")

            prev_line_num = line_num

    if not output_lines:
        return "No matches found."

    return "\n".join(output_lines)


def _format_files_output(
    file_matches: dict[Path, List[Tuple[int, str, bool]]],
    workspace: Path,
    head_limit: int,
    offset: int,
) -> str:
    """Format search results as file list only."""
    output_lines: List[str] = []
    file_count = 0

    for file_path, matches in file_matches.items():
        if not matches:
            continue

        file_count += 1

        # Apply offset
        if file_count <= offset:
            continue

        # Apply head_limit
        if head_limit > 0 and len(output_lines) >= head_limit:
            break

        # Get relative path
        try:
            rel_path = file_path.relative_to(workspace)
        except ValueError:
            rel_path = file_path

        output_lines.append(str(rel_path))

    if not output_lines:
        return "No matches found."

    return "\n".join(output_lines)


def _format_count_output(
    file_matches: dict[Path, int],
    workspace: Path,
    head_limit: int,
    offset: int,
) -> str:
    """Format search results as match counts per file."""
    output_lines: List[str] = []
    file_count = 0

    for file_path, count in file_matches.items():
        if count == 0:
            continue

        file_count += 1

        # Apply offset
        if file_count <= offset:
            continue

        # Apply head_limit
        if head_limit > 0 and len(output_lines) >= head_limit:
            break

        # Get relative path
        try:
            rel_path = file_path.relative_to(workspace)
        except ValueError:
            rel_path = file_path

        output_lines.append(f"{rel_path}:{count}")

    if not output_lines:
        return "No matches found."

    return "\n".join(output_lines)


@tool(
    name="Grep",
    description=(
        "Search file contents with regex pattern. "
        "Output modes: 'files_with_matches' (default) shows file paths, "
        "'content' shows matching lines, 'count' shows match counts."
    ),
    parameters=[
        ToolParameter(
            "pattern",
            "string",
            "Regular expression pattern to search for (e.g., 'log.*Error', 'function\\s+\\w+')",
            required=True,
        ),
        ToolParameter(
            "path",
            "string",
            "File or directory to search in. Defaults to current working directory.",
            required=False,
            default=".",
        ),
        ToolParameter(
            "glob",
            "string",
            "Glob pattern to filter files (e.g., '*.js', '**/*.tsx')",
            required=False,
        ),
        ToolParameter(
            "type",
            "string",
            "File type to search (py, js, ts, go, java, etc.). More efficient than glob for standard file types.",
            required=False,
        ),
        ToolParameter(
            "output_mode",
            "string",
            "Output mode: 'content' shows matching lines, 'files_with_matches' shows file paths (default), 'count' shows match counts.",
            required=False,
            default="files_with_matches",
            enum=["content", "files_with_matches", "count"],
        ),
        ToolParameter(
            "-A",
            "integer",
            "Number of lines to show after each match. Requires output_mode='content'.",
            required=False,
            default=0,
        ),
        ToolParameter(
            "-B",
            "integer",
            "Number of lines to show before each match. Requires output_mode='content'.",
            required=False,
            default=0,
        ),
        ToolParameter(
            "-C",
            "integer",
            "Number of lines to show before and after each match. Requires output_mode='content'.",
            required=False,
            default=0,
        ),
        ToolParameter(
            "-n",
            "boolean",
            "Show line numbers in output. Requires output_mode='content'. Defaults to True.",
            required=False,
            default=True,
        ),
        ToolParameter(
            "-i",
            "boolean",
            "Case insensitive search",
            required=False,
            default=False,
        ),
        ToolParameter(
            "head_limit",
            "integer",
            "Limit output to first N lines/entries. Works across all output modes. Defaults to 0 (unlimited).",
            required=False,
            default=0,
        ),
        ToolParameter(
            "offset",
            "integer",
            "Skip first N lines/entries before applying head_limit. Defaults to 0.",
            required=False,
            default=0,
        ),
        ToolParameter(
            "multiline",
            "boolean",
            "Enable multiline mode where patterns can span across lines. Default: False.",
            required=False,
            default=False,
        ),
    ],
)
async def grep_content(
    pattern: str,
    path: str = ".",
    glob: Optional[str] = None,
    type: Optional[str] = None,
    output_mode: str = "files_with_matches",
    workspace: Optional[Path] = None,
    multiline: bool = False,
    head_limit: int = 0,
    offset: int = 0,
    **kwargs: Any,
) -> str:
    """Search file contents with regex pattern.

    A powerful search tool inspired by ripgrep, supporting multiple output modes,
    context lines, and pagination.

    Features:
        - Full regex pattern support
        - Multiple output modes (content, files_with_matches, count)
        - Context lines before/after matches (-A, -B, -C)
        - Line number display (-n)
        - Case-insensitive search (-i)
        - File type filtering
        - Glob pattern filtering
        - Pagination (head_limit, offset)
        - Multiline pattern matching

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

    Example:
        >>> # Find files containing "def main"
        >>> result = await grep_content("def main", type="py")
        >>> print(result)
        src/main.py
        src/cli.py

        >>> # Show matching lines with context
        >>> result = await grep_content("def main", output_mode="content", **{"-C": 2})
        >>> print(result)
        src/main.py:8-import sys
        src/main.py:9-
        src/main.py:10:def main():
        src/main.py:11-    pass
        src/main.py:12-

        >>> # Count matches per file
        >>> result = await grep_content("TODO", output_mode="count")
        >>> print(result)
        src/main.py:3
        src/utils.py:5
    """
    # Extract context parameters from kwargs
    # Support both new style (-A, -B, -C, -i) and legacy style (context_after, context_before, ignore_case)
    context_after = kwargs.get("-A", kwargs.get("A", kwargs.get("context_after", 0)))
    context_before = kwargs.get("-B", kwargs.get("B", kwargs.get("context_before", 0)))
    context_both = kwargs.get("-C", kwargs.get("C", 0))
    show_line_numbers = kwargs.get("-n", kwargs.get("n", True))
    ignore_case = kwargs.get("-i", kwargs.get("i", kwargs.get("ignore_case", False)))

    # Legacy max_matches maps to head_limit for content mode
    if head_limit == 0 and "max_matches" in kwargs:
        max_matches = kwargs.get("max_matches", 0)
        if max_matches > 0:
            head_limit = max_matches
            # For legacy behavior, use content mode if max_matches is specified
            if output_mode == "files_with_matches":
                output_mode = "content"

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
        file_counts: dict[Path, int] = {}
        for file_path in files:
            count = _count_matches_in_file(file_path, regex)
            if count > 0:
                file_counts[file_path] = count
        return _format_count_output(file_counts, workspace, head_limit, offset)

    elif output_mode == "files_with_matches":
        file_matches: dict[Path, List[Tuple[int, str, bool]]] = {}
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
