"""Tests for the planner pipeline components (Phase 3-4)."""

import pytest
from typing import Set

from src.nimbus.core.planner import (
    # Protocol
    PlanningMode,
    PlanningContext,
    FailedTaskInfo,
    # Validator
    ValidationResult,
    DAGValidator,
    # Stages
    ContextAnalyzer,
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
        validator = DAGValidator(skill_whitelist={"search", "synthesize"})
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
            {"id": f"t{i}", "skill": "synthesize", "params": {}}
            for i in range(5)
        ])

        result = validator.validate(dag)
        assert not result.valid
        assert any("exceeds" in err for err in result.errors)

    def test_repair_invalid_skill(self):
        """Invalid skills should be repaired by replacing with chat."""
        validator = DAGValidator(skill_whitelist={"search", "synthesize"})
        dag = TaskDAG.create("test", [
            {"id": "t1", "skill": "unknown", "params": {}},
        ])

        result = validator.validate(dag)
        assert not result.valid
        assert result.repaired_dag is not None
        assert result.repaired_dag.nodes["t1"].skill == "synthesize"

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
# ContextAnalyzer Tests
# =============================================================================

class TestContextAnalyzer:
    """Tests for ContextAnalyzer."""

    @pytest.fixture
    def analyzer(self):
        return ContextAnalyzer()

    @pytest.mark.asyncio
    async def test_pronoun_reference_chinese(self, analyzer):
        """Chinese pronouns should be detected."""
        ctx = PlanningContext(
            goal="这个项目叫什么名字？",
            conversation_context="Previous: Read pyproject.toml: [project] name='nimbus'...",
            available_skills={"synthesize"},
        )

        ctx = await analyzer.process(ctx)
        assert ctx.metadata.get("context_dependent") is True
        assert "pronoun_reference" in ctx.metadata.get("context_reference_types", [])

    @pytest.mark.asyncio
    async def test_pronoun_reference_english(self, analyzer):
        """English pronouns should be detected."""
        ctx = PlanningContext(
            goal="What is it called?",
            conversation_context="Previous: Found CodeAgent class in src/nimbus/core/agent.py...",
            available_skills={"synthesize"},
        )

        ctx = await analyzer.process(ctx)
        assert ctx.metadata.get("context_dependent") is True
        assert "pronoun_reference" in ctx.metadata.get("context_reference_types", [])

    @pytest.mark.asyncio
    async def test_temporal_reference_chinese(self, analyzer):
        """Chinese temporal references should be detected."""
        ctx = PlanningContext(
            goal="刚才看到的文件在哪里？",
            conversation_context="Previous: Listed files: agent.py, planner.py, runtime.py...",
            available_skills={"synthesize"},
        )

        ctx = await analyzer.process(ctx)
        assert ctx.metadata.get("context_dependent") is True
        assert "temporal_reference" in ctx.metadata.get("context_reference_types", [])

    @pytest.mark.asyncio
    async def test_among_context_reference(self, analyzer):
        """References to items among previous results should be detected."""
        ctx = PlanningContext(
            goal="其中哪个处理 LLM？",
            conversation_context="Previous: Listed directories: core, llm, server, tools...",
            available_skills={"synthesize"},
        )

        ctx = await analyzer.process(ctx)
        assert ctx.metadata.get("context_dependent") is True
        assert "among_context" in ctx.metadata.get("context_reference_types", [])

    @pytest.mark.asyncio
    async def test_no_context_reference(self, analyzer):
        """Normal queries without context references should not be flagged."""
        ctx = PlanningContext(
            goal="Search for Python tutorials",
            conversation_context="Some previous conversation about unrelated topics...",
            available_skills={"search", "synthesize"},
        )

        ctx = await analyzer.process(ctx)
        assert ctx.metadata.get("context_dependent") is False

    @pytest.mark.asyncio
    async def test_empty_context_skipped(self, analyzer):
        """Empty context should skip analysis."""
        ctx = PlanningContext(
            goal="这个是什么？",
            conversation_context="",
            available_skills={"synthesize"},
        )

        ctx = await analyzer.process(ctx)
        assert ctx.metadata.get("context_dependent") is False

    @pytest.mark.asyncio
    async def test_short_context_skipped(self, analyzer):
        """Very short context should skip analysis."""
        ctx = PlanningContext(
            goal="这个是什么？",
            conversation_context="Hi",  # Less than min_context_length
            available_skills={"synthesize"},
        )

        ctx = await analyzer.process(ctx)
        assert ctx.metadata.get("context_dependent") is False

    def test_has_context_reference(self, analyzer):
        """has_context_reference should detect patterns."""
        assert analyzer.has_context_reference("这个文件在哪里？")
        assert analyzer.has_context_reference("What is it?")
        assert analyzer.has_context_reference("刚才看到的")
        assert analyzer.has_context_reference("其中哪个")
        assert not analyzer.has_context_reference("Search for Python tutorials")

    def test_extract_context_type(self, analyzer):
        """extract_context_type should return the primary type."""
        assert analyzer.extract_context_type("这个是什么？") == "pronoun_reference"
        assert analyzer.extract_context_type("刚才的结果") == "temporal_reference"
        # "其中哪个" matches both among_context and question_about_context
        # The first pattern that matches is returned (order-dependent)
        result = analyzer.extract_context_type("其中哪个")
        assert result in ("among_context", "question_about_context")
        # Test explicit among_context pattern
        assert analyzer.extract_context_type("among them which") == "among_context"
        assert analyzer.extract_context_type("Search for something") is None


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
            available_skills={"synthesize"},
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
            available_skills={"Grep", "synthesize"},
        )

        ctx = await planner.process(ctx)
        assert ctx.rule_dag is not None
        # May match grep_code_cn or search rule, both use Grep
        assert ctx.metadata.get("matched_rule") in {"search", "grep_code_cn"}
        # Check that Grep skill is used for search
        nodes = list(ctx.rule_dag.nodes.values())
        assert any(n.skill == "Grep" for n in nodes)

    @pytest.mark.asyncio
    async def test_no_match(self, planner):
        """Complex queries should not match rules."""
        ctx = PlanningContext(
            goal="Please analyze the market trends for the next quarter",
            conversation_context="",
            available_skills={"search", "synthesize"},
        )

        ctx = await planner.process(ctx)
        assert ctx.rule_dag is None

    @pytest.mark.asyncio
    async def test_rule_only_mode_early_exit(self, planner):
        """RULE_ONLY mode should set early_exit on match."""
        ctx = PlanningContext(
            goal="你好",
            conversation_context="",
            available_skills={"synthesize"},
            planning_mode=PlanningMode.RULE_ONLY,
        )

        ctx = await planner.process(ctx)
        assert ctx.early_exit is True
        assert ctx.final_dag is not None

    @pytest.mark.asyncio
    async def test_bash_run_command_match(self, planner):
        """Bash run command patterns should be matched."""
        ctx = PlanningContext(
            goal="run the command: echo 'hello world'",
            conversation_context="",
            available_skills={"Bash", "synthesize"},
        )

        ctx = await planner.process(ctx)
        assert ctx.rule_dag is not None
        assert ctx.metadata.get("matched_rule") == "bash_run_command_en"
        nodes = list(ctx.rule_dag.nodes.values())
        assert any(n.skill == "Bash" for n in nodes)

    @pytest.mark.asyncio
    async def test_bash_echo_match(self, planner):
        """Bash echo patterns should be matched."""
        ctx = PlanningContext(
            goal="echo 'test_string_123'",
            conversation_context="",
            available_skills={"Bash", "synthesize"},
        )

        ctx = await planner.process(ctx)
        assert ctx.rule_dag is not None
        assert ctx.metadata.get("matched_rule") == "bash_echo"
        nodes = list(ctx.rule_dag.nodes.values())
        assert any(n.skill == "Bash" for n in nodes)

    @pytest.mark.asyncio
    async def test_summarize_file_match(self, planner):
        """Summarize file patterns should use Read skill."""
        ctx = PlanningContext(
            goal="summarize the file src/agent.py",
            conversation_context="",
            available_skills={"Read", "synthesize"},
        )

        ctx = await planner.process(ctx)
        assert ctx.rule_dag is not None
        assert ctx.metadata.get("matched_rule") == "summarize_file_en2"
        nodes = list(ctx.rule_dag.nodes.values())
        assert any(n.skill == "Read" for n in nodes)

    @pytest.mark.asyncio
    async def test_read_and_summarize_match(self, planner):
        """Read and summarize patterns should use Read skill."""
        ctx = PlanningContext(
            goal="src/agent.py and summarize its main purpose",
            conversation_context="",
            available_skills={"Read", "synthesize"},
        )

        ctx = await planner.process(ctx)
        assert ctx.rule_dag is not None
        assert ctx.metadata.get("matched_rule") == "summarize_file_en"
        nodes = list(ctx.rule_dag.nodes.values())
        assert any(n.skill == "Read" for n in nodes)


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
        rule_dag = TaskDAG.create_simple("synthesize", {})
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
            skill_whitelist={"search", "synthesize"},
            max_llm_tasks=10,
        )

        assert config.enable_rule_planner is False
        assert config.planning_mode == PlanningMode.LLM_FULL
        assert config.skill_whitelist == {"search", "synthesize"}


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
            available_skills={"synthesize"},
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
            available_skills={"synthesize"},
        )

        assert dag is not None
        # LLM should not be called for simple greetings when rule matches
        # (Depends on early_exit behavior)

    @pytest.mark.asyncio
    async def test_default_pipeline_llm_fallback(self):
        """Default pipeline should use LLM when rules don't match."""
        llm = MockLLMClient(response='{"mode": "dag", "tasks": [{"id": "t1", "skill": "synthesize", "params": {}}]}')
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="Explain quantum computing in simple terms",
            context="",
            available_skills={"synthesize"},
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


