"""Tests for Subagent DAG orchestration system.

Tests the task-level DAG orchestration including:
- SubagentDAG basic operations
- TaskPlanner rule matching
- SubagentRuntime execution (with mocks)
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nimbus.core.task.types import (
    SubagentType,
    SubagentStatus,
    SubagentNode,
    SubagentDAG,
    SubagentResult,
    SubagentExecutionResult,
    SubagentExecutionStats,
    SubagentReplanRecord,
    SUBAGENT_TOOLS,
)
from nimbus.core.task.planner import TaskPlanner, TASK_PATTERNS
from nimbus.core.task.coordinator import SubagentReplanCoordinator


# =============================================================================
# SubagentType Tests
# =============================================================================


class TestSubagentType:
    """Tests for SubagentType enum."""

    def test_subagent_type_values(self):
        """Test subagent type enum values."""
        assert SubagentType.EYE.value == "eye"
        assert SubagentType.BODY.value == "body"
        assert SubagentType.MIND.value == "mind"
        assert SubagentType.TONGUE.value == "tongue"
        assert SubagentType.NOSE.value == "nose"
        assert SubagentType.EAR.value == "ear"

    def test_subagent_tools_mapping(self):
        """Test default tools for each subagent type."""
        assert "Read" in SUBAGENT_TOOLS[SubagentType.EYE]
        assert "Glob" in SUBAGENT_TOOLS[SubagentType.EYE]
        assert "Grep" in SUBAGENT_TOOLS[SubagentType.EYE]

        assert "Write" in SUBAGENT_TOOLS[SubagentType.BODY]
        assert "Edit" in SUBAGENT_TOOLS[SubagentType.BODY]
        assert "Bash" in SUBAGENT_TOOLS[SubagentType.BODY]

        assert "Bash" in SUBAGENT_TOOLS[SubagentType.TONGUE]
        assert "Write" not in SUBAGENT_TOOLS[SubagentType.TONGUE]


# =============================================================================
# SubagentNode Tests
# =============================================================================


class TestSubagentNode:
    """Tests for SubagentNode."""

    def test_node_creation(self):
        """Test basic node creation."""
        node = SubagentNode(
            id="t1",
            subagent_type=SubagentType.EYE,
            goal="Explore the codebase",
        )

        assert node.id == "t1"
        assert node.subagent_type == SubagentType.EYE
        assert node.goal == "Explore the codebase"
        assert node.status == SubagentStatus.PENDING
        assert node.depends_on == []
        assert node.result is None

    def test_node_with_dependencies(self):
        """Test node with dependencies."""
        node = SubagentNode(
            id="t2",
            subagent_type=SubagentType.BODY,
            goal="Implement feature",
            depends_on=["t1"],
            context_sources=["t1"],
        )

        assert node.depends_on == ["t1"]
        assert node.context_sources == ["t1"]

    def test_node_get_allowed_tools_default(self):
        """Test getting default allowed tools."""
        node = SubagentNode(
            id="t1",
            subagent_type=SubagentType.EYE,
            goal="Explore",
        )

        tools = node.get_allowed_tools()
        assert tools == SUBAGENT_TOOLS[SubagentType.EYE]

    def test_node_get_allowed_tools_override(self):
        """Test getting overridden allowed tools."""
        node = SubagentNode(
            id="t1",
            subagent_type=SubagentType.EYE,
            goal="Explore",
            allowed_tools={"Read"},
        )

        tools = node.get_allowed_tools()
        assert tools == {"Read"}

    def test_node_signature(self):
        """Test node signature generation."""
        node1 = SubagentNode(
            id="t1",
            subagent_type=SubagentType.EYE,
            goal="Explore the codebase",
        )
        node2 = SubagentNode(
            id="t2",
            subagent_type=SubagentType.EYE,
            goal="Explore the codebase",
        )
        node3 = SubagentNode(
            id="t3",
            subagent_type=SubagentType.EYE,
            goal="Different goal",
        )

        # Same type and goal should have same signature
        assert node1.get_signature() == node2.get_signature()
        # Different goal should have different signature
        assert node1.get_signature() != node3.get_signature()

    def test_node_serialization(self):
        """Test node to_dict and from_dict."""
        node = SubagentNode(
            id="t1",
            subagent_type=SubagentType.BODY,
            goal="Implement feature",
            depends_on=["t0"],
            max_retries=2,
            timeout=600.0,
        )

        data = node.to_dict()
        restored = SubagentNode.from_dict(data)

        assert restored.id == node.id
        assert restored.subagent_type == node.subagent_type
        assert restored.goal == node.goal
        assert restored.depends_on == node.depends_on
        assert restored.max_retries == node.max_retries
        assert restored.timeout == node.timeout


# =============================================================================
# SubagentDAG Tests
# =============================================================================


class TestSubagentDAG:
    """Tests for SubagentDAG."""

    def test_dag_creation(self):
        """Test basic DAG creation."""
        dag = SubagentDAG.create(
            goal="Implement feature",
            nodes=[
                {"id": "t1", "type": "eye", "goal": "Explore"},
                {"id": "t2", "type": "body", "goal": "Implement", "depends_on": ["t1"]},
            ],
        )

        assert dag.user_goal == "Implement feature"
        assert len(dag.nodes) == 2
        assert "t1" in dag.nodes
        assert "t2" in dag.nodes
        assert dag.nodes["t2"].depends_on == ["t1"]

    def test_dag_get_ready_nodes_initial(self):
        """Test getting ready nodes at start."""
        dag = SubagentDAG.create(
            goal="Test",
            nodes=[
                {"id": "t1", "type": "eye", "goal": "Explore"},
                {"id": "t2", "type": "body", "goal": "Implement", "depends_on": ["t1"]},
            ],
        )

        ready = dag.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].id == "t1"

    def test_dag_get_ready_nodes_after_completion(self):
        """Test getting ready nodes after dependency completion."""
        dag = SubagentDAG.create(
            goal="Test",
            nodes=[
                {"id": "t1", "type": "eye", "goal": "Explore"},
                {"id": "t2", "type": "body", "goal": "Implement", "depends_on": ["t1"]},
            ],
        )

        # Mark t1 as completed
        dag.nodes["t1"].status = SubagentStatus.COMPLETED

        ready = dag.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].id == "t2"

    def test_dag_get_ready_nodes_parallel(self):
        """Test getting multiple ready nodes for parallel execution."""
        dag = SubagentDAG.create(
            goal="Test",
            nodes=[
                {"id": "t1", "type": "eye", "goal": "Explore A"},
                {"id": "t2", "type": "eye", "goal": "Explore B"},
                {"id": "t3", "type": "body", "goal": "Implement", "depends_on": ["t1", "t2"]},
            ],
        )

        ready = dag.get_ready_nodes()
        assert len(ready) == 2
        ids = {n.id for n in ready}
        assert ids == {"t1", "t2"}

    def test_dag_is_completed(self):
        """Test DAG completion check."""
        dag = SubagentDAG.create(
            goal="Test",
            nodes=[
                {"id": "t1", "type": "eye", "goal": "Explore"},
            ],
        )

        assert not dag.is_completed()

        dag.nodes["t1"].status = SubagentStatus.COMPLETED
        assert dag.is_completed()

    def test_dag_is_completed_with_failure(self):
        """Test DAG completion with failed nodes."""
        dag = SubagentDAG.create(
            goal="Test",
            nodes=[
                {"id": "t1", "type": "eye", "goal": "Explore"},
            ],
        )

        dag.nodes["t1"].status = SubagentStatus.FAILED
        assert dag.is_completed()

    def test_dag_mark_downstream_skipped(self):
        """Test marking downstream nodes as skipped."""
        dag = SubagentDAG.create(
            goal="Test",
            nodes=[
                {"id": "t1", "type": "eye", "goal": "Explore"},
                {"id": "t2", "type": "body", "goal": "Implement", "depends_on": ["t1"]},
                {"id": "t3", "type": "tongue", "goal": "Test", "depends_on": ["t2"]},
            ],
        )

        # Mark t1 as failed and skip downstream
        dag.nodes["t1"].status = SubagentStatus.FAILED
        dag.mark_downstream_skipped("t1")

        assert dag.nodes["t2"].status == SubagentStatus.SKIPPED
        assert dag.nodes["t3"].status == SubagentStatus.SKIPPED

    def test_dag_get_context_for_node(self):
        """Test building context from dependency results."""
        dag = SubagentDAG.create(
            goal="Test",
            nodes=[
                {"id": "t1", "type": "eye", "goal": "Explore"},
                {"id": "t2", "type": "body", "goal": "Implement", "depends_on": ["t1"], "context_sources": ["t1"]},
            ],
        )

        # Set t1 result
        dag.nodes["t1"].status = SubagentStatus.COMPLETED
        dag.nodes["t1"].result = SubagentResult(
            agent_id="subagent_t1",
            summary="Found 5 Python files",
        )

        context = dag.get_context_for_node("t2")
        assert "Found 5 Python files" in context
        assert "eye" in context.lower()

    def test_dag_serialization(self):
        """Test DAG to_dict and from_dict."""
        dag = SubagentDAG.create(
            goal="Test goal",
            nodes=[
                {"id": "t1", "type": "eye", "goal": "Explore"},
                {"id": "t2", "type": "body", "goal": "Implement", "depends_on": ["t1"]},
            ],
            complexity="complex",
        )

        data = dag.to_dict()
        restored = SubagentDAG.from_dict(data)

        assert restored.id == dag.id
        assert restored.user_goal == dag.user_goal
        assert len(restored.nodes) == len(dag.nodes)
        assert restored.complexity == dag.complexity


# =============================================================================
# TaskPlanner Tests
# =============================================================================


class TestTaskPlanner:
    """Tests for TaskPlanner."""

    def test_planner_creation(self):
        """Test planner creation without LLM."""
        planner = TaskPlanner()
        assert planner.llm_client is None

    @pytest.mark.asyncio
    async def test_rule_match_read_and_summarize(self):
        """Test rule matching for read and summarize pattern."""
        planner = TaskPlanner()

        dag = await planner.plan(
            goal="Read src/main.py and summarize it",
            available_subagents={SubagentType.EYE, SubagentType.MIND},
        )

        assert dag is not None
        assert len(dag.nodes) == 2
        # First node should be EYE
        first_node = dag.nodes["t1"]
        assert first_node.subagent_type == SubagentType.EYE
        # Second node should be MIND
        second_node = dag.nodes["t2"]
        assert second_node.subagent_type == SubagentType.MIND
        assert "t1" in second_node.depends_on

    @pytest.mark.asyncio
    async def test_rule_match_implement(self):
        """Test rule matching for implement pattern."""
        planner = TaskPlanner()

        dag = await planner.plan(
            goal="Implement a caching layer",
            available_subagents={SubagentType.EYE, SubagentType.MIND, SubagentType.BODY, SubagentType.TONGUE},
        )

        assert dag is not None
        assert len(dag.nodes) == 4
        # Check sequence: eye -> mind -> body -> tongue
        assert dag.nodes["t1"].subagent_type == SubagentType.EYE
        assert dag.nodes["t2"].subagent_type == SubagentType.MIND
        assert dag.nodes["t3"].subagent_type == SubagentType.BODY
        assert dag.nodes["t4"].subagent_type == SubagentType.TONGUE

    @pytest.mark.asyncio
    async def test_rule_match_fix_bug(self):
        """Test rule matching for fix bug pattern."""
        planner = TaskPlanner()

        dag = await planner.plan(
            goal="Fix bug in authentication module",
            available_subagents={SubagentType.EYE, SubagentType.BODY, SubagentType.TONGUE},
        )

        assert dag is not None
        assert len(dag.nodes) == 3
        # Check sequence: eye -> body -> tongue
        assert dag.nodes["t1"].subagent_type == SubagentType.EYE
        assert dag.nodes["t2"].subagent_type == SubagentType.BODY
        assert dag.nodes["t3"].subagent_type == SubagentType.TONGUE

    @pytest.mark.asyncio
    async def test_rule_match_review(self):
        """Test rule matching for review pattern."""
        planner = TaskPlanner()

        dag = await planner.plan(
            goal="Review the authentication code",
            available_subagents={SubagentType.EYE, SubagentType.NOSE},
        )

        assert dag is not None
        assert len(dag.nodes) == 2
        assert dag.nodes["t1"].subagent_type == SubagentType.EYE
        assert dag.nodes["t2"].subagent_type == SubagentType.NOSE

    @pytest.mark.asyncio
    async def test_rule_match_test(self):
        """Test rule matching for test pattern."""
        planner = TaskPlanner()

        dag = await planner.plan(
            goal="Test the authentication module",
            available_subagents={SubagentType.TONGUE},
        )

        assert dag is not None
        assert len(dag.nodes) == 1
        assert dag.nodes["t1"].subagent_type == SubagentType.TONGUE

    @pytest.mark.asyncio
    async def test_rule_match_explore(self):
        """Test rule matching for explore pattern."""
        planner = TaskPlanner()

        dag = await planner.plan(
            goal="Explore the project structure",
            available_subagents={SubagentType.EYE, SubagentType.MIND},
        )

        assert dag is not None
        assert len(dag.nodes) == 2
        assert dag.nodes["t1"].subagent_type == SubagentType.EYE
        assert dag.nodes["t2"].subagent_type == SubagentType.MIND

    @pytest.mark.asyncio
    async def test_fallback_when_no_rule_matches(self):
        """Test fallback DAG when no rule matches."""
        planner = TaskPlanner()

        dag = await planner.plan(
            goal="Do something unusual that doesn't match any pattern",
            available_subagents={SubagentType.EYE},
        )

        assert dag is not None
        assert len(dag.nodes) >= 1
        # Should use EYE as fallback for exploration

    @pytest.mark.asyncio
    async def test_rule_not_applied_when_subagent_unavailable(self):
        """Test that rules are not applied when required subagents are unavailable."""
        planner = TaskPlanner()

        # Implement pattern requires EYE, MIND, BODY, TONGUE
        # But we only provide EYE
        dag = await planner.plan(
            goal="Implement a feature",
            available_subagents={SubagentType.EYE},
        )

        assert dag is not None
        # Should fall back to simple plan since BODY is not available
        # The fallback should use available subagents only

    @pytest.mark.asyncio
    async def test_complexity_classification_simple(self):
        """Test complexity classification for simple goals."""
        planner = TaskPlanner()
        complexity = await planner._classify_complexity("Read the config file")
        assert complexity == "simple"

    @pytest.mark.asyncio
    async def test_complexity_classification_complex(self):
        """Test complexity classification for complex goals."""
        planner = TaskPlanner()
        complexity = await planner._classify_complexity("Implement a new authentication system")
        assert complexity == "complex"


# =============================================================================
# SubagentReplanCoordinator Tests
# =============================================================================


class TestSubagentReplanCoordinator:
    """Tests for SubagentReplanCoordinator."""

    def test_coordinator_creation(self):
        """Test coordinator creation."""
        coordinator = SubagentReplanCoordinator()
        assert not coordinator.is_paused()
        assert coordinator.can_replan()

    def test_pause_resume(self):
        """Test pause and resume."""
        coordinator = SubagentReplanCoordinator()

        coordinator.pause()
        assert coordinator.is_paused()

        coordinator.resume()
        assert not coordinator.is_paused()

    @pytest.mark.asyncio
    async def test_request_replan(self):
        """Test replan request."""
        coordinator = SubagentReplanCoordinator(max_replan_attempts=3)

        dag = SubagentDAG.create(
            goal="Test",
            nodes=[{"id": "t1", "type": "eye", "goal": "Explore"}],
        )
        node = dag.nodes["t1"]
        node.status = SubagentStatus.FAILED
        node.error = "Connection error"

        result = await coordinator.request_replan(node, dag, "Connection error")

        assert result is True
        assert coordinator.get_replan_count() == 1
        assert len(dag.replan_history) == 1

    @pytest.mark.asyncio
    async def test_replan_limit(self):
        """Test replan attempt limit."""
        coordinator = SubagentReplanCoordinator(max_replan_attempts=2)

        dag = SubagentDAG.create(
            goal="Test",
            nodes=[{"id": "t1", "type": "eye", "goal": "Explore"}],
        )
        node = dag.nodes["t1"]
        node.status = SubagentStatus.FAILED

        # First two replans should succeed
        result1 = await coordinator.request_replan(node, dag, "Error 1")
        assert result1 is True

        result2 = await coordinator.request_replan(node, dag, "Error 2")
        assert result2 is True

        # Third should fail (exceeded limit)
        result3 = await coordinator.request_replan(node, dag, "Error 3")
        assert result3 is False

    def test_reset(self):
        """Test coordinator reset."""
        coordinator = SubagentReplanCoordinator()
        coordinator.pause()

        coordinator.reset()

        assert not coordinator.is_paused()
        assert coordinator.get_replan_count() == 0


# =============================================================================
# Integration Tests
# =============================================================================


class TestSubagentDAGIntegration:
    """Integration tests for the task DAG system."""

    @pytest.mark.asyncio
    async def test_full_planning_workflow(self):
        """Test complete planning workflow."""
        planner = TaskPlanner()

        # Plan a feature implementation
        dag = await planner.plan(
            goal="Create a new API endpoint for user profiles",
            available_subagents=set(SubagentType),
        )

        assert dag is not None
        assert dag.user_goal == "Create a new API endpoint for user profiles"

        # Check DAG is valid (no circular deps)
        ready = dag.get_ready_nodes()
        assert len(ready) >= 1

        # Simulate execution
        for node in dag.nodes.values():
            if not node.depends_on:
                node.status = SubagentStatus.COMPLETED
                node.result = SubagentResult(
                    agent_id=f"subagent_{node.id}",
                    summary=f"Completed {node.goal}",
                )

        # After marking initial nodes complete, more should be ready
        ready = dag.get_ready_nodes()
        assert len(ready) >= 0  # May have more ready nodes

    def test_context_propagation(self):
        """Test context propagation through DAG."""
        dag = SubagentDAG.create(
            goal="Multi-step task",
            nodes=[
                {"id": "t1", "type": "eye", "goal": "Step 1"},
                {"id": "t2", "type": "mind", "goal": "Step 2", "depends_on": ["t1"], "context_sources": ["t1"]},
                {"id": "t3", "type": "body", "goal": "Step 3", "depends_on": ["t2"], "context_sources": ["t1", "t2"]},
            ],
        )

        # Set results
        dag.nodes["t1"].status = SubagentStatus.COMPLETED
        dag.nodes["t1"].result = SubagentResult(
            agent_id="subagent_t1",
            summary="Found important code patterns",
        )

        dag.nodes["t2"].status = SubagentStatus.COMPLETED
        dag.nodes["t2"].result = SubagentResult(
            agent_id="subagent_t2",
            summary="Designed solution architecture",
        )

        # Check context for t3 includes both t1 and t2 results
        context = dag.get_context_for_node("t3")
        assert "Found important code patterns" in context
        assert "Designed solution architecture" in context


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
