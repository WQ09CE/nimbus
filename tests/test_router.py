"""Tests for TaskRouter (ADR-010).

This module tests the TaskRouter component for lightweight complexity-based
task routing.
"""

import pytest
from typing import Optional

from src.nimbus.core.planner import (
    PlanningContext,
    PlanningMode,
)
from src.nimbus.core.planner.router import (
    TaskComplexity,
    RoutingResult,
    TaskRouter,
    TaskRouterStage,
    SIMPLE_PATTERNS,
    COMPLEX_PATTERNS,
    MODERATE_PATTERNS,
)


# =============================================================================
# Mock LLM Client
# =============================================================================


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, response: str = '{"level":"MODERATE","tools":["Read"]}'):
        self.response = response
        self.calls = []

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


class FailingLLMClient:
    """LLM client that always fails."""

    async def complete(self, prompt: str) -> str:
        raise RuntimeError("LLM unavailable")


# =============================================================================
# RoutingResult Tests
# =============================================================================


class TestRoutingResult:
    """Tests for RoutingResult dataclass."""

    def test_simple_result(self):
        """Simple result should be created correctly."""
        result = RoutingResult(
            complexity=TaskComplexity.SIMPLE,
            confidence=1.0,
        )
        assert result.complexity == TaskComplexity.SIMPLE
        assert result.suggested_tools == []
        assert result.subagent_type is None
        assert result.confidence == 1.0

    def test_moderate_result_with_tools(self):
        """Moderate result with tools should work."""
        result = RoutingResult(
            complexity=TaskComplexity.MODERATE,
            suggested_tools=["Read", "Glob"],
            confidence=0.9,
        )
        assert result.complexity == TaskComplexity.MODERATE
        assert result.suggested_tools == ["Read", "Glob"]

    def test_complex_result_with_subagent(self):
        """Complex result with subagent type should work."""
        result = RoutingResult(
            complexity=TaskComplexity.COMPLEX,
            subagent_type="coder",
            confidence=0.85,
        )
        assert result.complexity == TaskComplexity.COMPLEX
        assert result.subagent_type == "coder"

    def test_to_dict(self):
        """to_dict should serialize correctly."""
        result = RoutingResult(
            complexity=TaskComplexity.MODERATE,
            suggested_tools=["Read"],
            subagent_type=None,
            confidence=0.9,
            reasoning="matched_pattern",
        )
        d = result.to_dict()
        assert d["complexity"] == "moderate"
        assert d["suggested_tools"] == ["Read"]
        assert d["confidence"] == 0.9
        assert d["reasoning"] == "matched_pattern"


# =============================================================================
# TaskRouter - Simple Routing Tests
# =============================================================================


class TestTaskRouterSimple:
    """Tests for SIMPLE task routing."""

    @pytest.fixture
    def router(self):
        """Router with LLM disabled (rule-based only)."""
        return TaskRouter(llm_client=None, enable_llm=False)

    @pytest.mark.asyncio
    async def test_route_simple_greeting_chinese(self, router):
        """Chinese greetings should route to SIMPLE."""
        result = await router.route("你好")
        assert result.complexity == TaskComplexity.SIMPLE

    @pytest.mark.asyncio
    async def test_route_simple_greeting_english(self, router):
        """English greetings should route to SIMPLE."""
        result = await router.route("Hello")
        assert result.complexity == TaskComplexity.SIMPLE

    @pytest.mark.asyncio
    async def test_route_simple_greeting_variations(self, router):
        """Various greeting forms should route to SIMPLE."""
        greetings = ["hi", "hey", "good morning", "good evening", "早上好"]
        for greeting in greetings:
            result = await router.route(greeting)
            assert result.complexity == TaskComplexity.SIMPLE, f"Failed for: {greeting}"

    @pytest.mark.asyncio
    async def test_route_simple_thanks_chinese(self, router):
        """Chinese thanks should route to SIMPLE."""
        result = await router.route("谢谢")
        assert result.complexity == TaskComplexity.SIMPLE

    @pytest.mark.asyncio
    async def test_route_simple_thanks_english(self, router):
        """English thanks should route to SIMPLE."""
        result = await router.route("thanks")
        assert result.complexity == TaskComplexity.SIMPLE

    @pytest.mark.asyncio
    async def test_route_simple_acknowledgements(self, router):
        """Acknowledgements should route to SIMPLE."""
        acks = ["ok", "好的", "got it", "明白了"]
        for ack in acks:
            result = await router.route(ack)
            assert result.complexity == TaskComplexity.SIMPLE, f"Failed for: {ack}"


