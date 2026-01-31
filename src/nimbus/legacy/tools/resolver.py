"""Smart path resolver for intelligent path resolution and fuzzy matching.

This module provides the SmartPathResolver class that resolves potentially
ambiguous file paths to actual file locations using multiple strategies.

Example:
    >>> resolver = SmartPathResolver(Path("/project"))
    >>> candidates = resolver.resolve("utils")
    >>> # Returns [PathCandidate(path="src/utils.py", score=0.95, reason="suffix")]
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .filetree import FileTreeCache


@dataclass
class PathCandidate:
    """A candidate path with confidence score.

    Attributes:
        path: Resolved absolute path.
        score: Confidence score (0.0 to 1.0).
        reason: Matching strategy used ("exact", "suffix", "fuzzy", "recent").
        original: Original input string.
    """

    path: Path
    score: float
    reason: str
    original: str

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"PathCandidate(path={self.path!r}, score={self.score:.2f}, "
            f"reason={self.reason!r})"
        )


class SmartPathResolver:
    """Intelligent path resolver with multiple matching strategies.

    Resolves potentially ambiguous file paths using the following strategies
    (in priority order):
        1. Exact match - Path exists exactly as specified
        2. Suffix completion - Add common file extensions (.py, .ts, etc.)
        3. Fuzzy search - Match using edit distance
        4. Recent files - Match against recently modified files

    Attributes:
        workspace: Root directory for path resolution.
        file_tree_cache: Optional FileTreeCache for efficient lookups.
        suffix_priority: Extensions to try for suffix completion.
        fuzzy_threshold: Minimum similarity for fuzzy matches.

    Example:
        >>> resolver = SmartPathResolver(Path("/project"))
        >>> candidates = resolver.resolve("utlis")  # typo
        >>> # Returns [PathCandidate(path="src/utils.py", score=0.8, reason="fuzzy")]
    """

    DEFAULT_SUFFIX_PRIORITY: List[str] = [
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".rb",
        ".php",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".swift",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
    ]

    def __init__(
        self,
        workspace: Path,
        file_tree_cache: Optional["FileTreeCache"] = None,
        suffix_priority: Optional[List[str]] = None,
        fuzzy_threshold: float = 0.6,
    ) -> None:
        """Initialize the path resolver.

        Args:
            workspace: Root directory for path resolution.
            file_tree_cache: Optional FileTreeCache for efficient lookups.
            suffix_priority: File extensions to try (default: common code extensions).
            fuzzy_threshold: Minimum similarity for fuzzy matches (default: 0.6).
        """
        self.workspace = workspace.resolve()
        self.cache = file_tree_cache
        self.suffix_priority = suffix_priority or self.DEFAULT_SUFFIX_PRIORITY
        self.fuzzy_threshold = fuzzy_threshold

    def resolve(self, path: str, max_candidates: int = 5) -> List[PathCandidate]:
        """Resolve a path to a list of candidates.

        Tries multiple resolution strategies and returns ranked candidates.

        Args:
            path: Path string to resolve.
            max_candidates: Maximum number of candidates to return.

        Returns:
            List of PathCandidate objects sorted by score descending.

        Example:
            >>> candidates = resolver.resolve("utils")
            >>> for c in candidates:
            ...     print(f"{c.path}: {c.score:.2f} ({c.reason})")
        """
        candidates: List[PathCandidate] = []
        seen_paths: set[Path] = set()

        # Strategy 1: Exact match
        exact = self._try_exact(path)
        if exact:
            candidates.append(exact)
            seen_paths.add(exact.path)

        # Strategy 2: Suffix completion
        suffix_matches = self._try_suffix_completion(path)
        for candidate in suffix_matches:
            if candidate.path not in seen_paths:
                candidates.append(candidate)
                seen_paths.add(candidate.path)

        # Strategy 3: Fuzzy search (only if no high-confidence matches yet)
        if not candidates or candidates[0].score < 0.9:
            fuzzy_matches = self._try_fuzzy_search(path)
            for candidate in fuzzy_matches:
                if candidate.path not in seen_paths:
                    candidates.append(candidate)
                    seen_paths.add(candidate.path)

        # Strategy 4: Recent files match (only if still no matches)
        if not candidates:
            recent_matches = self._try_recent_match(path)
            for candidate in recent_matches:
                if candidate.path not in seen_paths:
                    candidates.append(candidate)
                    seen_paths.add(candidate.path)

        # Sort by score descending
        candidates.sort(key=lambda c: c.score, reverse=True)

        return candidates[:max_candidates]

    def resolve_single(self, path: str, threshold: float = 0.9) -> Optional[Path]:
        """Resolve to a single path if confidence is high enough.

        Convenience method for cases where you want automatic resolution
        only when there's high confidence.

        Args:
            path: Path string to resolve.
            threshold: Minimum score for automatic selection (default: 0.9).

        Returns:
            Resolved Path if confidence >= threshold, None otherwise.

        Example:
            >>> resolved = resolver.resolve_single("utils")
            >>> if resolved:
            ...     print(f"Auto-resolved to: {resolved}")
        """
        candidates = self.resolve(path, max_candidates=1)
        if candidates and candidates[0].score >= threshold:
            return candidates[0].path
        return None

    def _try_exact(self, path: str) -> Optional[PathCandidate]:
        """Try exact path match.

        Args:
            path: Path string to check.

        Returns:
            PathCandidate with score 1.0 if path exists, None otherwise.
        """
        # Handle both absolute and relative paths
        path_obj = Path(path)
        if not path_obj.is_absolute():
            path_obj = self.workspace / path_obj

        resolved = path_obj.resolve()

        if resolved.exists() and resolved.is_file():
            return PathCandidate(
                path=resolved,
                score=1.0,
                reason="exact",
                original=path,
            )
        return None

    def _try_suffix_completion(self, path: str) -> List[PathCandidate]:
        """Try adding common file extensions.

        Args:
            path: Path string without extension.

        Returns:
            List of PathCandidate objects for existing files.
        """
        candidates: List[PathCandidate] = []

        # Only try suffix completion if path has no extension
        path_obj = Path(path)
        if path_obj.suffix:
            return candidates

        # Try with cache first (faster)
        if self.cache and self.cache.initialized:
            stem = path_obj.name
            entries = self.cache.find_by_stem(stem)

            # Filter to entries that match the path structure
            for entry in entries:
                # Check if entry path ends with the expected relative path
                try:
                    rel_path = entry.path.relative_to(self.workspace)
                    expected_parent = path_obj.parent
                    if expected_parent == Path(".") or str(rel_path.parent).endswith(str(expected_parent)):
                        # Score based on suffix priority
                        suffix_idx = self._get_suffix_priority_index(entry.suffix)
                        score = 0.95 - (suffix_idx * 0.01)  # Higher priority = higher score
                        candidates.append(PathCandidate(
                            path=entry.path,
                            score=score,
                            reason="suffix",
                            original=path,
                        ))
                except ValueError:
                    continue

            return candidates

        # Fallback: check filesystem directly
        base_path = self.workspace / path_obj if not path_obj.is_absolute() else path_obj

        for i, suffix in enumerate(self.suffix_priority):
            test_path = base_path.with_suffix(suffix)
            if test_path.exists() and test_path.is_file():
                # Score based on suffix priority
                score = 0.95 - (i * 0.01)
                candidates.append(PathCandidate(
                    path=test_path.resolve(),
                    score=score,
                    reason="suffix",
                    original=path,
                ))

        return candidates

    def _try_fuzzy_search(self, path: str) -> List[PathCandidate]:
        """Try fuzzy matching against file names.

        Args:
            path: Path string to match.

        Returns:
            List of PathCandidate objects with fuzzy matches.
        """
        candidates: List[PathCandidate] = []

        # Extract the filename part
        path_obj = Path(path)
        query = path_obj.stem if path_obj.suffix else path_obj.name

        if self.cache and self.cache.initialized:
            # Use cache for fuzzy search
            fuzzy_results = self.cache.find_fuzzy(query, threshold=self.fuzzy_threshold)

            for entry, score in fuzzy_results[:10]:  # Limit results
                # Adjust score for fuzzy matches
                adjusted_score = score * 0.9  # Cap fuzzy at 0.9
                candidates.append(PathCandidate(
                    path=entry.path,
                    score=adjusted_score,
                    reason="fuzzy",
                    original=path,
                ))
        else:
            # Fallback: limited fuzzy search on filesystem
            # This is expensive, so we limit scope
            candidates.extend(self._fuzzy_scan_directory(self.workspace, query, depth=0, max_depth=3))

        return candidates

    def _try_recent_match(self, path: str) -> List[PathCandidate]:
        """Try matching against recently modified files.

        Args:
            path: Path string to match.

        Returns:
            List of PathCandidate objects from recent files.
        """
        candidates: List[PathCandidate] = []

        if not self.cache or not self.cache.initialized:
            return candidates

        path_obj = Path(path)
        query = path_obj.stem if path_obj.suffix else path_obj.name

        recent = self.cache.find_recent(limit=50)
        for entry in recent:
            # Check for partial match
            if query.lower() in entry.name.lower() or query.lower() in entry.stem.lower():
                candidates.append(PathCandidate(
                    path=entry.path,
                    score=0.7,
                    reason="recent",
                    original=path,
                ))

        return candidates[:5]

    def _fuzzy_scan_directory(
        self,
        directory: Path,
        query: str,
        depth: int,
        max_depth: int,
    ) -> List[PathCandidate]:
        """Scan directory for fuzzy matches (fallback without cache).

        Args:
            directory: Directory to scan.
            query: Search query.
            depth: Current depth.
            max_depth: Maximum depth to scan.

        Returns:
            List of PathCandidate objects.
        """
        candidates: List[PathCandidate] = []

        if depth > max_depth:
            return candidates

        try:
            for item in directory.iterdir():
                if item.name.startswith("."):
                    continue

                if item.is_file():
                    score = self._calculate_similarity(query.lower(), item.stem.lower())
                    if score >= self.fuzzy_threshold:
                        candidates.append(PathCandidate(
                            path=item.resolve(),
                            score=score * 0.9,
                            reason="fuzzy",
                            original=query,
                        ))
                elif item.is_dir() and item.name not in ("node_modules", ".git", "__pycache__", ".venv"):
                    candidates.extend(
                        self._fuzzy_scan_directory(item, query, depth + 1, max_depth)
                    )
        except (OSError, PermissionError):
            pass

        return candidates

    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """Calculate similarity between two strings.

        Uses normalized Levenshtein distance.

        Args:
            s1: First string.
            s2: Second string.

        Returns:
            Similarity score (0.0 to 1.0).
        """
        if s1 == s2:
            return 1.0

        max_len = max(len(s1), len(s2))
        if max_len == 0:
            return 1.0

        distance = self._levenshtein(s1, s2)
        return 1.0 - (distance / max_len)

    def _levenshtein(self, s1: str, s2: str) -> int:
        """Calculate Levenshtein distance.

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
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    def _get_suffix_priority_index(self, suffix: str) -> int:
        """Get the priority index for a suffix.

        Args:
            suffix: File extension (including dot).

        Returns:
            Index in suffix_priority list, or len(suffix_priority) if not found.
        """
        try:
            return self.suffix_priority.index(suffix)
        except ValueError:
            return len(self.suffix_priority)

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"SmartPathResolver(workspace={self.workspace!r}, "
            f"fuzzy_threshold={self.fuzzy_threshold})"
        )
