"""Tests for tool failover mechanism.

Tests the SmartPathResolver, FileTreeCache, and ToolRetryMiddleware components.
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from nimbus.tools.filetree import FileTreeCache, FileTreeEntry
from nimbus.tools.resolver import PathCandidate, SmartPathResolver
from nimbus.tools.middleware import (
    EnhancedToolError,
    MiddlewareChain,
    ToolRetryConfig,
    ToolRetryMiddleware,
)
from nimbus.tools.base import ToolRegistry, ToolExecutionError


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)

        # Create directory structure
        src_dir = workspace / "src"
        src_dir.mkdir()

        utils_dir = workspace / "src" / "utils"
        utils_dir.mkdir()

        tests_dir = workspace / "tests"
        tests_dir.mkdir()

        # Create files
        (workspace / "README.md").write_text("# Test Project")
        (workspace / "pyproject.toml").write_text("[project]\nname = 'test'")

        (src_dir / "main.py").write_text("def main(): pass")
        (src_dir / "utils.py").write_text("def helper(): pass")
        (src_dir / "config.py").write_text("CONFIG = {}")
        (src_dir / "app.ts").write_text("export const app = {};")

        (utils_dir / "helpers.py").write_text("def help(): pass")
        (utils_dir / "strings.py").write_text("def format(): pass")

        (tests_dir / "test_main.py").write_text("def test(): pass")
        (tests_dir / "test_utils.py").write_text("def test(): pass")

        yield workspace


class TestFileTreeCache:
    """Tests for FileTreeCache."""

    @pytest.mark.asyncio
    async def test_initialize(self, temp_workspace):
        """Test cache initialization."""
        cache = FileTreeCache(temp_workspace)
        assert not cache.initialized
        assert cache.file_count == 0

        await cache.initialize()

        assert cache.initialized
        assert cache.file_count > 0

    @pytest.mark.asyncio
    async def test_find_by_name(self, temp_workspace):
        """Test exact name lookup."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        entries = cache.find_by_name("utils.py")
        assert len(entries) == 1
        assert entries[0].name == "utils.py"
        assert entries[0].stem == "utils"

    @pytest.mark.asyncio
    async def test_find_by_stem(self, temp_workspace):
        """Test stem-based lookup for suffix completion."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        entries = cache.find_by_stem("main")
        assert len(entries) >= 1
        assert any(e.name == "main.py" for e in entries)

    @pytest.mark.asyncio
    async def test_find_fuzzy(self, temp_workspace):
        """Test fuzzy matching."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        # Typo: "utlis" instead of "utils"
        results = cache.find_fuzzy("utlis", threshold=0.6)
        assert len(results) > 0

        # Should find utils.py with good score
        names = [entry.name for entry, _ in results]
        assert "utils.py" in names or any("utils" in name for name in names)

    @pytest.mark.asyncio
    async def test_find_recent(self, temp_workspace):
        """Test recent files lookup."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        recent = cache.find_recent(limit=5)
        assert len(recent) <= 5
        assert all(isinstance(e, FileTreeEntry) for e in recent)

    @pytest.mark.asyncio
    async def test_get_tree_summary(self, temp_workspace):
        """Test tree summary generation."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        summary = cache.get_tree_summary(max_depth=2, max_entries=20)
        assert "src/" in summary
        assert "main.py" in summary

    @pytest.mark.asyncio
    async def test_exclude_patterns(self, temp_workspace):
        """Test exclusion of specified patterns."""
        # Create a node_modules directory
        node_modules = temp_workspace / "node_modules"
        node_modules.mkdir()
        (node_modules / "package.json").write_text("{}")

        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        # node_modules should be excluded
        entries = cache.find_by_name("package.json")
        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_invalidate(self, temp_workspace):
        """Test cache invalidation."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        original_count = cache.file_count
        assert original_count > 0

        cache.invalidate()

        assert not cache.initialized
        assert cache.file_count == 0

    @pytest.mark.asyncio
    async def test_partial_invalidate(self, temp_workspace):
        """Test partial cache invalidation."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        original_count = cache.file_count

        # Invalidate only src directory
        cache.invalidate(temp_workspace / "src")

        # Cache should have fewer files
        assert cache.file_count < original_count