# =============================================================================
# TaskRouter - Moderate Routing Tests
# =============================================================================


class TestTaskRouterModerate:
    """Tests for MODERATE task routing."""

    @pytest.fixture
    def router(self):
        """Router with LLM disabled (rule-based only)."""
        return TaskRouter(llm_client=None, enable_llm=False)

    @pytest.mark.asyncio
    async def test_route_moderate_read_file(self, router):
        """File reading should route to MODERATE with Read tool."""
        result = await router.route("read main.py")
        assert result.complexity == TaskComplexity.MODERATE
        assert "Read" in result.suggested_tools

    @pytest.mark.asyncio
    async def test_route_moderate_read_file_chinese(self, router):
        """Chinese file reading should route to MODERATE."""
        result = await router.route("读取 main.py")
        assert result.complexity == TaskComplexity.MODERATE
        assert "Read" in result.suggested_tools

    @pytest.mark.asyncio
    async def test_route_moderate_search(self, router):
        """Code search should route to MODERATE with Grep tool."""
        result = await router.route("search for TODO comments")
        assert result.complexity == TaskComplexity.MODERATE
        assert "Grep" in result.suggested_tools

    @pytest.mark.asyncio
    async def test_route_moderate_search_chinese(self, router):
        """Chinese search should route to MODERATE."""
        result = await router.route("搜索 error 日志")
        assert result.complexity == TaskComplexity.MODERATE
        assert "Grep" in result.suggested_tools

    @pytest.mark.asyncio
    async def test_route_moderate_list_files(self, router):
        """File listing should route to MODERATE with Glob tool."""
        result = await router.route("list all Python files")
        assert result.complexity == TaskComplexity.MODERATE
        assert "Glob" in result.suggested_tools

    @pytest.mark.asyncio
    async def test_route_moderate_find_files(self, router):
        """Finding files should route to MODERATE."""
        result = await router.route("find .py files")
        assert result.complexity == TaskComplexity.MODERATE
        assert "Glob" in result.suggested_tools


# =============================================================================
# TaskRouter - Complex Routing Tests
# =============================================================================


class TestTaskRouterComplex:
    """Tests for COMPLEX task routing."""

    @pytest.fixture
    def router(self):
        """Router with LLM disabled (rule-based only)."""
        return TaskRouter(llm_client=None, enable_llm=False)

    @pytest.mark.asyncio
    async def test_route_complex_edit_code(self, router):
        """Code editing should route to COMPLEX."""
        result = await router.route("edit the main.py file to add error handling")
        assert result.complexity == TaskComplexity.COMPLEX
        assert result.subagent_type == "coder"

    @pytest.mark.asyncio
    async def test_route_complex_edit_code_chinese(self, router):
        """Chinese code editing should route to COMPLEX."""
        result = await router.route("修改 main.py 添加错误处理")
        assert result.complexity == TaskComplexity.COMPLEX
        assert result.subagent_type == "coder"

    @pytest.mark.asyncio
    async def test_route_complex_refactor(self, router):
        """Refactoring should route to COMPLEX."""
        result = await router.route("refactor the authentication module")
        assert result.complexity == TaskComplexity.COMPLEX

    @pytest.mark.asyncio
    async def test_route_complex_create_file(self, router):
        """File creation should route to COMPLEX."""
        result = await router.route("create a new test file for the router")
        assert result.complexity == TaskComplexity.COMPLEX
        assert result.subagent_type == "coder"

    @pytest.mark.asyncio
    async def test_route_complex_run_tests(self, router):
        """Running tests should route to COMPLEX."""
        result = await router.route("run the tests in the tests directory")
        assert result.complexity == TaskComplexity.COMPLEX

    @pytest.mark.asyncio
    async def test_route_complex_fix_bug(self, router):
        """Bug fixing should route to COMPLEX."""
        result = await router.route("fix the bug in the authentication")
        assert result.complexity == TaskComplexity.COMPLEX
        assert result.subagent_type == "coder"

    @pytest.mark.asyncio
    async def test_route_complex_implement_feature(self, router):
        """Feature implementation should route to COMPLEX."""
        result = await router.route("implement caching for the API responses")
        assert result.complexity == TaskComplexity.COMPLEX
        assert result.subagent_type == "coder"


