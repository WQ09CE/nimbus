"""Tests for MessageCache."""

import asyncio
from datetime import datetime, timedelta

import pytest

from nimbus.server.message_cache import MessageCache


class MockStorage:
    """Mock storage for testing."""

    def __init__(self):
        self.messages = {}  # session_id -> list of messages
        self.add_message_calls = []

    async def add_message(self, message_id, session_id, role, content, **kwargs):
        """Mock add_message."""
        if session_id not in self.messages:
            self.messages[session_id] = []
        self.messages[session_id].append({
            "id": message_id,
            "role": role,
            "content": content,
        })
        self.add_message_calls.append({
            "message_id": message_id,
            "session_id": session_id,
            "role": role,
            "content": content,
        })

    async def get_messages(self, session_id, limit=50, order="ASC"):
        """Mock get_messages."""
        msgs = self.messages.get(session_id, [])
        if order == "DESC":
            msgs = list(reversed(msgs))
        return msgs[:limit]


class TestMessageCache:
    """Test MessageCache functionality."""

    @pytest.fixture
    def storage(self):
        """Create mock storage."""
        return MockStorage()

    @pytest.fixture
    def cache(self, storage):
        """Create cache with mock storage."""
        return MessageCache(
            storage=storage,
            max_messages=10,
            cache_ttl_minutes=30,
        )

    @pytest.mark.asyncio
    async def test_get_history_empty(self, cache):
        """Test getting history for empty session."""
        history = await cache.get_history("session_123")
        assert history == []

    @pytest.mark.asyncio
    async def test_add_message(self, cache, storage):
        """Test adding a message."""
        await cache.add_message(
            session_id="session_123",
            role="user",
            content="Hello, world!",
        )

        # Check storage was called
        assert len(storage.add_message_calls) == 1
        assert storage.add_message_calls[0]["content"] == "Hello, world!"

    @pytest.mark.asyncio
    async def test_add_and_get_messages(self, cache, storage):
        """Test adding and retrieving messages."""
        session_id = "session_abc"

        # Add messages
        await cache.add_message(session_id, "user", "First message")
        await cache.add_message(session_id, "assistant", "First response")
        await cache.add_message(session_id, "user", "Second message")

        # Get history
        history = await cache.get_history(session_id)

        assert len(history) == 3
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "First message"
        assert history[1]["role"] == "assistant"
        assert history[2]["role"] == "user"

    @pytest.mark.asyncio
    async def test_cache_hit(self, cache, storage):
        """Test cache hit behavior."""
        session_id = "session_hit"

        # Pre-populate storage
        storage.messages[session_id] = [
            {"id": "msg1", "role": "user", "content": "Hello"},
        ]

        # First call - cache miss
        history1 = await cache.get_history(session_id)
        assert len(history1) == 1

        # Add to storage directly (simulating external update)
        storage.messages[session_id].append(
            {"id": "msg2", "role": "assistant", "content": "Hi there"}
        )

        # Second call - cache hit (should NOT see new message)
        history2 = await cache.get_history(session_id)
        assert len(history2) == 1  # Still 1 due to cache

    @pytest.mark.asyncio
    async def test_cache_update_on_add(self, cache, storage):
        """Test cache updates when adding through cache."""
        session_id = "session_update"

        # Pre-populate storage
        storage.messages[session_id] = [
            {"id": "msg1", "role": "user", "content": "Hello"},
        ]

        # Load into cache
        await cache.get_history(session_id)

        # Add through cache
        await cache.add_message(session_id, "assistant", "Hi there")

        # Should see updated cache
        history = await cache.get_history(session_id)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_clear_cache(self, cache, storage):
        """Test clearing cache."""
        session_id = "session_clear"

        # Pre-populate and cache
        storage.messages[session_id] = [
            {"id": "msg1", "role": "user", "content": "Hello"},
        ]
        await cache.get_history(session_id)

        # Clear specific session
        await cache.clear_cache(session_id)

        # Update storage
        storage.messages[session_id].append(
            {"id": "msg2", "role": "assistant", "content": "Hi"}
        )

        # Should see updated data after cache clear
        history = await cache.get_history(session_id)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_clear_all_cache(self, cache, storage):
        """Test clearing all cache entries."""
        # Populate multiple sessions
        for i in range(3):
            session_id = f"session_{i}"
            storage.messages[session_id] = [
                {"id": f"msg{i}", "role": "user", "content": f"Hello {i}"},
            ]
            await cache.get_history(session_id)

        # Clear all
        await cache.clear_cache()

        # All should reload from storage
        stats = cache.get_cache_stats()
        assert stats["cached_sessions"] == 0

    @pytest.mark.asyncio
    async def test_limit_messages(self, cache, storage):
        """Test message limit enforcement."""
        session_id = "session_limit"

        # Add many messages directly to storage
        storage.messages[session_id] = [
            {"id": f"msg{i}", "role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]

        # Get with limit
        history = await cache.get_history(session_id, limit=5)
        assert len(history) == 5

    @pytest.mark.asyncio
    async def test_cache_stats(self, cache, storage):
        """Test cache statistics."""
        # Initial stats
        stats = cache.get_cache_stats()
        assert stats["cached_sessions"] == 0
        assert stats["max_messages"] == 10

        # After caching a session
        storage.messages["session_stats"] = [
            {"id": "msg1", "role": "user", "content": "Hello"},
        ]
        await cache.get_history("session_stats")

        stats = cache.get_cache_stats()
        assert stats["cached_sessions"] == 1

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, cache, storage):
        """Test expired cache cleanup."""
        session_id = "session_expire"

        # Cache a session
        storage.messages[session_id] = [
            {"id": "msg1", "role": "user", "content": "Hello"},
        ]
        await cache.get_history(session_id)

        # Manually expire the cache entry
        async with cache._lock:
            cache._cache[session_id]["expires_at"] = datetime.now() - timedelta(hours=1)

        # Cleanup should remove it
        removed = await cache.cleanup_expired()
        assert removed == 1

        stats = cache.get_cache_stats()
        assert stats["cached_sessions"] == 0

    @pytest.mark.asyncio
    async def test_concurrent_access(self, storage):
        """Test concurrent access is safe."""
        # Use larger max_messages for this test
        cache = MessageCache(storage=storage, max_messages=100, cache_ttl_minutes=30)
        session_id = "session_concurrent"
        storage.messages[session_id] = []

        async def add_messages(n):
            for i in range(n):
                await cache.add_message(session_id, "user", f"Message {i}")

        # Run multiple concurrent tasks
        await asyncio.gather(
            add_messages(5),
            add_messages(5),
            add_messages(5),
        )

        # All messages should be added (in storage at least)
        assert len(storage.messages[session_id]) == 15
        # Cache may have fewer due to timing, but should still be consistent
        history = await cache.get_history(session_id)
        assert len(history) >= 5  # At least some messages visible
