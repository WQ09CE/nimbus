"""File tree cache for fast path lookup and fuzzy matching.

This module provides a FileTreeCache class that caches the workspace file tree
for efficient path resolution and fuzzy matching.

Example:
    >>> cache = FileTreeCache(Path("/project"))
    >>> await cache.initialize()
    >>> entries = cache.find_by_name("utils.py")
    >>> fuzzy = cache.find_fuzzy("utlis", threshold=0.6)
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class FileTreeEntry:
    """Entry in the file tree cache.

    Attributes:
        path: Absolute path to the file/directory.
        name: File name (including extension).
        stem: File name without extension.
        suffix: File extension (including dot).
        mtime: Last modification time (Unix timestamp).
        is_dir: Whether this is a directory.
    """

    path: Path
    name: str
    stem: str
    suffix: str
    mtime: float
    is_dir: bool

    @classmethod
    def from_path(cls, path: Path) -> "FileTreeEntry":
        """Create a FileTreeEntry from a Path.

        Args:
            path: Path to create entry from.

        Returns:
            FileTreeEntry instance.
        """
        stat = path.stat()
        return cls(
            path=path,
            name=path.name,
            stem=path.stem,
            suffix=path.suffix,
            mtime=stat.st_mtime,
            is_dir=path.is_dir(),
        )


class FileTreeCache:
    """Cache for workspace file tree.

    Provides fast lookup and fuzzy matching for file paths within a workspace.

    Features:
        - Index by name and stem for O(1) lookup
        - Fuzzy matching using Levenshtein distance
        - Configurable exclusion patterns
        - Memory-efficient with configurable limits

    Attributes:
        workspace: Root directory of the workspace.
        exclude_patterns: Patterns to exclude from scanning.
        max_depth: Maximum directory depth to scan.
        max_files: Maximum number of files to cache.

    Example:
        >>> cache = FileTreeCache(Path("/project"))
        >>> await cache.initialize()
        >>> entries = cache.find_by_stem("utils")  # Find utils.py, utils.ts, etc.
    """

    DEFAULT_EXCLUDE_PATTERNS: List[str] = [
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".env",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "target",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "*.egg-info",
        ".idea",
        ".vscode",
    ]

    def __init__(
        self,
        workspace: Path,
        exclude_patterns: Optional[List[str]] = None,
        max_depth: int = 10,
        max_files: int = 50000,
    ) -> None:
        """Initialize the file tree cache.

        Args:
            workspace: Root directory to cache.
            exclude_patterns: Directory/file patterns to exclude.
            max_depth: Maximum directory depth to scan (default 10).
            max_files: Maximum number of files to cache (default 50000).
        """
        self.workspace = workspace.resolve()
        self.exclude_patterns: Set[str] = set(
            exclude_patterns if exclude_patterns is not None else self.DEFAULT_EXCLUDE_PATTERNS
        )
        self.max_depth = max_depth
        self.max_files = max_files

        # Index structures
        self._entries: Dict[Path, FileTreeEntry] = {}
        self._by_name: Dict[str, List[Path]] = {}  # filename -> [paths]
        self._by_stem: Dict[str, List[Path]] = {}  # stem -> [paths]
        self._recent: List[Path] = []  # Recently modified files

        self._initialized = False
        self._file_count = 0

    @property
    def initialized(self) -> bool:
        """Check if the cache has been initialized."""
        return self._initialized

    @property
    def file_count(self) -> int:
        """Get the number of cached files."""
        return self._file_count

    async def initialize(self) -> None:
        """Initialize the file tree cache asynchronously.

        Scans the workspace directory and builds the index structures.
        """
        # Run the scan in a thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._scan_sync)

    def _scan_sync(self) -> None:
        """Synchronous directory scan (run in thread pool)."""
        self._entries.clear()
        self._by_name.clear()
        self._by_stem.clear()
        self._recent.clear()
        self._file_count = 0

        self._scan_directory(self.workspace, depth=0)

        # Build recent files list (sorted by mtime descending)
        self._recent = sorted(
            [p for p, e in self._entries.items() if not e.is_dir],
            key=lambda p: self._entries[p].mtime,
            reverse=True,
        )[:100]

        self._initialized = True

    def _scan_directory(self, directory: Path, depth: int) -> None:
        """Recursively scan a directory.

        Args:
            directory: Directory to scan.
            depth: Current depth level.
        """
        if depth > self.max_depth:
            return

        if self._file_count >= self.max_files:
            return

        try:
            for item in directory.iterdir():
                # Check exclusion patterns
                if self._should_exclude(item):
                    continue

                if self._file_count >= self.max_files:
                    return

                try:
                    entry = FileTreeEntry.from_path(item)
                    self._add_entry(entry)

                    if item.is_dir():
                        self._scan_directory(item, depth + 1)
                except (OSError, PermissionError):
                    # Skip files we can't access
                    continue

        except (OSError, PermissionError):
            # Skip directories we can't access
            pass

    def _should_exclude(self, path: Path) -> bool:
        """Check if a path should be excluded.

        Args:
            path: Path to check.

        Returns:
            True if path should be excluded.
        """
        name = path.name

        # Direct name match
        if name in self.exclude_patterns:
            return True

        # Glob pattern match (simple wildcard support)
        for pattern in self.exclude_patterns:
            if pattern.startswith("*") and name.endswith(pattern[1:]):
                return True
            if pattern.endswith("*") and name.startswith(pattern[:-1]):
                return True

        return False

    def _add_entry(self, entry: FileTreeEntry) -> None:
        """Add an entry to the index.

        Args:
            entry: Entry to add.
        """
        self._entries[entry.path] = entry

        if not entry.is_dir:
            self._file_count += 1

            # Index by name
            if entry.name not in self._by_name:
                self._by_name[entry.name] = []
            self._by_name[entry.name].append(entry.path)

            # Index by stem
            if entry.stem not in self._by_stem:
                self._by_stem[entry.stem] = []
            self._by_stem[entry.stem].append(entry.path)

    def find_by_name(self, name: str) -> List[FileTreeEntry]:
        """Find files by exact name match.

        Args:
            name: File name to search for (including extension).

        Returns:
            List of matching FileTreeEntry objects.

        Example:
            >>> entries = cache.find_by_name("utils.py")
        """
        paths = self._by_name.get(name, [])
        return [self._entries[p] for p in paths if p in self._entries]

    def find_by_stem(self, stem: str) -> List[FileTreeEntry]:
        """Find files by stem (name without extension).

        Useful for suffix completion (e.g., "utils" -> "utils.py").

        Args:
            stem: File stem to search for.

        Returns:
            List of matching FileTreeEntry objects.

        Example:
            >>> entries = cache.find_by_stem("utils")  # Finds utils.py, utils.ts
        """
        paths = self._by_stem.get(stem, [])
        return [self._entries[p] for p in paths if p in self._entries]

    def find_fuzzy(
        self, query: str, threshold: float = 0.6
    ) -> List[Tuple[FileTreeEntry, float]]:
        """Find files using fuzzy matching.

        Uses normalized Levenshtein distance for similarity scoring.

        Args:
            query: Search query.
            threshold: Minimum similarity score (0.0 to 1.0).

        Returns:
            List of (FileTreeEntry, score) tuples, sorted by score descending.

        Example:
            >>> results = cache.find_fuzzy("utlis", threshold=0.7)
            >>> # Returns [("utils.py", 0.8), ...]
        """
        results: List[Tuple[FileTreeEntry, float]] = []
        query_lower = query.lower()
        seen_paths: Set[Path] = set()

        for entry in self._entries.values():
            if entry.is_dir:
                continue

            if entry.path in seen_paths:
                continue

            # Check name similarity
            name_score = self._similarity(query_lower, entry.name.lower())
            if name_score >= threshold:
                results.append((entry, name_score))
                seen_paths.add(entry.path)
                continue

            # Check stem similarity
            stem_score = self._similarity(query_lower, entry.stem.lower())
            if stem_score >= threshold:
                results.append((entry, stem_score))
                seen_paths.add(entry.path)

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def find_recent(self, limit: int = 10) -> List[FileTreeEntry]:
        """Get recently modified files.

        Args:
            limit: Maximum number of files to return.

        Returns:
            List of FileTreeEntry objects, sorted by mtime descending.
        """
        return [
            self._entries[p] for p in self._recent[:limit] if p in self._entries
        ]

    def get_entry(self, path: Path) -> Optional[FileTreeEntry]:
        """Get entry for a specific path.

        Args:
            path: Path to look up.

        Returns:
            FileTreeEntry if found, None otherwise.
        """
        return self._entries.get(path.resolve())

    def get_tree_summary(
        self, max_depth: int = 2, max_entries: int = 50
    ) -> str:
        """Generate a tree-style summary of the workspace.

        Args:
            max_depth: Maximum depth to display.
            max_entries: Maximum number of entries to show.

        Returns:
            Tree-formatted string representation.

        Example:
            >>> print(cache.get_tree_summary())
            src/
              main.py
              utils/
                helpers.py
        """
        lines: List[str] = []
        entry_count = 0

        def add_directory(directory: Path, depth: int, prefix: str) -> None:
            nonlocal entry_count

            if depth > max_depth or entry_count >= max_entries:
                return

            try:
                items = sorted(
                    directory.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
            except (OSError, PermissionError):
                return

            for item in items:
                if self._should_exclude(item):
                    continue

                if entry_count >= max_entries:
                    lines.append(f"{prefix}... (truncated)")
                    return

                entry_count += 1
                rel_path = item.relative_to(self.workspace)

                if item.is_dir():
                    lines.append(f"{prefix}{rel_path.name}/")
                    add_directory(item, depth + 1, prefix + "  ")
                else:
                    lines.append(f"{prefix}{rel_path.name}")

        add_directory(self.workspace, 0, "")

        if not lines:
            return "[Empty workspace]"

        return "\n".join(lines)

    def _similarity(self, s1: str, s2: str) -> float:
        """Calculate similarity between two strings.

        Uses normalized Levenshtein distance.

        Args:
            s1: First string.
            s2: Second string.

        Returns:
            Similarity score between 0.0 and 1.0.
        """
        if s1 == s2:
            return 1.0

        max_len = max(len(s1), len(s2))
        if max_len == 0:
            return 1.0

        distance = self._levenshtein(s1, s2)
        return 1.0 - (distance / max_len)

    def _levenshtein(self, s1: str, s2: str) -> int:
        """Calculate Levenshtein distance between two strings.

        Args:
            s1: First string.
            s2: Second string.

        Returns:
            Edit distance.
        """
        if len(s1) < len(s2):
            return self._levenshtein(s2, s1)

        if len(s2) == 0:
            return len(s1)

        previous_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                # Cost is 0 if characters match, 1 otherwise
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    def invalidate(self, path: Optional[Path] = None) -> None:
        """Invalidate cache (full or partial).

        Args:
            path: If provided, only invalidate this path and its children.
                  If None, invalidate the entire cache.
        """
        if path is None:
            self._initialized = False
            self._entries.clear()
            self._by_name.clear()
            self._by_stem.clear()
            self._recent.clear()
            self._file_count = 0
        else:
            # Remove entries under the given path
            resolved = path.resolve()
            to_remove = [
                p for p in self._entries.keys()
                if p == resolved or resolved in p.parents
            ]

            for p in to_remove:
                entry = self._entries.pop(p, None)
                if entry and not entry.is_dir:
                    self._file_count -= 1

                    # Remove from name index
                    if entry.name in self._by_name:
                        self._by_name[entry.name] = [
                            ep for ep in self._by_name[entry.name] if ep != p
                        ]
                        if not self._by_name[entry.name]:
                            del self._by_name[entry.name]

                    # Remove from stem index
                    if entry.stem in self._by_stem:
                        self._by_stem[entry.stem] = [
                            ep for ep in self._by_stem[entry.stem] if ep != p
                        ]
                        if not self._by_stem[entry.stem]:
                            del self._by_stem[entry.stem]

            # Update recent list
            self._recent = [p for p in self._recent if p in self._entries]

    def __len__(self) -> int:
        """Return number of cached entries."""
        return len(self._entries)

    def __contains__(self, path: Path) -> bool:
        """Check if a path is in the cache."""
        return path.resolve() in self._entries

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"FileTreeCache(workspace={self.workspace!r}, "
            f"files={self._file_count}, initialized={self._initialized})"
        )
