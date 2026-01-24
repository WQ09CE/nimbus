"""Tests for ReplanCoordinator and CancellationToken."""

import asyncio
import pytest
from datetime import datetime

from nimbus.core.runtime import (
    ReplanCoordinator,
    CoordinatorConfig,
    CancellationToken,
)
from nimbus.core.types import TaskDAG, TaskNode, TaskStatus


# =============================================================================
# CancellationToken Tests
# =============================================================================


class TestCancellationToken:
    """Tests for CancellationToken."""

    def test_initial_state(self):
        """Token starts uncancelled."""
        token = CancellationToken()
        assert token.is_cancelled() is False
        assert token.reason is None

    def test_cancel(self):
        """Cancel sets cancelled flag and reason."""
        token = CancellationToken()
        token.cancel("test reason")

        assert token.is_cancelled() is True
        assert token.reason == "test reason"

    def test_cancel_default_reason(self):
        """Cancel has default reason."""
        token = CancellationToken()
        token.cancel()

        assert token.is_cancelled() is True
        assert token.reason == "replan requested"

    def test_reset(self):
        """Reset clears cancelled state."""
        token = CancellationToken()
        token.cancel("test")

        token.reset()

        assert token.is_cancelled() is False
        assert token.reason is None

    @pytest.mark.asyncio
    async def test_wait_for_cancel_succeeds(self):
        """wait_for_cancel returns True when cancelled."""
        token = CancellationToken()

        async def cancel_later():
            await asyncio.sleep(0.01)
            token.cancel("test")

        asyncio.create_task(cancel_later())

        result = await token.wait_for_cancel(timeout=1.0)
        assert result is True
        assert token.is_cancelled() is True

    @pytest.mark.asyncio
    async def test_wait_for_cancel_timeout(self):
        """wait_for_cancel returns False on timeout."""
        token = CancellationToken()

        result = await token.wait_for_cancel(timeout=0.01)
        assert result is False
        assert token.is_cancelled() is False

    def test_repr_uncancelled(self):
        """Repr shows uncancelled state."""
        token = CancellationToken()
        assert "cancelled=False" in repr(token)

    def test_repr_cancelled(self):
        """Repr shows cancelled state with reason."""
        token = CancellationToken()
        token.cancel("test reason")
        assert "cancelled=True" in repr(token)
        assert "test reason" in repr(token)


# =============================================================================
# Helper Functions
# =============================================================================


def create_test_dag(
    task_configs: list[dict],
    goal: str = "test goal",
) -> TaskDAG:
    """Create a test DAG with specified tasks.

    Args:
        task_configs: List of dicts with id, skill, params, depends_on.
        goal: Goal for the DAG.

    Returns:
        TaskDAG instance.
    """
    return TaskDAG.create(goal, task_configs)


def set_task_status(dag: TaskDAG, task_id: str, status: TaskStatus, result: any = None):
    """Set status on a task in the DAG."""
    node = dag.nodes[task_id]
    node.status = status
    if status == TaskStatus.COMPLETED:
        node.result = result
        node.started_at = datetime.now()
        node.finished_at = datetime.now()


# =============================================================================
# ReplanCoordinator Tests
# =============================================================================


class TestReplanCoordinator:
    """Tests for ReplanCoordinator."""

    def test_initial_state(self):
        """Coordinator starts unpaused with no active tasks."""
        coordinator = ReplanCoordinator()
        assert coordinator.is_paused() is False
        assert coordinator.get_active_task_ids() == []

    def test_pause_resume(self):
        """Pause and resume work correctly."""
        coordinator = ReplanCoordinator()

        coordinator.pause_scheduling()
        assert coordinator.is_paused() is True

        coordinator.resume_scheduling()
        assert coordinator.is_paused() is False

    @pytest.mark.asyncio
    async def test_register_unregister_task(self):
        """Tasks can be registered and unregistered."""
        coordinator = ReplanCoordinator()
        token = CancellationToken()
        task = asyncio.create_task(asyncio.sleep(10))

        try:
            coordinator.register_task("t1", task, token)
            assert "t1" in coordinator.get_active_task_ids()

            coordinator.unregister_task("t1")
            assert "t1" not in coordinator.get_active_task_ids()
        finally:
            # Clean up
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def test_unregister_nonexistent_task(self):
        """Unregistering nonexistent task doesn't error."""
        coordinator = ReplanCoordinator()
        coordinator.unregister_task("nonexistent")  # Should not raise


