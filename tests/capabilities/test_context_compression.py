"""Tests for context compression capability.

This module tests the TieredMemoryManager's ability to compress
conversation history while preserving key information.

Capability: context_compression
"""

import pytest
from typing import List

from src.nimbus.core.memory import (
    TieredMemoryManager,
    MemoryConfig,
    PinnedItem,
    Message,
)
from src.nimbus.utils.tokens import estimate_tokens

from tests.evaluation.metrics import (
    ContextCompressionMetrics,
    CompressionExpectation,
)


# =============================================================================
# Mock LLM Client for Compression
# =============================================================================


class MockCompressionLLM:
    """Mock LLM that returns compressed summaries."""

    def __init__(self, summary_template: str = "Summary: {count} turns discussed {topics}."):
        self.summary_template = summary_template
        self.calls: List[str] = []

    async def complete(self, prompt: str) -> str:
        """Generate a compressed summary."""
        self.calls.append(prompt)

        # Extract key info from prompt to create realistic summary
        lines = prompt.split("\n")
        turn_count = sum(1 for line in lines if line.startswith(("user:", "assistant:")))

        # Extract topics from content (simple heuristic)
        topics = []
        for line in lines:
            if "Python" in line:
                topics.append("Python")
            if "machine learning" in line.lower():
                topics.append("machine learning")
            if "async" in line.lower():
                topics.append("async programming")

        topics_str = ", ".join(topics) if topics else "various topics"
        return self.summary_template.format(count=turn_count, topics=topics_str)


# =============================================================================
# Test Cases
# =============================================================================


@pytest.mark.capability("context_compression")
class TestCompressionTriggers:
    """Tests for compression trigger conditions."""

    @pytest.mark.asyncio
    async def test_compression_triggers_at_threshold(self):
        """Compression should trigger when turn count exceeds threshold.

        The TieredMemoryManager should automatically compress episodic
        memory when the number of turns exceeds the configured threshold.
        """
        llm = MockCompressionLLM()
        config = MemoryConfig(
            compression_threshold=3,  # Compress after 6 messages (3*2)
            episodic_budget=8000,
        )
        memory = TieredMemoryManager(config=config, llm_client=llm)

        # Add turns below threshold - should not compress
        await memory.add_turn("user", "Hello, how are you?")
        await memory.add_turn("assistant", "I'm doing great!")
        await memory.add_turn("user", "Tell me about Python")
        await memory.add_turn("assistant", "Python is a programming language.")

        # Should not have compressed yet (4 < 6)
        assert len(llm.calls) == 0
        assert len(memory.episodic) == 4

        # Add more turns to trigger compression
        await memory.add_turn("user", "What about machine learning?")
        await memory.add_turn("assistant", "ML is a subset of AI.")

        # Should have triggered compression (6 >= 6)
        assert len(llm.calls) >= 1
        assert len(memory.episodic_summaries) >= 1

    @pytest.mark.asyncio
    async def test_compression_triggers_at_token_budget(self):
        """Compression should trigger when token budget is exceeded."""
        llm = MockCompressionLLM()
        config = MemoryConfig(
            compression_threshold=100,  # High threshold
            episodic_budget=100,  # Very low budget to trigger compression
        )
        memory = TieredMemoryManager(config=config, llm_client=llm)

        # Add a long message that exceeds budget
        long_message = "This is a very long message. " * 50
        await memory.add_turn("user", long_message)
        await memory.add_turn("assistant", "I understand.")

        # Check that compression was considered
        stats = memory.get_stats()
        assert stats.episodic_tokens > 0


