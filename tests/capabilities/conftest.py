"""Pytest configuration for capability tests.

Provides:
- capability marker for filtering tests by dimension
- Common fixtures for capability testing
"""

import pytest
from typing import Set, Optional, Dict, Any, List
from dataclasses import dataclass, field

from src.nimbus.core.planner import (
    PlannerPipeline,
    PipelineConfig,
    PlanningMode,
    PlanningContext,
    RulePlanner,
)
from src.nimbus.core.types import TaskDAG, TaskNode


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "capability(name): mark test as testing a specific capability dimension"
    )
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow-running (may require external resources)"
    )


def pytest_collection_modifyitems(config, items):
    """Filter tests by capability if --capability option is specified."""
    capability_filter = config.getoption("--capability", default=None)
    if capability_filter is None:
        return

    selected = []
    deselected = []

    for item in items:
        markers = [m for m in item.iter_markers(name="capability")]
        if markers:
            capability_names = [m.args[0] for m in markers if m.args]
            if capability_filter in capability_names:
                selected.append(item)
            else:
                deselected.append(item)
        else:
            deselected.append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


def pytest_addoption(parser):
    """Add --capability option for filtering."""
    parser.addoption(
        "--capability",
        action="store",
        default=None,
        help="Filter tests by capability dimension (e.g., task_decomposition, code_search)"
    )


# =============================================================================
# Mock LLM Client
# =============================================================================


class MockLLMClient:
    """Mock LLM client for testing.

    Can be configured with different response strategies:
    - Fixed response
    - Response based on prompt patterns
    - Response sequence
    """

    def __init__(
        self,
        response: str = '{"mode": "direct", "response": "Hello!"}',
        responses: Optional[List[str]] = None,
        response_map: Optional[Dict[str, str]] = None,
    ):
        """Initialize mock LLM client.

        Args:
            response: Default response for all prompts.
            responses: List of responses to return in sequence.
            response_map: Dict mapping prompt substrings to responses.
        """
        self.default_response = response
        self.responses = responses or []
        self.response_map = response_map or {}
        self.calls: List[str] = []
        self._call_index = 0

    async def complete(self, prompt: str) -> str:
        """Complete a prompt and return response."""
        self.calls.append(prompt)

        # Try response map first
        for pattern, resp in self.response_map.items():
            if pattern.lower() in prompt.lower():
                return resp

        # Try response sequence
        if self.responses and self._call_index < len(self.responses):
            response = self.responses[self._call_index]
            self._call_index += 1
            return response

        return self.default_response

    def reset(self):
        """Reset call tracking."""
        self.calls = []
        self._call_index = 0


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_llm():
    """Provide a configurable mock LLM client."""
    return MockLLMClient()


@pytest.fixture
def rule_pipeline():
    """Provide a rule-only planner pipeline."""
    return PlannerPipeline.rule_only()


@pytest.fixture
def default_skills() -> Set[str]:
    """Provide default skill set for testing."""
    return {"synthesize", "search", "summarize", "Read", "Glob", "Grep"}


@pytest.fixture
def code_skills() -> Set[str]:
    """Provide code exploration skill set."""
    return {"synthesize", "Read", "Glob", "Grep"}


@pytest.fixture
def planning_context_factory(default_skills):
    """Factory for creating PlanningContext instances."""
    def _create(
        goal: str,
        context: str = "",
        skills: Optional[Set[str]] = None,
        mode: PlanningMode = PlanningMode.HYBRID,
    ) -> PlanningContext:
        return PlanningContext(
            goal=goal,
            conversation_context=context,
            available_skills=skills or default_skills,
            planning_mode=mode,
        )
    return _create


# =============================================================================
# Assertion Helpers
# =============================================================================


