"""TaskPlanner for subagent task decomposition.

This module provides the TaskPlanner class which decomposes user goals
into SubagentDAG structures with appropriate subagent assignments and
dependencies.

Features:
- Rule-based fast path for common patterns
- Complexity classification
- LLM-based decomposition for complex goals
- Validation and repair of generated DAGs

Example:
    >>> from nimbus.core.task.planner import TaskPlanner
    >>> from nimbus.core.task.types import SubagentType
    >>>
    >>> planner = TaskPlanner(llm_client)
    >>> dag = await planner.plan(
    ...     goal="Implement a caching layer",
    ...     context="Working on a Python web service",
    ...     available_subagents={SubagentType.EYE, SubagentType.BODY, SubagentType.TONGUE},
    ... )
"""

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from .types import (
    SubagentType,
    SubagentNode,
    SubagentDAG,
    SubagentStatus,
    SUBAGENT_TOOLS,
)

if TYPE_CHECKING:
    from nimbus.core.planner import LLMClient


# =============================================================================
# Planning Rules
# =============================================================================

# Rule patterns for common task types
# Each rule maps a pattern to a sequence of subagent types and their goals
TASK_PATTERNS: List[Dict[str, Any]] = [
    # Read and summarize/explain pattern
    {
        "name": "read_and_summarize",
        "pattern": r"^(?:read|look at|examine)\s+(.+?)\s+and\s+(?:summarize|explain|describe)",
        "sequence": [
            {"type": SubagentType.EYE, "goal_template": "Read and explore: {match}"},
            {
                "type": SubagentType.MIND,
                "goal_template": "Summarize findings from the exploration",
                "context_sources": ["t1"],
            },
        ],
        "complexity": "simple",
    },
    # Implement/create/build/add pattern (full workflow)
    {
        "name": "implement_feature",
        "pattern": r"^(?:implement|create|build|add|develop)\s+(.+)",
        "sequence": [
            {"type": SubagentType.EYE, "goal_template": "Explore existing code structure and understand the codebase relevant to: {match}"},
            {
                "type": SubagentType.MIND,
                "goal_template": "Design implementation approach for: {match}",
                "context_sources": ["t1"],
            },
            {
                "type": SubagentType.BODY,
                "goal_template": "Implement: {match}",
                "context_sources": ["t1", "t2"],
            },
            {
                "type": SubagentType.TONGUE,
                "goal_template": "Run tests to verify the implementation",
                "context_sources": ["t3"],
            },
        ],
        "complexity": "complex",
    },
    # Fix bug pattern
    {
        "name": "fix_bug",
        "pattern": r"^(?:fix|repair|resolve|debug)\s+(?:bug|issue|error|problem)\s+(?:in|with|at)?\s*(.+)",
        "sequence": [
            {"type": SubagentType.EYE, "goal_template": "Explore and understand the bug context: {match}"},
            {
                "type": SubagentType.BODY,
                "goal_template": "Fix the bug: {match}",
                "context_sources": ["t1"],
            },
            {
                "type": SubagentType.TONGUE,
                "goal_template": "Run tests to verify the fix",
                "context_sources": ["t2"],
            },
        ],
        "complexity": "moderate",
    },
    # Simple fix pattern (without "bug" keyword)
    {
        "name": "fix_simple",
        "pattern": r"^(?:fix|repair|resolve)\s+(.+)",
        "sequence": [
            {"type": SubagentType.EYE, "goal_template": "Explore and understand: {match}"},
            {
                "type": SubagentType.BODY,
                "goal_template": "Fix: {match}",
                "context_sources": ["t1"],
            },
            {
                "type": SubagentType.TONGUE,
                "goal_template": "Run tests to verify the fix",
                "context_sources": ["t2"],
            },
        ],
        "complexity": "moderate",
    },
    # Review/audit pattern
    {
        "name": "review_code",
        "pattern": r"^(?:review|audit|check|analyze)\s+(.+)",
        "sequence": [
            {"type": SubagentType.EYE, "goal_template": "Explore the code to be reviewed: {match}"},
            {
                "type": SubagentType.NOSE,
                "goal_template": "Review and provide feedback: {match}",
                "context_sources": ["t1"],
            },
        ],
        "complexity": "moderate",
    },
    # Test pattern
    {
        "name": "run_tests",
        "pattern": r"^(?:test|run tests?|verify)\s+(.+)",
        "sequence": [
            {"type": SubagentType.TONGUE, "goal_template": "Run tests for: {match}"},
        ],
        "complexity": "simple",
    },
    # Explore/understand pattern
    {
        "name": "explore_code",
        "pattern": r"^(?:explore|understand|learn about|investigate)\s+(.+)",
        "sequence": [
            {"type": SubagentType.EYE, "goal_template": "Explore: {match}"},
            {
                "type": SubagentType.MIND,
                "goal_template": "Synthesize findings and provide understanding",
                "context_sources": ["t1"],
            },
        ],
        "complexity": "simple",
    },
    # Refactor pattern
    {
        "name": "refactor_code",
        "pattern": r"^(?:refactor|restructure|reorganize|clean up)\s+(.+)",
        "sequence": [
            {"type": SubagentType.EYE, "goal_template": "Explore existing code structure: {match}"},
            {
                "type": SubagentType.MIND,
                "goal_template": "Design refactoring approach",
                "context_sources": ["t1"],
            },
            {
                "type": SubagentType.BODY,
                "goal_template": "Refactor: {match}",
                "context_sources": ["t1", "t2"],
            },
            {
                "type": SubagentType.TONGUE,
                "goal_template": "Run tests to verify refactoring didn't break anything",
                "context_sources": ["t3"],
            },
        ],
        "complexity": "complex",
    },
    # Write tests pattern
    {
        "name": "write_tests",
        "pattern": r"^(?:write|add|create)\s+tests?\s+(?:for)?\s*(.+)",
        "sequence": [
            {"type": SubagentType.EYE, "goal_template": "Explore the code to be tested: {match}"},
            {
                "type": SubagentType.BODY,
                "goal_template": "Write tests for: {match}",
                "context_sources": ["t1"],
            },
            {
                "type": SubagentType.TONGUE,
                "goal_template": "Run the new tests to verify they work",
                "context_sources": ["t2"],
            },
        ],
        "complexity": "moderate",
    },
    # Document pattern
    {
        "name": "document_code",
        "pattern": r"^(?:document|write docs for|add documentation to)\s+(.+)",
        "sequence": [
            {"type": SubagentType.EYE, "goal_template": "Explore the code to be documented: {match}"},
            {
                "type": SubagentType.MIND,
                "goal_template": "Write documentation based on exploration",
                "context_sources": ["t1"],
            },
        ],
        "complexity": "simple",
    },
]


