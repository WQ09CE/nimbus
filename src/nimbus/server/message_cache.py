"""Message cache with in-memory caching for session history.

This module provides a caching layer for conversation history that:
- Caches hot sessions in memory for fast access
- Supports TTL-based expiration with auto-renewal on access
- Limits message count to prevent memory bloat
- Thread-safe through asyncio locks

Production Note:
    The current implementation uses in-memory caching. For production
    environments with multiple server instances, consider replacing with
    Redis or another distributed cache.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MessageCache:
    """
    Message history cache with in-memory storage.

    Provides fast access to recent conversation history by caching
    messages in memory with automatic TTL-based expiration.

    Features:
    - LRU-like behavior through TTL expiration
    - Automatic renewal on cache hits
    - Configurable message limits
    - Concurrent-safe through async locks

    Attributes:
        max_messages: Maximum number of messages to keep per session.
        cache_ttl: Time-to-live for cached entries.

    Example:
        >>> cache = MessageCache(storage, max_messages=50)
        >>> history = await cache.get_history("session_123")
        >>> await cache.add_message("session_123", "user", "Hello!")
    """

    def __init__(
        self,
        storage,  # SQLiteStorage
        max_messages: int = 50,
        cache_ttl_minutes: int = 30,
    ):
        """Initialize the message cache.

        Args:
            storage: SQLiteStorage instance for persistent storage.
            max_messages: Maximum messages to cache per session.
            cache_ttl_minutes: Minutes before cache entries expire.
        """
        self._storage = storage
        self._max_messages = max_messages
        self._cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._cache: Dict[str, dict] = {}  # session_id -> {messages, expires_at}
        self._lock = asyncio.Lock()

    async def get_history(
        self,
        session_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get session conversation history with caching.

        Checks the cache first, loading from storage on cache miss.
        Cache entries are automatically renewed on access.

        Args:
            session_id: Session identifier.
            limit: Maximum messages to return. Defaults to max_messages.

        Returns:
            List of message dicts with 'role' and 'content' keys.
            Messages are ordered chronologically (oldest first).

        Example:
            >>> history = await cache.get_history("sess_abc", limit=20)
            >>> for msg in history:
            ...     print(f"{msg['role']}: {msg['content'][:50]}")
        """
        limit = limit or self._max_messages

        async with self._lock:
            # Check cache
            if session_id in self._cache:
                cache_entry = self._cache[session_id]
                if datetime.now() < cache_entry["expires_at"]:
                    # Cache hit - renew TTL
                    cache_entry["expires_at"] = datetime.now() + self._cache_ttl
                    logger.debug(f"Cache hit for session {session_id}")
                    return cache_entry["messages"][-limit:]
                else:
                    # Cache expired - remove
                    del self._cache[session_id]

        # Cache miss - load from storage
        logger.debug(f"Cache miss for session {session_id}, loading from storage")
        messages = await self._load_from_storage(session_id, limit)

        # Write to cache
        async with self._lock:
            self._cache[session_id] = {
                "messages": messages,
                "expires_at": datetime.now() + self._cache_ttl,
            }

        return messages

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        message_id: Optional[str] = None,
    ) -> None:
        """
        Add message to history (updates both cache and storage).

        Writes to persistent storage first, then updates the cache.
        If the session is not cached, only writes to storage.

        Args:
            session_id: Session identifier.
            role: Message role ("user", "assistant", "system").
            content: Message content.
            message_id: Optional message ID. Auto-generated if not provided.

        Example:
            >>> await cache.add_message(
            ...     session_id="sess_abc",
            ...     role="user",
            ...     content="What is the weather today?"
            ... )
        """
        if not message_id:
            message_id = f"msg_{uuid.uuid4().hex[:12]}"

        # Write to persistent storage
        await self._storage.add_message(
            message_id=message_id,
            session_id=session_id,
            role=role,
            content=content,
        )

        # Update cache
        async with self._lock:
            if session_id in self._cache:
                self._cache[session_id]["messages"].append(
                    {
                        "role": role,
                        "content": content,
                    }
                )
                # Limit cache size (keep 2x max to reduce thrashing)
                if len(self._cache[session_id]["messages"]) > self._max_messages * 2:
                    self._cache[session_id]["messages"] = self._cache[session_id]["messages"][
                        -self._max_messages :
                    ]
                # Renew TTL
                self._cache[session_id]["expires_at"] = datetime.now() + self._cache_ttl

    async def _load_from_storage(
        self,
        session_id: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Load messages from persistent storage.

        Args:
            session_id: Session identifier.
            limit: Maximum messages to load.

        Returns:
            List of message dicts with 'role' and 'content' keys.
        """
        try:
            result = await self._storage.get_messages(
                session_id=session_id,
                limit=limit,
                order="ASC",  # Chronological order
            )
            # Convert to simple format
            messages = []
            for msg in result:
                messages.append(
                    {
                        "role": msg.get("role"),
                        "content": msg.get("content"),
                    }
                )
            return messages
        except Exception as e:
            logger.error(f"Failed to load messages from storage: {e}")
            return []

    async def clear_cache(self, session_id: Optional[str] = None) -> None:
        """Clear cached entries.

        Args:
            session_id: Specific session to clear. If None, clears all.
        """
        async with self._lock:
            if session_id:
                self._cache.pop(session_id, None)
            else:
                self._cache.clear()

    async def cleanup_expired(self) -> int:
        """Remove expired cache entries.

        Should be called periodically to prevent memory leaks.

        Returns:
            Number of entries cleaned up.
        """
        now = datetime.now()
        expired = []
        async with self._lock:
            for session_id, entry in self._cache.items():
                if now >= entry["expires_at"]:
                    expired.append(session_id)
            for session_id in expired:
                del self._cache[session_id]
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired cache entries")
        return len(expired)

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring.

        Returns:
            Dictionary with cache stats.
        """
        return {
            "cached_sessions": len(self._cache),
            "max_messages": self._max_messages,
            "cache_ttl_minutes": self._cache_ttl.total_seconds() / 60,
        }
