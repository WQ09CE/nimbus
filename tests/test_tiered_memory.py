"""Tests for TieredMemoryManager."""

import pytest
import asyncio
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

# Note: Only async test functions should use @pytest.mark.asyncio decorator

from nimbus.core.memory import (
    TieredMemoryManager,
    MemoryConfig,
    PinnedItem,
    Message,
    MemoryTier,
    MemoryStats,
)


class MockLLMClient:
    """Mock LLM client for testing compression."""

    def __init__(self, response: str = "This is a summary of the conversation."):
        self.response = response
        self.call_count = 0
        self.last_prompt = None

    async def complete(self, prompt: str) -> str:
        self.call_count += 1
        self.last_prompt = prompt
        return self.response


@pytest.fixture
def temp_checkpoint_dir():
    """Create temporary directory for checkpoints."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def memory_config(temp_checkpoint_dir):
    """Create test memory config."""
    return MemoryConfig(
        pinned_budget=500,
        working_budget=2000,
        episodic_budget=4000,
        semantic_budget=2000,
        compression_threshold=4,
        checkpoint_interval=3,
        checkpoint_path=temp_checkpoint_dir,
    )


@pytest.fixture
def memory(memory_config):
    """Create TieredMemoryManager instance."""
    return TieredMemoryManager(config=memory_config, session_id="test-session")


@pytest.fixture
def memory_with_llm(memory_config):
    """Create TieredMemoryManager with mock LLM."""
    llm = MockLLMClient()
    return TieredMemoryManager(config=memory_config, llm_client=llm, session_id="test-session"), llm


class TestPinnedTier:
    """Tests for pinned memory tier."""

    def test_pin_and_unpin(self, memory):
        """Test basic pin and unpin operations."""
        item = PinnedItem(
            id="test-1",
            type="user_instruction",
            content="Always respond in Chinese",
            priority=10
        )

        # Pin item
        result = memory.pin(item)
        assert result is True
        assert len(memory.pinned) == 1

        # Get pinned items
        pinned = memory.get_pinned()
        assert len(pinned) == 1
        assert pinned[0].id == "test-1"

        # Unpin item
        result = memory.unpin("test-1")
        assert result is True
        assert len(memory.pinned) == 0

        # Unpin non-existent item
        result = memory.unpin("non-existent")
        assert result is False

    def test_pin_budget_limit(self, memory):
        """Test that pin respects budget limit."""
        # Create item that exceeds budget
        large_content = "x" * 2000  # ~666 tokens, exceeds 500 budget
        item = PinnedItem(
            id="large-item",
            type="user_instruction",
            content=large_content
        )

        result = memory.pin(item)
        assert result is False
        assert len(memory.pinned) == 0

    def test_pin_priority_sorting(self, memory):
        """Test that get_pinned returns items sorted by priority."""
        items = [
            PinnedItem(id="low", type="test", content="low priority", priority=1),
            PinnedItem(id="high", type="test", content="high priority", priority=100),
            PinnedItem(id="medium", type="test", content="medium priority", priority=50),
        ]

        for item in items:
            memory.pin(item)

        pinned = memory.get_pinned()
        assert pinned[0].id == "high"
        assert pinned[1].id == "medium"
        assert pinned[2].id == "low"

    def test_pin_update_existing(self, memory):
        """Test that pinning existing item updates it."""
        item1 = PinnedItem(id="test", type="test", content="original", priority=1)
        item2 = PinnedItem(id="test", type="test", content="updated", priority=2)

        memory.pin(item1)
        memory.pin(item2)

        assert len(memory.pinned) == 1
        assert memory.pinned[0].content == "updated"
        assert memory.pinned[0].priority == 2


class TestWorkingTier:
    """Tests for working memory tier."""

    def test_working_memory(self, memory):
        """Test basic working memory operations."""
        # Set values
        memory.set_working("current_task", "Write a report")
        memory.set_working("status", "in_progress")

        # Get values
        assert memory.get_working("current_task") == "Write a report"
        assert memory.get_working("status") == "in_progress"
        assert memory.get_working("non_existent") is None
        assert memory.get_working("non_existent", "default") == "default"

        # Clear working memory
        memory.clear_working()
        assert memory.get_working("current_task") is None


class TestEpisodicTier:
    """Tests for episodic memory tier."""

    @pytest.mark.asyncio
    async def test_add_turn(self, memory):
        """Test adding conversation turns."""
        await memory.add_turn("user", "Hello")
        await memory.add_turn("assistant", "Hi there!")

        assert len(memory.episodic) == 2
        assert memory.episodic[0].role == "user"
        assert memory.episodic[1].role == "assistant"
        assert memory._turn_count == 2

    def test_add_turn_sync(self, memory):
        """Test synchronous turn addition."""
        memory.add_turn_sync("user", "Hello")
        memory.add_turn_sync("assistant", "Hi!")

        assert len(memory.episodic) == 2

    @pytest.mark.asyncio
    async def test_compression_triggered(self, memory_with_llm):
        """Test that compression is triggered after threshold."""
        memory, llm = memory_with_llm

        # Add turns up to compression threshold * 2
        for i in range(8):  # threshold=4, so 8 triggers compression
            role = "user" if i % 2 == 0 else "assistant"
            await memory.add_turn(role, f"Message {i}")

        # Compression should have been triggered
        assert llm.call_count >= 1
        assert len(memory.episodic_summaries) >= 1

    @pytest.mark.asyncio
    async def test_compression_preserves_recent(self, memory_with_llm):
        """Test that compression keeps recent messages."""
        memory, llm = memory_with_llm

        # Add 10 turns
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            await memory.add_turn(role, f"Message {i}")

        # Recent messages should still exist
        assert len(memory.episodic) > 0
        # Summaries should exist from compression
        assert len(memory.episodic_summaries) > 0


class TestSemanticTier:
    """Tests for semantic (RAG cache) tier."""

    def test_cache_semantic(self, memory):
        """Test semantic caching."""
        memory.cache_semantic("what is AI?", ["AI is...", "Machine learning..."])

        results = memory.get_semantic("what is AI?")
        assert results is not None
        assert len(results) == 2

        # Non-existent query
        assert memory.get_semantic("unknown") is None

    def test_cache_limit(self, memory):
        """Test that cache respects size limit."""
        # Add more than limit (10)
        for i in range(15):
            memory.cache_semantic(f"query {i}", [f"result {i}"])

        # Should only keep 10 most recent
        assert len(memory.semantic_cache) <= 10

    def test_clear_semantic(self, memory):
        """Test clearing semantic cache."""
        memory.cache_semantic("query", ["result"])
        memory.clear_semantic()
        assert len(memory.semantic_cache) == 0


class TestContextAssembly:
    """Tests for context assembly."""

    @pytest.mark.asyncio
    async def test_get_context_structure(self, memory):
        """Test that context includes all tiers."""
        # Add pinned item
        memory.pin(PinnedItem(id="p1", type="instruction", content="Be helpful"))

        # Add working memory
        memory.set_working("task", "testing")

        # Add episodic
        await memory.add_turn("user", "Hello")
        await memory.add_turn("assistant", "Hi!")

        context = memory.get_context()

        assert "关键信息" in context  # Pinned section
        assert "当前任务状态" in context  # Working section
        assert "最近对话" in context  # Episodic section
        assert "Be helpful" in context
        assert "task: testing" in context
        assert "Hello" in context

    @pytest.mark.asyncio
    async def test_get_context_with_summaries(self, memory_with_llm):
        """Test context includes summaries after compression."""
        memory, llm = memory_with_llm

        # Trigger compression
        for i in range(10):
            await memory.add_turn("user" if i % 2 == 0 else "assistant", f"Msg {i}")

        if memory.episodic_summaries:
            context = memory.get_context()
            assert "历史摘要" in context


class TestCheckpoint:
    """Tests for checkpoint and restore."""

    @pytest.mark.asyncio
    async def test_checkpoint_and_restore(self, memory, temp_checkpoint_dir):
        """Test checkpoint save and restore."""
        # Setup state
        memory.pin(PinnedItem(id="p1", type="test", content="pinned content"))
        memory.set_working("key", "value")
        await memory.add_turn("user", "Hello")

        # Checkpoint
        filepath = await memory.checkpoint()
        assert Path(filepath).exists()

        # Create new memory and restore
        new_memory = TieredMemoryManager(
            config=MemoryConfig(checkpoint_path=temp_checkpoint_dir),
            session_id="test-session"
        )

        result = await new_memory.restore()
        assert result is True

        # Verify restored state
        assert len(new_memory.pinned) == 1
        assert new_memory.pinned[0].content == "pinned content"
        assert new_memory.working.get("key") == "value"
        assert len(new_memory.episodic) == 1

    @pytest.mark.asyncio
    async def test_restore_no_checkpoint(self, memory):
        """Test restore returns False when no checkpoint exists."""
        result = await memory.restore()
        assert result is False

    @pytest.mark.asyncio
    async def test_auto_checkpoint(self, memory, temp_checkpoint_dir):
        """Test automatic checkpointing based on interval."""
        # interval is 3, so after 3 turns should checkpoint
        for i in range(3):
            await memory.add_turn("user", f"Message {i}")

        # Check that checkpoint was created
        checkpoints = list(Path(temp_checkpoint_dir).glob("test-session_*.json"))
        assert len(checkpoints) >= 1


class TestStats:
    """Tests for memory statistics."""

    @pytest.mark.asyncio
    async def test_stats(self, memory):
        """Test get_stats returns correct values."""
        memory.pin(PinnedItem(id="p1", type="test", content="some content"))
        memory.set_working("key", "value")
        await memory.add_turn("user", "Hello world")
        memory.cache_semantic("query", ["result one", "result two"])

        stats = memory.get_stats()

        assert isinstance(stats, MemoryStats)
        assert stats.pinned_tokens > 0
        assert stats.working_tokens > 0
        assert stats.episodic_tokens > 0
        assert stats.semantic_tokens > 0
        assert stats.total_tokens > 0
        assert stats.turn_count == 1
        assert stats.compression_count == 0


class TestClear:
    """Tests for clear operations."""

    @pytest.mark.asyncio
    async def test_clear_all(self, memory):
        """Test clearing all memory."""
        memory.pin(PinnedItem(id="p1", type="test", content="content"))
        memory.set_working("key", "value")
        await memory.add_turn("user", "Hello")
        memory.cache_semantic("query", ["result"])

        memory.clear()

        assert len(memory.pinned) == 0
        assert len(memory.working) == 0
        assert len(memory.episodic) == 0
        assert len(memory.semantic_cache) == 0
        assert memory._turn_count == 0

    @pytest.mark.asyncio
    async def test_clear_history(self, memory):
        """Test clearing only history."""
        memory.pin(PinnedItem(id="p1", type="test", content="content"))
        await memory.add_turn("user", "Hello")

        memory.clear_history()

        assert len(memory.pinned) == 1  # Pinned preserved
        assert len(memory.episodic) == 0  # History cleared


class TestMessage:
    """Tests for Message dataclass."""

    def test_message_token_estimation(self):
        """Test message token estimation."""
        msg = Message(role="user", content="Hello world!")
        tokens = msg.estimate_tokens()
        assert tokens > 0

    def test_message_chinese_tokens(self):
        """Test token estimation for Chinese text."""
        msg = Message(role="user", content="你好世界")
        tokens = msg.estimate_tokens()
        assert tokens > 0


class TestPinnedItem:
    """Tests for PinnedItem dataclass."""

    def test_pinned_item_creation(self):
        """Test PinnedItem creation."""
        item = PinnedItem(
            id="test",
            type="instruction",
            content="test content",
            priority=5
        )

        assert item.id == "test"
        assert item.type == "instruction"
        assert item.priority == 5
        assert isinstance(item.created_at, datetime)

    def test_pinned_item_token_estimation(self):
        """Test PinnedItem token estimation."""
        item = PinnedItem(id="test", type="test", content="Hello world!")
        tokens = item.estimate_tokens()
        assert tokens > 0