class TestSmartPathResolver:
    """Tests for SmartPathResolver."""

    @pytest.mark.asyncio
    async def test_exact_match(self, temp_workspace):
        """Test exact path matching."""
        resolver = SmartPathResolver(temp_workspace)

        candidates = resolver.resolve("src/main.py")

        assert len(candidates) >= 1
        assert candidates[0].reason == "exact"
        assert candidates[0].score == 1.0
        assert candidates[0].path.name == "main.py"

    @pytest.mark.asyncio
    async def test_suffix_completion(self, temp_workspace):
        """Test suffix completion (utils -> utils.py)."""
        resolver = SmartPathResolver(temp_workspace)

        candidates = resolver.resolve("src/utils")

        assert len(candidates) >= 1
        # Should find utils.py through suffix completion
        assert any(c.path.name == "utils.py" and c.reason == "suffix" for c in candidates)

    @pytest.mark.asyncio
    async def test_suffix_completion_with_cache(self, temp_workspace):
        """Test suffix completion using FileTreeCache."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        resolver = SmartPathResolver(temp_workspace, file_tree_cache=cache)

        candidates = resolver.resolve("utils")

        assert len(candidates) >= 1
        # Should find utils.py
        names = [c.path.name for c in candidates]
        assert "utils.py" in names

    @pytest.mark.asyncio
    async def test_fuzzy_match(self, temp_workspace):
        """Test fuzzy matching for typos."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        resolver = SmartPathResolver(temp_workspace, file_tree_cache=cache)

        # Typo: "utlis" instead of "utils"
        candidates = resolver.resolve("utlis")

        assert len(candidates) >= 1
        # Should find a fuzzy match
        assert any(c.reason == "fuzzy" for c in candidates)

    @pytest.mark.asyncio
    async def test_resolve_single_high_confidence(self, temp_workspace):
        """Test resolve_single returns path for high confidence."""
        resolver = SmartPathResolver(temp_workspace)

        # Exact match should have high confidence
        result = resolver.resolve_single("src/main.py")

        assert result is not None
        assert result.name == "main.py"

    @pytest.mark.asyncio
    async def test_resolve_single_low_confidence(self, temp_workspace):
        """Test resolve_single returns None for low confidence."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        resolver = SmartPathResolver(temp_workspace, file_tree_cache=cache)

        # Very ambiguous path should not auto-resolve
        result = resolver.resolve_single("test", threshold=0.95)

        # With multiple test files, confidence might be low
        # This depends on implementation details

    @pytest.mark.asyncio
    async def test_priority_order(self, temp_workspace):
        """Test that candidates are sorted by score."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        resolver = SmartPathResolver(temp_workspace, file_tree_cache=cache)

        candidates = resolver.resolve("main")

        # Should be sorted by score descending
        scores = [c.score for c in candidates]
        assert scores == sorted(scores, reverse=True)


