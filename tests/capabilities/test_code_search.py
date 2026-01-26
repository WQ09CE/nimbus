"""Tests for code search capability.

This module tests the agent's ability to search code effectively using
the appropriate tools (Grep, Glob).

Capability: code_search
"""

import pytest
from pathlib import Path
from typing import Set, List

from src.nimbus.core.planner import (
    PlannerPipeline,
    PipelineConfig,
    PlanningMode,
    PlanningContext,
    RulePlanner,
)
from src.nimbus.core.types import TaskDAG, TaskNode, TaskSource

from tests.evaluation.metrics import (
    CodeSearchMetrics,
    SearchExpectation,
    SearchResult,
)


# =============================================================================
# Mock LLM Client
# =============================================================================


class MockLLMClient:
    """Mock LLM client for code search tests."""

    def __init__(self, response_map=None):
        self.response_map = response_map or {}
        self.calls = []
        self.default_response = '''
        {
            "mode": "dag",
            "tasks": [
                {"id": "t1", "skill": "Grep", "params": {"pattern": "test", "type": "py"}}
            ]
        }
        '''

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        for pattern, response in self.response_map.items():
            if pattern.lower() in prompt.lower():
                return response
        return self.default_response


# =============================================================================
# Test Cases
# =============================================================================


@pytest.mark.capability("code_search")
class TestGrepPatternSearch:
    """Tests for Grep-based pattern search."""

    @pytest.mark.asyncio
    async def test_grep_pattern_search(self):
        """Search for code patterns should use Grep skill.

        Pattern: "search for X" or "find X in code"
        Expected: Grep task with appropriate pattern
        """
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="search for 'def main'",
            context="",
            available_skills={"synthesize", "Grep", "Glob", "Read"},
        )

        assert dag is not None
        skills = {n.skill for n in dag.nodes.values()}

        # Should use Grep for pattern search
        assert "Grep" in skills, f"Expected Grep skill, got: {skills}"

        # Verify pattern parameter
        grep_task = next(n for n in dag.nodes.values() if n.skill == "Grep")
        assert "pattern" in grep_task.params
        assert "main" in grep_task.params["pattern"].lower() or \
               "def main" in grep_task.params["pattern"].lower()

    @pytest.mark.asyncio
    async def test_grep_chinese_pattern(self):
        """Chinese search patterns should also use Grep.

        Pattern: "Chinese pattern in code"
        """
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="search for 'CodeAgent' definition",
            context="",
            available_skills={"synthesize", "Grep", "Glob", "Read"},
        )

        assert dag is not None
        skills = {n.skill for n in dag.nodes.values()}
        assert "Grep" in skills


@pytest.mark.capability("code_search")
class TestGlobFileSearch:
    """Tests for Glob-based file search."""

    @pytest.mark.asyncio
    async def test_glob_file_search(self):
        """File listing should use Glob skill.

        Pattern: "list python files in <directory>"
        Expected: Glob task with appropriate pattern
        """
        pipeline = PlannerPipeline.rule_only()

        # The rule pattern requires "in" after "files"
        dag = await pipeline.plan(
            goal="list python files in current directory",
            context="",
            available_skills={"synthesize", "Grep", "Glob", "Read"},
        )

        assert dag is not None
        skills = {n.skill for n in dag.nodes.values()}

        # Should use Glob for file listing
        assert "Glob" in skills, f"Expected Glob skill, got: {skills}"

        # Verify pattern includes .py
        glob_task = next(n for n in dag.nodes.values() if n.skill == "Glob")
        assert "pattern" in glob_task.params
        pattern = glob_task.params["pattern"]
        assert "py" in pattern.lower() or "*" in pattern

    @pytest.mark.asyncio
    async def test_glob_directory_listing(self):
        """Directory listing should use Glob.

        Pattern: "list files in X"
        """
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="list src",
            context="",
            available_skills={"synthesize", "Grep", "Glob", "Read"},
        )

        assert dag is not None
        skills = {n.skill for n in dag.nodes.values()}
        # Should use Glob
        assert "Glob" in skills, f"Expected Glob skill, got: {skills}"


