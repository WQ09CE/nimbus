"""Tests for DAG types, DAGPlanner, and AsyncRuntime."""

import pytest
import asyncio
from datetime import datetime

from nimbus.core.types import (
    TaskStatus,
    TaskNode,
    TaskDAG,
    RuntimeConfig,
    ExecutionStats,
    ExecutionResult,
)
from nimbus.core.planner import DAGPlanner
from nimbus.core.runtime import AsyncRuntime


class TestTaskNode:
    """Tests for TaskNode dataclass."""

    def test_task_node_creation(self):
        """Test basic TaskNode creation."""
        node = TaskNode(
            id="t1",
            skill="search",
            params={"query": "test"},
        )

        assert node.id == "t1"
        assert node.skill == "search"
        assert node.params == {"query": "test"}
        assert node.depends_on == []
        assert node.status == TaskStatus.PENDING
        assert node.result is None
        assert node.error is None

    def test_task_node_with_dependencies(self):
        """Test TaskNode with dependencies."""
        node = TaskNode(
            id="t2",
            skill="summarize",
            params={"source": "t1"},
            depends_on=["t1"],
        )

        assert node.depends_on == ["t1"]

    def test_task_node_duration(self):
        """Test duration calculation."""
        node = TaskNode(id="t1", skill="test", params={})
        node.started_at = datetime(2024, 1, 1, 12, 0, 0)
        node.finished_at = datetime(2024, 1, 1, 12, 0, 1, 500000)  # 1.5 seconds

        assert node.duration_ms == 1500

    def test_task_node_to_dict(self):
        """Test serialization."""
        node = TaskNode(
            id="t1",
            skill="search",
            params={"query": "test"},
            status=TaskStatus.COMPLETED,
            result={"data": "result"},
        )

        d = node.to_dict()

        assert d["id"] == "t1"
        assert d["skill"] == "search"
        assert d["status"] == "completed"
        assert d["result"] == {"data": "result"}


