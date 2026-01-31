"""
Tests for Nimbus v2 Scheduler.

Tests cover:
- Task state transitions
- DAG validation
- Dependency resolution
- Concurrent execution
- Cancellation propagation
- IPC result injection
- Event emission
"""

import asyncio
import pytest
from typing import Any, Dict, List

from nimbus.core.scheduler import (
    DAG,
    Task,
    TaskSpec,
    TaskState,
    Scheduler,
    SchedulerConfig,
    EventStream,
    create_dag,
    create_linear_dag,
    is_terminal_state,
    is_success_state,
    VALID_TRANSITIONS,
)
from nimbus.core.protocol import Event, Fault, ToolResult


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def scheduler() -> Scheduler:
    """Create a scheduler with default config."""
    return Scheduler(config=SchedulerConfig())


@pytest.fixture
def event_stream() -> EventStream:
    """Create an event stream for testing."""
    return EventStream()


@pytest.fixture
def simple_dag() -> DAG:
    """Create a simple DAG with two tasks: t1 -> t2."""
    return DAG(
        id="test-dag",
        tasks={
            "t1": Task(id="t1", spec=TaskSpec(goal="Task 1")),
            "t2": Task(id="t2", spec=TaskSpec(goal="Task 2"), depends_on=["t1"]),
        },
        root_task_id="t2"
    )


@pytest.fixture
def diamond_dag() -> DAG:
    r"""
    Create a diamond DAG:
        t1
       /  \
      t2   t3
       \  /
        t4
    """
    return DAG(
        id="diamond-dag",
        tasks={
            "t1": Task(id="t1", spec=TaskSpec(goal="Task 1")),
            "t2": Task(id="t2", spec=TaskSpec(goal="Task 2"), depends_on=["t1"]),
            "t3": Task(id="t3", spec=TaskSpec(goal="Task 3"), depends_on=["t1"]),
            "t4": Task(id="t4", spec=TaskSpec(goal="Task 4"), depends_on=["t2", "t3"]),
        },
        root_task_id="t4"
    )


# =============================================================================
# Task State Tests
# =============================================================================

class TestTaskState:
    """Tests for task state transitions."""

    def test_valid_transitions(self):
        """Test that valid transitions are allowed."""
        task = Task(id="t1", spec=TaskSpec(goal="Test"))
        assert task.state == "PENDING"

        # PENDING -> READY
        assert task.transition_to("READY")
        assert task.state == "READY"

        # READY -> RUNNING
        assert task.transition_to("RUNNING")
        assert task.state == "RUNNING"
        assert task.started_at is not None

        # RUNNING -> SUCCEEDED
        assert task.transition_to("SUCCEEDED")
        assert task.state == "SUCCEEDED"
        assert task.finished_at is not None

    def test_invalid_transitions(self):
        """Test that invalid transitions are rejected."""
        task = Task(id="t1", spec=TaskSpec(goal="Test"))

        # PENDING -> RUNNING (invalid, must go through READY)
        assert not task.transition_to("RUNNING")
        assert task.state == "PENDING"

        # PENDING -> SUCCEEDED (invalid)
        assert not task.transition_to("SUCCEEDED")
        assert task.state == "PENDING"

    def test_terminal_states(self):
        """Test that terminal states cannot transition."""
        task = Task(id="t1", spec=TaskSpec(goal="Test"), state="SUCCEEDED")

        # Terminal states cannot transition
        assert not task.transition_to("PENDING")
        assert not task.transition_to("READY")
        assert not task.transition_to("RUNNING")
        assert not task.transition_to("FAILED")

    def test_is_terminal_state(self):
        """Test is_terminal_state helper."""
        assert is_terminal_state("SUCCEEDED")
        assert is_terminal_state("FAILED")
        assert is_terminal_state("CANCELLED")
        assert not is_terminal_state("PENDING")
        assert not is_terminal_state("READY")
        assert not is_terminal_state("RUNNING")

    def test_is_success_state(self):
        """Test is_success_state helper."""
        assert is_success_state("SUCCEEDED")
        assert not is_success_state("FAILED")
        assert not is_success_state("CANCELLED")
        assert not is_success_state("PENDING")


# =============================================================================
# DAG Tests
# =============================================================================

