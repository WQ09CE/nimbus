"""Tests for DAG types, PlannerPipeline, and AsyncRuntime."""

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
from nimbus.core.planner import (
    PlannerPipeline,
    PipelineConfig,
    PlanningMode,
    DAGValidator,
)
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
        dag = TaskDAG.create_simple("synthesize", {"message": "hello"})

        assert len(dag.nodes) == 1
        task = list(dag.nodes.values())[0]
        assert task.skill == "synthesize"
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


class TestPlannerPipelineDAG:
    """Tests for PlannerPipeline DAG creation."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM client."""
        class MockLLM:
            def __init__(self, response: str):
                self.response = response
                self.calls = []

            async def complete(self, prompt: str) -> str:
                self.calls.append(prompt)
                return self.response

        return MockLLM

    @pytest.mark.asyncio
    async def test_plan_direct_response(self, mock_llm):
        """Test planning direct response."""
        llm = mock_llm('{"mode": "direct", "response": "Hello!"}')
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="Say hi",
            context="",
            available_skills={"synthesize", "search"},
        )

        assert len(dag.nodes) == 1
        task = list(dag.nodes.values())[0]
        assert task.skill == "synthesize"

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
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="Search AI and summarize",
            context="",
            available_skills={"synthesize", "search", "summarize"},
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
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="Search Python and Rust",
            context="",
            available_skills={"synthesize", "search"},
        )

        assert len(dag.nodes) == 2
        # Both should be ready (no dependencies)
        ready = dag.get_ready_tasks()
        assert len(ready) == 2