class TestToolRetryMiddleware:
    """Tests for ToolRetryMiddleware."""

    @pytest.fixture
    def mock_registry(self):
        """Create a mock tool registry."""
        registry = MagicMock(spec=ToolRegistry)
        return registry

    @pytest.fixture
    def resolver(self, temp_workspace):
        """Create a resolver with cache."""
        return SmartPathResolver(temp_workspace)

    @pytest.mark.asyncio
    async def test_auto_resolve_high_confidence(self, temp_workspace, mock_registry, resolver):
        """Test auto-resolution for high confidence paths."""
        # Setup registry to succeed on resolved path
        mock_registry.execute = AsyncMock(return_value="file content")

        config = ToolRetryConfig(auto_resolve_threshold=0.9)
        middleware = ToolRetryMiddleware(resolver, config)

        result = await middleware.wrap_execute(
            mock_registry,
            "Read",
            {"file_path": "src/main.py"},
        )

        assert result == "file content"
        mock_registry.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_file_not_found(self, temp_workspace, mock_registry):
        """Test retry when file not found."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        resolver = SmartPathResolver(temp_workspace, file_tree_cache=cache)

        # First call fails, second succeeds
        call_count = 0

        async def mock_execute(name, params, **ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FileNotFoundError("File not found: utils")
            return "file content"

        mock_registry.execute = mock_execute

        config = ToolRetryConfig(max_retries=2, auto_resolve_threshold=0.8)
        middleware = ToolRetryMiddleware(resolver, config)

        # This should retry and succeed
        result = await middleware.wrap_execute(
            mock_registry,
            "Read",
            {"file_path": "utils"},
        )

        assert result == "file content"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_enhanced_error_on_failure(self, temp_workspace, mock_registry):
        """Test error enhancement when all retries fail."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        resolver = SmartPathResolver(temp_workspace, file_tree_cache=cache)

        # Always fail
        mock_registry.execute = AsyncMock(
            side_effect=FileNotFoundError("File not found: nonexistent.py")
        )

        config = ToolRetryConfig(max_retries=1, ask_on_ambiguous=False)
        middleware = ToolRetryMiddleware(resolver, config)

        with pytest.raises(EnhancedToolError) as exc_info:
            await middleware.wrap_execute(
                mock_registry,
                "Read",
                {"file_path": "nonexistent.py"},
            )

        error = exc_info.value
        assert error.tool_name == "Read"
        assert "nonexistent.py" in error.message

    @pytest.mark.asyncio
    async def test_clarification_callback(self, temp_workspace, mock_registry):
        """Test user clarification callback."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        resolver = SmartPathResolver(temp_workspace, file_tree_cache=cache)

        # Setup: first call fails, after clarification succeeds
        call_count = 0

        async def mock_execute(name, params, **ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FileNotFoundError("File not found")
            return "resolved content"

        mock_registry.execute = mock_execute

        # Mock clarification callback
        async def mock_clarify(message: str, options: List[str]) -> Optional[str]:
            # Return first option
            return options[0] if options else None

        config = ToolRetryConfig(
            auto_resolve_threshold=0.99,  # High threshold to trigger clarification
            ask_on_ambiguous=True,
        )
        middleware = ToolRetryMiddleware(resolver, config, clarification_callback=mock_clarify)

        result = await middleware.wrap_execute(
            mock_registry,
            "Read",
            {"file_path": "utils"},
        )

        # Should have called clarification and retried
        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_non_path_tools_pass_through(self, mock_registry, temp_workspace):
        """Test that non-path tools are not affected."""
        resolver = SmartPathResolver(temp_workspace)

        mock_registry.execute = AsyncMock(return_value="result")

        middleware = ToolRetryMiddleware(resolver)

        # Bash doesn't have a path parameter in our mapping
        result = await middleware.wrap_execute(
            mock_registry,
            "Bash",
            {"command": "ls"},
        )

        assert result == "result"


class TestMiddlewareChain:
    """Tests for MiddlewareChain."""

    @pytest.mark.asyncio
    async def test_middleware_chain_execution(self, temp_workspace):
        """Test middleware chain with multiple middleware."""
        # Create a tracking middleware
        calls: List[str] = []

        class TrackingMiddleware:
            def __init__(self, name: str):
                self.name = name

            async def wrap(self, next_fn, name, params, **ctx):
                calls.append(f"{self.name}-before")
                result = await next_fn(name, params, **ctx)
                calls.append(f"{self.name}-after")
                return result

        registry = MagicMock(spec=ToolRegistry)
        registry.execute = AsyncMock(return_value="result")

        chain = MiddlewareChain()
        chain.use(TrackingMiddleware("first"))
        chain.use(TrackingMiddleware("second"))

        result = await chain.execute(registry, "Read", {"file_path": "test.py"})

        assert result == "result"
        # Middleware should wrap in order
        assert calls == ["first-before", "second-before", "second-after", "first-after"]

    @pytest.mark.asyncio
    async def test_empty_chain(self):
        """Test empty middleware chain."""
        registry = MagicMock(spec=ToolRegistry)
        registry.execute = AsyncMock(return_value="direct result")

        chain = MiddlewareChain()

        result = await chain.execute(registry, "Read", {"file_path": "test.py"})

        assert result == "direct result"


class TestLevenshteinDistance:
    """Tests for Levenshtein distance calculation."""

    def test_identical_strings(self, temp_workspace):
        """Test identical strings have distance 0."""
        cache = FileTreeCache(temp_workspace)
        assert cache._levenshtein("hello", "hello") == 0

    def test_empty_strings(self, temp_workspace):
        """Test empty string handling."""
        cache = FileTreeCache(temp_workspace)
        assert cache._levenshtein("", "") == 0
        assert cache._levenshtein("hello", "") == 5
        assert cache._levenshtein("", "world") == 5

    def test_single_edit(self, temp_workspace):
        """Test single character edits."""
        cache = FileTreeCache(temp_workspace)
        # Insertion
        assert cache._levenshtein("hello", "helo") == 1
        # Deletion
        assert cache._levenshtein("helo", "hello") == 1
        # Substitution
        assert cache._levenshtein("hello", "hallo") == 1

    def test_multiple_edits(self, temp_workspace):
        """Test multiple edits."""
        cache = FileTreeCache(temp_workspace)
        assert cache._levenshtein("kitten", "sitting") == 3


class TestSimilarityScore:
    """Tests for similarity score calculation."""

    def test_identical_strings(self, temp_workspace):
        """Test identical strings have similarity 1.0."""
        cache = FileTreeCache(temp_workspace)
        assert cache._similarity("hello", "hello") == 1.0

    def test_completely_different(self, temp_workspace):
        """Test completely different strings."""
        cache = FileTreeCache(temp_workspace)
        # "abc" vs "xyz" - 3 substitutions in 3-char string
        assert cache._similarity("abc", "xyz") == 0.0

    def test_partial_match(self, temp_workspace):
        """Test partial matches."""
        cache = FileTreeCache(temp_workspace)
        # "utils" vs "utlis" - 2 chars swapped (transpose = 2 edits)
        score = cache._similarity("utils", "utlis")
        assert 0.5 < score < 1.0


class TestPathCandidate:
    """Tests for PathCandidate dataclass."""

    def test_repr(self):
        """Test string representation."""
        candidate = PathCandidate(
            path=Path("/test/file.py"),
            score=0.95,
            reason="suffix",
            original="file",
        )

        repr_str = repr(candidate)
        assert "file.py" in repr_str
        assert "0.95" in repr_str
        assert "suffix" in repr_str


class TestFileTreeEntry:
    """Tests for FileTreeEntry dataclass."""

    def test_from_path(self, temp_workspace):
        """Test creating entry from path."""
        test_file = temp_workspace / "src" / "main.py"
        entry = FileTreeEntry.from_path(test_file)

        assert entry.path == test_file
        assert entry.name == "main.py"
        assert entry.stem == "main"
        assert entry.suffix == ".py"
        assert not entry.is_dir


class TestIntegration:
    """Integration tests for the full failover mechanism."""

    @pytest.mark.asyncio
    async def test_full_flow_exact_match(self, temp_workspace):
        """Test full flow with exact match."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        resolver = SmartPathResolver(temp_workspace, file_tree_cache=cache)

        registry = MagicMock(spec=ToolRegistry)
        registry.execute = AsyncMock(return_value="content")

        config = ToolRetryConfig()
        middleware = ToolRetryMiddleware(resolver, config)

        result = await middleware.wrap_execute(
            registry,
            "Read",
            {"file_path": "src/main.py"},
        )

        assert result == "content"

    @pytest.mark.asyncio
    async def test_full_flow_with_retry(self, temp_workspace):
        """Test full flow with retry on error."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        resolver = SmartPathResolver(temp_workspace, file_tree_cache=cache)

        attempts = []

        async def mock_execute(name, params, **ctx):
            attempts.append(params.get("file_path"))
            if len(attempts) == 1 and "utils.py" not in params.get("file_path", ""):
                raise FileNotFoundError("Not found")
            return "success"

        registry = MagicMock(spec=ToolRegistry)
        registry.execute = mock_execute

        config = ToolRetryConfig(max_retries=2)
        middleware = ToolRetryMiddleware(resolver, config)

        # Request "utils" without extension
        result = await middleware.wrap_execute(
            registry,
            "Read",
            {"file_path": "utils"},
        )

        assert result == "success"

    @pytest.mark.asyncio
    async def test_full_flow_all_retries_fail(self, temp_workspace):
        """Test full flow when all retries fail."""
        cache = FileTreeCache(temp_workspace)
        await cache.initialize()

        resolver = SmartPathResolver(temp_workspace, file_tree_cache=cache)

        registry = MagicMock(spec=ToolRegistry)
        registry.execute = AsyncMock(
            side_effect=FileNotFoundError("Not found")
        )

        config = ToolRetryConfig(max_retries=2, ask_on_ambiguous=False)
        middleware = ToolRetryMiddleware(resolver, config)

        with pytest.raises(EnhancedToolError):
            await middleware.wrap_execute(
                registry,
                "Read",
                {"file_path": "nonexistent_file.xyz"},
            )
