"""
Workspace snapshot and diff utilities.

Takes fast snapshots of the workspace filesystem (file list + mtime + size)
and computes diffs between snapshots to detect Executor's file changes.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

# Directories to always skip when snapshotting
SKIP_DIRS: Set[str] = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "*.egg-info",
}


@dataclass
class FileInfo:
    """Snapshot of a single file."""
    path: str
    mtime: float
    size: int


@dataclass
class WorkspaceSnapshot:
    """Immutable snapshot of workspace file metadata."""
    files: Dict[str, FileInfo] = field(default_factory=dict)


@dataclass
class WorkspaceDiff:
    """Diff between two workspace snapshots."""
    created: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.created or self.modified or self.deleted)

    def summary(self) -> str:
        """Human-readable summary of changes."""
        lines = []
        for f in self.created:
            lines.append(f"  + {f} (created)")
        for f in self.modified:
            lines.append(f"  ~ {f} (modified)")
        for f in self.deleted:
            lines.append(f"  - {f} (deleted)")
        return "\n".join(lines) if lines else "  (no file changes detected)"


def _should_skip(name: str) -> bool:
    """Check if a directory name should be skipped."""
    if name in SKIP_DIRS:
        return True
    if name.endswith(".egg-info"):
        return True
    return False


def take_snapshot(workspace: Path, max_depth: int = 10) -> WorkspaceSnapshot:
    """
    Take a fast snapshot of workspace file metadata.

    Uses os.scandir for performance. Skips common non-essential directories.

    Args:
        workspace: Root directory to snapshot
        max_depth: Maximum directory traversal depth

    Returns:
        WorkspaceSnapshot with file metadata
    """
    snapshot = WorkspaceSnapshot()
    workspace_str = str(workspace)

    def _scan(dir_path: str, depth: int):
        if depth > max_depth:
            return
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    if entry.name.startswith(".") and entry.is_dir():
                        # Skip hidden dirs except a few
                        if entry.name not in (".github", ".config"):
                            continue
                    if entry.is_dir(follow_symlinks=False):
                        if _should_skip(entry.name):
                            continue
                        _scan(entry.path, depth + 1)
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            stat = entry.stat()
                            rel_path = os.path.relpath(entry.path, workspace_str)
                            snapshot.files[rel_path] = FileInfo(
                                path=rel_path,
                                mtime=stat.st_mtime,
                                size=stat.st_size,
                            )
                        except OSError:
                            pass
        except PermissionError:
            pass

    _scan(workspace_str, 0)
    return snapshot


def diff_snapshots(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> WorkspaceDiff:
    """
    Compute diff between two workspace snapshots.

    Args:
        before: Snapshot taken before Executor ran
        after: Snapshot taken after Executor ran

    Returns:
        WorkspaceDiff listing created, modified, and deleted files
    """
    result = WorkspaceDiff()
    before_keys = set(before.files.keys())
    after_keys = set(after.files.keys())

    # Created: in after but not in before
    for path in sorted(after_keys - before_keys):
        result.created.append(path)

    # Deleted: in before but not in after
    for path in sorted(before_keys - after_keys):
        result.deleted.append(path)

    # Modified: in both but mtime or size changed
    for path in sorted(before_keys & after_keys):
        bf = before.files[path]
        af = after.files[path]
        if bf.mtime != af.mtime or bf.size != af.size:
            result.modified.append(path)

    return result


def read_changed_files(
    workspace: Path,
    diff: WorkspaceDiff,
    max_file_size: int = 10000,
    max_files: int = 10,
) -> str:
    """
    Read contents of changed files for context injection into next Dispatch.

    Args:
        workspace: Workspace root
        diff: WorkspaceDiff from previous dispatch
        max_file_size: Max bytes per file to include
        max_files: Max number of files to include

    Returns:
        Formatted string with file contents
    """
    changed = diff.created + diff.modified
    if not changed:
        return ""

    lines = ["## Files from previous Dispatch (current state)\n"]
    count = 0

    for rel_path in changed:
        if count >= max_files:
            lines.append(f"\n... and {len(changed) - count} more files")
            break

        full_path = workspace / rel_path
        if not full_path.is_file():
            continue

        try:
            size = full_path.stat().st_size
            if size > max_file_size:
                content = full_path.read_text(errors="replace")[:max_file_size]
                content += f"\n... (truncated, total {size} bytes)"
            else:
                content = full_path.read_text(errors="replace")

            lines.append(f"### {rel_path}")
            lines.append(f"```\n{content}\n```\n")
            count += 1
        except (OSError, UnicodeDecodeError):
            pass

    return "\n".join(lines)