class TestDAGValidatorIntegration:
    """Tests for DAGValidator used in planning."""

    def test_validate_dag_valid(self):
        """Test validating a valid DAG."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ]
        dag = TaskDAG.create("Test", tasks)

        validator = DAGValidator(skill_whitelist={"search", "summarize"})
        result = validator.validate(dag)

        assert result.valid
        assert len(result.errors) == 0

    def test_validate_dag_unknown_skill(self):
        """Test validating DAG with unknown skill."""
        tasks = [
            {"id": "t1", "skill": "unknown_skill", "params": {}, "depends_on": []},
        ]
        dag = TaskDAG.create("Test", tasks)

        validator = DAGValidator(skill_whitelist={"search", "summarize"})
        result = validator.validate(dag)

        assert not result.valid
        assert any("unknown_skill" in err for err in result.errors)

    def test_validate_dag_missing_dependency(self):
        """Test validating DAG with missing dependency."""
        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": ["t0"]},
        ]
        dag = TaskDAG.create("Test", tasks)

        validator = DAGValidator()
        result = validator.validate(dag)

        assert not result.valid
        assert any("t0" in err for err in result.errors)

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

        validator = DAGValidator(skill_whitelist={"a", "b"})
        result = validator.validate(dag)

        assert not result.valid
        assert any("cycle" in e.lower() for e in result.errors)


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


class TestChatDependencyInjection:
    """Tests for chat skill dependency result injection."""

    @pytest.fixture
    def chat_skills(self):
        """Create skills for testing chat dependency injection."""
        async def glob(pattern: str = "") -> list:
            await asyncio.sleep(0.01)
            return ["file1.py", "file2.py", "file3.py"]

        async def read(file_path: str = "") -> str:
            await asyncio.sleep(0.01)
            return f"Content of {file_path}:\ndef hello():\n    print('Hello')"

        async def grep(pattern: str = "", path: str = "") -> str:
            await asyncio.sleep(0.01)
            return f"file1.py:10: {pattern} found here"

        async def synthesize(message: str = "", context: str = "", **kwargs) -> str:
            await asyncio.sleep(0.01)
            # Return the context so we can verify injection
            return f"Context received: {context}"

        return {
            "Glob": glob,
            "Read": read,
            "Grep": grep,
            "synthesize": synthesize,
        }

    @pytest.mark.asyncio
    async def test_chat_receives_glob_results(self, chat_skills):
        """Test chat skill receives glob results in context."""
        runtime = AsyncRuntime(skills=chat_skills)

        dag = TaskDAG.create("Test chat with glob", [
            {"id": "t1", "skill": "Glob", "params": {"pattern": "*.py"}, "depends_on": []},
            {"id": "t2", "skill": "synthesize", "params": {"message": "What files?", "context": ""}, "depends_on": ["t1"]},
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        assert result.stats.completed == 2

        # Verify chat received the glob results
        chat_result = result.results["t2"]
        assert "Glob Results" in chat_result
        assert "file1.py" in chat_result

    @pytest.mark.asyncio
    async def test_chat_receives_read_results(self, chat_skills):
        """Test chat skill receives file read results in context."""
        runtime = AsyncRuntime(skills=chat_skills)

        dag = TaskDAG.create("Test chat with read", [
            {"id": "t1", "skill": "Read", "params": {"file_path": "test.py"}, "depends_on": []},
            {"id": "t2", "skill": "synthesize", "params": {"message": "Explain code", "context": ""}, "depends_on": ["t1"]},
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        chat_result = result.results["t2"]
        assert "File Content" in chat_result
        assert "test.py" in chat_result
        assert "def hello():" in chat_result

    @pytest.mark.asyncio
    async def test_chat_receives_multiple_dependency_results(self, chat_skills):
        """Test chat skill receives results from multiple dependencies."""
        runtime = AsyncRuntime(skills=chat_skills)

        dag = TaskDAG.create("Test chat with multiple deps", [
            {"id": "t1", "skill": "Glob", "params": {"pattern": "*.py"}, "depends_on": []},
            {"id": "t2", "skill": "Read", "params": {"file_path": "main.py"}, "depends_on": []},
            {"id": "t3", "skill": "synthesize", "params": {"message": "Analyze", "context": "User context"}, "depends_on": ["t1", "t2"]},
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        chat_result = result.results["t3"]

        # Should have both glob and read results
        assert "Glob Results" in chat_result
        assert "File Content" in chat_result
        # Should preserve original context
        assert "User context" in chat_result

    @pytest.mark.asyncio
    async def test_non_chat_skills_not_affected(self, chat_skills):
        """Test non-chat skills don't get dependency injection."""
        runtime = AsyncRuntime(skills=chat_skills)

        dag = TaskDAG.create("Test non-chat", [
            {"id": "t1", "skill": "Glob", "params": {"pattern": "*.py"}, "depends_on": []},
            {"id": "t2", "skill": "Read", "params": {"file_path": "test.py"}, "depends_on": ["t1"]},
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        # Read should get its normal params, not injected context
        read_result = result.results["t2"]
        assert "Content of test.py" in read_result

    @pytest.mark.asyncio
    async def test_chat_with_grep_results(self, chat_skills):
        """Test chat skill receives grep results in context."""
        runtime = AsyncRuntime(skills=chat_skills)

        dag = TaskDAG.create("Test chat with grep", [
            {"id": "t1", "skill": "Grep", "params": {"pattern": "TODO", "path": "."}, "depends_on": []},
            {"id": "t2", "skill": "synthesize", "params": {"message": "Fix TODOs", "context": ""}, "depends_on": ["t1"]},
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        chat_result = result.results["t2"]
        assert "Grep Results" in chat_result
        assert "TODO" in chat_result


# =============================================================================
# Legacy DAGPlanner Tests (with deprecation warning suppressed)
# =============================================================================

@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestDAGPlannerLegacy:
    """Legacy tests for DAGPlanner (deprecated).

    These tests verify backward compatibility with the deprecated DAGPlanner.
    New code should use PlannerPipeline instead.
    """

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
        from nimbus.core.planner import DAGPlanner

        llm = mock_llm('{"mode": "direct", "response": "Hello!"}')
        planner = DAGPlanner(llm)

        dag = await planner.create_plan(
            goal="Say hi",
            context="",
            available_skills={"synthesize", "search"},
        )

        assert len(dag.nodes) == 1
        task = list(dag.nodes.values())[0]
        assert task.skill == "synthesize"

    @pytest.mark.asyncio
    async def test_plan_dag_with_dependencies(self, mock_llm):
        """Test planning DAG with dependencies."""
        from nimbus.core.planner import DAGPlanner

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
            goal="Search and summarize",
            context="",
            available_skills={"synthesize", "search", "summarize"},
        )

        assert len(dag.nodes) == 2
        assert dag.nodes["t1"].depends_on == []
        assert dag.nodes["t2"].depends_on == ["t1"]

    def test_validate_dag_valid(self, mock_llm):
        """Test validating a valid DAG."""
        from nimbus.core.planner import DAGPlanner

        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ]
        dag = TaskDAG.create("Test", tasks)

        planner = DAGPlanner(None)
        errors = planner.validate_dag(dag, {"search", "summarize"})

        assert errors == []

    def test_validate_dag_unknown_skill(self, mock_llm):
        """Test validating DAG with unknown skill."""
        from nimbus.core.planner import DAGPlanner

        tasks = [
            {"id": "t1", "skill": "unknown_skill", "params": {}, "depends_on": []},
        ]
        dag = TaskDAG.create("Test", tasks)

        planner = DAGPlanner(None)
        errors = planner.validate_dag(dag, {"search", "summarize"})

        assert len(errors) == 1
        assert "unknown_skill" in errors[0]

    def test_validate_dag_missing_dependency(self, mock_llm):
        """Test validating DAG with missing dependency."""
        from nimbus.core.planner import DAGPlanner

        tasks = [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": ["t0"]},
        ]
        dag = TaskDAG.create("Test", tasks)

        planner = DAGPlanner(None)
        errors = planner.validate_dag(dag, {"search"})

        assert len(errors) == 1
        assert "t0" in errors[0]

    def test_validate_dag_cycle(self, mock_llm):
        """Test validating DAG with cycle."""
        from nimbus.core.planner import DAGPlanner

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