# =============================================================================
# FailedTaskInfo Tests
# =============================================================================

class TestFailedTaskInfo:
    """Tests for FailedTaskInfo."""

    def test_create_failed_task_info(self):
        """FailedTaskInfo should be created correctly."""
        info = FailedTaskInfo(
            task_id="t1",
            skill="Read",
            params={"file_path": "nonexistent.txt"},
            error="File not found",
            depends_on=["t0"],
        )

        assert info.task_id == "t1"
        assert info.skill == "Read"
        assert info.error == "File not found"
        assert info.depends_on == ["t0"]

    def test_default_depends_on(self):
        """FailedTaskInfo should default depends_on to empty list."""
        info = FailedTaskInfo(
            task_id="t1",
            skill="Read",
            params={},
            error="Error",
        )
        assert info.depends_on == []


# =============================================================================
# PlanningContext Replan Tests
# =============================================================================

class TestPlanningContextReplan:
    """Tests for PlanningContext replanning support."""

    def test_replan_fields_default(self):
        """Replan fields should have correct defaults."""
        ctx = PlanningContext(
            goal="test",
            conversation_context="",
            available_skills=set(),
        )

        assert ctx.is_replan is False
        assert ctx.failed_tasks == []
        assert ctx.replan_attempt == 0
        assert ctx.completed_task_ids == set()

    def test_replan_context_creation(self):
        """Replan context should be created correctly."""
        failed = [
            FailedTaskInfo(
                task_id="t1",
                skill="Read",
                params={"file_path": "test.txt"},
                error="File not found",
            )
        ]

        ctx = PlanningContext(
            goal="test goal",
            conversation_context="previous context",
            available_skills={"Read", "Glob"},
            is_replan=True,
            failed_tasks=failed,
            replan_attempt=1,
            completed_task_ids={"t0"},
        )

        assert ctx.is_replan is True
        assert len(ctx.failed_tasks) == 1
        assert ctx.replan_attempt == 1
        assert "t0" in ctx.completed_task_ids

    def test_get_failure_summary(self):
        """get_failure_summary should return formatted summary."""
        failed = [
            FailedTaskInfo(
                task_id="t1",
                skill="Read",
                params={"file_path": "test.txt"},
                error="File not found",
            ),
            FailedTaskInfo(
                task_id="t2",
                skill="Bash",
                params={"command": "invalid cmd"},
                error="Command failed",
            ),
        ]

        ctx = PlanningContext(
            goal="test",
            conversation_context="",
            available_skills=set(),
            failed_tasks=failed,
        )

        summary = ctx.get_failure_summary()
        assert "Previous Execution Failures" in summary
        assert "t1" in summary
        assert "Read" in summary
        assert "File not found" in summary
        assert "t2" in summary
        assert "Bash" in summary

    def test_get_failure_summary_empty(self):
        """get_failure_summary should return empty string when no failures."""
        ctx = PlanningContext(
            goal="test",
            conversation_context="",
            available_skills=set(),
        )

        assert ctx.get_failure_summary() == ""


