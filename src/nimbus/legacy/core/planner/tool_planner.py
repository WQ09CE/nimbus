"""Lightweight Tool DAG Planner for read-only operations.

This module provides ToolDAGPlanner, a simplified planner that only
generates DAGs with read-only tools (Read, Glob, Grep, Synthesize).

The prompt is kept under 500 characters for efficiency.

Example:
    >>> planner = ToolDAGPlanner(llm_client)
    >>> dag = await planner.plan("Find all Python files")
    >>> print(dag.nodes)  # Contains Glob task
"""

import json
import re
from typing import Any, Dict, List, Optional, Protocol

from ..logging import get_logger
from ..types import TaskDAG, TaskSource
from .protocol import PlanningContext
from .validator import DAGValidator

logger = get_logger("planner.tool")


class LLMClient(Protocol):
    """Protocol for LLM client interface."""

    async def complete(self, prompt: str) -> str:
        ...


# =============================================================================
# Constants
# =============================================================================

# Read-only tools allowed by this planner
READONLY_TOOLS = {"Read", "Glob", "Grep", "Synthesize"}

# Compact prompt (< 500 characters excluding goal)
TOOL_DAG_PROMPT = """Generate tool call plan. JSON only.

Tools:
- Read: {{file_path}}
- Glob: {{pattern, path?}}
- Grep: {{pattern, path?, type?}} type=file extension: py,js,ts,go,java,etc

Output: {{"tasks": [{{"id": "t1", "skill": "Tool", "params": {{...}}, "depends_on": []}}]}}

Rules:
1. Parallel tasks: depends_on = []
2. Sequential: fill depends_on with task ids

Task: {goal}
"""


# =============================================================================
# ToolDAGPlanner
# =============================================================================


class ToolDAGPlanner:
    """Lightweight planner for read-only tool DAGs.

    Only generates DAGs with Read, Glob, Grep, and Synthesize skills.
    Uses a compact prompt (< 500 characters) for efficiency.

    Attributes:
        llm_client: LLM client for completions.
        validator: DAG validator with read-only tool whitelist.
        context_stack: Optional context stack for scoped context views.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        context_stack: Optional[Any] = None,
    ):
        """Initialize the tool planner.

        Args:
            llm_client: LLM client for completions.
            context_stack: Optional ContextStack for focused context views.
        """
        self.llm_client = llm_client
        self.context_stack = context_stack
        self.validator = DAGValidator(
            skill_whitelist=READONLY_TOOLS,
            max_tasks=5,  # Keep it simple
            max_depth=3,
        )

    @property
    def name(self) -> str:
        """Stage name for logging/tracing."""
        return "tool_planner"

    async def plan(
        self,
        goal: str,
        context: Optional[str] = None,
    ) -> TaskDAG:
        """Generate a read-only tool DAG.

        Args:
            goal: User's goal/request.
            context: Optional additional context.

        Returns:
            TaskDAG with read-only tool tasks.
        """
        # Use context stack if available
        if self.context_stack:
            from ..context import FrameFactory

            frame = FrameFactory.planner(goal, READONLY_TOOLS)
            async with self.context_stack.frame(frame):
                return await self._do_plan(goal, context)
        else:
            return await self._do_plan(goal, context)

    async def _do_plan(
        self,
        goal: str,
        context: Optional[str] = None,
    ) -> TaskDAG:
        """Internal planning logic.

        Args:
            goal: User's goal.
            context: Optional context.

        Returns:
            Generated TaskDAG.
        """
        prompt = TOOL_DAG_PROMPT.format(goal=goal)

        try:
            response = await self.llm_client.complete(prompt)
            dag = self._parse_response(response, goal)

            if dag:
                # Validate and repair
                result = self.validator.validate(dag)
                if not result.valid:
                    if result.repaired_dag:
                        dag = result.repaired_dag
                        logger.debug("Tool DAG repaired")
                    else:
                        logger.warning(f"Tool DAG validation failed: {result.errors}")
                        dag = self._create_fallback(goal)
                return dag
            else:
                return self._create_fallback(goal)

        except Exception as e:
            logger.error(f"Tool planning failed: {e}")
            return self._create_fallback(goal)

    def _parse_response(self, response: str, goal: str) -> Optional[TaskDAG]:
        """Parse LLM response into TaskDAG.

        Args:
            response: LLM response text.
            goal: Original goal for DAG.

        Returns:
            Parsed TaskDAG or None.
        """
        try:
            data = self._extract_json(response)
            tasks_data = data.get("tasks", [])

            if not tasks_data:
                return None

            tasks: List[Dict[str, Any]] = []
            for task_data in tasks_data:
                skill = task_data.get("skill", "")

                # Filter to read-only tools only
                if skill not in READONLY_TOOLS:
                    logger.debug(f"Skipping non-readonly skill: {skill}")
                    continue

                tasks.append({
                    "id": task_data.get("id", f"t{len(tasks) + 1}"),
                    "skill": skill,
                    "params": task_data.get("params", {}),
                    "depends_on": task_data.get("depends_on", []),
                    "source": TaskSource.LLM.value,
                })

            if not tasks:
                return None

            return TaskDAG.create(goal, tasks)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse tool planner response: {e}")
            return None

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON from response text.

        Args:
            text: Response text.

        Returns:
            Parsed JSON dict.

        Raises:
            json.JSONDecodeError: If no valid JSON found.
        """
        text = text.strip()

        # Try direct parse
        if text.startswith("{"):
            try:
                result: Dict[str, Any] = json.loads(text)
                return result
            except json.JSONDecodeError:
                pass

        # Try finding JSON object
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())
                return result
            except json.JSONDecodeError:
                pass

        raise json.JSONDecodeError("No JSON found", text, 0)

    def _create_fallback(self, goal: str) -> TaskDAG:
        """Create fallback DAG with Glob search.

        Args:
            goal: Original goal.

        Returns:
            Simple Glob + Synthesize DAG.
        """
        return TaskDAG.create(goal, [
            {
                "id": "t1",
                "skill": "Glob",
                "params": {"pattern": "**/*", "limit": 20},
                "source": TaskSource.LLM.value,
            },
            {
                "id": "t2",
                "skill": "Synthesize",
                "params": {"message": goal},
                "depends_on": ["t1"],
                "source": TaskSource.LLM.value,
            },
        ])


