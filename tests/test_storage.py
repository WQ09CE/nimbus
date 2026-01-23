"""Tests for SQLite storage layer."""

import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from nimbus.storage.sqlite import SQLiteStorage
from nimbus.core.memory import MemoryConfig, Message, PinnedItem, TieredMemoryManager
from nimbus.core.types import TaskDAG, TaskNode, TaskStatus


class TestSQLiteStorage:
    """Test suite for SQLiteStorage."""

    @pytest.fixture
    async def storage(self):
        """Create a temporary storage instance for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            storage = SQLiteStorage(db_path)
            await storage.initialize()
            yield storage
            await storage.close()

    @pytest.fixture
    async def storage_with_session(self, storage):
        """Storage with a pre-created session."""
        await storage.create_session(
            session_id="test_session",
            name="Test Session",
            workspace_path="/tmp/workspace",
        )
        return storage

    # =========================================================================
    # Session Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_create_session(self, storage):
        """Test session creation."""
        session = await storage.create_session(
            session_id="sess_001",
            name="My Session",
            workspace_path="/path/to/workspace",
            memory_type="tiered",
            planner_type="dag",
        )

        assert session["id"] == "sess_001"
        assert session["name"] == "My Session"
        assert session["workspace_path"] == "/path/to/workspace"
        assert session["memory_type"] == "tiered"
        assert session["planner_type"] == "dag"
        assert session["status"] == "active"

    @pytest.mark.asyncio
    async def test_get_session(self, storage):
        """Test session retrieval."""
        await storage.create_session(session_id="sess_001", name="Test")

        session = await storage.get_session("sess_001")
        assert session is not None
        assert session["id"] == "sess_001"
        assert session["name"] == "Test"

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, storage):
        """Test session retrieval when not found."""
        session = await storage.get_session("nonexistent")
        assert session is None

    @pytest.mark.asyncio
    async def test_list_sessions(self, storage):
        """Test session listing with pagination."""
        # Create multiple sessions
        for i in range(5):
            await storage.create_session(
                session_id=f"sess_{i:03d}",
                name=f"Session {i}",
            )

        # List with pagination
        sessions, total = await storage.list_sessions(limit=3, offset=0)
        assert len(sessions) == 3
        assert total == 5

        sessions, total = await storage.list_sessions(limit=3, offset=3)
        assert len(sessions) == 2
        assert total == 5

    @pytest.mark.asyncio
    async def test_update_session(self, storage):
        """Test session update."""
        await storage.create_session(session_id="sess_001", name="Original")

        await storage.update_session("sess_001", name="Updated", workspace_path="/new/path")

        session = await storage.get_session("sess_001")
        assert session["name"] == "Updated"
        assert session["workspace_path"] == "/new/path"

    @pytest.mark.asyncio
    async def test_delete_session_soft(self, storage):
        """Test soft session deletion."""
        await storage.create_session(session_id="sess_001", name="Test")

        await storage.delete_session("sess_001", hard_delete=False)

        session = await storage.get_session("sess_001")
        assert session["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_session_hard(self, storage):
        """Test hard session deletion."""
        await storage.create_session(session_id="sess_001", name="Test")

        await storage.delete_session("sess_001", hard_delete=True)

        session = await storage.get_session("sess_001")
        assert session is None

    @pytest.mark.asyncio
    async def test_archive_session(self, storage):
        """Test session archiving."""
        await storage.create_session(session_id="sess_001", name="Test")

        await storage.archive_session("sess_001")

        session = await storage.get_session("sess_001")
        assert session["status"] == "archived"
        assert session["archived_at"] is not None

    # =========================================================================
    # Message Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_add_message(self, storage_with_session):
        """Test message creation."""
        msg = await storage_with_session.add_message(
            message_id="msg_001",
            session_id="test_session",
            role="user",
            content="Hello, world!",
        )

        assert msg["id"] == "msg_001"
        assert msg["role"] == "user"
        assert msg["content"] == "Hello, world!"

    @pytest.mark.asyncio
    async def test_add_message_with_artifacts(self, storage_with_session):
        """Test message creation with artifacts."""
        artifacts = [
            {"id": "art_001", "type": "code", "title": "Example Code", "data": "print('hello')"}
        ]

        msg = await storage_with_session.add_message(
            message_id="msg_001",
            session_id="test_session",
            role="assistant",
            content="Here's some code",
            artifacts=artifacts,
        )

        assert msg["artifacts"] == artifacts

    @pytest.mark.asyncio
    async def test_get_messages(self, storage_with_session):
        """Test message retrieval."""
        for i in range(5):
            await storage_with_session.add_message(
                message_id=f"msg_{i:03d}",
                session_id="test_session",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
            )

        messages = await storage_with_session.get_messages("test_session")
        assert len(messages) == 5

        # Test limit
        messages = await storage_with_session.get_messages("test_session", limit=3)
        assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_get_message_count(self, storage_with_session):
        """Test message count."""
        for i in range(3):
            await storage_with_session.add_message(
                message_id=f"msg_{i:03d}",
                session_id="test_session",
                role="user",
                content=f"Message {i}",
            )

        count = await storage_with_session.get_message_count("test_session")
        assert count == 3

    # =========================================================================
    # DAG Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_save_and_get_dag(self, storage_with_session):
        """Test DAG persistence."""
        dag = TaskDAG.create(
            goal="Test Goal",
            tasks=[
                {"id": "task_001", "skill": "read_file", "params": {"path": "/test"}},
                {"id": "task_002", "skill": "analyze", "params": {}, "depends_on": ["task_001"]},
            ],
        )

        await storage_with_session.save_dag("test_session", dag)

        # Retrieve and verify
        loaded_dag = await storage_with_session.get_dag(dag.id)
        assert loaded_dag is not None
        assert loaded_dag.goal == "Test Goal"
        assert len(loaded_dag.nodes) == 2
        assert "task_001" in loaded_dag.nodes
        assert "task_002" in loaded_dag.nodes
        assert loaded_dag.nodes["task_002"].depends_on == ["task_001"]

    @pytest.mark.asyncio
    async def test_list_dags(self, storage_with_session):
        """Test DAG listing."""
        for i in range(3):
            dag = TaskDAG.create(
                goal=f"Goal {i}",
                tasks=[{"id": f"task_{i}", "skill": "test", "params": {}}],
            )
            await storage_with_session.save_dag("test_session", dag)

        dags = await storage_with_session.list_dags("test_session")
        assert len(dags) == 3

    # =========================================================================
    # Memory Checkpoint Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_save_and_load_memory_checkpoint(self, storage_with_session):
        """Test memory checkpoint persistence."""
        # Create a memory manager with state
        memory = TieredMemoryManager(session_id="test_session")

        # Add some data
        memory.pinned.append(
            PinnedItem(
                id="pin_001",
                type="user_instruction",
                content="Always be helpful",
                priority=10,
                description="User preferences",
                read_only=True,
            )
        )
        memory.working["current_task"] = "Testing"
        memory.episodic.append(Message(role="user", content="Hello"))
        memory.episodic.append(Message(role="assistant", content="Hi there!"))
        memory.episodic_summaries.append("User greeted the assistant")
        memory.semantic_cache["test query"] = ["result1", "result2"]
        memory._turn_count = 5
        memory._compression_count = 2

        # Save checkpoint
        checkpoint_id = await storage_with_session.save_memory_checkpoint(
            "test_session", memory
        )
        assert checkpoint_id is not None

        # Load checkpoint
        loaded_memory = await storage_with_session.load_memory_checkpoint("test_session")
        assert loaded_memory is not None

        # Verify pinned items
        assert len(loaded_memory.pinned) == 1
        assert loaded_memory.pinned[0].id == "pin_001"
        assert loaded_memory.pinned[0].content == "Always be helpful"
        assert loaded_memory.pinned[0].read_only is True

        # Verify working memory
        assert loaded_memory.working["current_task"] == "Testing"

        # Verify episodic memory
        assert len(loaded_memory.episodic) == 2
        assert loaded_memory.episodic[0].role == "user"
        assert loaded_memory.episodic[0].content == "Hello"

        # Verify summaries and cache
        assert loaded_memory.episodic_summaries == ["User greeted the assistant"]
        assert loaded_memory.semantic_cache == {"test query": ["result1", "result2"]}

        # Verify counters
        assert loaded_memory._turn_count == 5
        assert loaded_memory._compression_count == 2

    @pytest.mark.asyncio
    async def test_multiple_memory_checkpoints(self, storage_with_session):
        """Test multiple checkpoint versions."""
        memory = TieredMemoryManager(session_id="test_session")

        # Save multiple checkpoints
        for i in range(3):
            memory._turn_count = i + 1
            await storage_with_session.save_memory_checkpoint("test_session", memory)

        # List checkpoints
        checkpoints = await storage_with_session.list_memory_checkpoints("test_session")
        assert len(checkpoints) == 3

        # Load latest (should be turn_count=3)
        loaded = await storage_with_session.load_memory_checkpoint("test_session")
        assert loaded._turn_count == 3

        # Load specific checkpoint
        loaded = await storage_with_session.load_memory_checkpoint(
            "test_session", checkpoint_num=1
        )
        assert loaded._turn_count == 1

    @pytest.mark.asyncio
    async def test_prune_old_checkpoints(self, storage_with_session):
        """Test checkpoint pruning."""
        memory = TieredMemoryManager(session_id="test_session")

        # Save 10 checkpoints
        for i in range(10):
            memory._turn_count = i + 1
            await storage_with_session.save_memory_checkpoint("test_session", memory)

        # Prune to keep only 3
        deleted = await storage_with_session.prune_old_checkpoints("test_session", keep_count=3)
        assert deleted == 7

        checkpoints = await storage_with_session.list_memory_checkpoints("test_session")
        assert len(checkpoints) == 3

    # =========================================================================
    # Permission Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_permission_rules(self, storage):
        """Test permission rule CRUD."""
        # Set rule
        await storage.set_permission_rule("bash", "ask")
        await storage.set_permission_rule("read_file", "allow_always")
        await storage.set_permission_rule("write_file", "deny")

        # Get rules
        assert await storage.get_permission_rule("bash") == "ask"
        assert await storage.get_permission_rule("read_file") == "allow_always"
        assert await storage.get_permission_rule("write_file") == "deny"
        assert await storage.get_permission_rule("nonexistent") is None

        # List all rules
        rules = await storage.get_all_permission_rules()
        assert len(rules) == 3

        # Delete rule
        await storage.delete_permission_rule("bash")
        assert await storage.get_permission_rule("bash") is None

    @pytest.mark.asyncio
    async def test_permission_requests(self, storage_with_session):
        """Test permission request workflow."""
        # Create request
        request = await storage_with_session.create_permission_request(
            request_id="perm_001",
            session_id="test_session",
            tool="bash",
            args={"command": "ls -la"},
        )

        assert request["id"] == "perm_001"
        assert request["tool"] == "bash"
        assert request["args"] == {"command": "ls -la"}
        assert request["resolved_at"] is None

        # Get pending requests
        pending = await storage_with_session.get_pending_permission_requests("test_session")
        assert len(pending) == 1

        # Resolve request
        resolved = await storage_with_session.resolve_permission_request(
            "perm_001", "allow_once"
        )
        assert resolved["decision"] == "allow_once"
        assert resolved["resolved_at"] is not None

        # No more pending
        pending = await storage_with_session.get_pending_permission_requests("test_session")
        assert len(pending) == 0

    # =========================================================================
    # Key-Value Store Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_kv_store(self, storage):
        """Test key-value store operations."""
        # String operations
        await storage.kv_set("key1", "value1")
        assert await storage.kv_get("key1") == "value1"
        assert await storage.kv_get("nonexistent") is None

        # JSON operations
        await storage.kv_set_json("config", {"setting1": True, "setting2": [1, 2, 3]})
        config = await storage.kv_get_json("config")
        assert config == {"setting1": True, "setting2": [1, 2, 3]}

        # Delete
        await storage.kv_delete("key1")
        assert await storage.kv_get("key1") is None

    # =========================================================================
    # Utility Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_database_stats(self, storage_with_session):
        """Test database statistics."""
        # Add some data
        await storage_with_session.add_message(
            message_id="msg_001",
            session_id="test_session",
            role="user",
            content="Test",
        )

        stats = await storage_with_session.get_database_stats()

        assert stats["sessions_count"] == 1
        assert stats["messages_count"] == 1
        assert stats["database_size_bytes"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
