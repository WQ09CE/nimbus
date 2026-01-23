"""Tests for Checkpoint persistence functionality."""

import asyncio
import json
import pytest
from datetime import datetime
from pathlib import Path

from nimbus.core.checkpoint import (
    CheckpointMeta,
    CheckpointSaver,
    JsonCheckpointSaver,
)
from nimbus.core.types import (
    TaskDAG,
    TaskNode,
    TaskStatus,
    RuntimeConfig,
)
from nimbus.core.runtime import AsyncRuntime


class TestCheckpointMeta:
    """Tests for CheckpointMeta dataclass."""

    def test_create_meta(self):
        """Test creating checkpoint metadata."""
        now = datetime.now()
        meta = CheckpointMeta(
            checkpoint_id="20240101T120000_000000",
            dag_id="dag_test123",
            timestamp=now,
            completed_nodes=3,
            total_nodes=5,
        )

        assert meta.checkpoint_id == "20240101T120000_000000"
        assert meta.dag_id == "dag_test123"
        assert meta.timestamp == now
        assert meta.completed_nodes == 3
        assert meta.total_nodes == 5

    def test_meta_serialization(self):
        """Test metadata serialization round-trip."""
        now = datetime.now()
        meta = CheckpointMeta(
            checkpoint_id="20240101T120000_000000",
            dag_id="dag_test123",
            timestamp=now,
            completed_nodes=3,
            total_nodes=5,
        )

        # Serialize
        data = meta.to_dict()
        assert data["checkpoint_id"] == "20240101T120000_000000"
        assert data["dag_id"] == "dag_test123"
        assert data["completed_nodes"] == 3
        assert data["total_nodes"] == 5

        # Deserialize
        restored = CheckpointMeta.from_dict(data)
        assert restored.checkpoint_id == meta.checkpoint_id
        assert restored.dag_id == meta.dag_id
        assert restored.completed_nodes == meta.completed_nodes
        assert restored.total_nodes == meta.total_nodes


