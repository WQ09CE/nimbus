"""Content grep tool for regex-based file content searching.

This module provides a tool for searching file contents with regex patterns,
supporting context lines and file type filtering.

Example:
    >>> from pathlib import Path
    >>> result = await grep_content("def main", path="src", workspace=Path("/project"))
    >>> print(result)
    Found 3 matches in 2 files:

    src/main.py:
      10:def main():
      11-    parser = argparse.ArgumentParser()

    src/cli.py:
      25:def main():
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

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
DEFAULT_MAX_MATCHES = 50
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
) -> List[Tuple[int, str, bool]]:
    """Search a file for pattern matches with context.

    Returns list of (line_number, line_content, is_match) tuples.
    """
    lines = _read_file_lines(file_path)
    if lines is None:
        return []

    results: List[Tuple[int, str, bool]] = []
    match_indices: set[int] = set()

    # Find all matching line indices
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


def _format_results(
    file_matches: dict[Path, List[Tuple[int, str, bool]]],
    workspace: Path,
    max_matches: int,
) -> Tuple[str, int]:
    """Format search results into output string.

    Returns (formatted_output, total_match_count).
    """
    total_matches = 0
    file_count = 0
    output_parts: List[str] = []
    truncated = False

    for file_path, matches in file_matches.items():
        if not matches:
            continue

        file_count += 1
        match_count = sum(1 for _, _, is_match in matches if is_match)
        total_matches += match_count

        if total_matches > max_matches and not truncated:
            truncated = True

        # Get relative path
        try:
            rel_path = file_path.relative_to(workspace)
        except ValueError:
            rel_path = file_path

        # Format file header and matches
        file_output = [f"\n{rel_path}:"]

        prev_line_num = -2  # Track for gap detection
        for line_num, content, is_match in matches:
            # Add gap indicator if lines are not consecutive
            if line_num > prev_line_num + 1 and prev_line_num > 0:
                file_output.append("  ...")

            # Format line with match indicator
            prefix = ":" if is_match else "-"
            file_output.append(f"  {line_num}{prefix}{content}")
            prev_line_num = line_num

        output_parts.append("\n".join(file_output))

    # Build final output
    if not output_parts:
        return "No matches found.", 0

    header = f"Found {total_matches} match(es) in {file_count} file(s):"
    result = header + "".join(output_parts)

    if truncated:
        result += f"\n\n[Output truncated at {max_matches} matches]"

    return result, total_matches


@tool(
    name="Grep",
    description="Search file contents with regex pattern. Returns matching lines with file:line:content format.",
    parameters=[
        ToolParameter(
            "pattern",
            "string",
            "Regex pattern to search for (e.g., 'def main', 'import.*os')",
            required=True,
        ),
        ToolParameter(
            "path",
            "string",
            "Directory to search in. Defaults to workspace root.",
            required=False,
            default=".",
        ),
        ToolParameter(
            "glob",
            "string",
            "File pattern filter (e.g., '*.py', '**/*.ts')",
            required=False,
        ),
        ToolParameter(
            "type",
            "string",
            "File type to search (py, js, ts, go, java, etc.)",
            required=False,
        ),
        ToolParameter(
            "context_before",
            "integer",
            "Lines of context before each match (like grep -B)",
            required=False,
            default=0,
        ),
        ToolParameter(
            "context_after",
            "integer",
            "Lines of context after each match (like grep -A)",
            required=False,
            default=0,
        ),
        ToolParameter(
            "max_matches",
            "integer",
            f"Maximum matches to return. Defaults to {DEFAULT_MAX_MATCHES}.",
            required=False,
            default=DEFAULT_MAX_MATCHES,
        ),
        ToolParameter(
            "ignore_case",
            "boolean",
            "Case-insensitive search. Defaults to False.",
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
    context_before: int = 0,
    context_after: int = 0,
    max_matches: int = DEFAULT_MAX_MATCHES,
    ignore_case: bool = False,
    workspace: Optional[Path] = None,
    **kwargs,
) -> str:
    """Search file contents with regex pattern.

    Searches for regex pattern matches within files, supporting
    file type filtering and context lines around matches.

    Features:
        - Full regex pattern support
        - File type filtering (py, js, ts, go, etc.)
        - Glob pattern filtering
        - Context lines before/after matches
        - Case-insensitive search option
        - Match count limiting

    Args:
        pattern: Regex pattern to search for.
        path: Directory to search in. Defaults to ".".
        glob: Optional glob pattern to filter files (e.g., "*.py").
        type: Optional file type to filter (e.g., "py", "js").
        context_before: Number of context lines before each match.
        context_after: Number of context lines after each match.
        max_matches: Maximum number of matches to return.
        ignore_case: If True, perform case-insensitive search.
        workspace: Workspace directory for sandbox validation.

    Returns:
        Formatted search results with file paths, line numbers, and content.

    Raises:
        SandboxError: If search path escapes workspace.
        ValueError: If pattern is invalid regex or parameters are invalid.

    Example:
        >>> result = await grep_content("def main", type="py")
        >>> print(result)
        Found 2 matches in 2 files:

        src/main.py:
          10:def main():

        src/cli.py:
          25:def main():
    """
    # Validate parameters
    if not pattern:
        raise ValueError("pattern cannot be empty")

    if context_before < 0:
        raise ValueError(f"context_before must be non-negative, got {context_before}")

    if context_after < 0:
        raise ValueError(f"context_after must be non-negative, got {context_after}")

    if max_matches <= 0:
        raise ValueError(f"max_matches must be positive, got {max_matches}")

    # Compile regex pattern
    try:
        flags = re.IGNORECASE if ignore_case else 0
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

    # Ensure base path is a directory
    if not base_path.is_dir():
        raise NotADirectoryError(f"Search path is not a directory: {path}")

    # Get files to search
    try:
        files = _get_files_to_search(base_path, glob, type, sandbox)
    except ValueError:
        raise

    if not files:
        return f"No files found to search in '{path}'"

    # Search files
    file_matches: dict[Path, List[Tuple[int, str, bool]]] = {}
    total_matches = 0

    for file_path in files:
        if total_matches >= max_matches:
            break

        matches = _search_file(file_path, regex, context_before, context_after)
        if matches:
            file_matches[file_path] = matches
            total_matches += sum(1 for _, _, is_match in matches if is_match)

    # Format and return results
    output, _ = _format_results(file_matches, workspace, max_matches)
    return output