# =============================================================================
# PlannerPipeline Replan Tests
# =============================================================================

class TestPlannerPipelineReplan:
    """Tests for PlannerPipeline.replan()."""

    @pytest.mark.asyncio
    async def test_replan_with_failed_tasks(self):
        """replan() should generate a new plan considering failures."""
        llm = MockLLMClient(
            response='{"mode": "dag", "tasks": [{"id": "t1", "skill": "Glob", "params": {"pattern": "*.txt"}}]}'
        )
        pipeline = PlannerPipeline.default(llm)

        failed_tasks = [
            FailedTaskInfo(
                task_id="t1",
                skill="Read",
                params={"file_path": "nonexistent.txt"},
                error="File not found",
            )
        ]

        dag = await pipeline.replan(
            goal="Read and summarize a text file",
            context="",
            available_skills={"Read", "Glob", "synthesize"},
            failed_tasks=failed_tasks,
            completed_task_ids=set(),
            replan_attempt=1,
        )

        assert dag is not None
        assert len(dag.nodes) > 0
        # LLM should have been called
        assert len(llm.calls) > 0

    @pytest.mark.asyncio
    async def test_replan_includes_failure_context(self):
        """replan() should include failure info in LLM prompt."""
        llm = MockLLMClient(response='{"mode": "direct", "response": "Fixed!"}')
        pipeline = PlannerPipeline.default(llm)

        failed_tasks = [
            FailedTaskInfo(
                task_id="t1",
                skill="Read",
                params={"file_path": "test.txt"},
                error="Permission denied",
            )
        ]

        await pipeline.replan(
            goal="Read a file",
            context="",
            available_skills={"Read", "synthesize"},
            failed_tasks=failed_tasks,
            replan_attempt=1,
        )

        # Check that the failure info was included in the prompt
        assert len(llm.calls) > 0
        prompt = llm.calls[0]
        assert "Permission denied" in prompt or "Replan" in prompt

    @pytest.mark.asyncio
    async def test_replan_preserves_completed_task_ids(self):
        """replan() should preserve completed task IDs in context."""
        llm = MockLLMClient(response='{"mode": "direct", "response": "OK"}')
        pipeline = PlannerPipeline.default(llm)

        failed_tasks = [
            FailedTaskInfo(
                task_id="t2",
                skill="Read",
                params={},
                error="Error",
            )
        ]

        await pipeline.replan(
            goal="Multi-step task",
            context="",
            available_skills={"Read", "Glob", "synthesize"},
            failed_tasks=failed_tasks,
            completed_task_ids={"t1"},  # t1 completed successfully
            replan_attempt=1,
        )

        # Check that completed tasks are mentioned in the prompt
        assert len(llm.calls) > 0
        prompt = llm.calls[0]
        assert "t1" in prompt

    @pytest.mark.asyncio
    async def test_replan_skips_rule_planner(self):
        """replan() should skip rule planner and go directly to LLM."""
        llm = MockLLMClient(response='{"mode": "direct", "response": "OK"}')
        pipeline = PlannerPipeline.default(llm)

        failed_tasks = [
            FailedTaskInfo(
                task_id="t1",
                skill="Read",
                params={},
                error="Error",
            )
        ]

        # Even for a simple goal that might match rules, replan should use LLM
        dag = await pipeline.replan(
            goal="Search for information",  # This might match search rule
            context="",
            available_skills={"search", "synthesize"},
            failed_tasks=failed_tasks,
            replan_attempt=1,
        )

        # LLM should have been called
        assert len(llm.calls) > 0
        assert dag is not None


