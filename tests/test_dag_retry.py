"""Tests for DAG conditional branching and retry loop mechanism (ADR-007)."""

import pytest
import asyncio
from datetime import datetime

from nimbus.core.types import (
    TaskStatus,
    TaskNode,
    TaskDAG,
    TaskSource,
    RetryLoopConfig,
    RuntimeConfig,
)
from nimbus.core.planner.rule_planner import RulePlanner, PLANNING_RULES
from nimbus.core.planner.protocol import PlanningContext, PlanningMode
from nimbus.core.runtime import AsyncRuntime


class TestTaskNodeRetryFields:
    """Tests for TaskNode retry-related fields."""

    def test_task_node_default_retry_fields(self):
        """Test that default retry fields are set correctly."""
        node = TaskNode(
            id="t1",
            skill="test",
            params={},
        )

        assert node.on_failure is None
        assert node.retry_target is None
        assert node.max_retries == 0
        assert node.retry_count == 0
        assert node.inactive is False

    def test_task_node_with_retry_fields(self):
        """Test TaskNode creation with retry fields."""
        node = TaskNode(
            id="t1",
            skill="verify",
            params={"command": "pytest"},
            on_failure="t2_fix",
            max_retries=3,
        )

        assert node.on_failure == "t2_fix"
        assert node.max_retries == 3

    def test_task_node_fix_task(self):
        """Test TaskNode as a fix task with retry_target."""
        node = TaskNode(
            id="t2_fix",
            skill="fix",
            params={"error": "some error"},
            retry_target="t1",
            inactive=True,
        )

        assert node.retry_target == "t1"
        assert node.inactive is True

    def test_task_node_to_dict_includes_retry_fields(self):
        """Test that to_dict includes retry fields."""
        node = TaskNode(
            id="t1",
            skill="verify",
            params={},
            on_failure="t2_fix",
            retry_target=None,
            max_retries=3,
            retry_count=1,
            inactive=False,
        )

        d = node.to_dict()

        assert d["on_failure"] == "t2_fix"
        assert d["retry_target"] is None
        assert d["max_retries"] == 3
        assert d["retry_count"] == 1
        assert d["inactive"] is False

    def test_task_node_from_dict_restores_retry_fields(self):
        """Test that from_dict restores retry fields."""
        data = {
            "id": "t1",
            "skill": "verify",
            "params": {},
            "on_failure": "t2_fix",
            "retry_target": None,
            "max_retries": 3,
            "retry_count": 2,
            "inactive": False,
        }

        node = TaskNode.from_dict(data)

        assert node.on_failure == "t2_fix"
        assert node.retry_target is None
        assert node.max_retries == 3
        assert node.retry_count == 2
        assert node.inactive is False

    def test_task_node_from_dict_backward_compatible(self):
        """Test that from_dict handles missing retry fields (backward compatibility)."""
        data = {
            "id": "t1",
            "skill": "search",
            "params": {"query": "test"},
        }

        node = TaskNode.from_dict(data)

        assert node.on_failure is None
        assert node.retry_target is None
        assert node.max_retries == 0
        assert node.retry_count == 0
        assert node.inactive is False


class TestRetryLoopConfig:
    """Tests for RetryLoopConfig dataclass."""

    def test_retry_loop_config_creation(self):
        """Test basic RetryLoopConfig creation."""
        config = RetryLoopConfig(
            verify_task="t3_verify",
            fix_task="t4_fix",
        )

        assert config.verify_task == "t3_verify"
        assert config.fix_task == "t4_fix"
        assert config.max_attempts == 3  # default
        assert config.backoff_seconds == 0.0  # default

    def test_retry_loop_config_with_custom_values(self):
        """Test RetryLoopConfig with custom values."""
        config = RetryLoopConfig(
            verify_task="verify",
            fix_task="fix",
            max_attempts=5,
            backoff_seconds=2.0,
        )

        assert config.max_attempts == 5
        assert config.backoff_seconds == 2.0

    def test_retry_loop_config_to_dict(self):
        """Test serialization."""
        config = RetryLoopConfig(
            verify_task="t3",
            fix_task="t4",
            max_attempts=3,
            backoff_seconds=1.0,
        )

        d = config.to_dict()

        assert d["verify_task"] == "t3"
        assert d["fix_task"] == "t4"
        assert d["max_attempts"] == 3
        assert d["backoff_seconds"] == 1.0

    def test_retry_loop_config_from_dict(self):
        """Test deserialization."""
        data = {
            "verify_task": "t3",
            "fix_task": "t4",
            "max_attempts": 5,
            "backoff_seconds": 0.5,
        }

        config = RetryLoopConfig.from_dict(data)

        assert config.verify_task == "t3"
        assert config.fix_task == "t4"
        assert config.max_attempts == 5
        assert config.backoff_seconds == 0.5


