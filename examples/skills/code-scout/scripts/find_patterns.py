#!/usr/bin/env python3
"""Search for code patterns in a project directory."""

import argparse
import os
import re
import sys
from pathlib import Path

BUILTIN_PATTERNS = {
    "functions": r"^\s*(?:def |function |(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\(|(?:async\s+)?(?:export\s+)?function\*?\s+)\w+",
    "classes": r"^\s*(?:(?:export\s+)?(?:abstract\s+)?class\s+\w+|class\s+\w+)",
    "todos": r"(?:#|//|/\*|\*)\s*(?:TODO|FIXME|HACK|XXX|WARN)[:\s]",
    "imports": r"^\s*(?:import\s+|from\s+\S+\s+import|(?:const|let|var)\s+.*=\s*require\()",
}

EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    "venv",
    ".tox",
}

TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".php",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
    ".md", ".txt", ".yaml", ".yml", ".toml", ".json",
    ".sh", ".bash", ".zsh", ".sql",
    ".html", ".css", ".scss", ".less", ".vue", ".svelte",
}


def collect_files(root: Path, ext_filter: str | None) -> list[Path]:
    """Walk the project tree and collect matching files."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories in-place
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for fname in filenames:
            _, ext = os.path.splitext(fname)
            ext_lower = ext.lower()
            if ext_filter:
                if ext_lower != ext_filter.lower():
                    continue
            else:
                if ext_lower not in TEXT_EXTENSIONS:
                    continue
            files.append(Path(dirpath) / fname)

    files.sort()
    return files


def search_file(filepath: Path, regex: re.Pattern) -> list[tuple[int, str]]:
    """Search a single file for pattern matches. Returns list of (line_number, line_text)."""
    matches: list[tuple[int, str]] = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for lineno, line in enumerate(f, start=1):
                if regex.search(line):
                    text = line.rstrip()
                    if len(text) > 120:
                        text = text[:120] + "..."
                    matches.append((lineno, text))
    except (OSError, PermissionError):
        pass
    return matches


def find_patterns(root: Path, pattern: str, ext_filter: str | None, max_results: int) -> str:
    """Main search logic. Returns formatted output string."""
    # Resolve regex
    if pattern in BUILTIN_PATTERNS:
        regex_str = BUILTIN_PATTERNS[pattern]
        display_pattern = pattern
    else:
        regex_str = pattern
        display_pattern = pattern

    try:
        regex = re.compile(regex_str, re.MULTILINE)
    except re.error as e:
        return f"Error: invalid regex pattern: {e}"

    files = collect_files(root, ext_filter)

    # Collect all matches
    all_matches: list[tuple[str, int, str]] = []  # (rel_path, lineno, text)
    for filepath in files:
        rel_path = os.path.relpath(filepath, root)
        file_matches = search_file(filepath, regex)
        for lineno, text in file_matches:
            all_matches.append((rel_path, lineno, text))
            if len(all_matches) >= max_results:
                break
        if len(all_matches) >= max_results:
            break

    total_found = len(all_matches)

    # Build output
    parts: list[str] = []
    parts.append(f'=== Pattern Search: "{display_pattern}" in {root.resolve()} ===')

    filter_str = f"*{ext_filter}" if ext_filter else "all supported"
    parts.append(f"Filter: {filter_str} | Max results: {max_results}")
    parts.append("")

    if total_found == 0:
        parts.append("No matches found.")
    else:
        parts.append(f"Found {total_found} matches:")
        parts.append("")

        # Calculate alignment: find max width of "path:lineno"
        loc_strs = [f"{m[0]}:{m[1]}" for m in all_matches]
        max_loc_len = max(len(s) for s in loc_strs)

        for (rel_path, lineno, text), loc_str in zip(all_matches, loc_strs):
            parts.append(f"  {loc_str:<{max_loc_len}}  {text}")

        parts.append("")
        parts.append(f"(showing {total_found} of {total_found} matches)")

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Search for code patterns in a project directory."
    )
    parser.add_argument(
        "--path",
        required=True,
        help="Root path of the project to search",
    )
    parser.add_argument(
        "--pattern",
        required=True,
        help="Pattern type (functions, classes, todos, imports) or a custom regex",
    )
    parser.add_argument(
        "--ext",
        default=None,
        help="File extension filter, e.g. .py",
    )
    parser.add_argument(
        "--max_results",
        type=int,
        default=50,
        help="Maximum number of results (default: 50)",
    )
    args = parser.parse_args()

    root = Path(args.path)

    if not root.exists():
        print(f"Error: path does not exist: {root}", file=sys.stderr)
        sys.exit(1)

    if not root.is_dir():
        print(f"Error: path is not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    try:
        output = find_patterns(root, args.pattern, args.ext, args.max_results)
        print(output)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
