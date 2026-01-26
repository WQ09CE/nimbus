"""Tests for ToolDAGPlanner (lightweight read-only planner)."""

import pytest
from typing import Optional

from src.nimbus.core.planner import (
    ToolDAGPlanner,
    ToolPlannerStage,
    READONLY_TOOLS,
    TOOL_DAG_PROMPT,
    get_prompt_size,
    validate_prompt_size,
    PlanningContext,
    PlanningMode,
)
from src.nimbus.core.types import TaskDAG, TaskSource


# =============================================================================
# Mock LLM Client
# =============================================================================


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, response: str = '{"tasks": []}'):
        self.response = response
        self.calls = []

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


# =============================================================================
# ToolDAGPlanner Tests
# =============================================================================


class TestToolDAGPlanner:
    """Tests for ToolDAGPlanner."""

    @pytest.mark.asyncio
    async def test_plan_read_file(self):
        """Should generate Read task for file reading request."""
        llm = MockLLMClient(
            response='{"tasks": [{"id": "t1", "skill": "Read", "params": {"file_path": "main.py"}}]}'
        )
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Read main.py")

        assert dag is not None
        assert len(dag.nodes) == 1
        assert dag.nodes["t1"].skill == "Read"
        assert dag.nodes["t1"].params.get("file_path") == "main.py"

    @pytest.mark.asyncio
    async def test_plan_glob_search(self):
        """Should generate Glob task for file search request."""
        llm = MockLLMClient(
            response='{"tasks": [{"id": "t1", "skill": "Glob", "params": {"pattern": "**/*.py"}}]}'
        )
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Find all Python files")

        assert dag is not None
        assert len(dag.nodes) == 1
        assert dag.nodes["t1"].skill == "Glob"
        assert "*.py" in dag.nodes["t1"].params.get("pattern", "")

    @pytest.mark.asyncio
    async def test_plan_grep_search(self):
        """Should generate Grep task for code search request."""
        llm = MockLLMClient(
            response='{"tasks": [{"id": "t1", "skill": "Grep", "params": {"pattern": "def main"}}]}'
        )
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Search for main function")

        assert dag is not None
        assert len(dag.nodes) == 1
        assert dag.nodes["t1"].skill == "Grep"

    @pytest.mark.asyncio
    async def test_plan_parallel_tasks(self):
        """Should generate parallel tasks with empty depends_on."""
        llm = MockLLMClient(
            response='{"tasks": ['
            '{"id": "t1", "skill": "Glob", "params": {"pattern": "**/*.py"}, "depends_on": []},'
            '{"id": "t2", "skill": "Glob", "params": {"pattern": "**/*.js"}, "depends_on": []}'
            ']}'
        )
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Find Python and JavaScript files")

        assert len(dag.nodes) == 2
        assert dag.nodes["t1"].depends_on == []
        assert dag.nodes["t2"].depends_on == []

    @pytest.mark.asyncio
    async def test_plan_sequential_tasks(self):
        """Should generate sequential tasks with depends_on."""
        llm = MockLLMClient(
            response='{"tasks": ['
            '{"id": "t1", "skill": "Glob", "params": {"pattern": "*.py"}},'
            '{"id": "t2", "skill": "Read", "params": {"file_path": "found.py"}, "depends_on": ["t1"]}'
            ']}'
        )
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Find and read Python files")

        assert len(dag.nodes) == 2
        assert dag.nodes["t2"].depends_on == ["t1"]

    @pytest.mark.asyncio
    async def test_filters_non_readonly_tools(self):
        """Should filter out non-read-only tools."""
        llm = MockLLMClient(
            response='{"tasks": ['
            '{"id": "t1", "skill": "Read", "params": {"file_path": "test.py"}},'
            '{"id": "t2", "skill": "Bash", "params": {"command": "rm -rf /"}},'
            '{"id": "t3", "skill": "Write", "params": {"file_path": "bad.py"}}'
            ']}'
        )
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Do something")

        # Only Read should remain
        assert len(dag.nodes) == 1
        assert "t1" in dag.nodes
        assert dag.nodes["t1"].skill == "Read"

    @pytest.mark.asyncio
    async def test_fallback_on_empty_response(self):
        """Should create fallback DAG when LLM returns empty tasks."""
        llm = MockLLMClient(response='{"tasks": []}')
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Do something")

        # Fallback should have Glob + Synthesize
        assert len(dag.nodes) == 2
        skills = {n.skill for n in dag.nodes.values()}
        assert "Glob" in skills
        assert "Synthesize" in skills

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_json(self):
        """Should create fallback DAG on invalid JSON response."""
        llm = MockLLMClient(response="This is not valid JSON")
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Do something")

        # Should get fallback DAG
        assert dag is not None
        assert len(dag.nodes) >= 1

    @pytest.mark.asyncio
    async def test_fallback_on_exception(self):
        """Should create fallback DAG when LLM raises exception."""
        llm = MockLLMClient()

        async def raise_error(prompt: str) -> str:
            raise Exception("LLM error")

        llm.complete = raise_error
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Do something")

        # Should get fallback DAG
        assert dag is not None

    @pytest.mark.asyncio
    async def test_task_source_is_llm(self):
        """Tasks should be marked with LLM source."""
        llm = MockLLMClient(
            response='{"tasks": [{"id": "t1", "skill": "Read", "params": {}}]}'
        )
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Read something")

        assert dag.nodes["t1"].source == TaskSource.LLM