# =============================================================================
# TaskRouter - Subagent Type Determination
# =============================================================================


class TestSubagentTypeDetermination:
    """Tests for subagent type determination."""

    @pytest.fixture
    def router(self):
        return TaskRouter(llm_client=None, enable_llm=False)

    @pytest.mark.asyncio
    async def test_coder_type_for_edit(self, router):
        """Edit tasks should use coder subagent."""
        result = await router.route("edit the config file")
        assert result.subagent_type == "coder"

    @pytest.mark.asyncio
    async def test_coder_type_for_fix(self, router):
        """Fix tasks should use coder subagent."""
        result = await router.route("fix the broken test")
        assert result.subagent_type == "coder"

    @pytest.mark.asyncio
    async def test_explorer_type_for_analyze(self, router):
        """Analysis tasks should use explorer subagent."""
        # Pattern: analyze + architecture
        result = await router.route("analyze the project architecture")
        assert result.complexity == TaskComplexity.COMPLEX
        assert result.subagent_type == "explorer"

    @pytest.mark.asyncio
    async def test_reviewer_type_for_review(self, router):
        """Review tasks should use reviewer subagent."""
        # Pattern: review + code/module
        result = await router.route("review the authentication code")
        assert result.complexity == TaskComplexity.COMPLEX
        assert result.subagent_type == "reviewer"


# =============================================================================
# TaskRouter - LLM Integration Tests
# =============================================================================


class TestTaskRouterLLM:
    """Tests for LLM-based routing."""

    @pytest.mark.asyncio
    async def test_llm_routing_simple(self):
        """LLM should correctly route to SIMPLE."""
        llm = MockLLMClient('{"level":"SIMPLE"}')
        router = TaskRouter(llm_client=llm, enable_llm=True)

        # Use a goal that won't match rule-based patterns for greetings
        result = await router.route("what is your name")
        assert result.complexity == TaskComplexity.SIMPLE
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_llm_routing_moderate_with_tools(self):
        """LLM should correctly route to MODERATE with tools."""
        llm = MockLLMClient('{"level":"MODERATE","tools":["Read","Glob"]}')
        router = TaskRouter(llm_client=llm, enable_llm=True)

        # Use a complex goal that needs LLM to parse properly
        result = await router.route("find all configuration files and read them")
        assert result.complexity == TaskComplexity.MODERATE
        assert "Read" in result.suggested_tools
        assert "Glob" in result.suggested_tools

    @pytest.mark.asyncio
    async def test_llm_routing_complex_with_type(self):
        """LLM should correctly route to COMPLEX with subagent type."""
        llm = MockLLMClient('{"level":"COMPLEX","type":"coder"}')
        router = TaskRouter(llm_client=llm, enable_llm=True)

        result = await router.route("implement feature X")
        assert result.complexity == TaskComplexity.COMPLEX
        assert result.subagent_type == "coder"

    @pytest.mark.asyncio
    async def test_llm_routing_with_markdown(self):
        """LLM response with markdown should be parsed correctly."""
        llm = MockLLMClient('```json\n{"level":"MODERATE","tools":["Read"]}\n```')
        router = TaskRouter(llm_client=llm, enable_llm=True)

        result = await router.route("read config.yaml")
        assert result.complexity == TaskComplexity.MODERATE

    @pytest.mark.asyncio
    async def test_llm_routing_with_extra_text(self):
        """LLM response with extra text should still parse JSON."""
        llm = MockLLMClient('Based on the task, I think: {"level":"SIMPLE"} is appropriate.')
        router = TaskRouter(llm_client=llm, enable_llm=True)

        result = await router.route("thanks")
        assert result.complexity == TaskComplexity.SIMPLE


# =============================================================================
# TaskRouter - Fallback Tests
# =============================================================================