class TestDAG:
    """Tests for DAG operations."""

    def test_get_task(self, simple_dag: DAG):
        """Test getting a task by ID."""
        task = simple_dag.get_task("t1")
        assert task is not None
        assert task.id == "t1"
        assert task.spec.goal == "Task 1"

        # Non-existent task
        assert simple_dag.get_task("t999") is None

    def test_get_downstream_tasks(self, diamond_dag: DAG):
        """Test getting downstream tasks."""
        # t1 has downstream t2 and t3
        downstream = diamond_dag.get_downstream_tasks("t1")
        assert set(downstream) == {"t2", "t3"}

        # t2 has downstream t4
        downstream = diamond_dag.get_downstream_tasks("t2")
        assert downstream == ["t4"]

        # t4 has no downstream
        downstream = diamond_dag.get_downstream_tasks("t4")
        assert downstream == []


# =============================================================================
# Scheduler Tests
# =============================================================================

class TestScheduler:
    """Tests for Scheduler."""

    @pytest.mark.asyncio
    async def test_submit_dag(self, scheduler: Scheduler, simple_dag: DAG):
        """Test submitting a DAG."""
        dag_id = await scheduler.submit_dag(simple_dag)
        assert dag_id == "test-dag"

        # Check DAG is stored
        dag = scheduler.get_dag(dag_id)
        assert dag is not None
        assert len(dag.tasks) == 2

        # Check tasks without dependencies are READY
        t1 = dag.get_task("t1")
        assert t1 is not None
        assert t1.state == "READY"

        # Check tasks with dependencies are still PENDING
        t2 = dag.get_task("t2")
        assert t2 is not None
        assert t2.state == "PENDING"

    @pytest.mark.asyncio
    async def test_submit_invalid_dag_missing_root(self, scheduler: Scheduler):
        """Test submitting a DAG with missing root task."""
        dag = DAG(
            id="bad-dag",
            tasks={"t1": Task(id="t1", spec=TaskSpec(goal="Task 1"))},
            root_task_id="missing"
        )

        with pytest.raises(ValueError, match="Root task not found"):
            await scheduler.submit_dag(dag)

    @pytest.mark.asyncio
    async def test_submit_invalid_dag_missing_dependency(self, scheduler: Scheduler):
        """Test submitting a DAG with missing dependency."""
        dag = DAG(
            id="bad-dag",
            tasks={
                "t1": Task(id="t1", spec=TaskSpec(goal="Task 1"), depends_on=["missing"]),
            },
            root_task_id="t1"
        )

        with pytest.raises(ValueError, match="depends on non-existent task"):
            await scheduler.submit_dag(dag)

    @pytest.mark.asyncio
    async def test_submit_invalid_dag_cycle(self, scheduler: Scheduler):
        """Test submitting a DAG with a cycle."""
        dag = DAG(
            id="bad-dag",
            tasks={
                "t1": Task(id="t1", spec=TaskSpec(goal="Task 1"), depends_on=["t2"]),
                "t2": Task(id="t2", spec=TaskSpec(goal="Task 2"), depends_on=["t1"]),
            },
            root_task_id="t1"
        )

        with pytest.raises(ValueError, match="cycle"):
            await scheduler.submit_dag(dag)

    @pytest.mark.asyncio
    async def test_get_ready_tasks(self, scheduler: Scheduler, diamond_dag: DAG):
        """Test getting ready tasks."""
        await scheduler.submit_dag(diamond_dag)

        # Initially only t1 is ready
        ready = scheduler.get_ready_tasks("diamond-dag")
        assert len(ready) == 1
        assert ready[0].id == "t1"

        # Complete t1
        scheduler.complete_task(
            "diamond-dag",
            "t1",
            ToolResult(status="OK", output="Done")
        )

        # Now t2 and t3 should be ready
        ready = scheduler.get_ready_tasks("diamond-dag")
        assert len(ready) == 2
        ready_ids = {t.id for t in ready}
        assert ready_ids == {"t2", "t3"}

    @pytest.mark.asyncio
    async def test_run_dag_simple(self, scheduler: Scheduler, simple_dag: DAG):
        """Test running a simple DAG."""
        await scheduler.submit_dag(simple_dag)

        # Create a simple executor
        execution_order: List[str] = []

        async def executor(task: Task) -> ToolResult:
            execution_order.append(task.id)
            await asyncio.sleep(0.01)  # Simulate work
            return ToolResult(status="OK", output=f"Result of {task.id}")

        result = await scheduler.run_dag("test-dag", executor)

        assert result.status == "OK"
        assert execution_order == ["t1", "t2"]  # t1 must complete before t2

    @pytest.mark.asyncio
    async def test_run_dag_parallel(self, scheduler: Scheduler, diamond_dag: DAG):
        """Test running a DAG with parallel tasks."""
        await scheduler.submit_dag(diamond_dag)

        execution_times: Dict[str, float] = {}

        async def executor(task: Task) -> ToolResult:
            import time
            execution_times[task.id] = time.time()
            await asyncio.sleep(0.05)  # Simulate work
            return ToolResult(status="OK", output=f"Result of {task.id}")

        result = await scheduler.run_dag("diamond-dag", executor)

        assert result.status == "OK"

        # t2 and t3 should start at nearly the same time (parallel)
        time_diff = abs(execution_times["t2"] - execution_times["t3"])
        assert time_diff < 0.02, "t2 and t3 should run in parallel"

    @pytest.mark.asyncio
    async def test_run_dag_failure(self, scheduler: Scheduler, simple_dag: DAG):
        """Test running a DAG with a failing task."""
        await scheduler.submit_dag(simple_dag)

        async def executor(task: Task) -> ToolResult:
            if task.id == "t1":
                return ToolResult(
                    status="ERROR",
                    fault=Fault(
                        domain="TOOL",
                        code="TOOL_FAILURE",
                        message="Task failed",
                        retryable=False
                    )
                )
            return ToolResult(status="OK", output="Done")

        result = await scheduler.run_dag("test-dag", executor)

        # The root task should be cancelled due to dependency failure
        dag = scheduler.get_dag("test-dag")
        assert dag is not None

        t1 = dag.get_task("t1")
        assert t1 is not None
        assert t1.state == "FAILED"

        t2 = dag.get_task("t2")
        assert t2 is not None
        assert t2.state == "CANCELLED"

    @pytest.mark.asyncio
    async def test_cancel_task(self, scheduler: Scheduler, simple_dag: DAG):
        """Test cancelling a task."""
        await scheduler.submit_dag(simple_dag)

        # Cancel t1
        success = scheduler.cancel_task("test-dag", "t1")
        assert success

        dag = scheduler.get_dag("test-dag")
        assert dag is not None

        t1 = dag.get_task("t1")
        assert t1 is not None
        assert t1.state == "CANCELLED"

        # t2 should also be cancelled (downstream)
        t2 = dag.get_task("t2")
        assert t2 is not None
        assert t2.state == "CANCELLED"

    @pytest.mark.asyncio
    async def test_cancel_terminal_task(self, scheduler: Scheduler, simple_dag: DAG):
        """Test that terminal tasks cannot be cancelled."""
        await scheduler.submit_dag(simple_dag)

        # Complete t1
        scheduler.complete_task(
            "test-dag",
            "t1",
            ToolResult(status="OK", output="Done")
        )

        # Try to cancel t1 - should fail
        success = scheduler.cancel_task("test-dag", "t1")
        assert not success

        dag = scheduler.get_dag("test-dag")
        t1 = dag.get_task("t1")
        assert t1.state == "SUCCEEDED"

    @pytest.mark.asyncio
    async def test_inject_result(self, scheduler: Scheduler, simple_dag: DAG):
        """Test injecting results via IPC."""
        await scheduler.submit_dag(simple_dag)

        # Inject a result
        scheduler.inject_result("test-dag", "t1", "output", "injected_value")

        # Retrieve the result
        value = scheduler.get_injected_result("test-dag", "t1", "output")
        assert value == "injected_value"

        # Non-existent result
        value = scheduler.get_injected_result("test-dag", "t1", "missing")
        assert value is None

    @pytest.mark.asyncio
    async def test_get_dag_status(self, scheduler: Scheduler, simple_dag: DAG):
        """Test getting DAG status."""
        await scheduler.submit_dag(simple_dag)

        status = scheduler.get_dag_status("test-dag")
        assert status["total"] == 2
        assert status["ready"] == 1
        assert status["pending"] == 1

        # Complete t1
        scheduler.complete_task(
            "test-dag",
            "t1",
            ToolResult(status="OK", output="Done")
        )

        status = scheduler.get_dag_status("test-dag")
        assert status["succeeded"] == 1


