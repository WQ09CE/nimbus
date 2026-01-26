"""Tests for SubagentContext and SubagentContextSnapshot."""

import pytest
import tempfile
import shutil
from datetime import datetime
from typing import Dict, Any

from nimbus.core.memory import (
    SubagentContext,
    SubagentContextSnapshot,
    SimpleMemory,
    TieredMemoryManager,
    MemoryConfig,
    PinnedItem,
)


@pytest.fixture
def temp_checkpoint_dir():
    """Create temporary directory for checkpoints."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def simple_memory():
    """Create a SimpleMemory with some data."""
    memory = SimpleMemory(max_turns=20)
    memory.pin("data.csv", "[csv] Sales data with 1000 rows")
    memory.pin("report.pdf", "[pdf] Q4 Financial Report")
    memory.add_turn("user", "Analyze the sales data")
    memory.add_turn("assistant", "I'll analyze the data for you.")
    memory.add_turn("user", "Show me the top products")
    memory.add_turn("assistant", "Here are the top 5 products...")
    return memory


@pytest.fixture
def tiered_memory(temp_checkpoint_dir):
    """Create a TieredMemoryManager with some data."""
    config = MemoryConfig(
        pinned_budget=1000,
        working_budget=2000,
        episodic_budget=4000,
        checkpoint_path=temp_checkpoint_dir,
    )
    memory = TieredMemoryManager(config=config, session_id="test-session")

    # Add pinned items
    memory.pin(PinnedItem(
        id="instruction",
        type="user_instruction",
        content="Always respond in Chinese",
        priority=10
    ))
    memory.pin(PinnedItem(
        id="workspace",
        type="system",
        content="/path/to/project",
        priority=5
    ))

    # Add working context
    memory.set_working("current_task", "Code review")
    memory.set_working("workspace", "/path/to/project")

    # Add conversation history (sync version)
    memory.add_turn_sync("user", "Review the auth module")
    memory.add_turn_sync("assistant", "I'll review the authentication code.")
    memory.add_turn_sync("user", "Check for security issues")
    memory.add_turn_sync("assistant", "Found a potential SQL injection...")

    return memory


class TestSubagentContextSnapshot:
    """Tests for SubagentContextSnapshot dataclass."""

    def test_snapshot_creation(self):
        """Test creating a snapshot."""
        snapshot = SubagentContextSnapshot(
            pinned_items={"key1": "value1"},
            working_context={"task": "testing"},
            recent_history=[{"role": "user", "content": "hello"}],
            system_info={"workspace": "/tmp"},
        )

        assert snapshot.pinned_items == {"key1": "value1"}
        assert snapshot.working_context == {"task": "testing"}
        assert len(snapshot.recent_history) == 1
        assert snapshot.system_info == {"workspace": "/tmp"}

    def test_snapshot_copies_data(self):
        """Test that snapshot makes copies of mutable data."""
        original_pinned = {"key1": "value1"}
        original_working = {"task": "testing"}

        snapshot = SubagentContextSnapshot(
            pinned_items=original_pinned,
            working_context=original_working,
            recent_history=[],
            system_info={},
        )

        # Modify originals
        original_pinned["key2"] = "value2"
        original_working["status"] = "done"

        # Snapshot should not be affected
        assert "key2" not in snapshot.pinned_items
        assert "status" not in snapshot.working_context


class TestSubagentContextFromSimpleMemory:
    """Tests for SubagentContext created from SimpleMemory."""

    def test_create_from_simple_memory(self, simple_memory):
        """Test creating subagent context from SimpleMemory."""
        ctx = SubagentContext.from_parent_memory(
            parent_memory=simple_memory,
            subagent_id="sub-123",
            subagent_type="eye",
            max_history=3,
        )

        assert ctx.subagent_id == "sub-123"
        assert ctx.subagent_type == "eye"
        assert ctx.memory is not None
        assert ctx.memory.get_turn_count() == 0  # Local memory is empty

    def test_snapshot_contains_pinned_items(self, simple_memory):
        """Test that snapshot contains parent's pinned items."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        snapshot = ctx.parent_snapshot
        assert "data.csv" in snapshot.pinned_items
        assert "report.pdf" in snapshot.pinned_items

    def test_snapshot_contains_recent_history(self, simple_memory):
        """Test that snapshot contains limited recent history."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye", max_history=2
        )

        snapshot = ctx.parent_snapshot
        # Should only have last 2 turns
        assert len(snapshot.recent_history) == 2
        # Should be the most recent turns
        assert "top products" in snapshot.recent_history[0]["content"]

    def test_auto_generate_id(self, simple_memory):
        """Test auto-generating subagent ID when empty."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "", "eye"
        )

        assert ctx.subagent_id.startswith("eye-")
        assert len(ctx.subagent_id) > 4