# =============================================================================
# ToolPlannerStage Tests
# =============================================================================


class TestToolPlannerStage:
    """Tests for ToolPlannerStage."""

    @pytest.fixture
    def mock_llm(self):
        return MockLLMClient(
            response='{"tasks": [{"id": "t1", "skill": "Glob", "params": {"pattern": "*"}}]}'
        )

    @pytest.mark.asyncio
    async def test_processes_moderate_task(self, mock_llm):
        """Should process MODERATE tasks (routing_action=continue)."""
        stage = ToolPlannerStage(mock_llm)
        ctx = PlanningContext(
            goal="Find files",
            conversation_context="",
            available_skills={"Glob", "Read"},
        )
        ctx.metadata["routing_action"] = "continue"

        ctx = await stage.process(ctx)

        assert ctx.final_dag is not None
        assert ctx.metadata.get("tool_planner_used") is True

    @pytest.mark.asyncio
    async def test_skips_when_early_exit(self, mock_llm):
        """Should skip when early_exit is set."""
        stage = ToolPlannerStage(mock_llm)
        ctx = PlanningContext(
            goal="Find files",
            conversation_context="",
            available_skills={"Glob"},
        )
        ctx.early_exit = True

        ctx = await stage.process(ctx)

        assert ctx.metadata.get("tool_planner_used") is None
        assert len(mock_llm.calls) == 0

    @pytest.mark.asyncio
    async def test_skips_when_final_dag_set(self, mock_llm):
        """Should skip when final_dag is already set."""
        stage = ToolPlannerStage(mock_llm)
        existing_dag = TaskDAG.create_simple("synthesize", {"message": "Hi"})
        ctx = PlanningContext(
            goal="Find files",
            conversation_context="",
            available_skills={"Glob"},
        )
        ctx.final_dag = existing_dag

        ctx = await stage.process(ctx)

        assert ctx.final_dag is existing_dag
        assert len(mock_llm.calls) == 0

    @pytest.mark.asyncio
    async def test_skips_non_continue_routing(self, mock_llm):
        """Should skip when routing_action is not 'continue'."""
        stage = ToolPlannerStage(mock_llm)
        ctx = PlanningContext(
            goal="Hello",
            conversation_context="",
            available_skills={"synthesize"},
        )
        ctx.metadata["routing_action"] = "direct_reply"

        ctx = await stage.process(ctx)

        assert ctx.metadata.get("tool_planner_used") is None
        assert len(mock_llm.calls) == 0

    @pytest.mark.asyncio
    async def test_processes_without_routing_action(self, mock_llm):
        """Should process when no routing_action set (direct usage)."""
        stage = ToolPlannerStage(mock_llm)
        ctx = PlanningContext(
            goal="Find Python files",
            conversation_context="",
            available_skills={"Glob", "Read"},
        )
        # No routing_action set

        ctx = await stage.process(ctx)

        assert ctx.final_dag is not None
        assert ctx.metadata.get("tool_planner_used") is True


