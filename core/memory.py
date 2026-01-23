"""Memory management for conversation history.

This module provides:
- SimpleMemory: Basic memory with conversation history and pinned items
- TieredMemoryManager: Advanced multi-tier memory with compression and checkpointing
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from ..utils.tokens import estimate_tokens
from ..utils.checkpoint import CheckpointManager


class MemoryTier(Enum):
    """Memory tier classification."""
    PINNED = "pinned"        # Never compressed
    WORKING = "working"      # Current task state
    EPISODIC = "episodic"    # Conversation history
    SEMANTIC = "semantic"    # RAG cache


@dataclass
class PinnedItem:
    """Pinned item that is never compressed.

    Inspired by Letta's Block design with description and read_only fields.
    """
    id: str
    type: str                    # "file_meta", "user_instruction", "key_entity"
    content: str
    priority: int = 0            # Higher priority = shown first
    created_at: datetime = field(default_factory=datetime.now)
    # Letta-inspired fields
    description: str = ""        # Description for Agent (what this memory is for)
    read_only: bool = False      # If True, Agent cannot modify this item

    def estimate_tokens(self) -> int:
        """Estimate token count for this item."""
        return estimate_tokens(self.content)


@dataclass
class Message:
    """Conversation message."""
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)

    def estimate_tokens(self) -> int:
        """Estimate token count for this message."""
        return estimate_tokens(self.content)


@dataclass
class MemoryConfig:
    """Configuration for TieredMemoryManager."""
    pinned_budget: int = 1000         # Pinned tier token budget
    working_budget: int = 4000        # Working tier token budget
    episodic_budget: int = 8000       # Episodic tier token budget
    semantic_budget: int = 4000       # Semantic tier token budget
    compression_threshold: int = 6    # Trigger compression after N turns
    checkpoint_interval: int = 5      # Auto checkpoint every N turns
    checkpoint_path: str = "./.checkpoints"  # Checkpoint storage path


@dataclass
class MemoryStats:
    """Statistics for memory usage."""
    pinned_tokens: int
    working_tokens: int
    episodic_tokens: int
    semantic_tokens: int
    total_tokens: int
    compression_count: int
    turn_count: int


class LLMClientProtocol(Protocol):
    """Protocol for LLM client used in compression."""
    async def complete(self, prompt: str) -> str:
        """Generate completion for prompt."""
        ...


class TieredMemoryManager:
    """
    Multi-tiered memory manager with compression and checkpointing.

    Architecture:
    +--------------------------------------------------+
    |              Context Window (16K)                |
    +--------------------------------------------------+
    |  Pinned Context  |  1K  | Never compressed       |
    +------------------+------+------------------------+
    |  Working Memory  |  4K  | Current task state     |
    +------------------+------+------------------------+
    |  Episodic Memory |  8K  | Conversation + summary |
    +------------------+------+------------------------+
    |  Semantic Memory |  4K  | RAG cache              |
    +--------------------------------------------------+
    """

    def __init__(
        self,
        config: Optional[MemoryConfig] = None,
        llm_client: Optional[LLMClientProtocol] = None,
        session_id: str = "default"
    ):
        """Initialize tiered memory manager.

        Args:
            config: Memory configuration.
            llm_client: LLM client for compression (optional).
            session_id: Session identifier for checkpointing.
        """
        self.config = config or MemoryConfig()
        self.llm = llm_client
        self.session_id = session_id

        # Four-tier storage
        self.pinned: List[PinnedItem] = []
        self.working: Dict[str, Any] = {}
        self.episodic: List[Message] = []
        self.episodic_summaries: List[str] = []
        self.semantic_cache: Dict[str, List[str]] = {}

        # Statistics
        self._compression_count = 0
        self._turn_count = 0

        # Checkpoint manager
        self._checkpoint_mgr = CheckpointManager(self.config.checkpoint_path)

    # =========================================================================
    # Pinned Tier
    # =========================================================================

    def pin(self, item: PinnedItem) -> bool:
        """Add a pinned item.

        Args:
            item: Item to pin.

        Returns:
            True if pinned successfully, False if budget exceeded.
        """
        # Check if already exists
        for i, existing in enumerate(self.pinned):
            if existing.id == item.id:
                self.pinned[i] = item
                return True

        # Check budget
        current_tokens = sum(p.estimate_tokens() for p in self.pinned)
        if current_tokens + item.estimate_tokens() > self.config.pinned_budget:
            return False

        self.pinned.append(item)
        return True

    def unpin(self, item_id: str) -> bool:
        """Remove a pinned item.

        Args:
            item_id: ID of item to remove.

        Returns:
            True if removed, False if not found.
        """
        for i, item in enumerate(self.pinned):
            if item.id == item_id:
                self.pinned.pop(i)
                return True
        return False

    def get_pinned(self) -> List[PinnedItem]:
        """Get all pinned items sorted by priority (descending).

        Returns:
            List of pinned items.
        """
        return sorted(self.pinned, key=lambda x: -x.priority)

    # =========================================================================
    # Agent Memory Operations (Letta-inspired)
    # =========================================================================

    def memory_append(self, item_id: str, content: str) -> bool:
        """Append content to an existing pinned item (Agent-callable).

        Inspired by Letta's core_memory_append.

        Args:
            item_id: ID of the pinned item.
            content: Content to append.

        Returns:
            True if successful, False if item not found or read-only.
        """
        for item in self.pinned:
            if item.id == item_id:
                if item.read_only:
                    return False
                new_content = item.content + content
                # Check budget
                new_tokens = estimate_tokens(new_content)
                old_tokens = item.estimate_tokens()
                current_total = sum(p.estimate_tokens() for p in self.pinned)
                if current_total - old_tokens + new_tokens > self.config.pinned_budget:
                    return False
                item.content = new_content
                return True
        return False

    def memory_replace(self, item_id: str, old_content: str, new_content: str) -> bool:
        """Replace content in a pinned item (Agent-callable).

        Inspired by Letta's core_memory_replace.

        Args:
            item_id: ID of the pinned item.
            old_content: Content to find.
            new_content: Content to replace with.

        Returns:
            True if successful, False if item not found, read-only, or content not found.
        """
        for item in self.pinned:
            if item.id == item_id:
                if item.read_only:
                    return False
                if old_content not in item.content:
                    return False
                updated = item.content.replace(old_content, new_content, 1)
                # Check budget
                new_tokens = estimate_tokens(updated)
                old_tokens = item.estimate_tokens()
                current_total = sum(p.estimate_tokens() for p in self.pinned)
                if current_total - old_tokens + new_tokens > self.config.pinned_budget:
                    return False
                item.content = updated
                return True
        return False

    def memory_get(self, item_id: str) -> Optional[str]:
        """Get content of a pinned item by ID (Agent-callable).

        Args:
            item_id: ID of the pinned item.

        Returns:
            Content string or None if not found.
        """
        for item in self.pinned:
            if item.id == item_id:
                return item.content
        return None

    def memory_list(self) -> List[Dict[str, Any]]:
        """List all pinned items with metadata (Agent-callable).

        Returns a simplified view suitable for Agent consumption.

        Returns:
            List of dicts with id, type, description, read_only, token_count.
        """
        return [
            {
                "id": item.id,
                "type": item.type,
                "description": item.description,
                "read_only": item.read_only,
                "token_count": item.estimate_tokens(),
                "preview": item.content[:100] + "..." if len(item.content) > 100 else item.content
            }
            for item in self.get_pinned()
        ]

    # =========================================================================
    # Working Tier
    # =========================================================================

    def set_working(self, key: str, value: Any) -> None:
        """Set a working memory value.

        Args:
            key: Key name.
            value: Value to store.
        """
        self.working[key] = value

    def get_working(self, key: str, default: Any = None) -> Any:
        """Get a working memory value.

        Args:
            key: Key name.
            default: Default value if not found.

        Returns:
            Stored value or default.
        """
        return self.working.get(key, default)

    def clear_working(self) -> None:
        """Clear all working memory."""
        self.working.clear()

    # =========================================================================
    # Episodic Tier
    # =========================================================================

    async def add_turn(self, role: str, content: str) -> None:
        """Add a conversation turn, triggering compression if needed.

        Args:
            role: Message role ("user", "assistant", "system").
            content: Message content.
        """
        self.episodic.append(Message(role=role, content=content))
        self._turn_count += 1

        # Check if compression needed
        if self._should_compress():
            await self._compress_episodic()

        # Check if checkpoint needed
        if self._turn_count % self.config.checkpoint_interval == 0:
            await self.checkpoint()

    def add_turn_sync(self, role: str, content: str) -> None:
        """Synchronous version of add_turn (no compression).

        Args:
            role: Message role.
            content: Message content.
        """
        self.episodic.append(Message(role=role, content=content))
        self._turn_count += 1

    def _should_compress(self) -> bool:
        """Check if episodic memory needs compression.

        Returns:
            True if compression should be triggered.
        """
        # Method 1: Turn count trigger
        if len(self.episodic) >= self.config.compression_threshold * 2:
            return True

        # Method 2: Token budget exceeded
        if self._estimate_episodic_tokens() > self.config.episodic_budget:
            return True

        return False

    async def _compress_episodic(self) -> None:
        """Compress oldest turns into summary."""
        if not self.llm or len(self.episodic) < self.config.compression_threshold:
            return

        # Extract oldest N turns
        to_compress = self.episodic[:self.config.compression_threshold]
        self.episodic = self.episodic[self.config.compression_threshold:]

        # Generate summary via LLM
        text = "\n".join(f"{m.role}: {m.content}" for m in to_compress)
        prompt = f"""请将以下对话压缩为简洁的摘要（100字以内），保留关键信息：

