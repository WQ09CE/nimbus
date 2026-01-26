"""Evaluation metrics for capability testing.

This module provides metrics classes for measuring agent capabilities:
- CapabilityMetrics: Base class for all metrics
- TaskDecompositionMetrics: Measure task decomposition accuracy
- CodeSearchMetrics: Measure code search precision/recall/F1
- ContextCompressionMetrics: Measure memory compression quality
- ContextUnderstandingMetrics: Measure context understanding accuracy
- CodeModificationMetrics: Measure code modification correctness
- BashExecutionMetrics: Measure bash command execution accuracy
- CodeSummarizationMetrics: Measure code summarization quality
- RepoUnderstandingMetrics: Measure repository understanding accuracy
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Set, Optional, Tuple
from collections import Counter

from nimbus.core.types import TaskDAG, TaskNode


# =============================================================================
# Base Metrics Class
# =============================================================================


@dataclass
class MetricResult:
    """Result of a metric evaluation."""
    name: str
    value: float
    details: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.name}: {self.value:.4f}"


class CapabilityMetrics(ABC):
    """Base class for capability metrics.

    Subclasses should implement the evaluate() method to compute
    specific metrics for their capability dimension.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the capability being measured."""
        pass

    @abstractmethod
    def evaluate(self, *args, **kwargs) -> List[MetricResult]:
        """Evaluate the capability and return metrics.

        Returns:
            List of MetricResult objects.
        """
        pass

    def summary(self, results: List[MetricResult]) -> Dict[str, float]:
        """Convert results to a summary dictionary.

        Args:
            results: List of MetricResult objects.

        Returns:
            Dictionary mapping metric names to values.
        """
        return {r.name: r.value for r in results}


# =============================================================================
# Task Decomposition Metrics
# =============================================================================


@dataclass
class DecompositionExpectation:
    """Expected decomposition for a task."""
    task_count: Optional[int] = None
    min_tasks: int = 1
    max_tasks: int = 20
    required_skills: List[str] = field(default_factory=list)
    dependencies: List[Tuple[str, str]] = field(default_factory=list)


class TaskDecompositionMetrics(CapabilityMetrics):
    """Metrics for task decomposition capability.

    Measures:
    - decomposition_accuracy: Whether task count matches expected
    - skill_coverage: Whether required skills are present
    - dag_validity: Whether DAG is structurally valid
    - dependency_correctness: Whether dependencies are correct
    """

    @property
    def name(self) -> str:
        return "task_decomposition"

    def evaluate(
        self,
        dag: TaskDAG,
        expectation: DecompositionExpectation,
    ) -> List[MetricResult]:
        """Evaluate task decomposition against expectations.

        Args:
            dag: The produced TaskDAG.
            expectation: Expected decomposition properties.

        Returns:
            List of MetricResult objects.
        """
        results = []

        # 1. Decomposition accuracy (task count)
        task_count = len(dag.nodes)
        if expectation.task_count is not None:
            accuracy = 1.0 if task_count == expectation.task_count else 0.0
            results.append(MetricResult(
                name="decomposition_accuracy",
                value=accuracy,
                details={
                    "expected": expectation.task_count,
                    "actual": task_count,
                },
            ))
        else:
            # Check within range
            in_range = expectation.min_tasks <= task_count <= expectation.max_tasks
            results.append(MetricResult(
                name="decomposition_in_range",
                value=1.0 if in_range else 0.0,
                details={
                    "min": expectation.min_tasks,
                    "max": expectation.max_tasks,
                    "actual": task_count,
                },
            ))

        # 2. Skill coverage
        results.append(self._evaluate_skill_coverage(dag, expectation.required_skills))

        # 3. DAG validity
        results.append(self._evaluate_dag_validity(dag))

        # 4. Dependency correctness
        if expectation.dependencies:
            results.append(self._evaluate_dependencies(dag, expectation.dependencies))

        return results

    def _evaluate_skill_coverage(
        self,
        dag: TaskDAG,
        required_skills: List[str],
    ) -> MetricResult:
        """Evaluate whether required skills are present."""
        if not required_skills:
            return MetricResult(
                name="skill_coverage",
                value=1.0,
                details={"required": [], "found": list(set(n.skill for n in dag.nodes.values()))},
            )

        dag_skills = {n.skill for n in dag.nodes.values()}
        found = [s for s in required_skills if s in dag_skills]
        coverage = len(found) / len(required_skills)

        return MetricResult(
            name="skill_coverage",
            value=coverage,
            details={
                "required": required_skills,
                "found": found,
                "missing": [s for s in required_skills if s not in dag_skills],
            },
        )

    def _evaluate_dag_validity(self, dag: TaskDAG) -> MetricResult:
        """Evaluate structural validity of DAG."""
        errors = []

        # Check for empty DAG
        if not dag.nodes:
            errors.append("DAG has no nodes")
            return MetricResult(name="dag_validity", value=0.0, details={"errors": errors})

        # Check all dependencies exist
        for node_id, node in dag.nodes.items():
            for dep_id in node.depends_on:
                if dep_id not in dag.nodes:
                    errors.append(f"Missing dependency: {node_id} -> {dep_id}")

        # Check for cycles
        if self._has_cycle(dag):
            errors.append("DAG contains a cycle")

        validity = 1.0 if not errors else 0.0
        return MetricResult(
            name="dag_validity",
            value=validity,
            details={"errors": errors, "node_count": len(dag.nodes)},
        )

    def _has_cycle(self, dag: TaskDAG) -> bool:
        """Check if DAG contains a cycle using DFS."""
        visited = set()
        rec_stack = set()

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            node = dag.nodes.get(node_id)
            if node:
                for dep_id in node.depends_on:
                    if dep_id not in visited:
                        if dfs(dep_id):
                            return True
                    elif dep_id in rec_stack:
                        return True

            rec_stack.remove(node_id)
            return False

        for node_id in dag.nodes:
            if node_id not in visited:
                if dfs(node_id):
                    return True
        return False

    def _evaluate_dependencies(
        self,
        dag: TaskDAG,
        expected_deps: List[Tuple[str, str]],
    ) -> MetricResult:
        """Evaluate dependency correctness.

        Expected deps are (from_skill, to_skill) pairs where from_skill depends on to_skill.
        """
        if not expected_deps:
            return MetricResult(name="dependency_correctness", value=1.0, details={})

        # Build skill -> node_ids mapping
        skill_nodes: Dict[str, List[str]] = {}
        for node_id, node in dag.nodes.items():
            if node.skill not in skill_nodes:
                skill_nodes[node.skill] = []
            skill_nodes[node.skill].append(node_id)

        # Check each expected dependency
        found = 0
        missing = []

        for from_skill, to_skill in expected_deps:
            from_nodes = skill_nodes.get(from_skill, [])
            to_nodes = skill_nodes.get(to_skill, [])

            # Check if any from_node depends on any to_node
            dep_found = False
            for from_id in from_nodes:
                from_node = dag.nodes[from_id]
                for to_id in to_nodes:
                    if to_id in from_node.depends_on:
                        dep_found = True
                        break
                if dep_found:
                    break

            if dep_found:
                found += 1
            else:
                missing.append((from_skill, to_skill))

        correctness = found / len(expected_deps)
        return MetricResult(
            name="dependency_correctness",
            value=correctness,
            details={"expected": expected_deps, "missing": missing},
        )


# =============================================================================
# Code Search Metrics
# =============================================================================


@dataclass
class SearchResult:
    """A single search result."""
    path: str
    content: str = ""
    line_number: int = 0
    relevance: float = 1.0


@dataclass
class SearchExpectation:
    """Expected search results."""
    relevant_files: Set[str]
    irrelevant_files: Set[str] = field(default_factory=set)


class CodeSearchMetrics(CapabilityMetrics):
    """Metrics for code search capability.

    Measures:
    - precision: Fraction of retrieved results that are relevant
    - recall: Fraction of relevant results that are retrieved
    - f1: Harmonic mean of precision and recall
    - tool_selection: Whether correct search tool was used
    """

    @property
    def name(self) -> str:
        return "code_search"

    def evaluate(
        self,
        retrieved: List[SearchResult],
        expectation: SearchExpectation,
    ) -> List[MetricResult]:
        """Evaluate search results against expectations.

        Args:
            retrieved: List of retrieved search results.
            expectation: Expected search properties.

        Returns:
            List of MetricResult objects.
        """
        results = []

        # Extract retrieved file paths
        retrieved_files = {r.path for r in retrieved}

        # Calculate precision
        precision = self._calculate_precision(
            retrieved_files,
            expectation.relevant_files,
            expectation.irrelevant_files,
        )
        results.append(precision)

        # Calculate recall
        recall = self._calculate_recall(
            retrieved_files,
            expectation.relevant_files,
        )
        results.append(recall)

        # Calculate F1
        f1 = self._calculate_f1(precision.value, recall.value)
        results.append(f1)

        return results

    def _calculate_precision(
        self,
        retrieved: Set[str],
        relevant: Set[str],
        irrelevant: Set[str],
    ) -> MetricResult:
        """Calculate precision: relevant_retrieved / total_retrieved."""
        if not retrieved:
            return MetricResult(
                name="precision",
                value=0.0,
                details={"retrieved": 0, "relevant_retrieved": 0},
            )

        # Count how many retrieved items are relevant
        relevant_retrieved = len(retrieved & relevant)
        precision = relevant_retrieved / len(retrieved)

        return MetricResult(
            name="precision",
            value=precision,
            details={
                "retrieved": len(retrieved),
                "relevant_retrieved": relevant_retrieved,
                "retrieved_files": list(retrieved),
            },
        )

    def _calculate_recall(
        self,
        retrieved: Set[str],
        relevant: Set[str],
    ) -> MetricResult:
        """Calculate recall: relevant_retrieved / total_relevant."""
        if not relevant:
            return MetricResult(
                name="recall",
                value=1.0,  # Vacuously true
                details={"relevant": 0, "relevant_retrieved": 0},
            )

        relevant_retrieved = len(retrieved & relevant)
        recall = relevant_retrieved / len(relevant)

        return MetricResult(
            name="recall",
            value=recall,
            details={
                "relevant": len(relevant),
                "relevant_retrieved": relevant_retrieved,
                "missing": list(relevant - retrieved),
            },
        )

    def _calculate_f1(self, precision: float, recall: float) -> MetricResult:
        """Calculate F1 score: harmonic mean of precision and recall."""
        if precision + recall == 0:
            f1 = 0.0
        else:
            f1 = 2 * (precision * recall) / (precision + recall)

        return MetricResult(
            name="f1",
            value=f1,
            details={"precision": precision, "recall": recall},
        )

    def evaluate_tool_selection(
        self,
        dag: TaskDAG,
        expected_tool: str,
    ) -> MetricResult:
        """Evaluate whether the correct search tool was selected.

        Args:
            dag: The produced TaskDAG.
            expected_tool: Expected tool/skill name (e.g., "Grep", "Glob").

        Returns:
            MetricResult for tool selection accuracy.
        """
        dag_skills = [n.skill for n in dag.nodes.values()]
        tool_used = expected_tool in dag_skills

        return MetricResult(
            name="tool_selection",
            value=1.0 if tool_used else 0.0,
            details={
                "expected": expected_tool,
                "dag_skills": dag_skills,
                "correct": tool_used,
            },
        )


# =============================================================================
# Aggregate Metrics
# =============================================================================


class AggregateMetrics:
    """Aggregate metrics across multiple test cases."""

    def __init__(self):
        self._results: Dict[str, List[float]] = {}

    def add(self, results: List[MetricResult]) -> None:
        """Add results from a single test case."""
        for result in results:
            if result.name not in self._results:
                self._results[result.name] = []
            self._results[result.name].append(result.value)

    def mean(self, metric_name: str) -> float:
        """Get mean value for a metric."""
        values = self._results.get(metric_name, [])
        if not values:
            return 0.0
        return sum(values) / len(values)

    def summary(self) -> Dict[str, float]:
        """Get summary of all metrics."""
        return {name: self.mean(name) for name in self._results}

    def __str__(self) -> str:
        lines = ["Aggregate Metrics:"]
        for name, values in sorted(self._results.items()):
            mean = sum(values) / len(values) if values else 0.0
            lines.append(f"  {name}: {mean:.4f} (n={len(values)})")
        return "\n".join(lines)


# =============================================================================
# Context Compression Metrics
# =============================================================================


@dataclass
class CompressionExpectation:
    """Expected properties for context compression."""
    original_tokens: int
    max_compressed_tokens: int
    key_info: List[str] = field(default_factory=list)  # Key info that must be preserved
    min_compression_ratio: float = 0.3  # At least 30% reduction


class ContextCompressionMetrics(CapabilityMetrics):
    """Metrics for evaluating context compression quality.

    Measures:
    - compression_ratio: How much the content was compressed
    - meets_target: Whether compression meets token budget
    - key_info_preservation: Whether key information is preserved
    - meets_min_ratio: Whether minimum compression ratio is achieved
    """

    @property
    def name(self) -> str:
        return "context_compression"

    def evaluate(
        self,
        original_content: str,
        compressed_content: str,
        original_tokens: int,
        compressed_tokens: int,
        expectation: CompressionExpectation,
    ) -> List[MetricResult]:
        """Evaluate compression quality.

        Args:
            original_content: Original content before compression.
            compressed_content: Content after compression.
            original_tokens: Token count before compression.
            compressed_tokens: Token count after compression.
            expectation: Expected properties.

        Returns:
            List of metric results.
        """
        results = []

        # 1. Compression ratio (higher is better, 1.0 = no compression, 0.0 = full compression)
        if original_tokens > 0:
            ratio = 1 - (compressed_tokens / original_tokens)
        else:
            ratio = 0.0

        results.append(MetricResult(
            name="compression_ratio",
            value=ratio,
            details={
                "original_tokens": original_tokens,
                "compressed_tokens": compressed_tokens,
                "ratio": ratio,
            },
        ))

        # 2. Meets compression target
        meets_target = compressed_tokens <= expectation.max_compressed_tokens
        results.append(MetricResult(
            name="meets_target",
            value=1.0 if meets_target else 0.0,
            details={
                "compressed_tokens": compressed_tokens,
                "max_allowed": expectation.max_compressed_tokens,
            },
        ))

        # 3. Key info preservation
        if expectation.key_info:
            preserved = sum(
                1 for info in expectation.key_info
                if info.lower() in compressed_content.lower()
            )
            preservation = preserved / len(expectation.key_info)
            results.append(MetricResult(
                name="key_info_preservation",
                value=preservation,
                details={
                    "key_info": expectation.key_info,
                    "preserved": preserved,
                    "total": len(expectation.key_info),
                },
            ))
        else:
            results.append(MetricResult(name="key_info_preservation", value=1.0))

        # 4. Meets minimum compression ratio
        meets_ratio = ratio >= expectation.min_compression_ratio
        results.append(MetricResult(
            name="meets_min_ratio",
            value=1.0 if meets_ratio else 0.0,
            details={
                "actual_ratio": ratio,
                "min_required": expectation.min_compression_ratio,
            },
        ))

        return results


# =============================================================================
# Context Understanding Metrics
# =============================================================================


@dataclass
class ContextTestCase:
    """A single context understanding test case."""
    context: str
    query: str
    expected_reference: str  # What the reference should resolve to
    test_type: str = "pronoun"  # "pronoun", "cross_turn", "recall"


class ContextUnderstandingMetrics(CapabilityMetrics):
    """Metrics for evaluating context understanding quality.

    Measures:
    - pronoun_resolution: Whether pronouns are resolved correctly
    - cross_turn_reference: Whether cross-turn references are understood
    - information_recall: Whether important information is recalled
    - context_window_utilization: Efficiency of context window usage
    """

    @property
    def name(self) -> str:
        return "context_understanding"

    def evaluate(
        self,
        test_case: ContextTestCase,
        agent_response: str,
    ) -> List[MetricResult]:
        """Evaluate context understanding for a single test case.

        Args:
            test_case: The test case with context and query.
            agent_response: Agent's response.

        Returns:
            List of metric results.
        """
        results = []

        # Check if expected reference is in response
        resolved = test_case.expected_reference.lower() in agent_response.lower()

        if test_case.test_type == "pronoun":
            results.append(MetricResult(
                name="pronoun_resolution",
                value=1.0 if resolved else 0.0,
                details={
                    "expected_reference": test_case.expected_reference,
                    "found_in_response": resolved,
                },
            ))
        elif test_case.test_type == "cross_turn":
            results.append(MetricResult(
                name="cross_turn_reference",
                value=1.0 if resolved else 0.0,
                details={
                    "expected_reference": test_case.expected_reference,
                    "found_in_response": resolved,
                },
            ))
        elif test_case.test_type == "recall":
            results.append(MetricResult(
                name="information_recall",
                value=1.0 if resolved else 0.0,
                details={
                    "expected_reference": test_case.expected_reference,
                    "found_in_response": resolved,
                },
            ))

        return results

    def evaluate_pronoun_resolution(
        self,
        context: str,
        query: str,
        agent_response: str,
        expected_reference: str,
    ) -> MetricResult:
        """Evaluate if pronoun/reference was resolved correctly.

        Args:
            context: Conversation context.
            query: User query with pronoun/reference.
            agent_response: Agent's response.
            expected_reference: Expected resolution.

        Returns:
            MetricResult for resolution accuracy.
        """
        resolved = expected_reference.lower() in agent_response.lower()

        return MetricResult(
            name="pronoun_resolution",
            value=1.0 if resolved else 0.0,
            details={
                "expected_reference": expected_reference,
                "found_in_response": resolved,
            },
        )

    def evaluate_cross_turn_reference(
        self,
        turns: List[Dict[str, str]],
        query: str,
        agent_response: str,
        expected_info: str,
    ) -> MetricResult:
        """Evaluate if cross-turn reference was understood.

        Args:
            turns: Previous conversation turns.
            query: Current query referencing previous turns.
            agent_response: Agent's response.
            expected_info: Info that should be referenced from previous turns.

        Returns:
            MetricResult for cross-turn understanding.
        """
        understands = expected_info.lower() in agent_response.lower()

        return MetricResult(
            name="cross_turn_reference",
            value=1.0 if understands else 0.0,
            details={
                "expected_info": expected_info,
                "found_in_response": understands,
            },
        )

    def evaluate_information_recall(
        self,
        context: str,
        query: str,
        agent_response: str,
        required_facts: List[str],
    ) -> MetricResult:
        """Evaluate if important information was recalled correctly.

        Args:
            context: Conversation context with facts.
            query: Query asking about previously mentioned facts.
            agent_response: Agent's response.
            required_facts: Facts that must be recalled.

        Returns:
            MetricResult for information recall.
        """
        if not required_facts:
            return MetricResult(name="information_recall", value=1.0)

        recalled = sum(
            1 for fact in required_facts
            if fact.lower() in agent_response.lower()
        )
        recall_rate = recalled / len(required_facts)

        return MetricResult(
            name="information_recall",
            value=recall_rate,
            details={
                "required_facts": required_facts,
                "recalled": recalled,
                "total": len(required_facts),
            },
        )

    def evaluate_context_window_utilization(
        self,
        context_tokens: int,
        window_size: int,
        relevance_score: float,
    ) -> MetricResult:
        """Evaluate context window utilization efficiency.

        Args:
            context_tokens: Tokens used in context.
            window_size: Total available window size.
            relevance_score: Score for how relevant the included context is (0-1).

        Returns:
            MetricResult for utilization efficiency.
        """
        utilization = context_tokens / window_size if window_size > 0 else 0

        # Efficiency combines utilization with relevance
        efficiency = utilization * relevance_score

        return MetricResult(
            name="context_window_utilization",
            value=efficiency,
            details={
                "context_tokens": context_tokens,
                "window_size": window_size,
                "utilization": utilization,
                "relevance": relevance_score,
            },
        )


# =============================================================================
# Code Modification Metrics
# =============================================================================


@dataclass
class ModificationExpectation:
    """Expected properties for code modification."""
    expected_changes: List[str] = field(default_factory=list)  # Strings that should appear
    forbidden_changes: List[str] = field(default_factory=list)  # Strings that should NOT appear
    preserve_patterns: List[str] = field(default_factory=list)  # Patterns that must be preserved


class CodeModificationMetrics(CapabilityMetrics):
    """Metrics for evaluating code modification quality.

    Measures:
    - expected_changes: Whether expected modifications are present
    - forbidden_changes_absent: Whether forbidden modifications are absent
    - syntax_validity: Whether code is syntactically valid
    - minimal_change: Whether changes are minimal (avoid unnecessary changes)
    - pattern_preservation: Whether important patterns are preserved
    """

    @property
    def name(self) -> str:
        return "code_modification"

    def evaluate(
        self,
        original: str,
        modified: str,
        expectation: ModificationExpectation,
    ) -> List[MetricResult]:
        """Evaluate code modification quality.

        Args:
            original: Original code.
            modified: Modified code.
            expectation: Expected properties.

        Returns:
            List of metric results.
        """
        results = []

        # 1. Expected changes present
        if expectation.expected_changes:
            present = sum(1 for c in expectation.expected_changes if c in modified)
            change_accuracy = present / len(expectation.expected_changes)
            results.append(MetricResult(
                name="expected_changes",
                value=change_accuracy,
                details={
                    "expected": expectation.expected_changes,
                    "found": present,
                    "total": len(expectation.expected_changes),
                },
            ))
        else:
            results.append(MetricResult(name="expected_changes", value=1.0))

        # 2. Forbidden changes absent
        if expectation.forbidden_changes:
            absent = sum(1 for c in expectation.forbidden_changes if c not in modified)
            safety = absent / len(expectation.forbidden_changes)
            results.append(MetricResult(
                name="forbidden_changes_absent",
                value=safety,
                details={
                    "forbidden": expectation.forbidden_changes,
                    "absent": absent,
                    "total": len(expectation.forbidden_changes),
                },
            ))
        else:
            results.append(MetricResult(name="forbidden_changes_absent", value=1.0))

        # 3. Preserved patterns
        if expectation.preserve_patterns:
            preserved = sum(1 for p in expectation.preserve_patterns if p in modified)
            preservation = preserved / len(expectation.preserve_patterns)
            results.append(MetricResult(
                name="pattern_preservation",
                value=preservation,
                details={
                    "patterns": expectation.preserve_patterns,
                    "preserved": preserved,
                },
            ))
        else:
            results.append(MetricResult(name="pattern_preservation", value=1.0))

        # 4. Minimal change principle
        original_lines = set(original.splitlines())
        modified_lines = set(modified.splitlines())

        unchanged_lines = len(original_lines & modified_lines)
        total_original_lines = len(original_lines)

        if total_original_lines > 0:
            minimal_change = unchanged_lines / total_original_lines
        else:
            minimal_change = 1.0

        results.append(MetricResult(
            name="minimal_change",
            value=minimal_change,
            details={
                "unchanged_lines": unchanged_lines,
                "total_original_lines": total_original_lines,
            },
        ))

        return results

    def evaluate_syntax_validity(self, code: str, language: str = "python") -> MetricResult:
        """Evaluate if modified code has valid syntax.

        Args:
            code: Code to validate.
            language: Programming language.

        Returns:
            MetricResult for syntax validity.
        """
        if language == "python":
            try:
                compile(code, "<string>", "exec")
                valid = True
                error = None
            except SyntaxError as e:
                valid = False
                error = str(e)
        else:
            # For other languages, assume valid (would need external tools)
            valid = True
            error = None

        return MetricResult(
            name="syntax_validity",
            value=1.0 if valid else 0.0,
            details={"valid": valid, "error": error},
        )


# =============================================================================
# Bash Execution Metrics
# =============================================================================


@dataclass
class BashExpectation:
    """Expected properties for bash execution."""
    expected_exit_code: int = 0
    expected_output_contains: List[str] = field(default_factory=list)
    expected_output_not_contains: List[str] = field(default_factory=list)
    max_execution_time: Optional[float] = None  # in seconds


class BashExecutionMetrics(CapabilityMetrics):
    """Metrics for evaluating bash command execution.

    Measures:
    - exit_code_correct: Whether exit code matches expected
    - output_contains: Whether output contains expected strings
    - output_not_contains: Whether output avoids forbidden strings
    - within_time_limit: Whether execution completed in time
    - error_handling: Whether errors were handled properly
    """

    @property
    def name(self) -> str:
        return "bash_execution"

    def evaluate(
        self,
        exit_code: int,
        stdout: str,
        stderr: str,
        execution_time: float,
        expectation: BashExpectation,
    ) -> List[MetricResult]:
        """Evaluate bash execution results.

        Args:
            exit_code: Command exit code.
            stdout: Standard output.
            stderr: Standard error.
            execution_time: Time taken to execute in seconds.
            expectation: Expected properties.

        Returns:
            List of metric results.
        """
        results = []

        # 1. Exit code correctness
        exit_correct = exit_code == expectation.expected_exit_code
        results.append(MetricResult(
            name="exit_code_correct",
            value=1.0 if exit_correct else 0.0,
            details={
                "expected": expectation.expected_exit_code,
                "actual": exit_code,
            },
        ))

        # 2. Output contains expected strings
        output = stdout + "\n" + stderr
        if expectation.expected_output_contains:
            found = sum(1 for s in expectation.expected_output_contains if s in output)
            contains_accuracy = found / len(expectation.expected_output_contains)
            results.append(MetricResult(
                name="output_contains",
                value=contains_accuracy,
                details={
                    "expected": expectation.expected_output_contains,
                    "found": found,
                },
            ))
        else:
            results.append(MetricResult(name="output_contains", value=1.0))

        # 3. Output does not contain forbidden strings
        if expectation.expected_output_not_contains:
            not_found = sum(
                1 for s in expectation.expected_output_not_contains
                if s not in output
            )
            safety = not_found / len(expectation.expected_output_not_contains)
            results.append(MetricResult(
                name="output_not_contains",
                value=safety,
                details={
                    "forbidden": expectation.expected_output_not_contains,
                    "not_found": not_found,
                },
            ))
        else:
            results.append(MetricResult(name="output_not_contains", value=1.0))

        # 4. Execution time within limit
        if expectation.max_execution_time is not None:
            within_limit = execution_time <= expectation.max_execution_time
            results.append(MetricResult(
                name="within_time_limit",
                value=1.0 if within_limit else 0.0,
                details={
                    "execution_time": execution_time,
                    "max_allowed": expectation.max_execution_time,
                },
            ))

        return results

    def evaluate_error_handling(
        self,
        exit_code: int,
        stderr: str,
        expected_error_handled: bool,
    ) -> MetricResult:
        """Evaluate if errors were handled properly.

        Args:
            exit_code: Command exit code.
            stderr: Standard error output.
            expected_error_handled: Whether error was expected to be handled.

        Returns:
            MetricResult for error handling.
        """
        has_error = exit_code != 0 or bool(stderr.strip())
        handled = has_error == expected_error_handled

        return MetricResult(
            name="error_handling",
            value=1.0 if handled else 0.0,
            details={
                "has_error": has_error,
                "expected_error": expected_error_handled,
            },
        )


# =============================================================================
# Code Summarization Metrics
# =============================================================================


@dataclass
class SummarizationExpectation:
    """Expected properties for code summarization."""
    required_mentions: List[str] = field(default_factory=list)  # Must mention these
    forbidden_mentions: List[str] = field(default_factory=list)  # Must NOT mention (hallucination check)
    max_length: Optional[int] = None
    min_coverage: float = 0.7


class CodeSummarizationMetrics(CapabilityMetrics):
    """Metrics for evaluating code summarization quality.

    Measures:
    - coverage: Whether required concepts are mentioned
    - no_hallucination: Whether false information is avoided
    - within_length: Whether summary is within length limit
    - meets_min_coverage: Whether minimum coverage threshold is met
    - function_understanding: Whether function details are understood
    - class_understanding: Whether class structure is understood
    """

    @property
    def name(self) -> str:
        return "code_summarization"

    def evaluate(
        self,
        code: str,
        summary: str,
        expectation: SummarizationExpectation,
    ) -> List[MetricResult]:
        """Evaluate code summarization quality.

        Args:
            code: Original code.
            summary: Generated summary.
            expectation: Expected properties.

        Returns:
            List of metric results.
        """
        results = []
        coverage = 1.0

        # 1. Required mentions (coverage)
        if expectation.required_mentions:
            mentioned = sum(
                1 for m in expectation.required_mentions
                if m.lower() in summary.lower()
            )
            coverage = mentioned / len(expectation.required_mentions)
            results.append(MetricResult(
                name="coverage",
                value=coverage,
                details={
                    "required": expectation.required_mentions,
                    "mentioned": mentioned,
                    "total": len(expectation.required_mentions),
                },
            ))
        else:
            results.append(MetricResult(name="coverage", value=1.0))

        # 2. No hallucinations (forbidden mentions)
        if expectation.forbidden_mentions:
            not_mentioned = sum(
                1 for m in expectation.forbidden_mentions
                if m.lower() not in summary.lower()
            )
            accuracy = not_mentioned / len(expectation.forbidden_mentions)
            results.append(MetricResult(
                name="no_hallucination",
                value=accuracy,
                details={
                    "forbidden": expectation.forbidden_mentions,
                    "not_mentioned": not_mentioned,
                },
            ))
        else:
            results.append(MetricResult(name="no_hallucination", value=1.0))

        # 3. Length constraint
        if expectation.max_length is not None:
            within_length = len(summary) <= expectation.max_length
            results.append(MetricResult(
                name="within_length",
                value=1.0 if within_length else 0.0,
                details={
                    "actual_length": len(summary),
                    "max_length": expectation.max_length,
                },
            ))

        # 4. Minimum coverage threshold
        if expectation.required_mentions:
            meets_coverage = coverage >= expectation.min_coverage
            results.append(MetricResult(
                name="meets_min_coverage",
                value=1.0 if meets_coverage else 0.0,
                details={
                    "actual_coverage": coverage,
                    "min_required": expectation.min_coverage,
                },
            ))

        return results

    def evaluate_function_understanding(
        self,
        function_code: str,
        summary: str,
        expected_params: List[str],
        expected_return: Optional[str],
    ) -> MetricResult:
        """Evaluate if function is understood correctly.

        Args:
            function_code: Function source code.
            summary: Summary of the function.
            expected_params: Parameters that should be mentioned.
            expected_return: Return type/value that should be mentioned.

        Returns:
            MetricResult for function understanding.
        """
        score = 0.0
        total = 0
        details = {}

        # Check params
        if expected_params:
            param_mentioned = sum(
                1 for p in expected_params
                if p.lower() in summary.lower()
            )
            param_score = param_mentioned / len(expected_params)
            score += param_score
            total += 1
            details["params_covered"] = param_mentioned
            details["params_total"] = len(expected_params)

        # Check return
        if expected_return:
            return_mentioned = expected_return.lower() in summary.lower()
            score += 1.0 if return_mentioned else 0.0
            total += 1
            details["return_mentioned"] = return_mentioned

        final_score = score / total if total > 0 else 1.0

        return MetricResult(
            name="function_understanding",
            value=final_score,
            details=details,
        )

    def evaluate_class_understanding(
        self,
        class_code: str,
        summary: str,
        expected_methods: List[str],
        expected_attributes: List[str],
    ) -> MetricResult:
        """Evaluate if class is understood correctly.

        Args:
            class_code: Class source code.
            summary: Summary of the class.
            expected_methods: Methods that should be mentioned.
            expected_attributes: Attributes that should be mentioned.

        Returns:
            MetricResult for class understanding.
        """
        score = 0.0
        total = 0
        details = {}

        # Check methods
        if expected_methods:
            method_mentioned = sum(
                1 for m in expected_methods
                if m.lower() in summary.lower()
            )
            method_score = method_mentioned / len(expected_methods)
            score += method_score
            total += 1
            details["methods_covered"] = method_mentioned
            details["methods_total"] = len(expected_methods)

        # Check attributes
        if expected_attributes:
            attr_mentioned = sum(
                1 for a in expected_attributes
                if a.lower() in summary.lower()
            )
            attr_score = attr_mentioned / len(expected_attributes)
            score += attr_score
            total += 1
            details["attributes_covered"] = attr_mentioned
            details["attributes_total"] = len(expected_attributes)

        final_score = score / total if total > 0 else 1.0

        return MetricResult(
            name="class_understanding",
            value=final_score,
            details=details,
        )


# =============================================================================
# Repo Understanding Metrics
# =============================================================================


@dataclass
class RepoExpectation:
    """Expected properties for repository understanding."""
    key_modules: List[str] = field(default_factory=list)
    key_dependencies: List[str] = field(default_factory=list)
    key_entry_points: List[str] = field(default_factory=list)


class RepoUnderstandingMetrics(CapabilityMetrics):
    """Metrics for evaluating repository understanding quality.

    Measures:
    - module_understanding: Whether key modules are identified
    - dependency_awareness: Whether dependencies are correctly identified
    - navigation_efficiency: Efficiency of finding targets in codebase
    """

    @property
    def name(self) -> str:
        return "repo_understanding"

    def evaluate(
        self,
        agent_response: str,
        expectation: RepoExpectation,
    ) -> List[MetricResult]:
        """Evaluate repository understanding.

        Args:
            agent_response: Agent's description of repository.
            expectation: Expected properties.

        Returns:
            List of metric results.
        """
        results = []

        # Module understanding
        results.append(self.evaluate_module_understanding(
            agent_response,
            expectation.key_modules,
        ))

        # Dependency awareness
        results.append(self.evaluate_dependency_awareness(
            agent_response,
            expectation.key_dependencies,
        ))

        return results

    def evaluate_module_understanding(
        self,
        agent_response: str,
        expected_modules: List[str],
    ) -> MetricResult:
        """Evaluate if key modules are understood.

        Args:
            agent_response: Agent's description of repository.
            expected_modules: Key modules that should be identified.

        Returns:
            MetricResult for module understanding.
        """
        if not expected_modules:
            return MetricResult(name="module_understanding", value=1.0)

        identified = sum(
            1 for m in expected_modules
            if m.lower() in agent_response.lower()
        )
        understanding = identified / len(expected_modules)

        return MetricResult(
            name="module_understanding",
            value=understanding,
            details={
                "expected_modules": expected_modules,
                "identified": identified,
            },
        )

    def evaluate_dependency_awareness(
        self,
        agent_response: str,
        expected_dependencies: List[str],
    ) -> MetricResult:
        """Evaluate if dependencies are correctly identified.

        Args:
            agent_response: Agent's response about dependencies.
            expected_dependencies: Dependencies that should be identified.

        Returns:
            MetricResult for dependency awareness.
        """
        if not expected_dependencies:
            return MetricResult(name="dependency_awareness", value=1.0)

        identified = sum(
            1 for d in expected_dependencies
            if d.lower() in agent_response.lower()
        )
        awareness = identified / len(expected_dependencies)

        return MetricResult(
            name="dependency_awareness",
            value=awareness,
            details={
                "expected_dependencies": expected_dependencies,
                "identified": identified,
            },
        )

    def evaluate_navigation_efficiency(
        self,
        search_steps: int,
        optimal_steps: int,
    ) -> MetricResult:
        """Evaluate navigation efficiency in the codebase.

        Args:
            search_steps: Actual steps taken to find target.
            optimal_steps: Optimal number of steps.

        Returns:
            MetricResult for navigation efficiency.
        """
        if search_steps <= optimal_steps:
            efficiency = 1.0
        else:
            # Penalize extra steps, but don't go below 0
            efficiency = max(0.0, optimal_steps / search_steps)

        return MetricResult(
            name="navigation_efficiency",
            value=efficiency,
            details={
                "actual_steps": search_steps,
                "optimal_steps": optimal_steps,
            },
        )
