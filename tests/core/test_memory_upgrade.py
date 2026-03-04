"""
Tests for Memory GC (background reflection) using NimFS.

Replaces the old test that used ProfileStore/EpisodicStore with the
NimFS-backed MemoryManagerModule.
"""

import pytest
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from nimbus.core.heart_modules.memory import MemoryManagerModule
from nimbus.core.nimfs.manager import NimFSManager
from nimbus.core.nimfs.models import MemoryCategory, MemoryScope
from nimbus.core.heart import Heart, HeartConfig


# Dummy LLM client for mocking the reflection GC
class DummyLLMClient:
    class chat:
        class completions:
            @staticmethod
            def create(*args, **kwargs):
                class Msg:
                    content = '{"entries": [{"title": "user_preference", "content": "likes Python", "tags": "preference"}]}'
                class Ch:
                    message = Msg()
                class Resp:
                    choices = [Ch()]
                return Resp()


@pytest.mark.asyncio
async def test_memory_gc_nimfs_backed():
    """Test that background GC writes extracted knowledge to NimFS."""
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Pre-populate some NimFS memory so load_context returns data for GC
        nimfs = NimFSManager(workspace_path=temp_path)
        nimfs.write_memory(
            category=MemoryCategory.EVENTS,
            title="User loves Python",
            content="The user expressed strong preference for Python over JavaScript in multiple conversations.",
            summary="User prefers Python",
            tags=["preference", "language"],
            scope=MemoryScope.PROJECT,
        )

        # Setup Heart with MemoryManagerModule
        config = HeartConfig(workspace=str(temp_path), project_id="test", tick_interval=0.1)
        heart = Heart(config)

        mm_module = MemoryManagerModule(
            llm_client=DummyLLMClient(),
            gc_interval_ticks=1,
            lock_timeout=2.0,
        )
        heart.add_module(mm_module)

        # Trigger one GC tick
        await mm_module.run_cron(heart)

        # Verify GC extracted new knowledge into NimFS
        results = nimfs.search_memory(query="user_preference", top_k=10)
        assert len(results) >= 1, "GC should have written at least one new memory entry"
        found = any("user_preference" in r.title for r in results)
        assert found, "Expected to find 'user_preference' entry written by GC"