@dataclass
class DAGAssertions:
    """Helper class for asserting DAG properties."""
    dag: TaskDAG

    def has_task_count(self, expected: int) -> "DAGAssertions":
        """Assert DAG has expected number of tasks."""
        actual = len(self.dag.nodes)
        assert actual == expected, f"Expected {expected} tasks, got {actual}"
        return self

    def has_min_tasks(self, minimum: int) -> "DAGAssertions":
        """Assert DAG has at least minimum number of tasks."""
        actual = len(self.dag.nodes)
        assert actual >= minimum, f"Expected at least {minimum} tasks, got {actual}"
        return self

    def has_skill(self, skill: str) -> "DAGAssertions":
        """Assert DAG contains a task with given skill."""
        skills = {n.skill for n in self.dag.nodes.values()}
        assert skill in skills, f"Skill '{skill}' not found. Available: {skills}"
        return self

    def has_skills(self, *skills: str) -> "DAGAssertions":
        """Assert DAG contains all given skills."""
        dag_skills = {n.skill for n in self.dag.nodes.values()}
        for skill in skills:
            assert skill in dag_skills, f"Skill '{skill}' not found. Available: {dag_skills}"
        return self

    def has_dependency(self, from_id: str, to_id: str) -> "DAGAssertions":
        """Assert task from_id depends on to_id."""
        assert from_id in self.dag.nodes, f"Task '{from_id}' not found"
        deps = self.dag.nodes[from_id].depends_on
        assert to_id in deps, f"Task '{from_id}' does not depend on '{to_id}'. Deps: {deps}"
        return self

    def is_valid_dag(self) -> "DAGAssertions":
        """Assert DAG is valid (no cycles, all deps exist)."""
        # Check all dependencies exist
        for node in self.dag.nodes.values():
            for dep_id in node.depends_on:
                assert dep_id in self.dag.nodes, f"Missing dependency: {dep_id}"

        # Check for cycles using DFS
        visited = set()
        rec_stack = set()

        def has_cycle(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            node = self.dag.nodes[node_id]
            for dep_id in node.depends_on:
                if dep_id not in visited:
                    if has_cycle(dep_id):
                        return True
                elif dep_id in rec_stack:
                    return True

            rec_stack.remove(node_id)
            return False

        for node_id in self.dag.nodes:
            if node_id not in visited:
                assert not has_cycle(node_id), "DAG contains a cycle"

        return self

    def task_has_param(self, task_id: str, param: str, value: Any = None) -> "DAGAssertions":
        """Assert task has a parameter, optionally with specific value."""
        assert task_id in self.dag.nodes, f"Task '{task_id}' not found"
        params = self.dag.nodes[task_id].params
        assert param in params, f"Param '{param}' not in task '{task_id}'. Params: {params}"
        if value is not None:
            assert params[param] == value, f"Param '{param}' = {params[param]}, expected {value}"
        return self


@pytest.fixture
def assert_dag():
    """Factory for DAG assertions."""
    def _create(dag: TaskDAG) -> DAGAssertions:
        return DAGAssertions(dag)
    return _create


# =============================================================================
# Test Data Helpers
# =============================================================================


@dataclass
class CapabilityTestCase:
    """A single capability test case."""
    id: str
    input: str
    expected_skills: List[str] = field(default_factory=list)
    expected_task_count: Optional[int] = None
    min_task_count: int = 1
    expected_dependencies: List[tuple] = field(default_factory=list)
    context: str = ""
    description: str = ""


@pytest.fixture
def load_test_cases():
    """Factory for loading test cases from YAML."""
    import yaml
    from pathlib import Path

    def _load(filename: str) -> List[CapabilityTestCase]:
        data_dir = Path(__file__).parent.parent / "data" / "capabilities"
        filepath = data_dir / filename

        if not filepath.exists():
            return []

        with open(filepath) as f:
            data = yaml.safe_load(f)

        cases = []
        for item in data.get("test_cases", []):
            cases.append(CapabilityTestCase(
                id=item.get("id", "unknown"),
                input=item["input"],
                expected_skills=item.get("expected_skills", []),
                expected_task_count=item.get("expected_task_count"),
                min_task_count=item.get("min_task_count", 1),
                expected_dependencies=item.get("expected_dependencies", []),
                context=item.get("context", ""),
                description=item.get("description", ""),
            ))
        return cases

    return _load