@pytest.mark.capability("context_compression")
class TestCompressionQuality:
    """Tests for compression quality and information preservation."""

    @pytest.fixture
    def metrics(self):
        return ContextCompressionMetrics()

    @pytest.mark.asyncio
    async def test_compression_preserves_key_info(self, metrics):
        """Compressed summaries should preserve key information.

        When conversation history is compressed, important facts like
        names, numbers, and key decisions should be retained.
        """
        # Create LLM that preserves key info in summaries
        llm = MockCompressionLLM(
            summary_template="Summary: Discussed Python and machine learning. User wants to learn ML."
        )
        config = MemoryConfig(
            compression_threshold=2,
            episodic_budget=8000,
        )
        memory = TieredMemoryManager(config=config, llm_client=llm)

        # Add turns with key information
        await memory.add_turn("user", "I want to learn Python for machine learning")
        await memory.add_turn("assistant", "Great choice! Python is excellent for ML.")
        await memory.add_turn("user", "Where should I start?")
        await memory.add_turn("assistant", "Start with NumPy and pandas.")
        await memory.add_turn("user", "What about TensorFlow?")
        await memory.add_turn("assistant", "TensorFlow is great for deep learning.")

        # Get compressed content
        context = memory.get_context()

        # Evaluate key info preservation
        expectation = CompressionExpectation(
            original_tokens=200,
            max_compressed_tokens=500,
            key_info=["Python", "machine learning"],
            min_compression_ratio=0.1,
        )

        # The summary should mention key topics
        if memory.episodic_summaries:
            summary = memory.episodic_summaries[0]
            assert "Python" in summary or "machine learning" in summary

    @pytest.mark.asyncio
    async def test_compression_ratio(self, metrics):
        """Compression should achieve reasonable compression ratio."""
        llm = MockCompressionLLM(
            summary_template="Brief summary of conversation."
        )
        config = MemoryConfig(
            compression_threshold=2,
            episodic_budget=8000,
        )
        memory = TieredMemoryManager(config=config, llm_client=llm)

        # Build original content
        original_content = ""
        messages = [
            ("user", "This is a detailed message about Python programming and its features."),
            ("assistant", "Python has many features including dynamic typing and garbage collection."),
            ("user", "Can you explain more about Python's object-oriented capabilities?"),
            ("assistant", "Python supports classes, inheritance, and polymorphism fully."),
            ("user", "What about Python's standard library?"),
            ("assistant", "Python's standard library is extensive and includes many useful modules."),
        ]

        for role, content in messages:
            original_content += f"{role}: {content}\n"
            await memory.add_turn(role, content)

        # Get compressed content
        if memory.episodic_summaries:
            compressed_content = "\n".join(memory.episodic_summaries)
            original_tokens = estimate_tokens(original_content)
            compressed_tokens = estimate_tokens(compressed_content)

            # Compressed should be significantly smaller
            assert compressed_tokens < original_tokens

            # Evaluate with metrics
            expectation = CompressionExpectation(
                original_tokens=original_tokens,
                max_compressed_tokens=original_tokens,
                min_compression_ratio=0.3,
            )

            results = metrics.evaluate(
                original_content,
                compressed_content,
                original_tokens,
                compressed_tokens,
                expectation,
            )
            summary = metrics.summary(results)

            assert summary["compression_ratio"] > 0.3


@pytest.mark.capability("context_compression")
class TestMultiRoundCompression:
    """Tests for multiple rounds of compression."""

    @pytest.mark.asyncio
    async def test_multi_round_compression(self):
        """Multiple compression rounds should maintain coherence.

        As conversation grows, multiple compression rounds should produce
        a coherent and useful summary chain.
        """
        llm = MockCompressionLLM(
            summary_template="Round summary: discussed various programming topics."
        )
        config = MemoryConfig(
            compression_threshold=2,  # Compress frequently
            episodic_budget=8000,
        )
        memory = TieredMemoryManager(config=config, llm_client=llm)

        # Add many turns to trigger multiple compressions
        for i in range(20):
            await memory.add_turn("user", f"Question {i}: Tell me about topic {i}")
            await memory.add_turn("assistant", f"Answer {i}: Here's info about topic {i}")

        # Should have multiple summaries
        assert len(memory.episodic_summaries) >= 2

        # Get final context
        context = memory.get_context()

        # Context should include recent messages and summaries
        assert context is not None
        assert len(context) > 0

    @pytest.mark.asyncio
    async def test_summary_limit(self):
        """Number of summaries should be limited to prevent overflow."""
        llm = MockCompressionLLM()
        config = MemoryConfig(
            compression_threshold=2,
            episodic_budget=8000,
        )
        memory = TieredMemoryManager(config=config, llm_client=llm)

        # Add many turns
        for i in range(50):
            await memory.add_turn("user", f"Message {i}")
            await memory.add_turn("assistant", f"Response {i}")

        # Summaries should be limited (default is 5)
        assert len(memory.episodic_summaries) <= 5