class TestTaskDAGRetryFeatures:
    """Tests for TaskDAG retry-related features."""

    def test_dag_create_with_retry_fields(self):
        """Test DAG creation with retry fields in task definitions."""
        tasks = [
            {
                "id": "t1",
                "skill": "verify",
                "params": {"command": "test"},
                "on_failure": "t2",
                "max_retries": 3,
            },
            {
                "id": "t2",
                "skill": "fix",
                "params": {},
                "retry_target": "t1",
                "inactive": True,
            },
        ]

        dag = TaskDAG.create("Test retry", tasks)

        assert dag.nodes["t1"].on_failure == "t2"
        assert dag.nodes["t1"].max_retries == 3
        assert dag.nodes["t2"].retry_target == "t1"
        assert dag.nodes["t2"].inactive is True

    def test_dag_get_ready_tasks_skips_inactive(self):
        """Test that inactive tasks are not returned by get_ready_tasks."""
        tasks = [
            {
                "id": "t1",
                "skill": "verify",
                "params": {},
                "depends_on": [],
            },
            {
                "id": "t2",
                "skill": "fix",
                "params": {},
                "depends_on": [],
                "inactive": True,  # Should not be scheduled
            },
        ]

        dag = TaskDAG.create("Test", tasks)
        ready = dag.get_ready_tasks()

        assert len(ready) == 1
        assert ready[0].id == "t1"

    def test_dag_to_dict_includes_retry_loops(self):
        """Test that to_dict includes retry_loops."""
        dag = TaskDAG.create("Test", [
            {"id": "t1", "skill": "verify", "params": {}},
        ])
        dag.retry_loops.append(RetryLoopConfig(
            verify_task="t1",
            fix_task="t2",
            max_attempts=3,
        ))

        d = dag.to_dict()

        assert "retry_loops" in d
        assert len(d["retry_loops"]) == 1
        assert d["retry_loops"][0]["verify_task"] == "t1"

    def test_dag_from_dict_restores_retry_loops(self):
        """Test that from_dict restores retry_loops."""
        data = {
            "id": "dag_test",
            "goal": "Test",
            "nodes": {
                "t1": {"id": "t1", "skill": "verify", "params": {}},
            },
            "retry_loops": [
                {
                    "verify_task": "t1",
                    "fix_task": "t2",
                    "max_attempts": 5,
                    "backoff_seconds": 1.0,
                }
            ],
        }

        dag = TaskDAG.from_dict(data)

        assert len(dag.retry_loops) == 1
        assert dag.retry_loops[0].verify_task == "t1"
        assert dag.retry_loops[0].max_attempts == 5


class TestRulePlannerRetrySupport:
    """Tests for RulePlanner retry rule support."""

    def test_auto_test_writer_rule_exists(self):
        """Test that auto_test_writer rule is defined."""
        rule_names = [r.get("name") for r in PLANNING_RULES]
        assert "auto_test_writer" in rule_names

    @pytest.mark.asyncio
    async def test_auto_test_writer_rule_matches(self):
        """Test that auto_test_writer rule matches expected pattern."""
        planner = RulePlanner()
        ctx = PlanningContext(
            goal="为 src/main.py 写单元测试",
            conversation_context="",
            available_skills={"Read", "Bash", "synthesize"},
            planning_mode=PlanningMode.RULE_ONLY,
        )

        result = await planner.process(ctx)

        assert result.rule_dag is not None
        assert result.metadata.get("matched_rule") == "auto_test_writer"

    @pytest.mark.asyncio
    async def test_auto_test_writer_dag_structure(self):
        """Test that auto_test_writer creates correct DAG structure."""
        planner = RulePlanner()
        ctx = PlanningContext(
            goal="为 myfile.py 编写测试",
            conversation_context="",
            available_skills={"Read", "Bash", "synthesize"},
            planning_mode=PlanningMode.RULE_ONLY,
        )

        result = await planner.process(ctx)
        dag = result.rule_dag

        assert dag is not None

        # Check tasks exist
        assert "t1_read" in dag.nodes
        assert "t2_gen" in dag.nodes
        assert "t3_verify" in dag.nodes
        assert "t4_fix" in dag.nodes

        # Check t3_verify has on_failure
        t3 = dag.nodes["t3_verify"]
        assert t3.on_failure == "t4_fix"
        assert t3.max_retries == 3

        # Check t4_fix is inactive with retry_target
        t4 = dag.nodes["t4_fix"]
        assert t4.inactive is True
        assert t4.retry_target == "t3_verify"

    @pytest.mark.asyncio
    async def test_auto_test_writer_retry_loops(self):
        """Test that auto_test_writer creates retry_loops."""
        planner = RulePlanner()
        ctx = PlanningContext(
            goal="给 test.py 生成单元测试",
            conversation_context="",
            available_skills={"Read", "Bash", "synthesize"},
            planning_mode=PlanningMode.RULE_ONLY,
        )

        result = await planner.process(ctx)
        dag = result.rule_dag

        assert len(dag.retry_loops) == 1
        loop = dag.retry_loops[0]
        assert loop.verify_task == "t3_verify"
        assert loop.fix_task == "t4_fix"
        assert loop.max_attempts == 3

    @pytest.mark.asyncio
    async def test_code_fix_with_retry_rule_matches(self):
        """Test code_fix_with_retry rule matches."""
        planner = RulePlanner()
        ctx = PlanningContext(
            goal="修复 src/bug.py 并验证",
            conversation_context="",
            available_skills={"Bash", "synthesize"},
            planning_mode=PlanningMode.RULE_ONLY,
        )

        result = await planner.process(ctx)

        assert result.rule_dag is not None
        assert result.metadata.get("matched_rule") == "code_fix_with_retry"