class TaskPlanner:
    """Plans subagent task decomposition from user goals.

    Unlike PlannerPipeline which plans tool calls, TaskPlanner plans
    subagent delegation at a higher level of abstraction.

    Attributes:
        llm_client: LLM client for complexity classification and decomposition.
    """

    def __init__(self, llm_client: Optional["LLMClient"] = None):
        """Initialize TaskPlanner.

        Args:
            llm_client: LLM client for LLM-based decomposition.
                       If None, only rule-based planning is available.
        """
        self.llm_client = llm_client
        self._patterns = TASK_PATTERNS

    async def plan(
        self,
        goal: str,
        context: str = "",
        available_subagents: Optional[Set[SubagentType]] = None,
    ) -> SubagentDAG:
        """Generate SubagentDAG from user goal.

        Args:
            goal: User's goal/request.
            context: Optional context (conversation history, workspace info).
            available_subagents: Set of available subagent types.
                               If None, all types are available.

        Returns:
            SubagentDAG with subagent nodes and dependencies.
        """
        if available_subagents is None:
            available_subagents = set(SubagentType)

        # Step 1: Classify complexity
        complexity = await self._classify_complexity(goal)

        # Step 2: Try rule-based fast path
        dag = self._try_rule_match(goal, complexity, available_subagents)
        if dag is not None:
            return dag

        # Step 3: LLM-based decomposition
        if self.llm_client is not None:
            dag = await self._llm_decompose(goal, context, available_subagents, complexity)
            if dag is not None:
                # Step 4: Validate and repair
                dag = self._validate_and_repair(dag, available_subagents)
                return dag

        # Fallback: Simple single-subagent plan
        return self._create_fallback_dag(goal, available_subagents)

    def _try_rule_match(
        self,
        goal: str,
        complexity: str,
        available_subagents: Set[SubagentType],
    ) -> Optional[SubagentDAG]:
        """Try rule-based pattern matching for fast path.

        Args:
            goal: User's goal.
            complexity: Classified complexity level.
            available_subagents: Set of available subagent types.

        Returns:
            SubagentDAG if a rule matched, None otherwise.
        """
        goal_lower = goal.lower().strip()

        for pattern_def in self._patterns:
            match = re.match(pattern_def["pattern"], goal_lower, re.IGNORECASE)
            if match:
                # Check if all required subagent types are available
                sequence = pattern_def["sequence"]
                required_types = {step["type"] for step in sequence}
                if not required_types.issubset(available_subagents):
                    continue

                # Build nodes from sequence
                nodes = []
                for i, step in enumerate(sequence):
                    node_id = f"t{i + 1}"
                    goal_template = step["goal_template"]

                    # Substitute matched groups
                    node_goal = goal_template.format(match=match.group(1) if match.groups() else goal)

                    # Determine dependencies
                    depends_on = [f"t{i}"] if i > 0 else []

                    # Context sources default to dependencies
                    context_sources = step.get("context_sources", depends_on.copy())

                    nodes.append({
                        "id": node_id,
                        "type": step["type"],
                        "goal": node_goal,
                        "depends_on": depends_on,
                        "context_sources": context_sources,
                    })

                return SubagentDAG.create(
                    goal=goal,
                    nodes=nodes,
                    complexity=pattern_def.get("complexity", complexity),
                )

        return None

    async def _classify_complexity(self, goal: str) -> str:
        """Classify goal complexity.

        Args:
            goal: User's goal.

        Returns:
            Complexity level: "simple", "moderate", or "complex".
        """
        # Simple heuristics for complexity classification
        goal_lower = goal.lower()

        # Simple indicators
        simple_keywords = ["read", "show", "list", "find", "search", "test", "run"]
        for keyword in simple_keywords:
            if goal_lower.startswith(keyword):
                return "simple"

        # Complex indicators
        complex_keywords = [
            "implement", "create", "build", "refactor", "migrate",
            "integrate", "design", "architect", "develop",
        ]
        for keyword in complex_keywords:
            if keyword in goal_lower:
                return "complex"

        # Multiple action indicators
        if " and " in goal_lower or "then" in goal_lower:
            return "complex"

        return "moderate"

    async def _llm_decompose(
        self,
        goal: str,
        context: str,
        available_subagents: Set[SubagentType],
        complexity: str,
    ) -> Optional[SubagentDAG]:
        """Use LLM to decompose goal into subagent tasks.

        Args:
            goal: User's goal.
            context: Conversation context.
            available_subagents: Available subagent types.
            complexity: Pre-classified complexity.

        Returns:
            SubagentDAG from LLM decomposition, or None on failure.
        """
        if self.llm_client is None:
            return None

        # Build subagent descriptions
        subagent_descriptions = []
        for subagent_type in available_subagents:
            tools = SUBAGENT_TOOLS.get(subagent_type, set())
            subagent_descriptions.append(
                f"- {subagent_type.value}: {self._get_subagent_description(subagent_type)} "
                f"(Tools: {', '.join(tools)})"
            )

        prompt = f"""Decompose the following user goal into a sequence of subagent tasks.

USER GOAL: {goal}

CONTEXT:
{context[:2000] if context else "No additional context"}

AVAILABLE SUBAGENTS:
{chr(10).join(subagent_descriptions)}

TASK COMPLEXITY: {complexity}

OUTPUT FORMAT:
Return a JSON object with the following structure:
{{
    "nodes": [
        {{
            "id": "t1",
            "type": "<subagent_type>",
            "goal": "<clear task description for this subagent>",
            "depends_on": [],  // List of node IDs this depends on
            "context_sources": []  // List of node IDs to inject results from
        }},
        ...
    ]
}}

GUIDELINES:
1. Use the minimum number of subagents needed
2. Eye subagent should explore/gather information first
3. Body subagent does implementation
4. Mind subagent does design/architecture
5. Tongue subagent does testing
6. Nose subagent does code review
7. Set proper dependencies (sequential when order matters)
8. Set context_sources to inject relevant previous results

Return only valid JSON, no markdown or explanation."""

        try:
            response = await self.llm_client.complete(prompt)
            content = response.content if hasattr(response, "content") else str(response)

            # Extract JSON from response
            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                data = json.loads(json_match.group())
                nodes = data.get("nodes", [])

                if nodes:
                    return SubagentDAG.create(
                        goal=goal,
                        nodes=nodes,
                        complexity=complexity,
                    )

        except Exception:
            pass

        return None

    def _validate_and_repair(
        self,
        dag: SubagentDAG,
        available_subagents: Set[SubagentType],
    ) -> SubagentDAG:
        """Validate and repair a generated DAG.

        Args:
            dag: DAG to validate.
            available_subagents: Available subagent types.

        Returns:
            Validated (and possibly repaired) DAG.
        """
        # Check for invalid subagent types
        for node_id, node in list(dag.nodes.items()):
            if node.subagent_type not in available_subagents:
                # Replace with closest available type
                replacement = self._find_replacement_type(node.subagent_type, available_subagents)
                if replacement:
                    node.subagent_type = replacement
                else:
                    # Remove node and update dependencies
                    del dag.nodes[node_id]
                    for other_node in dag.nodes.values():
                        other_node.depends_on = [
                            dep for dep in other_node.depends_on if dep != node_id
                        ]
                        other_node.context_sources = [
                            src for src in other_node.context_sources if src != node_id
                        ]

        # Check for circular dependencies
        if self._has_circular_dependency(dag):
            # Remove problematic edges (simple fix)
            for node in dag.nodes.values():
                node.depends_on = [
                    dep for dep in node.depends_on
                    if dep in dag.nodes and dag.nodes[dep].id != node.id
                ]

        # Ensure at least one node has no dependencies (start node)
        has_start = any(len(node.depends_on) == 0 for node in dag.nodes.values())
        if not has_start and dag.nodes:
            # Make first node a start node
            first_node = next(iter(dag.nodes.values()))
            first_node.depends_on = []

        return dag

    def _has_circular_dependency(self, dag: SubagentDAG) -> bool:
        """Check if DAG has circular dependencies."""
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

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

    def _find_replacement_type(
        self,
        original: SubagentType,
        available: Set[SubagentType],
    ) -> Optional[SubagentType]:
        """Find a replacement subagent type.

        Args:
            original: Original subagent type.
            available: Available subagent types.

        Returns:
            Replacement type or None.
        """
        # Define fallback preferences
        fallbacks: Dict[SubagentType, List[SubagentType]] = {
            SubagentType.EYE: [SubagentType.NOSE, SubagentType.EAR],
            SubagentType.BODY: [SubagentType.MIND],
            SubagentType.MIND: [SubagentType.EYE, SubagentType.BODY],
            SubagentType.TONGUE: [SubagentType.BODY],
            SubagentType.NOSE: [SubagentType.EYE],
            SubagentType.EAR: [SubagentType.EYE],
        }

        for fallback in fallbacks.get(original, []):
            if fallback in available:
                return fallback

        # Return any available type as last resort
        if available:
            return next(iter(available))

        return None

    def _create_fallback_dag(
        self,
        goal: str,
        available_subagents: Set[SubagentType],
    ) -> SubagentDAG:
        """Create a simple fallback DAG.

        Args:
            goal: User's goal.
            available_subagents: Available subagent types.

        Returns:
            Simple single-node DAG.
        """
        # Prefer eye for exploration, then body for action
        if SubagentType.EYE in available_subagents:
            subagent_type = SubagentType.EYE
        elif SubagentType.BODY in available_subagents:
            subagent_type = SubagentType.BODY
        elif SubagentType.MIND in available_subagents:
            subagent_type = SubagentType.MIND
        else:
            subagent_type = next(iter(available_subagents), SubagentType.EYE)

        return SubagentDAG.create(
            goal=goal,
            nodes=[
                {
                    "id": "t1",
                    "type": subagent_type,
                    "goal": goal,
                }
            ],
            complexity="simple",
        )

    def _get_subagent_description(self, subagent_type: SubagentType) -> str:
        """Get description for a subagent type."""
        descriptions = {
            SubagentType.EYE: "Code exploration and information gathering",
            SubagentType.BODY: "Code implementation and modification",
            SubagentType.MIND: "Architecture design and documentation",
            SubagentType.TONGUE: "Testing and verification",
            SubagentType.NOSE: "Code review and quality analysis",
            SubagentType.EAR: "Requirements analysis and clarification",
        }
        return descriptions.get(subagent_type, "General purpose")
