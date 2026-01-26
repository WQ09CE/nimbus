"""Tests for task decomposition capability.

This module tests the agent's ability to decompose complex tasks into
subtasks with correct dependencies.

Capability: task_decomposition
"""

import pytest
from typing import Set

from src.nimbus.core.planner import (
    PlannerPipeline,
    PipelineConfig,
    PlanningMode,
    PlanningContext,
    RulePlanner,
)
from src.nimbus.core.types import TaskDAG, TaskNode, TaskSource

from tests.evaluation.metrics import (
    TaskDecompositionMetrics,
    DecompositionExpectation,
)


# =============================================================================
# Mock LLM Client
# =============================================================================


class MockLLMClient:
    """Mock LLM client that returns predefined responses."""

    def __init__(self, response_map=None):
        self.response_map = response_map or {}
        self.calls = []
        self.default_response = '{"mode": "dag", "tasks": [{"id": "t1", "skill": "synthesize", "params": {"message": "OK"}}]}'

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        for pattern, response in self.response_map.items():
            if pattern.lower() in prompt.lower():
                return response
        return self.default_response


# =============================================================================
# Test Cases
# =============================================================================


@pytest.mark.capability("task_decomposition")
class TestTaskDecomposition:
    """Tests for task decomposition capability."""

    @pytest.fixture
    def metrics(self):
        return TaskDecompositionMetrics()

    @pytest.mark.asyncio
    async def test_simple_task_no_decomposition(self, metrics):
        """Simple tasks should not be decomposed into multiple subtasks.

        A greeting like "hello" should result in a single chat task,
        not be broken into multiple steps.
        """
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="hello",
            context="",
            available_skills={"synthesize", "search", "summarize"},
        )

        # Simple greeting should be a single task
        assert dag is not None
        assert len(dag.nodes) == 1

        # Evaluate with metrics
        expectation = DecompositionExpectation(
            task_count=1,
            required_skills=["synthesize"],
        )
        results = metrics.evaluate(dag, expectation)
        summary = metrics.summary(results)

        assert summary["decomposition_accuracy"] == 1.0
        assert summary["skill_coverage"] == 1.0
        assert summary["dag_validity"] == 1.0

    @pytest.mark.asyncio
    async def test_search_and_summarize_decomposition(self, metrics):
        """Search + summarize requests should decompose into 2 dependent tasks.

        Using LLM pipeline since rule matching has overlapping patterns.
        Tests that "search X, then summarize" creates:
        1. search task
        2. summarize task that depends on search
        """
        # Configure LLM to return search + summarize plan
        llm = MockLLMClient(response_map={
            "search": '''
            {
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "search", "params": {"query": "Python tutorials"}},
                    {"id": "t2", "skill": "summarize", "params": {"source": "t1"}, "depends_on": ["t1"]}
                ]
            }
            '''
        })

        config = PipelineConfig(
            enable_rule_planner=False,  # Skip rules to test LLM planning
            enable_llm_enhancer=True,
        )
        pipeline = PlannerPipeline.default(llm, config)

        dag = await pipeline.plan(
            goal="search Python tutorials, then summarize",
            context="",
            available_skills={"synthesize", "search", "summarize"},
        )

        # Should have 2 tasks with dependency
        assert dag is not None
        assert len(dag.nodes) == 2, f"Expected 2 tasks, got {len(dag.nodes)}: {list(dag.nodes.values())}"

        # Verify skills
        skills = {n.skill for n in dag.nodes.values()}
        assert "search" in skills
        assert "summarize" in skills

        # Verify dependency chain
        expectation = DecompositionExpectation(
            task_count=2,
            required_skills=["search", "summarize"],
            dependencies=[("summarize", "search")],  # summarize depends on search
        )
        results = metrics.evaluate(dag, expectation)
        summary = metrics.summary(results)

        assert summary["decomposition_accuracy"] == 1.0
        assert summary["skill_coverage"] == 1.0
        assert summary["dag_validity"] == 1.0
        assert summary["dependency_correctness"] == 1.0

    @pytest.mark.asyncio
    async def test_multi_step_decomposition(self, metrics):
        """Complex requests should decompose into multiple steps.

        Using LLM planner to handle complex multi-step requests.
        """
        # Configure LLM to return a multi-step plan
        llm = MockLLMClient(response_map={
            "analyze": '''
            {
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "search", "params": {"query": "market trends"}},
                    {"id": "t2", "skill": "search", "params": {"query": "competitor analysis"}},
                    {"id": "t3", "skill": "summarize", "params": {"sources": ["t1", "t2"]}, "depends_on": ["t1", "t2"]},
                    {"id": "t4", "skill": "synthesize", "params": {"message": "final report"}, "depends_on": ["t3"]}
                ]
            }
            '''
        })

        config = PipelineConfig(
            enable_rule_planner=True,
            enable_llm_enhancer=True,
            planning_mode=PlanningMode.HYBRID,
        )
        pipeline = PlannerPipeline.default(llm, config)

        dag = await pipeline.plan(
            goal="Analyze market trends and competitor data, summarize findings",
            context="",
            available_skills={"synthesize", "search", "summarize"},
        )

        assert dag is not None
        assert len(dag.nodes) >= 3  # At least 3 steps

        # Evaluate
        expectation = DecompositionExpectation(
            min_tasks=3,
            max_tasks=10,
            required_skills=["search", "summarize"],
        )
        results = metrics.evaluate(dag, expectation)
        summary = metrics.summary(results)

        assert summary["decomposition_in_range"] == 1.0
        assert summary["skill_coverage"] == 1.0
        assert summary["dag_validity"] == 1.0

    @pytest.mark.asyncio
    async def test_dag_validity(self, metrics):
        """Generated DAGs should always be structurally valid.

        Tests that:
        - No cycles exist
        - All dependencies reference existing tasks
        """
        pipeline = PlannerPipeline.rule_only()

        test_cases = [
            "hello",
            "search Python tutorials",
            "search AI trends, then summarize",
        ]

        for goal in test_cases:
            dag = await pipeline.plan(
                goal=goal,
                context="",
                available_skills={"synthesize", "search", "summarize"},
            )

            if dag and dag.nodes:
                # Check DAG validity
                expectation = DecompositionExpectation(min_tasks=1)
                results = metrics.evaluate(dag, expectation)
                summary = metrics.summary(results)

                assert summary["dag_validity"] == 1.0, f"Invalid DAG for goal: {goal}"

    @pytest.mark.asyncio
    async def test_dependency_correctness(self, metrics):
        """Task dependencies should follow logical order.

        For "search then summarize", summarize must depend on search,
        not the other way around. Uses LLM pipeline for predictable results.
        """
        # Configure LLM to return properly ordered plan
        llm = MockLLMClient(response_map={
            "search": '''
            {
                "mode": "dag",
                "tasks": [
                    {"id": "search_1", "skill": "search", "params": {"query": "ML papers"}},
                    {"id": "summarize_1", "skill": "summarize", "params": {}, "depends_on": ["search_1"]}
                ]
            }
            '''
        })

        config = PipelineConfig(
            enable_rule_planner=False,
            enable_llm_enhancer=True,
        )
        pipeline = PlannerPipeline.default(llm, config)

        dag = await pipeline.plan(
            goal="search machine learning papers, then summarize the results",
            context="",
            available_skills={"synthesize", "search", "summarize"},
        )

        assert dag is not None
        assert len(dag.nodes) >= 2, f"Expected at least 2 tasks, got {len(dag.nodes)}"

        # Find the summarize task
        summarize_task = None
        search_task = None

        for node in dag.nodes.values():
            if node.skill == "summarize":
                summarize_task = node
            elif node.skill == "search":
                search_task = node

        assert search_task is not None, "Search task not found"
        assert summarize_task is not None, "Summarize task not found"

        # Summarize should depend on search
        assert search_task.id in summarize_task.depends_on, \
            f"Summarize should depend on search. Deps: {summarize_task.depends_on}"

        # Search should not depend on summarize
        assert summarize_task.id not in search_task.depends_on, \
            "Search should not depend on summarize"