class TestTaskDAG:
    """Tests for TaskDAG dataclass."""

    def test_dag_creation(self):
        """Test creating DAG from task definitions."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {"query": "A"}, "depends_on": []},
            {"id": "t2", "skill": "search", "params": {"query": "B"}, "depends_on": []},
            {"id": "t3", "skill": "summarize", "params": {}, "depends_on": ["t1", "t2"]},
        ]

        dag = TaskDAG.create("Test goal", tasks)

        assert dag.goal == "Test goal"
        assert len(dag.nodes) == 3
        assert "t1" in dag.nodes
        assert "t2" in dag.nodes
        assert "t3" in dag.nodes
        assert dag.nodes["t3"].depends_on == ["t1", "t2"]

    def test_dag_create_simple(self):
        """Test creating simple single-task DAG."""
        dag = TaskDAG.create_simple("chat", {"message": "hello"})

        assert len(dag.nodes) == 1
        task = list(dag.nodes.values())[0]
        assert task.skill == "chat"
        assert task.params == {"message": "hello"}

    def test_get_ready_tasks_initial(self):
        """Test getting ready tasks at start."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t3", "skill": "summarize", "params": {}, "depends_on": ["t1", "t2"]},
        ]

        dag = TaskDAG.create("Test", tasks)
        ready = dag.get_ready_tasks()

        assert len(ready) == 2
        ready_ids = {t.id for t in ready}
        assert ready_ids == {"t1", "t2"}

    def test_get_ready_tasks_after_completion(self):
        """Test getting ready tasks after some complete."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t3", "skill": "summarize", "params": {}, "depends_on": ["t1", "t2"]},
        ]

        dag = TaskDAG.create("Test", tasks)

        # Complete t1
        dag.nodes["t1"].status = TaskStatus.COMPLETED

        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t2"

        # Complete t2
        dag.nodes["t2"].status = TaskStatus.COMPLETED

        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t3"

    def test_is_completed(self):
        """Test DAG completion check."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ]

        dag = TaskDAG.create("Test", tasks)

        assert not dag.is_completed()

        dag.nodes["t1"].status = TaskStatus.COMPLETED
        assert not dag.is_completed()

        dag.nodes["t2"].status = TaskStatus.COMPLETED
        assert dag.is_completed()

    def test_is_completed_with_failures(self):
        """Test DAG completion with failed tasks."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
        ]

        dag = TaskDAG.create("Test", tasks)
        dag.nodes["t1"].status = TaskStatus.FAILED

        assert dag.is_completed()

    def test_get_results(self):
        """Test collecting results."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "search", "params": {}, "depends_on": []},
        ]

        dag = TaskDAG.create("Test", tasks)
        dag.nodes["t1"].status = TaskStatus.COMPLETED
        dag.nodes["t1"].result = "result1"
        dag.nodes["t2"].status = TaskStatus.FAILED
        dag.nodes["t2"].error = "error"

        results = dag.get_results()

        assert results == {"t1": "result1"}

    def test_mark_downstream_skipped(self):
        """Test marking downstream tasks as skipped."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "analyze", "params": {}, "depends_on": ["t1"]},
            {"id": "t3", "skill": "summarize", "params": {}, "depends_on": ["t2"]},
        ]

        dag = TaskDAG.create("Test", tasks)
        dag.nodes["t1"].status = TaskStatus.FAILED

        dag.mark_downstream_skipped("t1")

        assert dag.nodes["t2"].status == TaskStatus.SKIPPED
        assert dag.nodes["t3"].status == TaskStatus.SKIPPED
        assert "t1" in dag.nodes["t2"].error


class TestDAGPlanner:
    """Tests for DAGPlanner."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM client."""
        class MockLLM:
            def __init__(self, response: str):
                self.response = response

            async def complete(self, prompt: str) -> str:
                return self.response

        return MockLLM

    @pytest.mark.asyncio
    async def test_plan_direct_response(self, mock_llm):
        """Test planning direct response."""
        llm = mock_llm('{"mode": "direct", "response": "你好！"}')
        planner = DAGPlanner(llm)

        dag = await planner.create_plan(
            goal="你好",
            context="",
            available_skills={"chat", "search"},
        )

        assert len(dag.nodes) == 1
        task = list(dag.nodes.values())[0]
        assert task.skill == "chat"

    @pytest.mark.asyncio
    async def test_plan_dag_with_dependencies(self, mock_llm):
        """Test planning DAG with dependencies."""
        response = '''{
            "mode": "dag",
            "tasks": [
                {"id": "t1", "skill": "search", "params": {"query": "AI"}, "depends_on": []},
                {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]}
            ]
        }'''
        llm = mock_llm(response)
        planner = DAGPlanner(llm)

        dag = await planner.create_plan(
            goal="搜索 AI 然后总结",
            context="",
            available_skills={"chat", "search", "summarize"},
        )

        assert len(dag.nodes) == 2
        assert dag.nodes["t1"].depends_on == []
        assert dag.nodes["t2"].depends_on == ["t1"]

    @pytest.mark.asyncio
    async def test_plan_parallel_tasks(self, mock_llm):
        """Test planning parallel tasks."""
        response = '''{
            "mode": "dag",
            "tasks": [
                {"id": "t1", "skill": "search", "params": {"query": "Python"}, "depends_on": []},
                {"id": "t2", "skill": "search", "params": {"query": "Rust"}, "depends_on": []}
            ]
        }'''
        llm = mock_llm(response)
        planner = DAGPlanner(llm)

        dag = await planner.create_plan(
            goal="搜索 Python 和 Rust",
            context="",
            available_skills={"chat", "search"},
        )

        assert len(dag.nodes) == 2
        # Both should be ready (no dependencies)
        ready = dag.get_ready_tasks()
        assert len(ready) == 2

    def test_validate_dag_valid(self):
        """Test validating a valid DAG."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ]
        dag = TaskDAG.create("Test", tasks)

        planner = DAGPlanner(None)
        errors = planner.validate_dag(dag, {"search", "summarize"})

        assert errors == []

    def test_validate_dag_unknown_skill(self):
        """Test validating DAG with unknown skill."""
        tasks = [
            {"id": "t1", "skill": "unknown_skill", "params": {}, "depends_on": []},
        ]
        dag = TaskDAG.create("Test", tasks)

        planner = DAGPlanner(None)
        errors = planner.validate_dag(dag, {"search", "summarize"})

        assert len(errors) == 1
        assert "unknown_skill" in errors[0]

    def test_validate_dag_missing_dependency(self):
        """Test validating DAG with missing dependency."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": ["t0"]},
        ]
        dag = TaskDAG.create("Test", tasks)

        planner = DAGPlanner(None)
        errors = planner.validate_dag(dag, {"search"})

        assert len(errors) == 1
        assert "t0" in errors[0]

    def test_validate_dag_cycle(self):
        """Test validating DAG with cycle."""
        # Create nodes directly to introduce a cycle
        dag = TaskDAG(
            id="dag_test",
            goal="Test",
            nodes={
                "t1": TaskNode(id="t1", skill="a", params={}, depends_on=["t2"]),
                "t2": TaskNode(id="t2", skill="b", params={}, depends_on=["t1"]),
            },
        )

        planner = DAGPlanner(None)
        errors = planner.validate_dag(dag, {"a", "b"})

        assert any("cycle" in e.lower() for e in errors)