class TestSubagentContextFromTieredMemory:
    """Tests for SubagentContext created from TieredMemoryManager."""

    def test_create_from_tiered_memory(self, tiered_memory):
        """Test creating subagent context from TieredMemoryManager."""
        ctx = SubagentContext.from_parent_memory(
            parent_memory=tiered_memory,
            subagent_id="sub-456",
            subagent_type="body",
        )

        assert ctx.subagent_id == "sub-456"
        assert ctx.subagent_type == "body"

    def test_snapshot_contains_pinned_items(self, tiered_memory):
        """Test that snapshot contains parent's pinned items."""
        ctx = SubagentContext.from_parent_memory(
            tiered_memory, "sub-1", "body"
        )

        snapshot = ctx.parent_snapshot
        assert "instruction" in snapshot.pinned_items
        assert "workspace" in snapshot.pinned_items
        assert snapshot.pinned_items["instruction"] == "Always respond in Chinese"

    def test_snapshot_contains_working_context(self, tiered_memory):
        """Test that snapshot contains parent's working context."""
        ctx = SubagentContext.from_parent_memory(
            tiered_memory, "sub-1", "body"
        )

        snapshot = ctx.parent_snapshot
        assert "current_task" in snapshot.working_context
        assert snapshot.working_context["current_task"] == "Code review"


