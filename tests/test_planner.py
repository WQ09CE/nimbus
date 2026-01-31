"""Tests for PlannerPipeline and legacy SimplePlanner."""

import pytest
import asyncio
from nimbus.core.planner import (
    PlannerPipeline,
    PipelineConfig,
    PlanningMode,
    RulePlanner,
    LLMEnhancer,
    DAGValidator,
)
from nimbus.core.types import Plan, TaskType, TaskDAG


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, response: str):
        self.response = response
        self.last_prompt = None
        self.calls = []

    async def complete(self, prompt: str) -> str:
        self.last_prompt = prompt
        self.calls.append(prompt)
        return self.response


class TestPlannerPipelineBasic:
    """Test cases for PlannerPipeline basic functionality."""

    @pytest.mark.asyncio
    async def test_rule_only_greeting(self):
        """Test that greetings are handled by rule planner."""
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="你好",
            context="",
            available_skills={"synthesize"},
        )

        assert dag is not None
        assert len(dag.nodes) >= 1
        # Greeting should result in a synthesize task
        nodes = list(dag.nodes.values())
        assert any(n.skill == "synthesize" for n in nodes)

    @pytest.mark.asyncio
    async def test_rule_only_search(self):
        """Test that search patterns are handled by rule planner."""
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="搜索 Python 教程",
            context="",
            available_skills={"Grep", "synthesize"},
        )

        assert dag is not None
        nodes = list(dag.nodes.values())
        # Should use Grep skill for search
        assert any(n.skill == "Grep" for n in nodes)

    @pytest.mark.asyncio
    async def test_default_pipeline_with_llm_fallback(self):
        """Test that LLM is used when rules don't match."""
        response = '{"mode": "dag", "tasks": [{"id": "t1", "skill": "analyze", "params": {"data": "test"}}]}'
        llm = MockLLMClient(response)
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="Analyze the market trends for Q4",
            context="",
            available_skills={"analyze", "synthesize"},
        )

        assert dag is not None
        # LLM should have been called
        assert len(llm.calls) > 0

    @pytest.mark.asyncio
    async def test_direct_response_from_llm(self):
        """Test parsing direct response from LLM."""
        response = '{"mode": "direct", "response": "Hello, how can I help?"}'
        llm = MockLLMClient(response)
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="What's the weather?",
            context="",
            available_skills={"synthesize"},
        )

        assert dag is not None
        assert len(dag.nodes) == 1
        node = list(dag.nodes.values())[0]
        assert node.skill == "synthesize"

    @pytest.mark.asyncio
    async def test_multi_step_from_llm(self):
        """Test parsing multi-step response from LLM."""
        response = """{
            "mode": "dag",
            "tasks": [
                {"id": "t1", "skill": "search", "params": {"query": "AI trends"}, "depends_on": []},
                {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]}
            ]
        }"""
        llm = MockLLMClient(response)
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="Search AI trends and summarize",
            context="",
            available_skills={"search", "summarize", "synthesize"},
        )

        assert dag is not None
        assert len(dag.nodes) == 2
        assert "t1" in dag.nodes
        assert "t2" in dag.nodes
        assert dag.nodes["t2"].depends_on == ["t1"]


class TestPlannerPipelineJSONExtraction:
    """Test cases for JSON extraction in LLM responses."""

    @pytest.mark.asyncio
    async def test_extract_json_from_code_block(self):
        """Test extracting JSON from markdown code block."""
        response = """Here's my plan:
```json
{"mode": "direct", "response": "Test"}
```
"""
        llm = MockLLMClient(response)
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="test",
            context="",
            available_skills={"synthesize"},
        )

        assert dag is not None
        # Should have parsed the JSON successfully
        assert len(dag.nodes) >= 1

    @pytest.mark.asyncio
    async def test_extract_json_embedded(self):
        """Test extracting JSON embedded in text."""
        response = 'I think the answer is {"mode": "direct", "response": "OK"} yeah'
        llm = MockLLMClient(response)
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="test",
            context="",
            available_skills={"synthesize"},
        )

        assert dag is not None
        assert len(dag.nodes) >= 1

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_json(self):
        """Test that invalid JSON falls back to synthesize."""
        response = "I don't understand the format, but here's my answer."
        llm = MockLLMClient(response)
        pipeline = PlannerPipeline.default(llm)

        dag = await pipeline.plan(
            goal="test query",
            context="",
            available_skills={"synthesize"},
        )

        # Should fallback to synthesize
        assert dag is not None
        assert len(dag.nodes) >= 1
        node = list(dag.nodes.values())[0]
        assert node.skill == "synthesize"