# =============================================================================
# ToolPlannerStage
# =============================================================================


class ToolPlannerStage:
    """PlannerPipeline stage wrapper for ToolDAGPlanner.

    Integrates ToolDAGPlanner into the planning pipeline.
    Only activates for MODERATE complexity tasks.

    Attributes:
        planner: ToolDAGPlanner instance.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        context_stack: Optional[Any] = None,
    ):
        """Initialize the stage.

        Args:
            llm_client: LLM client for completions.
            context_stack: Optional context stack.
        """
        self.planner = ToolDAGPlanner(llm_client, context_stack)

    @property
    def name(self) -> str:
        """Stage name."""
        return "tool_planner"

    async def process(self, ctx: PlanningContext) -> PlanningContext:
        """Process planning context with tool planning.

        Only generates DAG if:
        - No final_dag set yet
        - No early_exit flag
        - routing_action is "continue" (MODERATE complexity)

        Args:
            ctx: Planning context.

        Returns:
            Updated planning context.
        """
        # Skip if already have a final DAG
        if ctx.final_dag or ctx.early_exit:
            logger.debug("Skipping tool planner: early_exit or final_dag set")
            return ctx

        # Only process MODERATE tasks (routing_action == "continue")
        routing_action = ctx.metadata.get("routing_action")
        if routing_action and routing_action != "continue":
            logger.debug(f"Skipping tool planner: routing_action={routing_action}")
            return ctx

        # Generate tool DAG
        logger.debug(f"Tool planner processing: {ctx.goal[:50]}...")

        dag = await self.planner.plan(ctx.goal, ctx.conversation_context)

        ctx.llm_dag = dag
        ctx.final_dag = dag
        ctx.metadata["tool_planner_used"] = True

        logger.info(f"Tool planner generated DAG with {len(dag.nodes)} tasks")

        return ctx


# =============================================================================
# Utility Functions
# =============================================================================


def get_prompt_size() -> int:
    """Get the size of the tool planner prompt template.

    Returns:
        Character count of TOOL_DAG_PROMPT (excluding {goal}).
    """
    # Remove the {goal} placeholder for accurate size
    template = TOOL_DAG_PROMPT.replace("{goal}", "")
    return len(template)


def validate_prompt_size(max_chars: int = 500) -> bool:
    """Validate that prompt template is under limit.

    Args:
        max_chars: Maximum allowed characters.

    Returns:
        True if prompt is under limit.
    """
    size = get_prompt_size()
    return size <= max_chars