class TestTaskRouterFallback:
    """Tests for fallback behavior when LLM fails."""

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self):
        """Should fallback to rules when LLM fails."""
        llm = FailingLLMClient()
        router = TaskRouter(llm_client=llm, enable_llm=True)

        # Should fallback to rule-based routing
        result = await router.route("你好")
        assert result.complexity == TaskComplexity.SIMPLE
        assert result.reasoning == "matched_simple_pattern"

    @pytest.mark.asyncio
    async def test_fallback_on_parse_error(self):
        """Should fallback to rules when LLM response is invalid."""
        llm = MockLLMClient("This is not JSON at all!")
        router = TaskRouter(llm_client=llm, enable_llm=True)

        # Should fallback to rule-based routing
        result = await router.route("read main.py")
        assert result.complexity == TaskComplexity.MODERATE
        assert "Read" in result.suggested_tools

    @pytest.mark.asyncio
    async def test_default_to_moderate(self):
        """Unknown tasks should default to MODERATE."""
        router = TaskRouter(llm_client=None, enable_llm=False)

        # Something that doesn't match any pattern
        result = await router.route("do something ambiguous with the system")
        assert result.complexity == TaskComplexity.MODERATE
        assert result.confidence == 0.5
        assert result.reasoning == "default_moderate"


# =============================================================================
# TaskRouter - Prompt Size Verification
# =============================================================================


class TestRouterPromptSize:
    """Tests to verify prompt size constraints."""

    def test_router_prompt_under_500_chars(self):
        """ROUTER_PROMPT_TEMPLATE must be under 500 characters (base)."""
        # Get the base prompt without {goal} substitution
        base_prompt = TaskRouter.ROUTER_PROMPT_TEMPLATE.replace("{goal}", "")
        assert len(base_prompt) < 500, f"Base prompt is {len(base_prompt)} chars, exceeds 500"

    def test_router_prompt_with_goal_reasonable(self):
        """ROUTER_PROMPT_TEMPLATE with typical goal should be reasonable size."""
        prompt = TaskRouter.ROUTER_PROMPT_TEMPLATE.format(goal="Read the main.py file and summarize it")
        # Even with goal, should be under 700 chars (accounting for goal)
        assert len(prompt) < 700, f"Full prompt is {len(prompt)} chars"


# =============================================================================
# TaskRouterStage Tests
# =============================================================================


class TestTaskRouterStage:
    """Tests for TaskRouterStage pipeline integration."""

    @pytest.fixture
    def router(self):
        return TaskRouter(llm_client=None, enable_llm=False)

    @pytest.fixture
    def stage(self, router):
        return TaskRouterStage(router)

    def test_stage_name(self, stage):
        """Stage should have correct name."""
        assert stage.name == "task_router"

    @pytest.mark.asyncio
    async def test_stage_simple_early_exit(self, stage):
        """SIMPLE tasks should trigger early exit."""
        ctx = PlanningContext(
            goal="你好",
            conversation_context="",
            available_skills={"synthesize"},
        )

        ctx = await stage.process(ctx)

        assert ctx.early_exit is True
        assert ctx.final_dag is not None
        assert ctx.metadata["routing_action"] == "direct_reply"

        # Verify synthesize DAG was created
        nodes = list(ctx.final_dag.nodes.values())
        assert len(nodes) == 1
        assert nodes[0].skill == "synthesize"

    @pytest.mark.asyncio
    async def test_stage_complex_subagent_dag(self, stage):
        """COMPLEX tasks should create Subagent DAG."""
        ctx = PlanningContext(
            goal="fix the bug in authentication",
            conversation_context="",
            available_skills={"Subagent", "synthesize"},
        )

        ctx = await stage.process(ctx)

        assert ctx.early_exit is True
        assert ctx.final_dag is not None
        assert ctx.metadata["routing_action"] == "subagent_delegation"

        # Verify Subagent DAG was created
        nodes = list(ctx.final_dag.nodes.values())
        assert any(n.skill == "Subagent" for n in nodes)

    @pytest.mark.asyncio
    async def test_stage_moderate_continues(self, stage):
        """MODERATE tasks should continue to next stage."""
        ctx = PlanningContext(
            goal="read config.yaml",
            conversation_context="",
            available_skills={"Read", "synthesize"},
        )

        ctx = await stage.process(ctx)

        assert ctx.early_exit is False
        assert ctx.final_dag is None
        assert ctx.metadata["routing_action"] == "continue"
        assert "suggested_tools" in ctx.metadata

    @pytest.mark.asyncio
    async def test_stage_stores_routing_result(self, stage):
        """Stage should store routing result in metadata."""
        ctx = PlanningContext(
            goal="thanks",
            conversation_context="",
            available_skills={"synthesize"},
        )

        ctx = await stage.process(ctx)

        assert "routing_result" in ctx.metadata
        routing_result = ctx.metadata["routing_result"]
        assert routing_result["complexity"] == "simple"