class TestPlannerPipelineConfiguration:
    """Test cases for PlannerPipeline configuration."""

    @pytest.mark.asyncio
    async def test_custom_config(self):
        """Test creating pipeline with custom config."""
        config = PipelineConfig(
            enable_rule_planner=True,
            enable_llm_enhancer=False,
            planning_mode=PlanningMode.RULE_ONLY,
        )
        # Use rule_only factory which respects enable_llm_enhancer=False
        pipeline = PlannerPipeline.rule_only(config)

        # Should only have rule_planner stage (no llm_enhancer)
        stage_names = [s.name for s in pipeline.stages]
        assert "rule_planner" in stage_names
        assert "llm_enhancer" not in stage_names

    @pytest.mark.asyncio
    async def test_skill_whitelist_validation(self):
        """Test that invalid skills are flagged in validation."""
        response = '{"mode": "dag", "tasks": [{"id": "t1", "skill": "unknown_skill", "params": {}}]}'
        llm = MockLLMClient(response)

        config = PipelineConfig(
            skill_whitelist={"search", "synthesize"},
        )
        pipeline = PlannerPipeline.default(llm, config)

        dag = await pipeline.plan(
            goal="test",
            context="",
            available_skills={"search", "synthesize"},
        )

        # Should have repaired or fallback
        assert dag is not None
        nodes = list(dag.nodes.values())
        # Unknown skill should be replaced with synthesize
        for node in nodes:
            assert node.skill in {"search", "synthesize"}

    def test_add_remove_stage(self):
        """Test adding and removing stages."""
        llm = MockLLMClient('{"mode": "direct", "response": "test"}')
        pipeline = PlannerPipeline.default(llm)

        original_count = len(pipeline.stages)

        # Remove rule_planner
        removed = pipeline.remove_stage("rule_planner")
        assert removed is True
        assert len(pipeline.stages) == original_count - 1

        # Add it back
        pipeline.add_stage(RulePlanner(), index=0)
        assert len(pipeline.stages) == original_count


class TestPlan:
    """Test cases for Plan dataclass."""

    def test_plan_direct_constructor(self):
        """Test Plan.direct() class method."""
        plan = Plan.direct("Hello")

        assert plan.mode == "direct"
        assert plan.direct_response == "Hello"
        assert plan.tasks == []
        assert plan.is_direct()

    def test_plan_multi_step_constructor(self):
        """Test Plan.multi_step() class method."""
        from nimbus.core.types import Task

        task = Task(
            id="t1",
            type=TaskType.SYNTHESIZE,
            skill="synthesize",
            params={"message": "hi"},
        )
        plan = Plan.multi_step([task])

        assert plan.mode == "multi_step"
        assert plan.direct_response is None
        assert len(plan.tasks) == 1
        assert not plan.is_direct()


# =============================================================================
# Legacy SimplePlanner Tests (with deprecation warning suppressed)
# These tests are kept for backward compatibility verification
# =============================================================================