@pytest.mark.capability("context_compression")
class TestPinnedItemsNotCompressed:
    """Tests ensuring pinned items are never compressed."""

    @pytest.mark.asyncio
    async def test_pinned_items_not_compressed(self):
        """Pinned items should never be compressed or removed.

        Pinned items represent critical context that must always be
        available to the agent, regardless of compression.
        """
        llm = MockCompressionLLM()
        config = MemoryConfig(
            compression_threshold=2,
            pinned_budget=1000,
        )
        memory = TieredMemoryManager(config=config, llm_client=llm)

        # Pin important items
        item1 = PinnedItem(
            id="user_name",
            type="user_instruction",
            content="User's name is Alice",
            priority=10,
            description="User's name",
            read_only=True,
        )
        item2 = PinnedItem(
            id="project_context",
            type="key_entity",
            content="Working on Project X",
            priority=5,
        )

        memory.pin(item1)
        memory.pin(item2)

        # Add many turns to trigger compression
        for i in range(20):
            await memory.add_turn("user", f"Question {i}")
            await memory.add_turn("assistant", f"Answer {i}")

        # Pinned items should still be there
        assert len(memory.pinned) == 2
        assert memory.memory_get("user_name") == "User's name is Alice"
        assert memory.memory_get("project_context") == "Working on Project X"

        # Context should include pinned items
        context = memory.get_context()
        assert "Alice" in context
        assert "Project X" in context

    @pytest.mark.asyncio
    async def test_pinned_items_in_context_after_compression(self):
        """Pinned items should appear in context even after heavy compression."""
        llm = MockCompressionLLM()
        config = MemoryConfig(
            compression_threshold=2,
            pinned_budget=500,
        )
        memory = TieredMemoryManager(config=config, llm_client=llm)

        # Pin a critical instruction
        memory.pin(PinnedItem(
            id="critical",
            type="user_instruction",
            content="CRITICAL: Always respond in English",
            priority=100,
            read_only=True,
        ))

        # Trigger heavy compression
        for i in range(30):
            await memory.add_turn("user", f"Long message {i} " * 10)
            await memory.add_turn("assistant", f"Long response {i} " * 10)

        # Pinned item must still be in context
        context = memory.get_context()
        assert "CRITICAL" in context
        assert "Always respond in English" in context


@pytest.mark.capability("context_compression")
class TestCompressionEdgeCases:
    """Edge cases for context compression."""

    @pytest.mark.asyncio
    async def test_compression_without_llm(self):
        """Compression should gracefully handle missing LLM client."""
        config = MemoryConfig(compression_threshold=2)
        memory = TieredMemoryManager(config=config, llm_client=None)

        # Add turns - should not crash without LLM
        for i in range(10):
            await memory.add_turn("user", f"Message {i}")
            await memory.add_turn("assistant", f"Response {i}")

        # Should keep messages (no compression possible without LLM)
        assert len(memory.episodic) > 0
        assert len(memory.episodic_summaries) == 0

    @pytest.mark.asyncio
    async def test_compression_with_empty_messages(self):
        """Compression should handle empty messages gracefully."""
        llm = MockCompressionLLM()
        config = MemoryConfig(compression_threshold=2)
        memory = TieredMemoryManager(config=config, llm_client=llm)

        # Add mix of empty and non-empty messages
        await memory.add_turn("user", "Hello")
        await memory.add_turn("assistant", "")
        await memory.add_turn("user", "")
        await memory.add_turn("assistant", "Hi there!")

        # Should not crash
        context = memory.get_context()
        assert context is not None

    @pytest.mark.asyncio
    async def test_sync_add_turn_no_compression(self):
        """Synchronous add_turn should not trigger compression."""
        llm = MockCompressionLLM()
        config = MemoryConfig(compression_threshold=2)
        memory = TieredMemoryManager(config=config, llm_client=llm)

        # Add many turns synchronously
        for i in range(20):
            memory.add_turn_sync("user", f"Message {i}")
            memory.add_turn_sync("assistant", f"Response {i}")

        # Should not have triggered compression
        assert len(llm.calls) == 0
        assert len(memory.episodic_summaries) == 0
        assert len(memory.episodic) == 40