# =============================================================================
# Event Tests
# =============================================================================

class TestEvents:
    """Tests for event emission."""

    @pytest.mark.asyncio
    async def test_task_events(self, simple_dag: DAG):
        """Test that task lifecycle events are emitted."""
        events = EventStream()
        scheduler = Scheduler(
            config=SchedulerConfig(emit_events=True),
            events=events
        )

        await scheduler.submit_dag(simple_dag)

        # Check TASK_CREATED events
        created_events = [e for e in events.get_events() if e.type == "TASK_CREATED"]
        assert len(created_events) == 2

        # Complete t1
        scheduler.complete_task(
            "test-dag",
            "t1",
            ToolResult(status="OK", output="Done")
        )

        # Check TASK_FINISHED event
        finished_events = [e for e in events.get_events() if e.type == "TASK_FINISHED"]
        assert len(finished_events) == 1
        assert finished_events[0].data["task_id"] == "t1"
        assert finished_events[0].data["task_state"] == "SUCCEEDED"

    @pytest.mark.asyncio
    async def test_events_disabled(self, simple_dag: DAG):
        """Test that events can be disabled."""
        events = EventStream()
        scheduler = Scheduler(
            config=SchedulerConfig(emit_events=False),
            events=events
        )

        await scheduler.submit_dag(simple_dag)

        # No events should be emitted
        assert len(events.get_events()) == 0