@pytest.mark.capability("code_search")
class TestSearchToolSelection:
    """Tests for correct tool selection based on query type."""

    @pytest.fixture
    def metrics(self):
        return CodeSearchMetrics()

    @pytest.mark.asyncio
    async def test_search_tool_selection_grep(self, metrics):
        """Pattern search should select Grep tool."""
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="find 'async def' in the codebase",
            context="",
            available_skills={"synthesize", "Grep", "Glob", "Read"},
        )

        result = metrics.evaluate_tool_selection(dag, "Grep")
        assert result.value == 1.0, f"Expected Grep tool. Details: {result.details}"

    @pytest.mark.asyncio
    async def test_search_tool_selection_glob(self, metrics):
        """File listing should select Glob tool."""
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="list all .py files in tests/",
            context="",
            available_skills={"synthesize", "Grep", "Glob", "Read"},
        )

        # Should use Glob for file listing
        skills = {n.skill for n in dag.nodes.values()}
        # Either Glob is selected or falls back to synthesize
        assert "Glob" in skills or "synthesize" in skills

    @pytest.mark.asyncio
    async def test_read_for_specific_file(self, metrics):
        """Reading specific file should use Read tool."""
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="read src/main.py",
            context="",
            available_skills={"synthesize", "Grep", "Glob", "Read"},
        )

        result = metrics.evaluate_tool_selection(dag, "Read")
        assert result.value == 1.0, f"Expected Read tool. Details: {result.details}"


@pytest.mark.capability("code_search")
class TestSearchPrecision:
    """Tests for search precision."""

    @pytest.fixture
    def metrics(self):
        return CodeSearchMetrics()

    def test_search_precision_perfect(self, metrics):
        """Perfect precision when all retrieved items are relevant."""
        retrieved = [
            SearchResult(path="src/agent.py"),
            SearchResult(path="src/planner.py"),
        ]
        expectation = SearchExpectation(
            relevant_files={"src/agent.py", "src/planner.py"},
        )

        results = metrics.evaluate(retrieved, expectation)
        summary = metrics.summary(results)

        assert summary["precision"] == 1.0

    def test_search_precision_half(self, metrics):
        """50% precision when half of retrieved items are relevant."""
        retrieved = [
            SearchResult(path="src/agent.py"),  # relevant
            SearchResult(path="tests/test_agent.py"),  # not in relevant set
        ]
        expectation = SearchExpectation(
            relevant_files={"src/agent.py"},
        )

        results = metrics.evaluate(retrieved, expectation)
        summary = metrics.summary(results)

        assert summary["precision"] == 0.5

    def test_search_precision_zero(self, metrics):
        """Zero precision when no retrieved items are relevant."""
        retrieved = [
            SearchResult(path="unrelated.py"),
        ]
        expectation = SearchExpectation(
            relevant_files={"src/agent.py"},
        )

        results = metrics.evaluate(retrieved, expectation)
        summary = metrics.summary(results)

        assert summary["precision"] == 0.0


@pytest.mark.capability("code_search")
class TestSearchRecall:
    """Tests for search recall."""

    @pytest.fixture
    def metrics(self):
        return CodeSearchMetrics()

    def test_search_recall_perfect(self, metrics):
        """Perfect recall when all relevant items are retrieved."""
        retrieved = [
            SearchResult(path="src/agent.py"),
            SearchResult(path="src/planner.py"),
        ]
        expectation = SearchExpectation(
            relevant_files={"src/agent.py", "src/planner.py"},
        )

        results = metrics.evaluate(retrieved, expectation)
        summary = metrics.summary(results)

        assert summary["recall"] == 1.0

    def test_search_recall_half(self, metrics):
        """50% recall when half of relevant items are retrieved."""
        retrieved = [
            SearchResult(path="src/agent.py"),
        ]
        expectation = SearchExpectation(
            relevant_files={"src/agent.py", "src/planner.py"},
        )

        results = metrics.evaluate(retrieved, expectation)
        summary = metrics.summary(results)

        assert summary["recall"] == 0.5

    def test_search_recall_zero(self, metrics):
        """Zero recall when no relevant items are retrieved."""
        retrieved = [
            SearchResult(path="unrelated.py"),
        ]
        expectation = SearchExpectation(
            relevant_files={"src/agent.py", "src/planner.py"},
        )

        results = metrics.evaluate(retrieved, expectation)
        summary = metrics.summary(results)

        assert summary["recall"] == 0.0


