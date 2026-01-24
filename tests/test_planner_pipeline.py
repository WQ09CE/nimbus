"""Tests for the planner pipeline components (Phase 3-4)."""

import pytest
from typing import Set

from src.nimbus.core.planner import (
    # Protocol
    PlanningMode,
    PlanningContext,
    # Validator
    ValidationResult,
    DAGValidator,
    # Stages
    RulePlanner,
    LLMEnhancer,
    # Pipeline
    PipelineConfig,
    PlannerPipeline,
)
from src.nimbus.core.types import TaskDAG, TaskNode, TaskStatus, TaskSource


# =============================================================================
# DAGValidator Tests
# =============================================================================

class TestDAGValidator:
    """Tests for DAGValidator."""

    def test_validate_empty_dag(self):
        """Empty DAG should fail validation."""
        validator = DAGValidator()
        dag = TaskDAG(id="test", goal="test", nodes={})

        result = validator.validate(dag)
        assert not result.valid
        assert any("no nodes" in err for err in result.errors)

    def test_validate_valid_dag(self):
        """Valid DAG should pass validation."""
        validator = DAGValidator()
        dag = TaskDAG.create("test goal", [
            {"id": "t1", "skill": "search", "params": {"query": "test"}},
            {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ])

        result = validator.validate(dag)
        assert result.valid
        assert len(result.errors) == 0

    def test_validate_skill_whitelist(self):
        """Invalid skills should be flagged."""
        validator = DAGValidator(skill_whitelist={"search", "chat"})
        dag = TaskDAG.create("test goal", [
            {"id": "t1", "skill": "unknown_skill", "params": {}},
        ])

        result = validator.validate(dag)
        assert not result.valid
        assert any("unknown_skill" in err for err in result.errors)

    def test_validate_missing_dependency(self):
        """Missing dependencies should be flagged."""
        validator = DAGValidator()
        dag = TaskDAG.create("test goal", [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": ["t0"]},
        ])

        result = validator.validate(dag)
        assert not result.valid
        assert any("t0" in err for err in result.errors)

    def test_validate_cycle_detection(self):
        """Cycles should be detected."""
        validator = DAGValidator()
        # Create a simple cycle: t1 -> t2 -> t1
        dag = TaskDAG.create("test", [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": ["t2"]},
            {"id": "t2", "skill": "search", "params": {}, "depends_on": ["t1"]},
        ])

        result = validator.validate(dag)
        assert not result.valid
        assert any("cycle" in err.lower() for err in result.errors)

    def test_validate_max_tasks_limit(self):
        """Max tasks limit should be enforced."""
        validator = DAGValidator(max_tasks=3)
        dag = TaskDAG.create("test", [
            {"id": f"t{i}", "skill": "chat", "params": {}}
            for i in range(5)
        ])

        result = validator.validate(dag)
        assert not result.valid
        assert any("exceeds" in err for err in result.errors)

    def test_repair_invalid_skill(self):
        """Invalid skills should be repaired by replacing with chat."""
        validator = DAGValidator(skill_whitelist={"search", "chat"})
        dag = TaskDAG.create("test", [
            {"id": "t1", "skill": "unknown", "params": {}},
        ])

        result = validator.validate(dag)
        assert not result.valid
        assert result.repaired_dag is not None
        assert result.repaired_dag.nodes["t1"].skill == "chat"

    def test_repair_missing_dependency(self):
        """Missing dependencies should be removed during repair."""
        validator = DAGValidator()
        dag = TaskDAG.create("test", [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": ["missing"]},
        ])

        result = validator.validate(dag)
        assert result.repaired_dag is not None
        assert len(result.repaired_dag.nodes["t1"].depends_on) == 0


# =============================================================================
# RulePlanner Tests
# =============================================================================

class TestRulePlanner:
    """Tests for RulePlanner."""

    @pytest.fixture
    def planner(self):
        return RulePlanner()

    @pytest.mark.asyncio
    async def test_greeting_match(self, planner):
        """Greetings should be matched."""
        ctx = PlanningContext(
            goal="你好",
            conversation_context="",
            available_skills={"chat"},
        )

        ctx = await planner.process(ctx)
        assert ctx.rule_dag is not None
        assert ctx.metadata.get("matched_rule") == "greeting"

    @pytest.mark.asyncio
    async def test_search_match(self, planner):
        """Search patterns should be matched."""
        ctx = PlanningContext(
            goal="搜索 Python 教程",
            conversation_context="",
            available_skills={"search", "chat"},
        )

        ctx = await planner.process(ctx)
        assert ctx.rule_dag is not None
        assert ctx.metadata.get("matched_rule") == "search"
        # Check that search skill is used
        nodes = list(ctx.rule_dag.nodes.values())
        assert any(n.skill == "search" for n in nodes)

    @pytest.mark.asyncio
    async def test_no_match(self, planner):
        """Complex queries should not match rules."""
        ctx = PlanningContext(
            goal="Please analyze the market trends for the next quarter",
            conversation_context="",
            available_skills={"search", "chat"},
        )

        ctx = await planner.process(ctx)
        assert ctx.rule_dag is None

    @pytest.mark.asyncio
    async def test_rule_only_mode_early_exit(self, planner):
        """RULE_ONLY mode should set early_exit on match."""
        ctx = PlanningContext(
            goal="你好",
            conversation_context="",
            available_skills={"chat"},
            planning_mode=PlanningMode.RULE_ONLY,
        )

        ctx = await planner.process(ctx)
        assert ctx.early_exit is True
        assert ctx.final_dag is not None


# =============================================================================
# PlanningContext Tests
# =============================================================================

class TestPlanningContext:
    """Tests for PlanningContext."""

    def test_add_error(self):
        """Errors should be added correctly."""
        ctx = PlanningContext(
            goal="test",
            conversation_context="",
            available_skills=set(),
        )

        ctx.add_error("Error 1")
        ctx.add_error("Error 2")

        assert len(ctx.errors) == 2
        assert ctx.has_errors() is True

    def test_get_dag_priority(self):
        """get_dag should return in priority order: final > llm > rule."""
        ctx = PlanningContext(
            goal="test",
            conversation_context="",
            available_skills=set(),
        )

        # No DAGs - should return None
        assert ctx.get_dag() is None

        # Add rule DAG
        rule_dag = TaskDAG.create_simple("chat", {})
        ctx.rule_dag = rule_dag
        assert ctx.get_dag() is rule_dag

        # Add LLM DAG - LLM has priority over rule (final > llm > rule)
        llm_dag = TaskDAG.create_simple("search", {})
        ctx.llm_dag = llm_dag
        assert ctx.get_dag() is llm_dag

        # Add final DAG - should return final (highest priority)
        final_dag = TaskDAG.create_simple("summarize", {})
        ctx.final_dag = final_dag
        assert ctx.get_dag() is final_dag


# =============================================================================
# PipelineConfig Tests
# =============================================================================

class TestPipelineConfig:
    """Tests for PipelineConfig."""

    def test_default_config(self):
        """Default config should have sensible values."""
        config = PipelineConfig()

        assert config.enable_rule_planner is True
        assert config.enable_llm_enhancer is True
        assert config.enable_validator is True
        assert config.planning_mode == PlanningMode.HYBRID
        assert config.max_llm_tasks == 20

    def test_custom_config(self):
        """Custom config should work."""
        config = PipelineConfig(
            enable_rule_planner=False,
            planning_mode=PlanningMode.LLM_FULL,
            skill_whitelist={"search", "chat"},
            max_llm_tasks=10,
        )

        assert config.enable_rule_planner is False
        assert config.planning_mode == PlanningMode.LLM_FULL
        assert config.skill_whitelist == {"search", "chat"}


# =============================================================================
# PlannerPipeline Tests (with mock LLM)
# =============================================================================

class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, response: str = '{"mode": "direct", "response": "Hello!"}'):
        self.response = response
        self.calls = []

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