class TestIsMeaningfulChange:
    """Tests for is_meaningful_change()."""

    def test_different_task_count(self):
        """Different number of tasks is meaningful."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
        ])

        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
            {"id": "t2", "skill": "summarize", "params": {}},
        ])

        assert coordinator.is_meaningful_change(old_dag, new_dag) is True

    def test_different_skills(self):
        """Different skill is meaningful."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
        ])

        new_dag = create_test_dag([
            {"id": "t1", "skill": "web_search", "params": {"q": "test"}},
        ])

        assert coordinator.is_meaningful_change(old_dag, new_dag) is True

    def test_different_params(self):
        """Different params is meaningful."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "old query"}},
        ])

        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "new query"}},
        ])

        assert coordinator.is_meaningful_change(old_dag, new_dag) is True

    def test_same_tasks_different_id(self):
        """Same signature with different ID is not meaningful."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
        ])

        new_dag = create_test_dag([
            {"id": "t99", "skill": "search", "params": {"q": "test"}},
        ])

        assert coordinator.is_meaningful_change(old_dag, new_dag) is False

    def test_pending_only_comparison(self):
        """Only pending tasks from old DAG are compared."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
            {"id": "t2", "skill": "summarize", "params": {}},  # No dependency for fair comparison
        ])
        # Mark t1 as completed
        set_task_status(old_dag, "t1", TaskStatus.COMPLETED, result="done")

        new_dag = create_test_dag([
            # Only t2 - same signature as pending t2 in old_dag
            {"id": "t2", "skill": "summarize", "params": {}},
        ])

        # t1 is completed so only t2 is compared - same signature, no change
        assert coordinator.is_meaningful_change(old_dag, new_dag) is False

    def test_different_dependencies(self):
        """Different dependency structure is meaningful."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "a"}},
            {"id": "t2", "skill": "search", "params": {"q": "b"}},
            {"id": "t3", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ])

        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "a"}},
            {"id": "t2", "skill": "search", "params": {"q": "b"}},
            {"id": "t3", "skill": "summarize", "params": {}, "depends_on": ["t1", "t2"]},
        ])

        assert coordinator.is_meaningful_change(old_dag, new_dag) is True


class TestMergeResults:
    """Tests for merge_results()."""

    def test_merge_completed_results(self):
        """Completed results are merged into new DAG."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
            {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ])
        set_task_status(old_dag, "t1", TaskStatus.COMPLETED, result="search results")

        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
            {"id": "t2", "skill": "analyze", "params": {}, "depends_on": ["t1"]},
        ])

        merged = coordinator.merge_results(old_dag, new_dag)

        # t1 should have completed status and result
        assert merged.nodes["t1"].status == TaskStatus.COMPLETED
        assert merged.nodes["t1"].result == "search results"

        # t2 should still be pending
        assert merged.nodes["t2"].status == TaskStatus.PENDING

    def test_merge_updates_dependencies(self):
        """Dependencies on completed tasks are removed."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
        ])
        set_task_status(old_dag, "t1", TaskStatus.COMPLETED, result="done")

        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
            {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ])

        merged = coordinator.merge_results(old_dag, new_dag)

        # t2's dependency on t1 should be removed since t1 is completed
        assert merged.nodes["t2"].depends_on == []

    def test_preserve_completed_disabled(self):
        """No merge when preserve_completed is False."""
        config = CoordinatorConfig(preserve_completed=False)
        coordinator = ReplanCoordinator(config=config)

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
        ])
        set_task_status(old_dag, "t1", TaskStatus.COMPLETED, result="done")

        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
        ])

        merged = coordinator.merge_results(old_dag, new_dag)

        # Should not merge - t1 stays pending
        assert merged.nodes["t1"].status == TaskStatus.PENDING


class TestResolveIdConflicts:
    """Tests for resolve_id_conflicts()."""

    def test_no_conflicts(self):
        """No changes when no conflicts."""
        coordinator = ReplanCoordinator()

        dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {}},
            {"id": "t2", "skill": "summarize", "params": {}},
        ])

        coordinator.resolve_id_conflicts(dag)

        assert "t1" in dag.nodes
        assert "t2" in dag.nodes

    def test_parse_task_id(self):
        """Task ID parsing works correctly."""
        coordinator = ReplanCoordinator()

        assert coordinator._parse_task_id("t1") == ("t1", 0)
        assert coordinator._parse_task_id("t1_g1") == ("t1", 1)
        assert coordinator._parse_task_id("t1_g5") == ("t1", 5)
        assert coordinator._parse_task_id("task_search_g2") == ("task_search", 2)


