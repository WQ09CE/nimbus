"""Tests for tool integration with core components.

Tests the integration of ToolRegistry with:
- AsyncRuntime: Tool execution in DAG workflows
- NotebookAgent: Default tool registration and availability
- DAGPlanner: Tool availability in planning prompts
"""

import pytest
import asyncio
import tempfile
from pathlib import Path

from nimbus.core.runtime import AsyncRuntime
from nimbus.core.types import TaskDAG, TaskStatus, RuntimeConfig
from nimbus.tools import ToolRegistry, read_file, glob_files, grep_content


class TestRuntimeToolIntegration:
    """Tests for AsyncRuntime with ToolRegistry."""

    @pytest.fixture
    def tool_registry(self):
        """Create a tool registry with default tools."""
        registry = ToolRegistry()
        registry.register_decorated(read_file)
        registry.register_decorated(glob_files)
        registry.register_decorated(grep_content)
        return registry

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace with test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)

            # Create test files
            (workspace / "main.py").write_text("def main():\n    print('Hello')\n")
            (workspace / "utils.py").write_text("def helper():\n    return 42\n")
            (workspace / "src").mkdir()
            (workspace / "src" / "module.py").write_text("class Agent:\n    pass\n")

            yield workspace

    @pytest.mark.asyncio
    async def test_runtime_with_tools(self, tool_registry, temp_workspace):
        """Test AsyncRuntime can execute tools from registry."""
        runtime = AsyncRuntime(
            tool_registry=tool_registry,
            workspace=temp_workspace,
        )

        # Runtime should include tool names
        skill_names = runtime.get_skill_names()
        assert "Read" in skill_names
        assert "Glob" in skill_names
        assert "Grep" in skill_names

    @pytest.mark.asyncio
    async def test_execute_read_tool(self, tool_registry, temp_workspace):
        """Test executing Read tool through runtime."""
        runtime = AsyncRuntime(
            tool_registry=tool_registry,
            workspace=temp_workspace,
        )

        dag = TaskDAG.create("Read main.py", [
            {
                "id": "t1",
                "skill": "Read",
                "params": {"file_path": str(temp_workspace / "main.py")},
                "depends_on": [],
            },
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        assert result.stats.completed == 1
        assert "t1" in result.results
        assert "def main()" in result.results["t1"]

    @pytest.mark.asyncio
    async def test_execute_glob_tool(self, tool_registry, temp_workspace):
        """Test executing Glob tool through runtime."""
        runtime = AsyncRuntime(
            tool_registry=tool_registry,
            workspace=temp_workspace,
        )

        dag = TaskDAG.create("Find Python files", [
            {
                "id": "t1",
                "skill": "Glob",
                "params": {"pattern": "**/*.py"},
                "depends_on": [],
            },
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        assert result.stats.completed == 1
        assert "t1" in result.results
        output = result.results["t1"]
        assert "main.py" in output
        assert "utils.py" in output
        assert "module.py" in output

    @pytest.mark.asyncio
    async def test_execute_grep_tool(self, tool_registry, temp_workspace):
        """Test executing Grep tool through runtime."""
        runtime = AsyncRuntime(
            tool_registry=tool_registry,
            workspace=temp_workspace,
        )

        dag = TaskDAG.create("Search for class definitions", [
            {
                "id": "t1",
                "skill": "Grep",
                "params": {"pattern": "class Agent", "type": "py"},
                "depends_on": [],
            },
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        assert result.stats.completed == 1
        assert "t1" in result.results
        output = result.results["t1"]
        assert "class Agent" in output
        assert "module.py" in output

    @pytest.mark.asyncio
    async def test_mixed_tools_and_skills(self, tool_registry, temp_workspace):
        """Test runtime can execute both tools and skills."""
        async def summarize(text: str = "") -> str:
            return f"Summary: {len(text)} chars"

        runtime = AsyncRuntime(
            skills={"summarize": summarize},
            tool_registry=tool_registry,
            workspace=temp_workspace,
        )

        # Should have both tools and skills
        names = runtime.get_skill_names()
        assert "Read" in names
        assert "summarize" in names

        # Execute a DAG with both tool and skill
        dag = TaskDAG.create("Read and summarize", [
            {
                "id": "t1",
                "skill": "Read",
                "params": {"file_path": str(temp_workspace / "main.py")},
                "depends_on": [],
            },
            {
                "id": "t2",
                "skill": "summarize",
                "params": {"text": "some text"},
                "depends_on": ["t1"],
            },
        ])

        result = await runtime.execute_dag(dag)

        assert result.status == "success"
        assert result.stats.completed == 2

    @pytest.mark.asyncio
    async def test_tool_takes_priority_over_skill(self, tool_registry, temp_workspace):
        """Test that tools are checked before skills when names match."""
        # Create a skill with the same name as a tool
        async def fake_read(file_path: str) -> str:
            return "FAKE READ"

        runtime = AsyncRuntime(
            skills={"Read": fake_read},  # Same name as tool
            tool_registry=tool_registry,
            workspace=temp_workspace,
        )

        dag = TaskDAG.create("Read file", [
            {
                "id": "t1",
                "skill": "Read",
                "params": {"file_path": str(temp_workspace / "main.py")},
                "depends_on": [],
            },
        ])

        result = await runtime.execute_dag(dag)

        # Should use the tool, not the skill
        assert result.status == "success"
        assert "def main()" in result.results["t1"]
        assert "FAKE READ" not in result.results["t1"]

    @pytest.mark.asyncio
    async def test_parallel_tool_execution(self, tool_registry, temp_workspace):
        """Test parallel execution of multiple tools."""
        runtime = AsyncRuntime(
            tool_registry=tool_registry,
            workspace=temp_workspace,
            config=RuntimeConfig(max_concurrent=10),
        )

        # Two parallel glob operations
        dag = TaskDAG.create("Find files", [
            {
                "id": "t1",
                "skill": "Glob",
                "params": {"pattern": "*.py"},
                "depends_on": [],
            },
            {
                "id": "t2",
                "skill": "Glob",
                "params": {"pattern": "src/*.py"},
                "depends_on": [],
            },
        ])

        import time
        start = time.time()
        result = await runtime.execute_dag(dag)
        duration = time.time() - start

        assert result.status == "success"
        assert result.stats.completed == 2

        # Both should be in results
        assert "t1" in result.results
        assert "t2" in result.results


class TestAgentToolIntegration:
    """Tests for NotebookAgent with ToolRegistry."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM client."""
        class MockLLM:
            def __init__(self, response: str = '{"mode": "direct", "response": "OK"}'):
                self.response = response

            async def complete(self, prompt: str) -> str:
                return self.response

        return MockLLM

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "test.py").write_text("# test file\n")
            yield workspace

    def test_agent_has_default_tools(self, mock_llm, temp_workspace):
        """Test that NotebookAgent registers default tools."""
        from nimbus.core.agent import NotebookAgent

        agent = NotebookAgent(
            llm_client=mock_llm(),
            planner_type="dag",
            workspace=temp_workspace,
            enable_logging=False,
        )

        # Agent should have tools registered
        assert agent.tool_registry is not None
        assert "Read" in agent.tool_registry
        assert "Glob" in agent.tool_registry
        assert "Grep" in agent.tool_registry

        # get_skill_names should include tools
        names = agent.get_skill_names()
        assert "Read" in names
        assert "Glob" in names
        assert "Grep" in names

    def test_agent_custom_tool_registry(self, mock_llm, temp_workspace):
        """Test that agent can use custom tool registry."""
        from nimbus.core.agent import NotebookAgent

        # Create custom registry with only Read
        custom_registry = ToolRegistry()
        custom_registry.register_decorated(read_file)

        agent = NotebookAgent(
            llm_client=mock_llm(),
            planner_type="dag",
            workspace=temp_workspace,
            tool_registry=custom_registry,
            enable_logging=False,
        )

        # Should only have Read
        assert "Read" in agent.tool_registry
        assert "Glob" not in agent.tool_registry
        assert "Grep" not in agent.tool_registry

    def test_agent_simple_mode_has_tools(self, mock_llm, temp_workspace):
        """Test that simple planner mode also has tool access."""
        from nimbus.core.agent import NotebookAgent

        agent = NotebookAgent(
            llm_client=mock_llm(),
            planner_type="simple",  # Not DAG mode
            workspace=temp_workspace,
            enable_logging=False,
        )

        # Should still have tools registered
        assert agent.tool_registry is not None
        names = agent.get_skill_names()
        assert "Read" in names
        assert "Glob" in names
        assert "Grep" in names


class TestDAGPlannerToolAwareness:
    """Tests for DAGPlanner awareness of tools."""

    def test_planner_prompt_includes_tools(self):
        """Test that DAG planning prompt includes tool descriptions."""
        from nimbus.core.planner.legacy import DAG_PLANNING_PROMPT

        # Check that tool documentation is in the prompt
        assert "Read" in DAG_PLANNING_PROMPT
        assert "Glob" in DAG_PLANNING_PROMPT
        assert "Grep" in DAG_PLANNING_PROMPT

        # Check parameter descriptions
        assert "file_path" in DAG_PLANNING_PROMPT
        assert "pattern" in DAG_PLANNING_PROMPT
        assert "type" in DAG_PLANNING_PROMPT

    @pytest.mark.asyncio
    async def test_planner_validates_tool_names(self):
        """Test that planner accepts tool names as valid skills."""
        from nimbus.core.planner import DAGPlanner
        from nimbus.core.types import TaskDAG

        class MockLLM:
            async def complete(self, prompt: str) -> str:
                return '{"mode": "dag", "tasks": [{"id": "t1", "skill": "Read", "params": {"file_path": "test.py"}, "depends_on": []}]}'

        planner = DAGPlanner(MockLLM())

        # Available skills should include tools
        available = {"chat", "search", "Read", "Glob", "Grep"}
        dag = await planner.create_plan(
            goal="Read test.py",
            context="",
            available_skills=available,
        )

        # Should not report validation errors for tool names
        errors = planner.validate_dag(dag, available)
        assert errors == []