class TestSubagentContextIsolation:
    """Tests for context isolation between parent and subagent."""

    def test_subagent_modifications_dont_affect_parent(self, simple_memory):
        """Test that subagent modifications don't affect parent memory."""
        original_turn_count = simple_memory.get_turn_count()
        original_pinned_count = simple_memory.get_pinned_count()

        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        # Modify subagent's local memory
        ctx.add_turn("user", "Subagent task")
        ctx.add_turn("assistant", "Subagent response")
        ctx.memory.pin("new_file.txt", "New file metadata")

        # Parent memory should be unchanged
        assert simple_memory.get_turn_count() == original_turn_count
        assert simple_memory.get_pinned_count() == original_pinned_count

        # Subagent should have its own data
        assert ctx.memory.get_turn_count() == 2
        assert ctx.memory.get_pinned_count() == 1

    def test_subagent_modifications_dont_affect_parent_tiered(self, tiered_memory):
        """Test isolation with TieredMemoryManager."""
        original_turn_count = tiered_memory.get_turn_count()
        original_pinned_count = tiered_memory.get_pinned_count()

        ctx = SubagentContext.from_parent_memory(
            tiered_memory, "sub-1", "body"
        )

        # Modify subagent's local memory
        ctx.add_turn("user", "Subagent task")
        ctx.add_turn("assistant", "Subagent response")

        # Parent memory should be unchanged
        assert tiered_memory.get_turn_count() == original_turn_count
        assert tiered_memory.get_pinned_count() == original_pinned_count

    def test_snapshot_modifications_dont_affect_parent(self, simple_memory):
        """Test that modifying snapshot data doesn't affect parent."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        # Try to modify snapshot data (should not affect parent)
        # Note: Snapshot is frozen, but the dict contents could be modified
        # if we didn't make copies
        snapshot_pinned = ctx.parent_snapshot.pinned_items
        snapshot_pinned["hacked"] = "malicious"

        # Parent should be unaffected
        assert "hacked" not in simple_memory.pinned


class TestSubagentContextMethods:
    """Tests for SubagentContext methods."""

    def test_add_turn(self, simple_memory):
        """Test adding conversation turns to local memory."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        ctx.add_turn("user", "Find all Python files")
        ctx.add_turn("assistant", "Found 50 Python files")

        assert ctx.memory.get_turn_count() == 2
        assert ctx.memory.history[0]["role"] == "user"
        assert ctx.memory.history[1]["content"] == "Found 50 Python files"

    def test_get_context(self, simple_memory):
        """Test getting complete context."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        ctx.add_turn("user", "Local task")
        ctx.add_turn("assistant", "Local response")

        context = ctx.get_context()

        # Should contain subagent identification
        assert "Subagent: eye" in context
        assert "sub-1" in context

        # Should contain parent context
        assert "Parent Context" in context or "Key Information" in context

        # Should contain local conversation
        assert "Local task" in context
        assert "Local response" in context

    def test_record_tool_call(self, simple_memory):
        """Test recording tool calls."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        ctx.record_tool_call(
            "Glob",
            {"pattern": "**/*.py"},
            ["file1.py", "file2.py"]
        )
        ctx.record_tool_call(
            "Read",
            {"file_path": "/path/to/file.py"},
            "file contents here"
        )

        assert len(ctx._tool_calls) == 2
        assert ctx._tool_calls[0]["tool"] == "Glob"
        assert ctx._tool_calls[1]["tool"] == "Read"

    def test_get_summary(self, simple_memory):
        """Test generating execution summary."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        # Simulate subagent execution
        ctx.add_turn("user", "Find all Python files")
        ctx.add_turn("assistant", "Found 50 Python files in the project")
        ctx.record_tool_call("Glob", {"pattern": "**/*.py"}, ["file1.py"])
        ctx.record_tool_call("Glob", {"pattern": "**/*.js"}, ["file2.js"])
        ctx.record_tool_call("Read", {"file_path": "file1.py"}, "contents")

        summary = ctx.get_summary()

        # Should contain summary header
        assert "Subagent Summary: eye" in summary
        assert "sub-1" in summary

        # Should contain turn count
        assert "Turns: 2" in summary

        # Should contain tool usage
        assert "Tools Used" in summary
        assert "Glob: 2x" in summary
        assert "Read: 1x" in summary

        # Should contain result
        assert "Result" in summary
        assert "Found 50 Python files" in summary

    def test_get_summary_truncates_long_response(self, simple_memory):
        """Test that summary truncates very long responses."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        long_response = "x" * 2000
        ctx.add_turn("user", "Task")
        ctx.add_turn("assistant", long_response)

        summary = ctx.get_summary()

        # Summary should be truncated
        assert "truncated" in summary
        assert len(summary) < len(long_response) + 500

    def test_get_local_history(self, simple_memory):
        """Test getting local conversation history."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        ctx.add_turn("user", "Task 1")
        ctx.add_turn("assistant", "Response 1")

        history = ctx.get_local_history()

        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "Response 1"

        # Should be a copy
        history.append({"role": "user", "content": "hacked"})
        assert ctx.memory.get_turn_count() == 2


class TestSubagentContextReadOnly:
    """Tests for read-only behavior of parent context."""

    def test_parent_snapshot_is_accessible(self, simple_memory):
        """Test that parent snapshot is accessible."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        snapshot = ctx.parent_snapshot
        assert snapshot is not None
        assert isinstance(snapshot, SubagentContextSnapshot)

    def test_readonly_context_string_built(self, simple_memory):
        """Test that readonly context string is built."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        # Readonly context should be built
        assert ctx._readonly_context is not None
        # Should contain pinned items
        assert "data.csv" in ctx._readonly_context or "Key Information" in ctx._readonly_context

    def test_multiple_subagents_independent(self, simple_memory):
        """Test that multiple subagents are independent."""
        ctx1 = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )
        ctx2 = SubagentContext.from_parent_memory(
            simple_memory, "sub-2", "body"
        )

        # Modify ctx1
        ctx1.add_turn("user", "Task for eye")
        ctx1.add_turn("assistant", "Eye response")

        # Modify ctx2
        ctx2.add_turn("user", "Task for body")

        # They should be independent
        assert ctx1.memory.get_turn_count() == 2
        assert ctx2.memory.get_turn_count() == 1

        # Different IDs
        assert ctx1.subagent_id != ctx2.subagent_id
        assert ctx1.subagent_type != ctx2.subagent_type


class TestSubagentContextEdgeCases:
    """Tests for edge cases."""

    def test_empty_parent_memory(self):
        """Test with empty parent memory."""
        memory = SimpleMemory()

        ctx = SubagentContext.from_parent_memory(
            memory, "sub-1", "eye"
        )

        assert ctx.parent_snapshot.pinned_items == {}
        assert ctx.parent_snapshot.recent_history == []

    def test_max_history_zero(self, simple_memory):
        """Test with max_history=0."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye", max_history=0
        )

        assert ctx.parent_snapshot.recent_history == []

    def test_max_history_larger_than_actual(self, simple_memory):
        """Test with max_history larger than actual history."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye", max_history=100
        )

        # Should get all available history
        assert len(ctx.parent_snapshot.recent_history) == simple_memory.get_turn_count()

    def test_summary_with_no_assistant_response(self, simple_memory):
        """Test summary when there's no assistant response."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        ctx.add_turn("user", "Only user message")

        summary = ctx.get_summary()

        # Should still work, just no Result section
        assert "Subagent Summary" in summary
        assert "Turns: 1" in summary

    def test_summary_with_no_tool_calls(self, simple_memory):
        """Test summary when there are no tool calls."""
        ctx = SubagentContext.from_parent_memory(
            simple_memory, "sub-1", "eye"
        )

        ctx.add_turn("user", "Task")
        ctx.add_turn("assistant", "Response")

        summary = ctx.get_summary()

        # Should work without tool calls section
        assert "Subagent Summary" in summary
        assert "Tools Used" not in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