class TestJsonCheckpointSaver:
    """Tests for JsonCheckpointSaver."""

    def test_save_and_load(self, tmp_path):
        """Test basic save and load functionality."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        # Create a test DAG
        dag = TaskDAG.create("Test goal", [
            {"id": "t1", "skill": "search", "params": {"query": "test"}, "depends_on": []},
            {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ])

        # Mark first task as completed
        dag.nodes["t1"].status = TaskStatus.COMPLETED
        dag.nodes["t1"].result = "search result"
        dag.nodes["t1"].started_at = datetime.now()
        dag.nodes["t1"].finished_at = datetime.now()

        # Save checkpoint
        checkpoint_id = saver.save(dag)
        assert checkpoint_id
        assert len(checkpoint_id) > 0

        # Verify file was created
        dag_dir = tmp_path / dag.id
        assert dag_dir.exists()
        assert (dag_dir / f"{checkpoint_id}.json").exists()
        assert (dag_dir / "latest.json").is_symlink()

        # Load checkpoint
        loaded = saver.load(dag.id)
        assert loaded is not None
        assert loaded.id == dag.id
        assert loaded.goal == dag.goal
        assert len(loaded.nodes) == 2
        assert loaded.nodes["t1"].status == TaskStatus.COMPLETED
        assert loaded.nodes["t1"].result == "search result"
        assert loaded.nodes["t2"].status == TaskStatus.PENDING

    def test_load_specific_checkpoint(self, tmp_path):
        """Test loading a specific checkpoint by ID."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "test", "params": {}, "depends_on": []},
        ])

        # Save first checkpoint
        cp1 = saver.save(dag)

        # Modify and save second checkpoint
        dag.nodes["t1"].status = TaskStatus.COMPLETED
        dag.nodes["t1"].result = "done"
        cp2 = saver.save(dag)

        # Load specific checkpoint (first one)
        loaded = saver.load(dag.id, checkpoint_id=cp1)
        assert loaded is not None
        assert loaded.nodes["t1"].status == TaskStatus.PENDING

        # Load latest (should be second)
        latest = saver.load(dag.id)
        assert latest is not None
        assert latest.nodes["t1"].status == TaskStatus.COMPLETED

    def test_load_nonexistent(self, tmp_path):
        """Test loading nonexistent checkpoint returns None."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        result = saver.load("nonexistent_dag")
        assert result is None

        result = saver.load("nonexistent_dag", checkpoint_id="fake_id")
        assert result is None

    def test_list_checkpoints(self, tmp_path):
        """Test listing checkpoints."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "test", "params": {}, "depends_on": []},
        ])

        # Save multiple checkpoints
        cp1 = saver.save(dag)
        dag.nodes["t1"].status = TaskStatus.RUNNING
        cp2 = saver.save(dag)
        dag.nodes["t1"].status = TaskStatus.COMPLETED
        cp3 = saver.save(dag)

        # List checkpoints
        checkpoints = saver.list(dag.id)
        assert len(checkpoints) == 3

        # Should be sorted newest first
        assert checkpoints[0].checkpoint_id == cp3
        assert checkpoints[1].checkpoint_id == cp2
        assert checkpoints[2].checkpoint_id == cp1

        # Verify metadata
        assert checkpoints[0].completed_nodes == 1
        assert checkpoints[2].completed_nodes == 0

    def test_list_empty(self, tmp_path):
        """Test listing checkpoints for nonexistent DAG."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        checkpoints = saver.list("nonexistent")
        assert checkpoints == []

    def test_delete_specific(self, tmp_path):
        """Test deleting a specific checkpoint."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "test", "params": {}, "depends_on": []},
        ])

        cp1 = saver.save(dag)
        cp2 = saver.save(dag)

        # Delete first checkpoint
        deleted = saver.delete(dag.id, cp1)
        assert deleted == 1

        # Verify only one remains
        checkpoints = saver.list(dag.id)
        assert len(checkpoints) == 1
        assert checkpoints[0].checkpoint_id == cp2

    def test_delete_all(self, tmp_path):
        """Test deleting all checkpoints for a DAG."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "test", "params": {}, "depends_on": []},
        ])

        saver.save(dag)
        saver.save(dag)
        saver.save(dag)

        # Delete all
        deleted = saver.delete(dag.id)
        assert deleted == 3

        # Verify none remain
        checkpoints = saver.list(dag.id)
        assert len(checkpoints) == 0

    def test_delete_nonexistent(self, tmp_path):
        """Test deleting nonexistent checkpoint."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        deleted = saver.delete("nonexistent", "fake_id")
        assert deleted == 0

    def test_cleanup_old(self, tmp_path):
        """Test cleanup of old checkpoints."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "test", "params": {}, "depends_on": []},
        ])

        # Save 7 checkpoints
        for i in range(7):
            dag.nodes["t1"].result = f"result_{i}"
            saver.save(dag)

        # Cleanup, keeping only 3
        deleted = saver.cleanup_old(dag.id, keep_count=3)
        assert deleted == 4

        # Verify only 3 remain
        checkpoints = saver.list(dag.id)
        assert len(checkpoints) == 3

    def test_default_directory(self):
        """Test default directory creation."""
        import os

        # Use a custom home-based path for testing
        saver = JsonCheckpointSaver()
        expected_path = Path(os.path.expanduser("~/.nimbus/checkpoints"))

        assert saver.base_dir == expected_path


class TestTaskNodeFromDict:
    """Tests for TaskNode.from_dict."""

    def test_basic_from_dict(self):
        """Test basic deserialization."""
        data = {
            "id": "t1",
            "skill": "search",
            "params": {"query": "test"},
            "depends_on": ["t0"],
            "status": "completed",
            "result": {"data": "result"},
            "error": None,
            "started_at": "2024-01-01T12:00:00",
            "finished_at": "2024-01-01T12:00:01",
            "is_checkpoint": True,
        }

        node = TaskNode.from_dict(data)

        assert node.id == "t1"
        assert node.skill == "search"
        assert node.params == {"query": "test"}
        assert node.depends_on == ["t0"]
        assert node.status == TaskStatus.COMPLETED
        assert node.result == {"data": "result"}
        assert node.is_checkpoint is True
        assert node.started_at is not None
        assert node.finished_at is not None

    def test_from_dict_minimal(self):
        """Test deserialization with minimal data."""
        data = {
            "id": "t1",
            "skill": "test",
        }

        node = TaskNode.from_dict(data)

        assert node.id == "t1"
        assert node.skill == "test"
        assert node.params == {}
        assert node.depends_on == []
        assert node.status == TaskStatus.PENDING
        assert node.result is None
        assert node.error is None

    def test_roundtrip(self):
        """Test serialization round-trip."""
        node = TaskNode(
            id="t1",
            skill="search",
            params={"query": "test"},
            depends_on=["t0"],
            status=TaskStatus.COMPLETED,
            result="done",
            started_at=datetime(2024, 1, 1, 12, 0, 0),
            finished_at=datetime(2024, 1, 1, 12, 0, 1),
            is_checkpoint=True,
        )

        data = node.to_dict()
        restored = TaskNode.from_dict(data)

        assert restored.id == node.id
        assert restored.skill == node.skill
        assert restored.params == node.params
        assert restored.depends_on == node.depends_on
        assert restored.status == node.status
        assert restored.result == node.result
        assert restored.is_checkpoint == node.is_checkpoint


class TestTaskDAGFromDict:
    """Tests for TaskDAG.from_dict."""

    def test_basic_from_dict(self):
        """Test basic deserialization."""
        data = {
            "id": "dag_test",
            "goal": "Test goal",
            "nodes": {
                "t1": {
                    "id": "t1",
                    "skill": "search",
                    "params": {},
                    "depends_on": [],
                    "status": "completed",
                },
                "t2": {
                    "id": "t2",
                    "skill": "summarize",
                    "params": {},
                    "depends_on": ["t1"],
                    "status": "pending",
                },
            },
            "created_at": "2024-01-01T12:00:00",
        }

        dag = TaskDAG.from_dict(data)

        assert dag.id == "dag_test"
        assert dag.goal == "Test goal"
        assert len(dag.nodes) == 2
        assert dag.nodes["t1"].status == TaskStatus.COMPLETED
        assert dag.nodes["t2"].status == TaskStatus.PENDING
        assert dag.nodes["t2"].depends_on == ["t1"]

    def test_roundtrip(self):
        """Test serialization round-trip."""
        dag = TaskDAG.create("Test goal", [
            {"id": "t1", "skill": "search", "params": {"q": "test"}, "depends_on": []},
            {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ])

        dag.nodes["t1"].status = TaskStatus.COMPLETED
        dag.nodes["t1"].result = "result"

        data = dag.to_dict()
        restored = TaskDAG.from_dict(data)

        assert restored.id == dag.id
        assert restored.goal == dag.goal
        assert len(restored.nodes) == len(dag.nodes)
        assert restored.nodes["t1"].status == TaskStatus.COMPLETED
        assert restored.nodes["t1"].result == "result"
        assert restored.nodes["t2"].depends_on == ["t1"]

    def test_completed_count_property(self):
        """Test completed_count property."""
        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "a", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "b", "params": {}, "depends_on": []},
            {"id": "t3", "skill": "c", "params": {}, "depends_on": []},
        ])

        assert dag.completed_count == 0

        dag.nodes["t1"].status = TaskStatus.COMPLETED
        assert dag.completed_count == 1

        dag.nodes["t2"].status = TaskStatus.COMPLETED
        assert dag.completed_count == 2

    def test_pending_count_property(self):
        """Test pending_count property."""
        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "a", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "b", "params": {}, "depends_on": []},
        ])

        assert dag.pending_count == 2

        dag.nodes["t1"].status = TaskStatus.COMPLETED
        assert dag.pending_count == 1


class TestAsyncRuntimeWithCheckpoint:
    """Tests for AsyncRuntime checkpoint integration."""

    @pytest.fixture
    def simple_skills(self):
        """Create simple test skills."""
        async def search(query: str = "") -> str:
            await asyncio.sleep(0.01)
            return f"Results for: {query}"

        async def summarize(text: str = "") -> str:
            await asyncio.sleep(0.01)
            return f"Summary of: {text}"

        async def failing_skill() -> str:
            raise ValueError("Intentional failure")

        return {
            "search": search,
            "summarize": summarize,
            "failing_skill": failing_skill,
        }

    @pytest.mark.asyncio
    async def test_checkpoint_saved_after_task(self, tmp_path, simple_skills):
        """Test that checkpoint is saved after each task completion."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))
        runtime = AsyncRuntime(
            skills=simple_skills,
            checkpointer=saver,
        )

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "search", "params": {"query": "A"}, "depends_on": []},
            {"id": "t2", "skill": "search", "params": {"query": "B"}, "depends_on": []},
        ])

        result = await runtime.execute_dag(dag, resume=False)

        assert result.status == "success"

        # Should have checkpoints saved
        checkpoints = saver.list(dag.id)
        assert len(checkpoints) >= 1  # At least one checkpoint

    @pytest.mark.asyncio
    async def test_resume_from_checkpoint(self, tmp_path, simple_skills):
        """Test resuming execution from checkpoint."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        # Create DAG and mark first task as complete
        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "search", "params": {"query": "A"}, "depends_on": []},
            {"id": "t2", "skill": "summarize", "params": {"text": "B"}, "depends_on": ["t1"]},
        ])

        # Simulate partial execution - t1 completed
        dag.nodes["t1"].status = TaskStatus.COMPLETED
        dag.nodes["t1"].result = "Pre-existing result"
        dag.nodes["t1"].started_at = datetime.now()
        dag.nodes["t1"].finished_at = datetime.now()

        # Save checkpoint
        saver.save(dag)

        # Create a fresh DAG with same ID (simulating restart)
        fresh_dag = TaskDAG(
            id=dag.id,
            goal="Test",
            nodes={
                "t1": TaskNode(id="t1", skill="search", params={"query": "A"}, depends_on=[]),
                "t2": TaskNode(id="t2", skill="summarize", params={"text": "B"}, depends_on=["t1"]),
            },
        )

        # Execute with resume
        runtime = AsyncRuntime(
            skills=simple_skills,
            checkpointer=saver,
        )

        result = await runtime.execute_dag(fresh_dag, resume=True)

        assert result.status == "success"
        # t1 should have pre-existing result (from checkpoint)
        # Note: the runtime replaces the dag, so we check results
        assert "t1" in result.results
        assert result.results["t1"] == "Pre-existing result"

    @pytest.mark.asyncio
    async def test_no_resume_when_disabled(self, tmp_path, simple_skills):
        """Test that resume=False skips checkpoint loading."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        # Create and save a checkpoint with completed task
        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "search", "params": {"query": "A"}, "depends_on": []},
        ])
        dag.nodes["t1"].status = TaskStatus.COMPLETED
        dag.nodes["t1"].result = "Old result"
        saver.save(dag)

        # Create fresh DAG
        fresh_dag = TaskDAG(
            id=dag.id,
            goal="Test",
            nodes={
                "t1": TaskNode(id="t1", skill="search", params={"query": "A"}, depends_on=[]),
            },
        )

        # Execute without resume
        runtime = AsyncRuntime(
            skills=simple_skills,
            checkpointer=saver,
        )

        result = await runtime.execute_dag(fresh_dag, resume=False)

        assert result.status == "success"
        # Should have new result, not old one
        assert "Results for: A" in result.results["t1"]

    @pytest.mark.asyncio
    async def test_runtime_without_checkpointer(self, simple_skills):
        """Test runtime works fine without checkpointer."""
        runtime = AsyncRuntime(skills=simple_skills)

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "search", "params": {"query": "test"}, "depends_on": []},
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        assert "test" in result.results["t1"]

    @pytest.mark.asyncio
    async def test_checkpoint_on_failure(self, tmp_path, simple_skills):
        """Test that checkpoint is saved even when task fails."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))
        config = RuntimeConfig(max_retries=0)
        runtime = AsyncRuntime(
            skills=simple_skills,
            config=config,
            checkpointer=saver,
        )

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "failing_skill", "params": {}, "depends_on": []},
        ])

        result = await runtime.execute_dag(dag, resume=False)

        assert result.status == "failed"

        # Checkpoint should still be saved
        checkpoints = saver.list(dag.id)
        assert len(checkpoints) >= 1


class TestEmptyAndEdgeCases:
    """Tests for edge cases."""

    def test_empty_dag_checkpoint(self, tmp_path):
        """Test checkpointing an empty DAG."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        dag = TaskDAG(id="empty_dag", goal="Empty", nodes={})

        cp_id = saver.save(dag)
        assert cp_id

        loaded = saver.load(dag.id)
        assert loaded is not None
        assert len(loaded.nodes) == 0

    def test_completed_dag_checkpoint(self, tmp_path):
        """Test checkpointing a fully completed DAG."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "a", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "b", "params": {}, "depends_on": ["t1"]},
        ])

        dag.nodes["t1"].status = TaskStatus.COMPLETED
        dag.nodes["t1"].result = "r1"
        dag.nodes["t2"].status = TaskStatus.COMPLETED
        dag.nodes["t2"].result = "r2"

        cp_id = saver.save(dag)
        loaded = saver.load(dag.id)

        assert loaded.is_completed()
        assert loaded.completed_count == 2

    def test_corrupted_checkpoint_file(self, tmp_path):
        """Test handling of corrupted checkpoint files."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        # Create a corrupted file
        dag_dir = tmp_path / "corrupted_dag"
        dag_dir.mkdir()
        corrupted_file = dag_dir / "bad_checkpoint.json"
        corrupted_file.write_text("{ invalid json }")

        # List should skip corrupted files
        checkpoints = saver.list("corrupted_dag")
        assert len(checkpoints) == 0

    def test_symlink_handling(self, tmp_path):
        """Test proper symlink handling."""
        saver = JsonCheckpointSaver(base_dir=str(tmp_path))

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "test", "params": {}, "depends_on": []},
        ])

        # Save multiple times
        cp1 = saver.save(dag)
        cp2 = saver.save(dag)
        cp3 = saver.save(dag)

        # Latest link should point to cp3
        dag_dir = tmp_path / dag.id
        latest_link = dag_dir / "latest.json"

        assert latest_link.is_symlink()
        assert latest_link.resolve().name == f"{cp3}.json"