@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestSimplePlannerLegacy:
    """Legacy tests for SimplePlanner (deprecated).

    These tests verify backward compatibility with the deprecated SimplePlanner.
    New code should use PlannerPipeline instead.
    """

    def test_extract_json_direct(self):
        """Test extracting direct JSON."""
        from nimbus.core.planner import SimplePlanner

        client = MockLLMClient("")
        planner = SimplePlanner(client)

        data = planner._extract_json('{"mode": "direct", "response": "Hello"}')
        assert data["mode"] == "direct"
        assert data["response"] == "Hello"

    def test_extract_json_from_code_block(self):
        """Test extracting JSON from markdown code block."""
        from nimbus.core.planner import SimplePlanner

        client = MockLLMClient("")
        planner = SimplePlanner(client)

        text = """Here's my plan:
```json
{"mode": "direct", "response": "Test"}
```
"""
        data = planner._extract_json(text)
        assert data["mode"] == "direct"

    def test_extract_json_embedded(self):
        """Test extracting JSON embedded in text."""
        from nimbus.core.planner import SimplePlanner

        client = MockLLMClient("")
        planner = SimplePlanner(client)

        text = 'I think the answer is {"mode": "direct", "response": "OK"} yeah'
        data = planner._extract_json(text)
        assert data["mode"] == "direct"

    def test_extract_json_invalid(self):
        """Test that invalid JSON raises error."""
        from nimbus.core.planner import SimplePlanner
        import json

        client = MockLLMClient("")
        planner = SimplePlanner(client)

        with pytest.raises(json.JSONDecodeError):
            planner._extract_json("no json here at all")

    def test_parse_direct_response(self):
        """Test parsing direct response plan."""
        from nimbus.core.planner import SimplePlanner

        client = MockLLMClient("")
        planner = SimplePlanner(client)

        response = '{"mode": "direct", "response": "Hello, how can I help?"}'
        plan = planner._parse_response(response)

        assert plan.is_direct()
        assert plan.direct_response == "Hello, how can I help?"
        assert len(plan.tasks) == 0

    def test_parse_multi_step_response(self):
        """Test parsing multi-step plan."""
        from nimbus.core.planner import SimplePlanner

        client = MockLLMClient("")
        planner = SimplePlanner(client)

        response = """{
            "mode": "multi_step",
            "tasks": [
                {"type": "synthesize", "skill": "synthesize", "params": {"message": "hi"}}
            ]
        }"""
        plan = planner._parse_response(response)

        assert not plan.is_direct()
        assert len(plan.tasks) == 1
        assert plan.tasks[0].type == TaskType.SYNTHESIZE
        assert plan.tasks[0].skill == "synthesize"

    def test_parse_invalid_falls_back_to_direct(self):
        """Test that invalid JSON falls back to direct response."""
        from nimbus.core.planner import SimplePlanner

        client = MockLLMClient("")
        planner = SimplePlanner(client)

        response = "I don't understand the format, but here's my answer."
        plan = planner._parse_response(response)

        assert plan.is_direct()
        assert "answer" in plan.direct_response

    def test_create_plan_direct(self):
        """Test create_plan for direct response."""
        from nimbus.core.planner import SimplePlanner

        response = '{"mode": "direct", "response": "Hi there!"}'
        client = MockLLMClient(response)
        planner = SimplePlanner(client)

        plan = asyncio.run(
            planner.create_plan(
                goal="Say hello",
                context="",
                available_skills=["synthesize"],
            )
        )

        assert plan.is_direct()
        assert plan.direct_response == "Hi there!"

    def test_create_plan_multi_step(self):
        """Test create_plan for multi-step."""
        from nimbus.core.planner import SimplePlanner

        response = """{
            "mode": "multi_step",
            "tasks": [
                {"type": "analyze", "skill": "analyze", "params": {"data": "test"}}
            ]
        }"""
        client = MockLLMClient(response)
        planner = SimplePlanner(client)

        plan = asyncio.run(
            planner.create_plan(
                goal="Analyze the data",
                context="file.csv uploaded",
                available_skills=["synthesize", "analyze"],
            )
        )

        assert not plan.is_direct()
        assert len(plan.tasks) == 1
        assert plan.tasks[0].type == TaskType.ANALYZE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