# =============================================================================
# Factory Function Tests
# =============================================================================

class TestFactoryFunctions:
    """Tests for DAG factory functions."""

    def test_create_dag(self):
        """Test create_dag function."""
        tasks = [
            Task(id="t1", spec=TaskSpec(goal="Task 1")),
            Task(id="t2", spec=TaskSpec(goal="Task 2"), depends_on=["t1"]),
        ]

        dag = create_dag(tasks)

        assert len(dag.tasks) == 2
        assert dag.root_task_id == "t2"  # Last task by default

    def test_create_dag_custom_root(self):
        """Test create_dag with custom root."""
        tasks = [
            Task(id="t1", spec=TaskSpec(goal="Task 1")),
            Task(id="t2", spec=TaskSpec(goal="Task 2")),
        ]

        dag = create_dag(tasks, root_task_id="t1")

        assert dag.root_task_id == "t1"

    def test_create_dag_empty(self):
        """Test create_dag with empty list."""
        with pytest.raises(ValueError, match="at least one task"):
            create_dag([])

    def test_create_linear_dag(self):
        """Test create_linear_dag function."""
        goals = ["Step 1", "Step 2", "Step 3"]

        dag = create_linear_dag(goals)

        assert len(dag.tasks) == 3
        assert dag.root_task_id == "t3"

        # Check dependencies
        t1 = dag.get_task("t1")
        assert t1 is not None
        assert t1.depends_on == []

        t2 = dag.get_task("t2")
        assert t2 is not None
        assert t2.depends_on == ["t1"]

        t3 = dag.get_task("t3")
        assert t3 is not None
        assert t3.depends_on == ["t2"]

    def test_create_linear_dag_empty(self):
        """Test create_linear_dag with empty list."""
        with pytest.raises(ValueError, match="cannot be empty"):
            create_linear_dag([])


# =============================================================================
# Concurrency Tests
# =============================================================================