# =============================================================================
# TaskRouterStage - LLM Integration
# =============================================================================


class TestTaskRouterStageLLM:
    """Tests for TaskRouterStage with LLM routing."""

    @pytest.mark.asyncio
    async def test_stage_with_llm_routing(self):
        """Stage should use LLM when available."""
        llm = MockLLMClient('{"level":"COMPLEX","type":"explorer"}')
        router = TaskRouter(llm_client=llm, enable_llm=True)
        stage = TaskRouterStage(router)

        # Use a goal that doesn't match any rule patterns to ensure LLM is called
        ctx = PlanningContext(
            goal="deeply understand the codebase design patterns",
            conversation_context="",
            available_skills={"Subagent", "synthesize"},
        )

        ctx = await stage.process(ctx)

        assert ctx.early_exit is True
        assert ctx.metadata["routing_result"]["subagent_type"] == "explorer"
        assert len(llm.calls) == 1


# =============================================================================
# Pattern Matching Tests
# =============================================================================


class TestPatternMatching:
    """Tests to verify pattern definitions."""

    def test_simple_patterns_compile(self):
        """All SIMPLE_PATTERNS should compile."""
        import re
        for pattern in SIMPLE_PATTERNS:
            re.compile(pattern, re.IGNORECASE)

    def test_complex_patterns_compile(self):
        """All COMPLEX_PATTERNS should compile."""
        import re
        for pattern in COMPLEX_PATTERNS:
            re.compile(pattern, re.IGNORECASE)

    def test_moderate_patterns_compile(self):
        """All MODERATE_PATTERNS should compile."""
        import re
        for pattern, tools in MODERATE_PATTERNS:
            re.compile(pattern, re.IGNORECASE)
            assert isinstance(tools, list)


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.fixture
    def router(self):
        return TaskRouter(llm_client=None, enable_llm=False)

    @pytest.mark.asyncio
    async def test_empty_goal(self, router):
        """Empty goal should default to MODERATE."""
        result = await router.route("")
        assert result.complexity == TaskComplexity.MODERATE

    @pytest.mark.asyncio
    async def test_whitespace_only_goal(self, router):
        """Whitespace-only goal should default to MODERATE."""
        result = await router.route("   \n\t  ")
        assert result.complexity == TaskComplexity.MODERATE

    @pytest.mark.asyncio
    async def test_very_long_goal(self, router):
        """Very long goal should still be processed."""
        long_goal = "read " + "a" * 1000 + ".py"
        result = await router.route(long_goal)
        # Should still match read pattern
        assert result.complexity == TaskComplexity.MODERATE

    @pytest.mark.asyncio
    async def test_unicode_goal(self, router):
        """Unicode characters should be handled."""
        result = await router.route("读取 config.yaml 配置文件")
        assert result.complexity == TaskComplexity.MODERATE

    @pytest.mark.asyncio
    async def test_mixed_language_goal(self, router):
        """Mixed language goals should work."""
        result = await router.route("read 配置文件 config.yaml")
        assert result.complexity == TaskComplexity.MODERATE


# =============================================================================
# TaskComplexity Enum Tests
# =============================================================================


class TestTaskComplexity:
    """Tests for TaskComplexity enum."""

    def test_simple_value(self):
        """SIMPLE should have correct value."""
        assert TaskComplexity.SIMPLE.value == "simple"

    def test_moderate_value(self):
        """MODERATE should have correct value."""
        assert TaskComplexity.MODERATE.value == "moderate"

    def test_complex_value(self):
        """COMPLEX should have correct value."""
        assert TaskComplexity.COMPLEX.value == "complex"

    def test_from_string(self):
        """Should be able to create from string."""
        assert TaskComplexity("simple") == TaskComplexity.SIMPLE
        assert TaskComplexity("moderate") == TaskComplexity.MODERATE
        assert TaskComplexity("complex") == TaskComplexity.COMPLEX