class TestCancelConflictingTasks:
    """Tests for cancel_conflicting_tasks()."""

    @pytest.mark.asyncio
    async def test_cancel_running_not_in_new_plan(self):
        """Running tasks not in new plan are cancelled."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "old"}},
            {"id": "t2", "skill": "analyze", "params": {}},
        ])
        set_task_status(old_dag, "t1", TaskStatus.RUNNING)
        set_task_status(old_dag, "t2", TaskStatus.RUNNING)

        # Register tasks
        token1 = CancellationToken()
        token2 = CancellationToken()

        async def dummy_task():
            await asyncio.sleep(10)

        task1 = asyncio.create_task(dummy_task())
        task2 = asyncio.create_task(dummy_task())

        coordinator.register_task("t1", task1, token1)
        coordinator.register_task("t2", task2, token2)

        # New plan only has t1 (with same signature)
        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "old"}},
        ])

        try:
            cancelled = await coordinator.cancel_conflicting_tasks(old_dag, new_dag)

            # t2 should be cancelled (not in new plan)
            assert "t2" in cancelled
            assert token2.is_cancelled() is True

            # t1 should NOT be cancelled (same signature in new plan)
            assert "t1" not in cancelled
            assert token1.is_cancelled() is False

        finally:
            task1.cancel()
            task2.cancel()
            try:
                await task1
            except asyncio.CancelledError:
                pass
            try:
                await task2
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_preserve_running_tasks(self):
        """Running tasks are preserved when config says so."""
        config = CoordinatorConfig(preserve_running=True)
        coordinator = ReplanCoordinator(config=config)

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {}},
        ])
        set_task_status(old_dag, "t1", TaskStatus.RUNNING)

        token = CancellationToken()
        task = asyncio.create_task(asyncio.sleep(10))
        coordinator.register_task("t1", task, token)

        new_dag = create_test_dag([
            {"id": "t2", "skill": "other", "params": {}},
        ])

        try:
            cancelled = await coordinator.cancel_conflicting_tasks(old_dag, new_dag)

            # Nothing should be cancelled
            assert cancelled == []
            assert token.is_cancelled() is False

        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class TestRequestReplan:
    """Tests for request_replan()."""

    @pytest.mark.asyncio
    async def test_replan_rejected_no_meaningful_change(self):
        """Replan is rejected when no meaningful change."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
        ])

        # Same DAG
        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
        ])

        result = await coordinator.request_replan(
            current_dag=old_dag,
            new_dag=new_dag,
            trigger="checkpoint",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_replan_accepted_with_changes(self):
        """Replan is accepted when there are meaningful changes."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "old"}},
        ])

        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "new"}},
        ])

        result = await coordinator.request_replan(
            current_dag=old_dag,
            new_dag=new_dag,
            trigger="checkpoint",
            trigger_task_id="t0",
        )

        assert result is not None
        # Should have replan record
        assert len(result.replan_history) == 1
        assert result.replan_history[0].trigger == "checkpoint"

    @pytest.mark.asyncio
    async def test_replan_without_meaningful_change_check(self):
        """Replan proceeds when require_meaningful_change is False."""
        config = CoordinatorConfig(require_meaningful_change=False)
        coordinator = ReplanCoordinator(config=config)

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
        ])

        # Same DAG
        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {"q": "test"}},
        ])

        result = await coordinator.request_replan(
            current_dag=old_dag,
            new_dag=new_dag,
            trigger="manual",
        )

        # Should accept even without meaningful change
        assert result is not None


class TestCreateReplanRecord:
    """Tests for _create_replan_record()."""

    def test_record_captures_metadata(self):
        """Record captures all relevant metadata."""
        coordinator = ReplanCoordinator()

        old_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {}},
            {"id": "t2", "skill": "summarize", "params": {}},
        ])

        new_dag = create_test_dag([
            {"id": "t1", "skill": "search", "params": {}},
            {"id": "t3", "skill": "analyze", "params": {}},
        ])

        record = coordinator._create_replan_record(
            old_dag=old_dag,
            new_dag=new_dag,
            trigger="checkpoint",
            trigger_task_id="t1",
            cancelled=["t2"],
        )

        assert record.trigger == "checkpoint"
        assert record.trigger_task_id == "t1"
        assert record.old_task_count == 2
        assert record.new_task_count == 2
        assert record.tasks_cancelled == ["t2"]
        assert "t3" in record.tasks_added
        assert "checkpoint" in record.reason