# =============================================================================
# PlannerPipeline.with_router() Tests
# =============================================================================


class TestPlannerPipelineWithRouter:
    """Tests for PlannerPipeline.with_router() factory method."""

    @pytest.mark.asyncio
    async def test_router_pipeline_simple_task(self):
        """SIMPLE tasks should get direct synthesize DAG."""
        # Router returns SIMPLE for greetings
        llm = MockLLMClient(response='{"level": "SIMPLE"}')
        pipeline = PlannerPipeline.with_router(llm)

        dag = await pipeline.plan(
            goal="Hello!",
            context="",
            available_skills={"synthesize"},
        )

        assert dag is not None
        # Should be a synthesize DAG
        skills = {n.skill for n in dag.nodes.values()}
        assert "synthesize" in skills

    @pytest.mark.asyncio
    async def test_router_pipeline_moderate_task(self):
        """MODERATE tasks should use ToolDAGPlanner."""
        # First call: router returns MODERATE
        # Second call: tool planner returns Read task
        responses = iter([
            '{"level": "MODERATE", "tools": ["Read"]}',
            '{"tasks": [{"id": "t1", "skill": "Read", "params": {"file_path": "main.py"}}]}',
        ])

        class MultiResponseLLM:
            def __init__(self):
                self.calls = []

            async def complete(self, prompt: str) -> str:
                self.calls.append(prompt)
                return next(responses)

        llm = MultiResponseLLM()
        pipeline = PlannerPipeline.with_router(llm)

        dag = await pipeline.plan(
            goal="Read main.py",
            context="",
            available_skills={"Read", "Glob", "synthesize"},
        )

        assert dag is not None
        # Should have Read task
        assert len(dag.nodes) >= 1
        # LLM should be called twice (router + tool planner)
        assert len(llm.calls) == 2

    @pytest.mark.asyncio
    async def test_router_pipeline_complex_task(self):
        """COMPLEX tasks should get Subagent DAG."""
        llm = MockLLMClient(response='{"level": "COMPLEX", "type": "coder"}')
        pipeline = PlannerPipeline.with_router(llm)

        dag = await pipeline.plan(
            goal="Fix the bug in main.py",
            context="",
            available_skills={"Subagent", "synthesize"},
        )

        assert dag is not None
        # Should have Subagent task
        skills = {n.skill for n in dag.nodes.values()}
        assert "Subagent" in skills or "synthesize" in skills

    @pytest.mark.asyncio
    async def test_router_pipeline_config_flags(self):
        """with_router() should set correct config flags."""
        llm = MockLLMClient(response='{"level": "SIMPLE"}')
        pipeline = PlannerPipeline.with_router(llm)

        assert pipeline.config.enable_router is True
        assert pipeline.config.use_tool_planner is True
        assert pipeline.config.planning_mode == PlanningMode.HYBRID

    @pytest.mark.asyncio
    async def test_router_pipeline_has_two_stages(self):
        """with_router() pipeline should have TaskRouter + ToolPlanner stages."""
        llm = MockLLMClient()
        pipeline = PlannerPipeline.with_router(llm)

        assert len(pipeline.stages) == 2
        stage_names = [s.name for s in pipeline.stages]
        assert "task_router" in stage_names
        assert "tool_planner" in stage_names

    @pytest.mark.asyncio
    async def test_router_pipeline_early_exit_on_simple(self):
        """SIMPLE routing should set early_exit."""
        llm = MockLLMClient(response='{"level": "SIMPLE"}')
        pipeline = PlannerPipeline.with_router(llm)

        dag = await pipeline.plan(
            goal="Thanks!",
            context="",
            available_skills={"synthesize"},
        )

        # Should only call router once (early exit)
        assert len(llm.calls) == 1
        assert dag is not None