class TestAsyncRuntime:
    """Tests for AsyncRuntime."""

    @pytest.fixture
    def simple_skills(self):
        """Create simple test skills."""
        async def search(query: str) -> str:
            await asyncio.sleep(0.01)
            return f"Results for: {query}"

        async def summarize(text: str = "") -> str:
            await asyncio.sleep(0.01)
            return f"Summary of: {text}"

        async def slow_skill(duration: float = 0.5) -> str:
            await asyncio.sleep(duration)
            return "done"

        async def failing_skill() -> str:
            raise ValueError("Intentional failure")

        return {
            "search": search,
            "summarize": summarize,
            "slow_skill": slow_skill,
            "failing_skill": failing_skill,
        }

    @pytest.mark.asyncio
    async def test_execute_simple_dag(self, simple_skills):
        """Test executing a simple DAG."""
        runtime = AsyncRuntime(skills=simple_skills)

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "search", "params": {"query": "test"}, "depends_on": []},
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        assert result.stats.completed == 1
        assert result.stats.failed == 0
        assert "t1" in result.results
        assert "test" in result.results["t1"]

    @pytest.mark.asyncio
    async def test_execute_parallel_tasks(self, simple_skills):
        """Test parallel execution."""
        runtime = AsyncRuntime(skills=simple_skills)

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "search", "params": {"query": "A"}, "depends_on": []},
            {"id": "t2", "skill": "search", "params": {"query": "B"}, "depends_on": []},
            {"id": "t3", "skill": "search", "params": {"query": "C"}, "depends_on": []},
        ])

        import time
        start = time.time()
        result = await runtime.execute_dag(dag)
        duration = time.time() - start

        assert result.status == "success"
        assert result.stats.completed == 3
        # Should complete faster than serial execution
        assert duration < 0.1  # 3 tasks @ 0.01s each should be ~0.01-0.03s

    @pytest.mark.asyncio
    async def test_execute_with_dependencies(self, simple_skills):
        """Test execution respects dependencies."""
        runtime = AsyncRuntime(skills=simple_skills)

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "search", "params": {"query": "A"}, "depends_on": []},
            {"id": "t2", "skill": "summarize", "params": {"text": "B"}, "depends_on": ["t1"]},
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        assert result.stats.completed == 2

        # Verify t1 finished before t2 started
        t1 = dag.nodes["t1"]
        t2 = dag.nodes["t2"]
        assert t1.finished_at <= t2.started_at

    @pytest.mark.asyncio
    async def test_execute_with_failure(self, simple_skills):
        """Test handling task failure."""
        runtime = AsyncRuntime(skills=simple_skills)

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "failing_skill", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "search", "params": {"query": "B"}, "depends_on": ["t1"]},
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "partial" or result.status == "failed"
        assert result.stats.failed == 1
        assert result.stats.skipped == 1
        assert dag.nodes["t2"].status == TaskStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_execute_with_timeout(self, simple_skills):
        """Test timeout handling."""
        config = RuntimeConfig(default_timeout=0.05, max_retries=0)
        runtime = AsyncRuntime(skills=simple_skills, config=config)

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "slow_skill", "params": {"duration": 1.0}, "depends_on": []},
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "failed"
        assert result.stats.failed == 1
        assert "Timeout" in dag.nodes["t1"].error

    @pytest.mark.asyncio
    async def test_execute_stream(self, simple_skills):
        """Test streaming execution."""
        runtime = AsyncRuntime(skills=simple_skills)

        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "search", "params": {"query": "test"}, "depends_on": []},
        ])

        events = []
        async for event in runtime.execute_stream(dag):
            events.append(event)

        # Should have: dag_start, task_start, task_done, dag_complete
        event_types = [e["type"] for e in events]
        assert "dag_start" in event_types
        assert "task_start" in event_types
        assert "task_done" in event_types
        assert "dag_complete" in event_types


class TestRuntimeConfig:
    """Tests for RuntimeConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RuntimeConfig()

        assert config.default_timeout == 30.0
        assert config.max_retries == 2
        assert config.retry_delay == 1.0
        assert config.max_concurrent == 10

    def test_custom_config(self):
        """Test custom configuration."""
        config = RuntimeConfig(
            default_timeout=60.0,
            max_retries=5,
            max_concurrent=20,
        )

        assert config.default_timeout == 60.0
        assert config.max_retries == 5
        assert config.max_concurrent == 20