{text}

摘要："""

        try:
            summary = await self.llm.complete(prompt)
            self.episodic_summaries.append(summary.strip())
            self._compression_count += 1

            # Limit summary count
            if len(self.episodic_summaries) > 5:
                self.episodic_summaries = self.episodic_summaries[-5:]
        except Exception:
            # If compression fails, keep original messages
            self.episodic = to_compress + self.episodic

    def _estimate_episodic_tokens(self) -> int:
        """Estimate total tokens in episodic tier.

        Returns:
            Estimated token count.
        """
        msg_tokens = sum(m.estimate_tokens() for m in self.episodic)
        summary_tokens = sum(estimate_tokens(s) for s in self.episodic_summaries)
        return msg_tokens + summary_tokens

    # =========================================================================
    # Semantic Tier
    # =========================================================================

    def cache_semantic(self, query: str, results: List[str]) -> None:
        """Cache RAG results for a query.

        Args:
            query: Query string.
            results: Retrieved results.
        """
        # Simple LRU-style: limit cache size
        if len(self.semantic_cache) >= 10:
            # Remove oldest entry
            oldest_key = next(iter(self.semantic_cache))
            del self.semantic_cache[oldest_key]

        self.semantic_cache[query] = results

    def get_semantic(self, query: str) -> Optional[List[str]]:
        """Get cached RAG results.

        Args:
            query: Query string.

        Returns:
            Cached results or None.
        """
        return self.semantic_cache.get(query)

    def clear_semantic(self) -> None:
        """Clear semantic cache."""
        self.semantic_cache.clear()

    # =========================================================================
    # Context Assembly
    # =========================================================================

    def get_context(self, current_goal: str = "") -> str:
        """Assemble complete context from all tiers.

        Args:
            current_goal: Current task goal (optional).

        Returns:
            Formatted context string.
        """
        parts = []

        # 1. Pinned (highest priority, never compressed)
        if self.pinned:
            pinned_lines = []
            for p in self.get_pinned():
                desc = f" ({p.description})" if p.description else ""
                ro = " [只读]" if p.read_only else ""
                pinned_lines.append(f"- [{p.type}]{desc}{ro}: {p.content}")
            pinned_text = "\n".join(pinned_lines)
            parts.append(f"## 关键信息（请始终记住）\n{pinned_text}")

        # 2. Working (current task state)
        if self.working:
            working_text = "\n".join(f"- {k}: {v}" for k, v in self.working.items())
            parts.append(f"## 当前任务状态\n{working_text}")

        # 3. Episodic summaries (compressed history)
        if self.episodic_summaries:
            parts.append(f"## 历史摘要\n" + "\n---\n".join(self.episodic_summaries[-3:]))

        # 4. Recent episodic (recent conversation)
        if self.episodic:
            recent = self.episodic[-10:]  # Last 10 turns
            history_text = "\n".join(f"{m.role}: {m.content}" for m in recent)
            parts.append(f"## 最近对话\n{history_text}")

        return "\n\n".join(parts)

    # =========================================================================
    # Checkpoint
    # =========================================================================

    async def checkpoint(self) -> str:
        """Save current state to checkpoint.

        Returns:
            Path to checkpoint file.
        """
        data = {
            "session_id": self.session_id,
            "turn_count": self._turn_count,
            "compression_count": self._compression_count,
            "pinned": [
                {
                    "id": p.id,
                    "type": p.type,
                    "content": p.content,
                    "priority": p.priority,
                    "created_at": p.created_at.isoformat(),
                    "description": p.description,
                    "read_only": p.read_only
                }
                for p in self.pinned
            ],
            "working": self.working,
            "episodic": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp.isoformat()
                }
                for m in self.episodic
            ],
            "episodic_summaries": self.episodic_summaries,
        }

        return await self._checkpoint_mgr.save(self.session_id, data)

    async def restore(self) -> bool:
        """Restore state from latest checkpoint.

        Returns:
            True if restored successfully, False if no checkpoint found.
        """
        data = await self._checkpoint_mgr.load_latest(self.session_id)
        if not data:
            return False

        self._turn_count = data.get("turn_count", 0)
        self._compression_count = data.get("compression_count", 0)

        # Restore pinned
        self.pinned = [
            PinnedItem(
                id=p["id"],
                type=p["type"],
                content=p["content"],
                priority=p.get("priority", 0),
                created_at=datetime.fromisoformat(p["created_at"]),
                description=p.get("description", ""),
                read_only=p.get("read_only", False)
            )
            for p in data.get("pinned", [])
        ]

        # Restore working
        self.working = data.get("working", {})

        # Restore episodic
        self.episodic = [
            Message(
                role=m["role"],
                content=m["content"],
                timestamp=datetime.fromisoformat(m["timestamp"])
            )
            for m in data.get("episodic", [])
        ]

        # Restore summaries
        self.episodic_summaries = data.get("episodic_summaries", [])

        return True

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> MemoryStats:
        """Get memory usage statistics.

        Returns:
            MemoryStats with current usage.
        """
        pinned_tokens = sum(p.estimate_tokens() for p in self.pinned)
        working_tokens = sum(estimate_tokens(str(v)) for v in self.working.values())
        episodic_tokens = self._estimate_episodic_tokens()
        semantic_tokens = sum(
            sum(estimate_tokens(r) for r in results)
            for results in self.semantic_cache.values()
        )

        return MemoryStats(
            pinned_tokens=pinned_tokens,
            working_tokens=working_tokens,
            episodic_tokens=episodic_tokens,
            semantic_tokens=semantic_tokens,
            total_tokens=pinned_tokens + working_tokens + episodic_tokens + semantic_tokens,
            compression_count=self._compression_count,
            turn_count=self._turn_count
        )

    # =========================================================================
    # Clear / Reset
    # =========================================================================

    def clear(self) -> None:
        """Clear all memory."""
        self.pinned.clear()
        self.working.clear()
        self.episodic.clear()
        self.episodic_summaries.clear()
        self.semantic_cache.clear()
        self._compression_count = 0
        self._turn_count = 0

    def clear_history(self) -> None:
        """Clear only episodic history, keep pinned and working."""
        self.episodic.clear()
        self.episodic_summaries.clear()

    def get_turn_count(self) -> int:
        """Get current turn count."""
        return self._turn_count

    def get_pinned_count(self) -> int:
        """Get number of pinned items."""
        return len(self.pinned)


class SimpleMemory:
    """Manages conversation history and pinned context."""

    def __init__(self, max_turns: int = 20):
        """Initialize memory with maximum turn limit.

        Args:
            max_turns: Maximum number of conversation turns to retain.
        """
        self.max_turns = max_turns
        self.history: List[Dict] = []
        self.pinned: Dict[str, str] = {}  # filename -> metadata

    def add_turn(self, role: str, content: str) -> None:
        """Add a conversation turn.

        Args:
            role: Either 'user' or 'assistant'.
            content: The message content.
        """
        self.history.append({"role": role, "content": content})
        # Trim if exceeds max_turns
        if len(self.history) > self.max_turns:
            self.history = self.history[-self.max_turns:]

    def get_context(self, recent_count: int = 10) -> str:
        """Assemble context from pinned items and recent history.

        Args:
            recent_count: Number of recent turns to include.

        Returns:
            Formatted context string.
        """
        parts = []

        # Add pinned context (file metadata)
        if self.pinned:
            parts.append("## Uploaded Files")
            for filename, metadata in self.pinned.items():
                parts.append(f"- {filename}: {metadata}")
            parts.append("")

        # Add recent conversation history
        recent = self.history[-recent_count:] if self.history else []
        if recent:
            parts.append("## Recent Conversation")
            for turn in recent:
                role = turn["role"].capitalize()
                content = turn["content"]
                # Truncate long content
                if len(content) > 500:
                    content = content[:500] + "..."
                parts.append(f"{role}: {content}")

        return "\n".join(parts)

    def pin(self, filename: str, metadata: str) -> None:
        """Pin file metadata to persistent context.

        Args:
            filename: Name of the uploaded file.
            metadata: File type and summary information.
        """
        self.pinned[filename] = metadata

    def unpin(self, filename: str) -> Optional[str]:
        """Remove pinned file metadata.

        Args:
            filename: Name of the file to unpin.

        Returns:
            The removed metadata, or None if not found.
        """
        return self.pinned.pop(filename, None)

    def clear(self) -> None:
        """Clear all history and pinned context."""
        self.history.clear()
        self.pinned.clear()

    def clear_history(self) -> None:
        """Clear only conversation history, keep pinned items."""
        self.history.clear()

    def get_turn_count(self) -> int:
        """Get the current number of turns in history."""
        return len(self.history)

    def get_pinned_count(self) -> int:
        """Get the number of pinned items."""
        return len(self.pinned)