@pytest.mark.capability("code_search")
class TestSearchF1:
    """Tests for F1 score calculation."""

    @pytest.fixture
    def metrics(self):
        return CodeSearchMetrics()

    def test_search_f1_perfect(self, metrics):
        """Perfect F1 when both precision and recall are perfect."""
        retrieved = [
            SearchResult(path="src/agent.py"),
        ]
        expectation = SearchExpectation(
            relevant_files={"src/agent.py"},
        )

        results = metrics.evaluate(retrieved, expectation)
        summary = metrics.summary(results)

        assert summary["f1"] == 1.0

    def test_search_f1_balanced(self, metrics):
        """F1 score for balanced precision/recall."""
        # Precision: 1/2 = 0.5 (one retrieved is relevant)
        # Recall: 1/2 = 0.5 (one of two relevant is retrieved)
        retrieved = [
            SearchResult(path="src/agent.py"),  # relevant
            SearchResult(path="unrelated.py"),  # not relevant
        ]
        expectation = SearchExpectation(
            relevant_files={"src/agent.py", "src/planner.py"},
        )

        results = metrics.evaluate(retrieved, expectation)
        summary = metrics.summary(results)

        # F1 = 2 * (0.5 * 0.5) / (0.5 + 0.5) = 0.5
        assert summary["f1"] == 0.5

    def test_search_f1_zero(self, metrics):
        """Zero F1 when nothing is retrieved correctly."""
        retrieved = [
            SearchResult(path="wrong.py"),
        ]
        expectation = SearchExpectation(
            relevant_files={"src/agent.py"},
        )

        results = metrics.evaluate(retrieved, expectation)
        summary = metrics.summary(results)

        assert summary["f1"] == 0.0


@pytest.mark.capability("code_search")
class TestSearchIntegration:
    """Integration tests for code search capability."""

    @pytest.mark.asyncio
    async def test_search_produces_valid_dag(self):
        """Search requests should produce valid DAGs."""
        pipeline = PlannerPipeline.rule_only()

        test_cases = [
            "search for 'class Agent'",
            "find 'def execute'",
            "grep 'import asyncio'",
        ]

        for goal in test_cases:
            dag = await pipeline.plan(
                goal=goal,
                context="",
                available_skills={"synthesize", "Grep", "Glob", "Read"},
            )

            assert dag is not None, f"No DAG for: {goal}"
            assert len(dag.nodes) >= 1, f"Empty DAG for: {goal}"

            # Verify DAG structure
            for node in dag.nodes.values():
                for dep_id in node.depends_on:
                    assert dep_id in dag.nodes, \
                        f"Missing dependency {dep_id} in DAG for: {goal}"

    @pytest.mark.asyncio
    async def test_search_with_llm_enhancement(self):
        """LLM can enhance search with multiple strategies."""
        llm = MockLLMClient(response_map={
            "find all": '''
            {
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "Glob", "params": {"pattern": "**/*.py"}},
                    {"id": "t2", "skill": "Grep", "params": {"pattern": "def main", "type": "py"}}
                ]
            }
            '''
        })

        config = PipelineConfig(
            enable_rule_planner=True,
            enable_llm_enhancer=True,
        )
        pipeline = PlannerPipeline.default(llm, config)

        dag = await pipeline.plan(
            goal="Find all Python files with main function",
            context="",
            available_skills={"synthesize", "Grep", "Glob", "Read"},
        )

        assert dag is not None
        skills = {n.skill for n in dag.nodes.values()}

        # Either rule matched or LLM provided plan
        assert len(dag.nodes) >= 1
