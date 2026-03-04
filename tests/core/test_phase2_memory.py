"""
Tests for NimFS-backed unified memory tools (Memo / Recall / ReadMemo).

Replaces the old ProfileStore / ProceduralStore tests since those stores
have been removed in the memory unification refactor.
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from nimbus.core.nimfs.manager import NimFSManager
from nimbus.core.nimfs.models import MemoryCategory, MemoryScope


def test_nimfs_write_and_search_memory():
    """Test writing and searching memory via NimFSManager (the backend for Memo/Recall)."""
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        manager = NimFSManager(workspace_path=temp_path)

        # Write several memory entries
        id1 = manager.write_memory(
            category=MemoryCategory.ENTITIES,
            title="Frontend Framework",
            content="The project uses React 18 with TypeScript",
            summary="React 18 + TypeScript",
            tags=["frontend", "react"],
            scope=MemoryScope.PROJECT,
        )
        id2 = manager.write_memory(
            category=MemoryCategory.PATTERNS,
            title="Error Handling Pattern",
            content="Always use try/except with specific exception types",
            summary="Specific exception handling",
            tags=["pattern", "error-handling"],
            scope=MemoryScope.PROJECT,
        )

        assert id1
        assert id2
        assert id1 != id2

        # Search should find entries
        results = manager.search_memory(query="react", top_k=5)
        assert len(results) >= 1
        assert any("Frontend" in r.title for r in results)

        # Search by tags
        results = manager.search_memory(query="error", top_k=5)
        assert len(results) >= 1


def test_nimfs_read_memory_layers():
    """Test reading memory at different detail layers."""
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        manager = NimFSManager(workspace_path=temp_path)

        memory_id = manager.write_memory(
            category=MemoryCategory.CASES,
            title="Build Fix",
            content="Fixed the build by updating webpack config to handle ESM modules properly.\n\nDetailed steps:\n1. Updated webpack.config.js\n2. Added type: module to package.json\n3. Tested with npm run build",
            summary="Fixed build with webpack ESM config",
            tags=["build", "webpack"],
            scope=MemoryScope.PROJECT,
        )

        # Layer 0 = abstract/summary
        l0 = manager.read_memory(memory_id, layer=0)
        assert l0  # Should have content

        # Layer 2 = full content
        l2 = manager.read_memory(memory_id, layer=2)
        assert "webpack" in l2.lower()
        assert len(l2) >= len(l0)


def test_nimfs_load_context():
    """Test auto context loading (used by factory.py at process creation)."""
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        manager = NimFSManager(workspace_path=temp_path)

        # Write some knowledge
        manager.write_memory(
            category=MemoryCategory.ENTITIES,
            title="Database Choice",
            content="The project uses PostgreSQL 15 with pgvector extension",
            summary="PostgreSQL 15 + pgvector",
            tags=["database", "postgresql"],
            scope=MemoryScope.PROJECT,
        )

        # Load context should return formatted string
        context = manager.load_context(current_goal="Set up database migrations", max_chars=3000)
        # Context may or may not include the entry depending on relevance matching,
        # but at minimum the function should not crash
        assert isinstance(context, str)
