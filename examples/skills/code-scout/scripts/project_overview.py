#!/usr/bin/env python3
"""Scan a project directory and produce a structured overview."""

import argparse
import os
import sys
from pathlib import Path

EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".egg-info",
    ".tox",
    "venv",
}

CONFIG_FILES = [
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    ".gitignore",
    "README.md",
    "requirements.txt",
    "setup.py",
    "tsconfig.json",
    "pom.xml",
    "build.gradle",
]


def should_exclude(name: str, is_root_level: bool) -> bool:
    """Check if a directory should be excluded from traversal."""
    if name in EXCLUDE_DIRS:
        return True
    # Exclude hidden directories, but not at root level (for config detection)
    if name.startswith(".") and not is_root_level:
        return True
    return False


def scan_project(root: Path):
    """Walk the project tree and collect statistics."""
    file_count = 0
    dir_count = 0
    ext_counter: dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        is_root = rel == "."
        depth_parts = [] if is_root else rel.split(os.sep)

        # Filter dirnames in-place to prune traversal
        filtered = []
        for d in dirnames:
            is_root_level = is_root
            if should_exclude(d, is_root_level):
                continue
            # For deeper levels, also skip hidden dirs
            if not is_root_level and d.startswith("."):
                continue
            filtered.append(d)
        dirnames[:] = filtered

        dir_count += len(dirnames)

        for fname in filenames:
            # Skip hidden files in non-root directories
            if not is_root and fname.startswith("."):
                # still count them
                pass
            file_count += 1
            _, ext = os.path.splitext(fname)
            if ext:
                ext_counter[ext] = ext_counter.get(ext, 0) + 1
            else:
                ext_counter["(no ext)"] = ext_counter.get("(no ext)", 0) + 1

    return file_count, dir_count, ext_counter


def detect_configs(root: Path) -> list[tuple[str, bool]]:
    """Check which key config files exist in the project root."""
    results = []
    for cfg in CONFIG_FILES:
        exists = (root / cfg).exists()
        results.append((cfg, exists))
    return results


def build_tree(root: Path, max_depth: int) -> list[str]:
    """Generate a directory tree up to max_depth."""
    lines: list[str] = []
    root_name = root.name or str(root)
    lines.append(f"{root_name}/")

    def _walk(current: Path, prefix: str, depth: int):
        if depth >= max_depth:
            return

        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return

        # Filter: only directories for the tree, exclude unwanted
        dirs = []
        for entry in entries:
            if not entry.is_dir():
                continue
            name = entry.name
            if name in EXCLUDE_DIRS:
                continue
            # Skip hidden dirs except at root level
            if name.startswith(".") and depth > 0:
                continue
            dirs.append(entry)

        for i, d in enumerate(dirs):
            is_last = i == len(dirs) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{d.name}/")
            extension = "    " if is_last else "│   "
            _walk(d, prefix + extension, depth + 1)

    _walk(root, "", 0)
    return lines


def format_overview(root: Path, depth: int) -> str:
    """Produce the full structured overview string."""
    file_count, dir_count, ext_counter = scan_project(root)
    configs = detect_configs(root)
    tree_lines = build_tree(root, depth)

    parts: list[str] = []

    # Header
    parts.append(f"=== Project Overview: {root.resolve()} ===")
    parts.append("")

    # Statistics
    parts.append("📊 Statistics:")
    parts.append(f"  Files: {file_count}")
    parts.append(f"  Directories: {dir_count}")
    parts.append("")

    # Language distribution
    parts.append("📝 Language Distribution:")
    sorted_exts = sorted(ext_counter.items(), key=lambda x: -x[1])
    top = sorted_exts[:15]
    if file_count > 0:
        max_ext_len = max(len(e) for e, _ in top) if top else 1
        for ext, count in top:
            pct = count / file_count * 100
            parts.append(f"  {ext:<{max_ext_len}}  {count:>5}  ({pct:>5.1f}%)")
    else:
        parts.append("  (no files found)")
    parts.append("")

    # Config files
    parts.append("🔧 Config Files Found:")
    for cfg, exists in configs:
        icon = "✅" if exists else "❌"
        parts.append(f"  {icon} {cfg}")
    parts.append("")

    # Directory tree
    parts.append(f"📁 Directory Tree (depth={depth}):")
    for line in tree_lines:
        parts.append(f"  {line}")

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Scan a project directory and produce a structured overview."
    )
    parser.add_argument(
        "--path",
        required=True,
        help="Root path of the project to analyze",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=3,
        help="Maximum directory tree depth (default: 3)",
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
        output = format_overview(root, args.depth)
        print(output)
    except PermissionError as e:
        print(f"Error: permission denied: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