# =============================================================================
# Prompt Size Tests
# =============================================================================


class TestPromptSize:
    """Tests for prompt size constraints."""

    def test_prompt_under_500_chars(self):
        """TOOL_DAG_PROMPT should be under 500 characters."""
        assert validate_prompt_size(500) is True

    def test_get_prompt_size(self):
        """get_prompt_size should return reasonable value."""
        size = get_prompt_size()
        # Prompt template without {goal} should be around 300-400 chars
        assert 200 < size < 500

    def test_readonly_tools_are_safe(self):
        """READONLY_TOOLS should only contain safe read-only tools."""
        # These tools should not modify anything
        safe_tools = {"Read", "Glob", "Grep", "Synthesize"}
        assert READONLY_TOOLS == safe_tools

        # Dangerous tools should NOT be included
        dangerous_tools = {"Bash", "Write", "Edit", "Subagent"}
        for tool in dangerous_tools:
            assert tool not in READONLY_TOOLS


# =============================================================================
# Validator Integration Tests
# =============================================================================


class TestToolPlannerValidator:
    """Tests for validator integration in ToolDAGPlanner."""

    @pytest.mark.asyncio
    async def test_validates_only_readonly_tools(self):
        """Validator should only allow read-only tools."""
        # Response includes both valid and invalid tools
        llm = MockLLMClient(
            response='{"tasks": ['
            '{"id": "t1", "skill": "Read", "params": {}},'
            '{"id": "t2", "skill": "Bash", "params": {}}'
            ']}'
        )
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Do something")

        # Bash should be filtered out
        skills = {n.skill for n in dag.nodes.values()}
        assert "Bash" not in skills
        assert "Read" in skills or "Glob" in skills  # Either original or fallback

    @pytest.mark.asyncio
    async def test_repairs_invalid_dependencies(self):
        """Validator should repair invalid dependencies."""
        llm = MockLLMClient(
            response='{"tasks": ['
            '{"id": "t1", "skill": "Read", "params": {}, "depends_on": ["missing"]}'
            ']}'
        )
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Read file")

        # Missing dependency should be removed
        if "t1" in dag.nodes:
            assert "missing" not in dag.nodes["t1"].depends_on

    @pytest.mark.asyncio
    async def test_max_tasks_limit(self):
        """Validator should limit max tasks to 5."""
        # Generate 10 tasks
        tasks = [
            f'{{"id": "t{i}", "skill": "Glob", "params": {{}}}}'
            for i in range(10)
        ]
        llm = MockLLMClient(
            response='{"tasks": [' + ",".join(tasks) + ']}'
        )
        planner = ToolDAGPlanner(llm)

        dag = await planner.plan("Find many things")

        # Should be limited or fall back
        assert len(dag.nodes) <= 5


# =============================================================================
# Context Stack Integration Tests
# =============================================================================


class TestToolPlannerWithContextStack:
    """Tests for ToolDAGPlanner with ContextStack integration."""

    @pytest.mark.asyncio
    async def test_plan_with_context_stack(self):
        """Should work with ContextStack provided."""
        from src.nimbus.core.context import ContextStack

        llm = MockLLMClient(
            response='{"tasks": [{"id": "t1", "skill": "Read", "params": {"file_path": "test.py"}}]}'
        )
        context_stack = ContextStack()
        planner = ToolDAGPlanner(llm, context_stack=context_stack)

        dag = await planner.plan("Read test.py")

        assert dag is not None
        assert len(dag.nodes) >= 1

    @pytest.mark.asyncio
    async def test_plan_without_context_stack(self):
        """Should work without ContextStack (None)."""
        llm = MockLLMClient(
            response='{"tasks": [{"id": "t1", "skill": "Glob", "params": {"pattern": "*.py"}}]}'
        )
        planner = ToolDAGPlanner(llm, context_stack=None)

        dag = await planner.plan("Find Python files")

        assert dag is not None
        assert len(dag.nodes) >= 1
