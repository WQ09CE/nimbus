"""DAG validation and repair utilities.

This module provides validation and repair functionality for LLM-generated DAGs,
ensuring they are structurally correct and use only allowed skills.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict, Any
from copy import deepcopy

from ..types import TaskDAG, TaskNode, TaskStatus, TaskSource
from ..logging import get_logger

logger = get_logger("planner.validator")


@dataclass
class ValidationResult:
    """Result of DAG validation.

    Attributes:
        valid: Whether the DAG is valid (no errors).
        errors: List of validation error messages.
        warnings: List of validation warning messages.
        repaired_dag: A repaired version of the DAG (if repair was successful).
    """
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    repaired_dag: Optional[TaskDAG] = None

    def __bool__(self) -> bool:
        """Return True if validation passed."""
        return self.valid


class DAGValidator:
    """Validates and repairs LLM-generated DAGs.

    Performs the following validations:
    1. Schema validation (required fields)
    2. Cycle detection using Kahn's algorithm
    3. Orphan node detection (nodes unreachable from root)
    4. Skill whitelist checking
    5. Dependency completeness (all referenced deps exist)
    6. Size limits (max tasks, max depth)

    Example:
        ```python
        validator = DAGValidator(
            skill_whitelist={"search", "summarize", "synthesize"},
            max_tasks=20,
            max_depth=10,
        )

        result = validator.validate(dag)
        if not result.valid:
            if result.repaired_dag:
                dag = result.repaired_dag
            else:
                raise ValueError(f"Invalid DAG: {result.errors}")
        ```
    """

    def __init__(
        self,
        skill_whitelist: Optional[Set[str]] = None,
        max_tasks: int = 20,
        max_depth: int = 10,
    ):
        """Initialize the DAG validator.

        Args:
            skill_whitelist: Set of allowed skill names. If empty, all skills allowed.
            max_tasks: Maximum number of tasks allowed in a DAG.
            max_depth: Maximum dependency depth allowed.
        """
        self.skill_whitelist = skill_whitelist or set()
        self.max_tasks = max_tasks
        self.max_depth = max_depth

    def validate(self, dag: TaskDAG) -> ValidationResult:
        """Full validation with repair attempt.

        Performs all validation checks and attempts to repair the DAG
        if possible when errors are found.

        Args:
            dag: The TaskDAG to validate.

        Returns:
            ValidationResult with validation status and optionally repaired DAG.
        """
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Schema validation
        schema_errors = self._validate_schema(dag)
        errors.extend(schema_errors)

        # 2. Cycle detection
        has_cycle = self._has_cycle(dag)
        if has_cycle:
            errors.append("DAG contains a cycle")

        # 3. Orphan node detection
        orphans = self._find_orphans(dag)
        if orphans:
            warnings.append(f"Orphan nodes detected: {', '.join(orphans)}")

        # 4. Skill whitelist check
        invalid_skills = self._check_skill_whitelist(dag)
        if invalid_skills:
            errors.extend([f"Invalid skill '{s}'" for s in invalid_skills])

        # 5. Dependency completeness
        missing_deps = self._check_dependencies(dag)
        if missing_deps:
            errors.extend([f"Missing dependency '{d}'" for d in missing_deps])

        # 6. Size limits (skip depth check if cycle detected)
        size_errors = self._check_size_limits(dag, has_cycle=has_cycle)
        errors.extend(size_errors)

        # Determine if valid
        is_valid = len(errors) == 0

        # Attempt repair if there are errors
        repaired_dag = None
        if not is_valid:
            repaired_dag = self._repair(dag, errors, warnings)
            if repaired_dag:
                # Re-validate the repaired DAG
                re_errors = []
                re_errors.extend(self._validate_schema(repaired_dag))
                repaired_has_cycle = self._has_cycle(repaired_dag)
                if repaired_has_cycle:
                    re_errors.append("Repaired DAG still contains a cycle")
                re_errors.extend([f"Invalid skill '{s}'" for s in self._check_skill_whitelist(repaired_dag)])
                re_errors.extend([f"Missing dependency '{d}'" for d in self._check_dependencies(repaired_dag)])
                re_errors.extend(self._check_size_limits(repaired_dag, has_cycle=repaired_has_cycle))

                if re_errors:
                    warnings.append("Repair attempted but failed")
                    repaired_dag = None
                else:
                    warnings.append("DAG was repaired successfully")

        return ValidationResult(
            valid=is_valid,
            errors=errors,
            warnings=warnings,
            repaired_dag=repaired_dag,
        )

    def _validate_schema(self, dag: TaskDAG) -> List[str]:
        """Validate DAG schema (required fields).

        Args:
            dag: The DAG to validate.

        Returns:
            List of schema validation errors.
        """
        errors = []

        if not dag.id:
            errors.append("DAG missing 'id' field")

        if not dag.nodes:
            errors.append("DAG has no nodes")

        for node_id, node in dag.nodes.items():
            if not node.id:
                errors.append(f"Node missing 'id' field")
            elif node.id != node_id:
                errors.append(f"Node ID mismatch: dict key='{node_id}', node.id='{node.id}'")

            if not node.skill:
                errors.append(f"Node '{node_id}' missing 'skill' field")

        return errors

    def _has_cycle(self, dag: TaskDAG) -> bool:
        """Check for cycles using Kahn's algorithm.

        Args:
            dag: The DAG to check.

        Returns:
            True if the DAG contains a cycle, False otherwise.
        """
        if not dag.nodes:
            return False

        # Build in-degree map and adjacency list
        in_degree: Dict[str, int] = {node_id: 0 for node_id in dag.nodes}
        out_edges: Dict[str, List[str]] = {node_id: [] for node_id in dag.nodes}

        for node in dag.nodes.values():
            for dep_id in node.depends_on:
                if dep_id in dag.nodes:
                    out_edges[dep_id].append(node.id)
                    in_degree[node.id] += 1

        # Start with nodes with no dependencies
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited = 0

        while queue:
            current = queue.pop(0)
            visited += 1

            for next_id in out_edges.get(current, []):
                in_degree[next_id] -= 1
                if in_degree[next_id] == 0:
                    queue.append(next_id)

        # If we visited all nodes, there's no cycle
        return visited != len(dag.nodes)

    def _find_orphans(self, dag: TaskDAG) -> List[str]:
        """Find nodes with no path to/from root (source) nodes.

        Root nodes are nodes with no dependencies.

        Args:
            dag: The DAG to check.

        Returns:
            List of orphan node IDs.
        """
        if not dag.nodes:
            return []

        # Build reverse adjacency (parent -> children)
        children: Dict[str, List[str]] = {node_id: [] for node_id in dag.nodes}
        for node in dag.nodes.values():
            for dep_id in node.depends_on:
                if dep_id in dag.nodes:
                    children[dep_id].append(node.id)

        # Find root nodes (no dependencies)
        roots = [
            node_id for node_id, node in dag.nodes.items()
            if not node.depends_on or all(d not in dag.nodes for d in node.depends_on)
        ]

        if not roots:
            # All nodes have dependencies - cycle or broken
            return list(dag.nodes.keys())

        # BFS from roots to find reachable nodes
        reachable: Set[str] = set()
        queue = list(roots)

        while queue:
            current = queue.pop(0)
            if current in reachable:
                continue
            reachable.add(current)
            queue.extend(children.get(current, []))

        # Orphans are nodes not reachable from roots
        orphans = [nid for nid in dag.nodes if nid not in reachable]
        return orphans

    def _check_skill_whitelist(self, dag: TaskDAG) -> List[str]:
        """Return list of skills not in the whitelist.

        Args:
            dag: The DAG to check.

        Returns:
            List of invalid skill names.
        """
        if not self.skill_whitelist:
            return []  # No whitelist = all skills allowed

        invalid = []
        for node in dag.nodes.values():
            # 'synthesize' is always allowed as a fallback
            if node.skill != "synthesize" and node.skill not in self.skill_whitelist:
                invalid.append(node.skill)

        return list(set(invalid))  # Deduplicate

    def _check_dependencies(self, dag: TaskDAG) -> List[str]:
        """Return list of missing dependency IDs.

        Args:
            dag: The DAG to check.

        Returns:
            List of dependency IDs that don't exist in the DAG.
        """
        missing = set()
        for node in dag.nodes.values():
            for dep_id in node.depends_on:
                if dep_id not in dag.nodes:
                    missing.add(dep_id)
        return list(missing)

    def _check_size_limits(self, dag: TaskDAG, has_cycle: bool = False) -> List[str]:
        """Check DAG size limits.

        Args:
            dag: The DAG to check.
            has_cycle: Whether the DAG has a cycle (skip depth check if True).

        Returns:
            List of size limit violation errors.
        """
        errors = []

        # Check max tasks
        if len(dag.nodes) > self.max_tasks:
            errors.append(
                f"DAG has {len(dag.nodes)} tasks, exceeds limit of {self.max_tasks}"
            )

        # Check max depth (only if no cycle to avoid infinite recursion)
        if not has_cycle:
            depth = self._calculate_depth(dag)
            if depth > self.max_depth:
                errors.append(
                    f"DAG has depth {depth}, exceeds limit of {self.max_depth}"
                )

        return errors

    def _calculate_depth(self, dag: TaskDAG) -> int:
        """Calculate the maximum depth of the DAG.

        Args:
            dag: The DAG to analyze.

        Returns:
            Maximum depth (longest path from root to leaf).
        """
        if not dag.nodes:
            return 0

        # Memoized depth calculation
        depths: Dict[str, int] = {}

        def get_depth(node_id: str) -> int:
            if node_id in depths:
                return depths[node_id]

            node = dag.nodes.get(node_id)
            if not node:
                return 0

            if not node.depends_on:
                depths[node_id] = 1
                return 1

            max_dep_depth = 0
            for dep_id in node.depends_on:
                if dep_id in dag.nodes:
                    dep_depth = get_depth(dep_id)
                    max_dep_depth = max(max_dep_depth, dep_depth)

            depths[node_id] = max_dep_depth + 1
            return depths[node_id]

        # Calculate depth for all nodes
        return max(get_depth(nid) for nid in dag.nodes)

    def _repair(
        self,
        dag: TaskDAG,
        errors: List[str],
        warnings: List[str],
    ) -> Optional[TaskDAG]:
        """Attempt to repair DAG based on errors.

        Repair strategies:
        1. Remove nodes with invalid skills (replace with chat)
        2. Remove missing dependencies from depends_on lists
        3. Remove orphan nodes
        4. Break cycles by removing back-edges

        Args:
            dag: The DAG to repair.
            errors: List of errors to address.
            warnings: List to append repair warnings to.

        Returns:
            Repaired DAG if successful, None if repair is not possible.
        """
        if not dag.nodes:
            return None

        try:
            # Deep copy to avoid modifying original
            repaired = TaskDAG(
                id=dag.id,
                goal=dag.goal,
                nodes={},
                created_at=dag.created_at,
                replan_history=list(dag.replan_history),
            )

            # Copy nodes
            for node_id, node in dag.nodes.items():
                repaired.nodes[node_id] = TaskNode(
                    id=node.id,
                    skill=node.skill,
                    params=dict(node.params),
                    depends_on=list(node.depends_on),
                    status=node.status,
                    result=node.result,
                    error=node.error,
                    started_at=node.started_at,
                    finished_at=node.finished_at,
                    is_checkpoint=node.is_checkpoint,
                    source=node.source,
                    confidence=node.confidence,
                    constraints=list(node.constraints),
                    generation=node.generation,
                )

            # 1. Fix invalid skills by replacing with chat
            if self.skill_whitelist:
                for node in repaired.nodes.values():
                    if node.skill != "synthesize" and node.skill not in self.skill_whitelist:
                        warnings.append(f"Replaced invalid skill '{node.skill}' with 'synthesize' in node '{node.id}'")
                        node.skill = "synthesize"
                        node.source = TaskSource.RULE

            # 2. Remove missing dependencies
            valid_ids = set(repaired.nodes.keys())
            for node in repaired.nodes.values():
                original_deps = node.depends_on
                node.depends_on = [d for d in node.depends_on if d in valid_ids]
                removed = set(original_deps) - set(node.depends_on)
                if removed:
                    warnings.append(f"Removed missing deps {removed} from node '{node.id}'")

            # 3. Remove orphan nodes (optional - keep them for now, just warn)
            # Orphans are handled as warnings, not removed

            # 4. Break cycles if present
            if self._has_cycle(repaired):
                repaired = self._break_cycles(repaired, warnings)
                if repaired is None:
                    return None

            # 5. Truncate if too many tasks
            if len(repaired.nodes) > self.max_tasks:
                # Keep first N tasks based on topological order
                sorted_ids = self._topological_sort(repaired)
                to_remove = sorted_ids[self.max_tasks:]
                for node_id in to_remove:
                    del repaired.nodes[node_id]
                    warnings.append(f"Removed node '{node_id}' to meet size limit")

                # Clean up dangling dependencies
                valid_ids = set(repaired.nodes.keys())
                for node in repaired.nodes.values():
                    node.depends_on = [d for d in node.depends_on if d in valid_ids]

            return repaired

        except Exception as e:
            logger.warning(f"DAG repair failed: {e}")
            return None

    def _break_cycles(self, dag: TaskDAG, warnings: List[str]) -> Optional[TaskDAG]:
        """Break cycles by removing back-edges.

        Uses DFS to detect and remove edges that create cycles.

        Args:
            dag: The DAG to fix.
            warnings: List to append warnings to.

        Returns:
            DAG with cycles broken, or None if impossible.
        """
        # Simple approach: remove edges greedily until acyclic
        max_iterations = len(dag.nodes) * len(dag.nodes)  # Prevent infinite loop
        iteration = 0

        while self._has_cycle(dag) and iteration < max_iterations:
            iteration += 1

            # Find a cycle using DFS
            cycle_edge = self._find_cycle_edge(dag)
            if cycle_edge is None:
                break

            from_node, to_node = cycle_edge

            # Remove the edge
            if to_node in dag.nodes[from_node].depends_on:
                dag.nodes[from_node].depends_on.remove(to_node)
                warnings.append(f"Removed cycle edge: {from_node} -> {to_node}")

        if self._has_cycle(dag):
            return None

        return dag

    def _find_cycle_edge(self, dag: TaskDAG) -> Optional[tuple]:
        """Find an edge that's part of a cycle.

        Args:
            dag: The DAG to search.

        Returns:
            Tuple (from_node, to_node) of an edge in a cycle, or None.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {nid: WHITE for nid in dag.nodes}
        parent: Dict[str, Optional[str]] = {nid: None for nid in dag.nodes}

        # Build reverse edges (node -> nodes that depend on it)
        dependents: Dict[str, List[str]] = {nid: [] for nid in dag.nodes}
        for node in dag.nodes.values():
            for dep_id in node.depends_on:
                if dep_id in dag.nodes:
                    dependents[dep_id].append(node.id)

        def dfs(node_id: str) -> Optional[tuple]:
            color[node_id] = GRAY

            for next_id in dependents.get(node_id, []):
                if next_id not in dag.nodes:
                    continue

                if color[next_id] == GRAY:
                    # Back edge found - this is part of a cycle
                    return (next_id, node_id)

                if color[next_id] == WHITE:
                    parent[next_id] = node_id
                    result = dfs(next_id)
                    if result:
                        return result

            color[node_id] = BLACK
            return None

        for node_id in dag.nodes:
            if color[node_id] == WHITE:
                result = dfs(node_id)
                if result:
                    return result

        return None

    def _topological_sort(self, dag: TaskDAG) -> List[str]:
        """Return nodes in topological order.

        Args:
            dag: The DAG to sort.

        Returns:
            List of node IDs in topological order.
        """
        in_degree: Dict[str, int] = {node_id: 0 for node_id in dag.nodes}
        out_edges: Dict[str, List[str]] = {node_id: [] for node_id in dag.nodes}

        for node in dag.nodes.values():
            for dep_id in node.depends_on:
                if dep_id in dag.nodes:
                    out_edges[dep_id].append(node.id)
                    in_degree[node.id] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        result = []

        while queue:
            current = queue.pop(0)
            result.append(current)

            for next_id in out_edges.get(current, []):
                in_degree[next_id] -= 1
                if in_degree[next_id] == 0:
                    queue.append(next_id)

        # Add any remaining nodes (shouldn't happen if acyclic)
        remaining = [nid for nid in dag.nodes if nid not in result]
        result.extend(remaining)

        return result