class TestConcurrency:
    """Tests for concurrent execution."""

    @pytest.mark.asyncio
    async def test_max_concurrent_tasks(self):
        """Test that max_concurrent_tasks is respected."""
        # Create a DAG with 5 independent tasks
        dag = DAG(
            id="parallel-dag",
            tasks={
                f"t{i}": Task(id=f"t{i}", spec=TaskSpec(goal=f"Task {i}"))
                for i in range(1, 6)
            },
            root_task_id="t5"  # Just pick one as root
        )

        # Allow only 2 concurrent tasks
        scheduler = Scheduler(config=SchedulerConfig(max_concurrent_tasks=2))
        await scheduler.submit_dag(dag)

        concurrent_count = 0
        max_concurrent = 0

        async def executor(task: Task) -> ToolResult:
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.05)
            concurrent_count -= 1
            return ToolResult(status="OK", output=f"Result of {task.id}")

        # Note: This test is simplified because all tasks are independent
        # In a real scenario, we'd need a more complex DAG structure
        # For now, just verify it completes without errors
        result = await scheduler.run_dag("parallel-dag", executor)

        # The max concurrent should be limited to config
        assert max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_timeout(self):
        """Test task timeout."""
        dag = DAG(
            id="timeout-dag",
            tasks={
                "t1": Task(id="t1", spec=TaskSpec(goal="Slow task")),
            },
            root_task_id="t1"
        )

        scheduler = Scheduler(config=SchedulerConfig(default_timeout=0.1))
        await scheduler.submit_dag(dag)

        async def slow_executor(task: Task) -> ToolResult:
            await asyncio.sleep(10)  # Very slow
            return ToolResult(status="OK", output="Done")

        result = await scheduler.run_dag("timeout-dag", slow_executor)

        assert result.status == "TIMEOUT"
        assert result.fault is not None
        assert result.fault.code == "TIMEOUT"


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """Integration tests for Scheduler."""

    @pytest.mark.asyncio
    async def test_complex_dag_execution(self):
        """Test executing a complex DAG with multiple branches and joins."""
        # Create a complex DAG:
        #       t1
        #      / | \
        #     t2 t3 t4
        #      \ | /
        #       t5
        dag = DAG(
            id="complex-dag",
            tasks={
                "t1": Task(id="t1", spec=TaskSpec(goal="Root task")),
                "t2": Task(id="t2", spec=TaskSpec(goal="Branch 1"), depends_on=["t1"]),
                "t3": Task(id="t3", spec=TaskSpec(goal="Branch 2"), depends_on=["t1"]),
                "t4": Task(id="t4", spec=TaskSpec(goal="Branch 3"), depends_on=["t1"]),
                "t5": Task(id="t5", spec=TaskSpec(goal="Final"), depends_on=["t2", "t3", "t4"]),
            },
            root_task_id="t5"
        )

        scheduler = Scheduler()
        await scheduler.submit_dag(dag)

        execution_order: List[str] = []

        async def executor(task: Task) -> ToolResult:
            execution_order.append(task.id)
            await asyncio.sleep(0.01)
            return ToolResult(status="OK", output=f"Done {task.id}")

        result = await scheduler.run_dag("complex-dag", executor)

        assert result.status == "OK"

        # t1 must be first
        assert execution_order[0] == "t1"

        # t5 must be last
        assert execution_order[-1] == "t5"

        # t2, t3, t4 can be in any order (parallel)
        middle = set(execution_order[1:-1])
        assert middle == {"t2", "t3", "t4"}

    @pytest.mark.asyncio
    async def test_partial_failure_recovery(self):
        """Test DAG behavior when one branch fails."""
        # Create a DAG where t3 fails:
        #     t1
        #    /  \
        #   t2   t3 (fails)
        #    \  /
        #     t4
        dag = DAG(
            id="failure-dag",
            tasks={
                "t1": Task(id="t1", spec=TaskSpec(goal="Root")),
                "t2": Task(id="t2", spec=TaskSpec(goal="Success branch"), depends_on=["t1"]),
                "t3": Task(id="t3", spec=TaskSpec(goal="Failing branch"), depends_on=["t1"]),
                "t4": Task(id="t4", spec=TaskSpec(goal="Join"), depends_on=["t2", "t3"]),
            },
            root_task_id="t4"
        )

        scheduler = Scheduler()
        await scheduler.submit_dag(dag)

        async def executor(task: Task) -> ToolResult:
            if task.id == "t3":
                return ToolResult(
                    status="ERROR",
                    fault=Fault(
                        domain="TOOL",
                        code="TOOL_FAILURE",
                        message="Simulated failure",
                        retryable=False
                    )
                )
            return ToolResult(status="OK", output=f"Done {task.id}")

        result = await scheduler.run_dag("failure-dag", executor)

        # Verify final states
        dag_obj = scheduler.get_dag("failure-dag")
        assert dag_obj is not None

        assert dag_obj.get_task("t1").state == "SUCCEEDED"
        assert dag_obj.get_task("t2").state == "SUCCEEDED"
        assert dag_obj.get_task("t3").state == "FAILED"
        assert dag_obj.get_task("t4").state == "CANCELLED"  # Cancelled due to t3 failure