class TestPlannerPipeline:
    """Tests for PlannerPipeline."""

    @pytest.mark.asyncio
    async def test_rule_only_pipeline(self):
        """Rule-only pipeline should work."""
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="你好",
            context="",
            available_skills={"chat"},
        )

        assert dag is not None
        assert len(dag.nodes) >= 1

    @pytest.mark.asyncio
    async def test_default_pipeline_with_rule_match(self):
        """Default pipeline should use rules when matched."""
        llm = MockLLMClient()
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="你好",
            context="",
            available_skills={"chat"},
        )

        assert dag is not None
        # LLM should not be called for simple greetings when rule matches
        # (Depends on early_exit behavior)

    @pytest.mark.asyncio
    async def test_default_pipeline_llm_fallback(self):
        """Default pipeline should use LLM when rules don't match."""
        llm = MockLLMClient(response='{"mode": "dag", "tasks": [{"id": "t1", "skill": "chat", "params": {}}]}')
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="Explain quantum computing in simple terms",
            context="",
            available_skills={"chat"},
        )

        assert dag is not None
        assert len(llm.calls) > 0  # LLM was called

    @pytest.mark.asyncio
    async def test_add_and_remove_stage(self):
        """Stages can be added and removed."""
        llm = MockLLMClient()
        pipeline = PlannerPipeline.default(llm)

        # Get original stage count
        original_count = len(pipeline.stages)

        # Remove rule_planner
        removed = pipeline.remove_stage("rule_planner")
        assert removed is True
        assert len(pipeline.stages) == original_count - 1

        # Add it back
        pipeline.add_stage(RulePlanner(), index=0)
        assert len(pipeline.stages) == original_count


# =============================================================================
# ValidationResult Tests
# =============================================================================

class TestValidationResult:
    """Tests for ValidationResult."""

    def test_bool_valid(self):
        """Valid result should be truthy."""
        result = ValidationResult(valid=True)
        assert bool(result) is True

    def test_bool_invalid(self):
        """Invalid result should be falsy."""
        result = ValidationResult(valid=False, errors=["error"])
        assert bool(result) is False

    def test_result_with_warnings(self):
        """Result can have warnings but still be valid."""
        result = ValidationResult(
            valid=True,
            warnings=["This is a warning"],
        )
        assert result.valid is True
        assert len(result.warnings) == 1