class TestAsyncRuntimeRetryLoop:
    """Tests for AsyncRuntime retry loop execution."""

    @pytest.fixture
    def retry_skills(self):
        """Create skills for retry testing."""
        call_counts = {"verify": 0, "fix": 0}

        async def verify_skill(command: str = "") -> str:
            call_counts["verify"] += 1
            if call_counts["verify"] < 3:
                raise ValueError(f"Test failed (attempt {call_counts['verify']})")
            return "All tests passed"

        async def fix_skill(prompt: str = "", **kwargs) -> str:
            call_counts["fix"] += 1
            return f"Fixed (attempt {call_counts['fix']})"

        async def always_fail(**kwargs) -> str:
            raise ValueError("Always fails")

        async def always_succeed(**kwargs) -> str:
            return "Success"

        return {
            "verify": verify_skill,
            "fix": fix_skill,
            "always_fail": always_fail,
            "always_succeed": always_succeed,
            "call_counts": call_counts,
        }

    @pytest.mark.asyncio
    async def test_retry_loop_success(self, retry_skills):
        """Test successful retry loop (fix succeeds, verify eventually passes)."""
        runtime = AsyncRuntime(
            skills={
                "verify": retry_skills["verify"],
                "fix": retry_skills["fix"],
            },
            config=RuntimeConfig(max_retries=0),  # Disable built-in retries
        )

        dag = TaskDAG.create("Test retry", [
            {
                "id": "t1",
                "skill": "verify",
                "params": {"command": "test"},
                "on_failure": "t2",
                "max_retries": 3,
            },
            {
                "id": "t2",
                "skill": "fix",
                "params": {"prompt": "fix error"},
                "retry_target": "t1",
                "inactive": True,
            },
        ])

        result = await runtime.execute_dag(dag)

        # Should eventually succeed after retries
        assert result.status == "success"
        assert dag.nodes["t1"].status == TaskStatus.COMPLETED

        # Verify was called 3 times (2 failures + 1 success)
        assert retry_skills["call_counts"]["verify"] == 3
        # Fix was called 2 times (for the 2 failures)
        assert retry_skills["call_counts"]["fix"] == 2

    @pytest.mark.asyncio
    async def test_retry_loop_max_retries_reached(self, retry_skills):
        """Test that retry loop stops at max_retries."""
        always_fail_count = {"count": 0}

        async def always_fail_verify(**kwargs):
            always_fail_count["count"] += 1
            raise ValueError("Always fails")

        runtime = AsyncRuntime(
            skills={
                "verify": always_fail_verify,
                "fix": retry_skills["fix"],
            },
            config=RuntimeConfig(max_retries=0),
        )

        dag = TaskDAG.create("Test max retries", [
            {
                "id": "t1",
                "skill": "verify",
                "params": {},
                "on_failure": "t2",
                "max_retries": 2,  # Only 2 retries
            },
            {
                "id": "t2",
                "skill": "fix",
                "params": {},
                "retry_target": "t1",
                "inactive": True,
            },
            {
                "id": "t3",
                "skill": "verify",  # Downstream task
                "params": {},
                "depends_on": ["t1"],
            },
        ])

        result = await runtime.execute_dag(dag)

        # Should fail after max_retries
        assert result.status == "failed" or result.status == "partial"
        assert dag.nodes["t1"].status == TaskStatus.FAILED
        assert dag.nodes["t3"].status == TaskStatus.SKIPPED

        # Verify was called 3 times (1 initial + 2 retries)
        assert always_fail_count["count"] == 3

    @pytest.mark.asyncio
    async def test_fix_task_failure(self, retry_skills):
        """Test behavior when fix task also fails."""
        runtime = AsyncRuntime(
            skills={
                "verify": retry_skills["always_fail"],
                "fix": retry_skills["always_fail"],  # Fix also fails
            },
            config=RuntimeConfig(max_retries=0),
        )

        dag = TaskDAG.create("Test fix failure", [
            {
                "id": "t1",
                "skill": "verify",
                "params": {},
                "on_failure": "t2",
                "max_retries": 2,
            },
            {
                "id": "t2",
                "skill": "fix",
                "params": {},
                "retry_target": "t1",
                "inactive": True,
            },
        ])

        result = await runtime.execute_dag(dag)

        # Should fail because fix failed
        assert result.status == "failed" or result.status == "partial"
        assert dag.nodes["t1"].status == TaskStatus.FAILED
        assert dag.nodes["t2"].status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_inactive_task_not_scheduled_initially(self, retry_skills):
        """Test that inactive tasks are not scheduled in get_ready_tasks."""
        # This test verifies that get_ready_tasks() does not return inactive tasks
        dag = TaskDAG.create("Test inactive", [
            {
                "id": "t1",
                "skill": "verify",
                "params": {},
                "depends_on": [],
            },
            {
                "id": "t2",
                "skill": "fix",
                "params": {},
                "depends_on": [],
                "inactive": True,  # Should not be in ready tasks
            },
        ])

        ready = dag.get_ready_tasks()

        # Only t1 should be ready (t2 is inactive)
        assert len(ready) == 1
        assert ready[0].id == "t1"

    @pytest.mark.asyncio
    async def test_inactive_task_activated_on_failure(self, retry_skills):
        """Test that inactive task is activated and executed on failure."""
        call_order = []

        async def verify_skill(**kwargs):
            call_order.append("verify")
            raise ValueError("Test failed")

        async def fix_skill(**kwargs):
            call_order.append("fix")
            return "Fixed"

        runtime = AsyncRuntime(
            skills={
                "verify": verify_skill,
                "fix": fix_skill,
            },
            config=RuntimeConfig(max_retries=0),
        )

        dag = TaskDAG.create("Test inactive activation", [
            {
                "id": "t1",
                "skill": "verify",
                "params": {},
                "on_failure": "t2",
                "max_retries": 1,
            },
            {
                "id": "t2",
                "skill": "fix",
                "params": {},
                "retry_target": "t1",
                "inactive": True,  # Will be activated on t1 failure
            },
        ])

        # t2 starts as inactive
        assert dag.nodes["t2"].inactive is True

        await runtime.execute_dag(dag)

        # Verify t2 was activated and executed
        assert "fix" in call_order
        # After activation, t2's inactive flag should be False
        assert dag.nodes["t2"].inactive is False

    @pytest.mark.asyncio
    async def test_retry_count_tracking(self, retry_skills):
        """Test that retry_count is properly tracked."""
        call_count = {"count": 0}

        async def counting_verify(**kwargs):
            call_count["count"] += 1
            if call_count["count"] < 3:
                raise ValueError("Fail")
            return "Pass"

        runtime = AsyncRuntime(
            skills={
                "verify": counting_verify,
                "fix": retry_skills["fix"],
            },
            config=RuntimeConfig(max_retries=0),
        )

        dag = TaskDAG.create("Test retry count", [
            {
                "id": "t1",
                "skill": "verify",
                "params": {},
                "on_failure": "t2",
                "max_retries": 3,
            },
            {
                "id": "t2",
                "skill": "fix",
                "params": {},
                "retry_target": "t1",
                "inactive": True,
            },
        ])

        await runtime.execute_dag(dag)

        # After 2 retries (to reach success on 3rd call)
        assert dag.nodes["t1"].retry_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_when_max_retries_zero(self, retry_skills):
        """Test that no retry happens when max_retries is 0."""
        fix_called = {"called": False}

        async def tracking_fix(**kwargs):
            fix_called["called"] = True
            return "Fixed"

        runtime = AsyncRuntime(
            skills={
                "verify": retry_skills["always_fail"],
                "fix": tracking_fix,
            },
            config=RuntimeConfig(max_retries=0),
        )

        dag = TaskDAG.create("Test no retry", [
            {
                "id": "t1",
                "skill": "verify",
                "params": {},
                "on_failure": "t2",
                "max_retries": 0,  # No retries - on_failure handler won't be triggered
            },
            {
                "id": "t2",
                "skill": "fix",
                "params": {},
                "retry_target": "t1",
                "inactive": True,
            },
        ])

        await runtime.execute_dag(dag)

        # t1 should fail without retry
        assert dag.nodes["t1"].status == TaskStatus.FAILED
        # Fix should NOT have been called (max_retries=0 means no on_failure trigger)
        assert fix_called["called"] is False