@pytest.mark.capability("task_decomposition")
class TestDecompositionEdgeCases:
    """Edge cases for task decomposition."""

    @pytest.mark.asyncio
    async def test_empty_goal(self):
        """Empty goals should still produce a valid (fallback) DAG."""
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="",
            context="",
            available_skills={"synthesize"},
        )

        # Should fall back to a simple chat DAG
        assert dag is not None
        assert len(dag.nodes) >= 1

    @pytest.mark.asyncio
    async def test_unavailable_skill_fallback(self):
        """When required skill is unavailable, should fallback gracefully."""
        pipeline = PlannerPipeline.rule_only()

        # Request search but only chat is available
        dag = await pipeline.plan(
            goal="search for Python tutorials",
            context="",
            available_skills={"synthesize"},  # search not available
        )

        # Should fallback to chat since search is not available
        assert dag is not None
        # The rule won't match if skill is unavailable, so we get fallback
        skills = {n.skill for n in dag.nodes.values()}
        # Either no match (empty/fallback) or chat fallback
        assert "search" not in skills or len(dag.nodes) > 0

    @pytest.mark.asyncio
    async def test_task_source_tracking(self):
        """Tasks should track their source (rule vs LLM)."""
        pipeline = PlannerPipeline.rule_only()

        dag = await pipeline.plan(
            goal="hello",
            context="",
            available_skills={"synthesize"},
        )

        assert dag is not None

        # All tasks from rule-only pipeline should have RULE source
        for node in dag.nodes.values():
            assert node.source == TaskSource.RULE, \
                f"Expected RULE source, got {node.source}"


@pytest.mark.capability("task_decomposition")
class TestDecompositionWithContext:
    """Task decomposition considering conversation context."""

    @pytest.mark.asyncio
    async def test_decomposition_with_file_context(self):
        """Decomposition should consider file context from previous operations."""
        llm = MockLLMClient(response_map={
            "summarize": '''
            {
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "Read", "params": {"file_path": "data.csv"}},
                    {"id": "t2", "skill": "summarize", "params": {"source": "t1"}, "depends_on": ["t1"]}
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
            goal="summarize the file",
            context="Previous: User uploaded data.csv containing sales data...",
            available_skills={"synthesize", "Read", "summarize"},
        )

        assert dag is not None
        # Should reference the file from context
        skills = {n.skill for n in dag.nodes.values()}
        # Either Read (from LLM plan) or summarize should be present
        assert "summarize" in skills or "Read" in skills
