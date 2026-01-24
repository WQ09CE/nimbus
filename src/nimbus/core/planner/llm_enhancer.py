"""LLM-based planning enhancement stage.

This module provides the LLM-based planning stage that can generate
or enhance DAGs using an LLM. It wraps the existing DAGPlanner logic.
"""

import json
import re
import uuid
from typing import Protocol, Set, Optional, Dict, Any, List

from ..types import TaskDAG, TaskNode, TaskSource
from ..logging import get_logger
from .protocol import PlannerStage, PlanningContext, PlanningMode
from .validator import DAGValidator, ValidationResult

logger = get_logger("planner.llm")


class LLMClient(Protocol):
    """Protocol for LLM client interface."""

    async def complete(self, prompt: str) -> str:
        """Generate completion for the given prompt."""
        ...


# LLM Planning prompt
LLM_PLANNING_PROMPT = """你是一个任务规划器。根据用户目标，生成一个可并行执行的任务 DAG（有向无环图）。

【重要】只输出 JSON，不要输出任何其他内容。

## 可用技能
{skills}

## 上下文
{context}

## 用户目标
{goal}

{existing_plan_section}

## 输出格式

简单对话（问候、闲聊、不需要技能）:
{{"mode": "direct", "response": "你的回复"}}

需要执行技能的任务:
{{
  "mode": "dag",
  "tasks": [
    {{"id": "t1", "skill": "技能名", "params": {{参数}}, "depends_on": []}},
    {{"id": "t2", "skill": "技能名", "params": {{参数}}, "depends_on": ["t1"]}}
  ]
}}

## 规则
1. 如果任务可以并行执行，depends_on 设为空数组 []
2. 每个任务只能依赖 id 比自己小的任务
3. skill 必须从可用技能列表中选择
4. id 必须唯一，建议使用 t1, t2, t3...

JSON:"""

EXISTING_PLAN_SECTION = """
## 已有规则计划
以下是规则引擎生成的初步计划，请评估是否需要增强或修改：
{rule_plan}

如果规则计划已经完整，直接返回 {{"mode": "continue"}}
如果需要增强或修改，返回完整的新计划。
"""


class LLMEnhancer:
    """LLM-based planning stage - wraps existing DAGPlanner logic.

    This stage uses an LLM to generate or enhance execution plans.
    It can work in three modes:
    1. Full planning: Generate entire DAG from scratch
    2. Enhancement: Enhance an existing rule-based DAG
    3. Skip: If rule DAG is complete, skip LLM call

    Example:
        ```python
        enhancer = LLMEnhancer(llm_client)
        ctx = PlanningContext(goal="...", ...)

        ctx = await enhancer.process(ctx)
        if ctx.llm_dag:
            # LLM generated or enhanced a DAG
            pass
        ```
    """

    def __init__(
        self,
        llm_client: LLMClient,
        validator: Optional[DAGValidator] = None,
    ):
        """Initialize the LLM enhancer.

        Args:
            llm_client: LLM client for generating completions.
            validator: Optional DAG validator for validating LLM output.
        """
        self.llm_client = llm_client
        self.validator = validator or DAGValidator()

    @property
    def name(self) -> str:
        """Stage name for logging/tracing."""
        return "llm_enhancer"

    async def process(self, ctx: PlanningContext) -> PlanningContext:
        """Generate or enhance DAG using LLM.

        Processing logic:
        1. If mode is RULE_ONLY, skip LLM call
        2. If rule_dag exists and is complete, optionally skip
        3. If rule_dag is partial, ask LLM to enhance
        4. If no rule_dag, ask LLM for full planning

        Args:
            ctx: The planning context.

        Returns:
            Updated planning context with llm_dag set.
        """
        # Skip if rule-only mode
        if ctx.planning_mode == PlanningMode.RULE_ONLY:
            logger.debug("Skipping LLM: rule_only mode")
            return ctx

        # Skip if we already have early_exit set
        if ctx.early_exit:
            logger.debug("Skipping LLM: early_exit flag set")
            return ctx

        # Determine if we should skip LLM
        if ctx.rule_dag and self._is_complete_plan(ctx.rule_dag, ctx):
            logger.debug("Skipping LLM: rule DAG is complete")
            ctx.llm_dag = ctx.rule_dag
            ctx.final_dag = ctx.rule_dag
            return ctx

        # Build prompt
        prompt = self._build_prompt(ctx)

        try:
            logger.debug(f"Invoking LLM for goal: {ctx.goal[:50]}...")
            response = await self.llm_client.complete(prompt)

            # Parse LLM response
            dag = self._parse_response(response, ctx)

            if dag:
                # Mark all tasks as LLM-generated
                for node in dag.nodes.values():
                    if node.source == TaskSource.RULE:
                        node.source = TaskSource.LLM

                # Validate DAG
                if ctx.skill_whitelist:
                    self.validator.skill_whitelist = ctx.skill_whitelist

                result = self.validator.validate(dag)
                if not result.valid:
                    if result.repaired_dag:
                        dag = result.repaired_dag
                        ctx.warnings.extend(result.warnings)
                    else:
                        ctx.errors.extend(result.errors)
                        logger.warning(f"LLM DAG validation failed: {result.errors}")
                        # Fallback to rule DAG if available
                        if ctx.rule_dag:
                            dag = ctx.rule_dag
                        else:
                            dag = self._create_fallback_dag(ctx)

                ctx.llm_dag = dag
                ctx.final_dag = dag
                logger.info(f"LLM generated DAG with {len(dag.nodes)} tasks")

        except Exception as e:
            logger.error(f"LLM planning failed: {e}")
            ctx.add_error(f"LLM planning failed: {str(e)}")

            # Fallback
            if ctx.rule_dag:
                ctx.final_dag = ctx.rule_dag
            else:
                ctx.final_dag = self._create_fallback_dag(ctx)

        return ctx

    def _build_prompt(self, ctx: PlanningContext) -> str:
        """Build the LLM prompt.

        Args:
            ctx: Planning context.

        Returns:
            Formatted prompt string.
        """
        skills_desc = self._format_skills(ctx.available_skills)

        existing_plan_section = ""
        if ctx.rule_dag and ctx.planning_mode == PlanningMode.HYBRID:
            # Include existing rule plan for enhancement
            rule_plan_summary = self._summarize_dag(ctx.rule_dag)
            existing_plan_section = EXISTING_PLAN_SECTION.format(
                rule_plan=rule_plan_summary
            )

        return LLM_PLANNING_PROMPT.format(
            skills=skills_desc,
            context=ctx.conversation_context or "无上下文",
            goal=ctx.goal,
            existing_plan_section=existing_plan_section,
        )

    def _format_skills(self, skills: Set[str]) -> str:
        """Format available skills for the prompt."""
        if not skills:
            return "chat (默认对话)"
        return ", ".join(sorted(skills))

    def _summarize_dag(self, dag: TaskDAG) -> str:
        """Create a summary of a DAG for the LLM.

        Args:
            dag: The DAG to summarize.

        Returns:
            Human-readable summary.
        """
        lines = []
        for node in dag.nodes.values():
            deps = f" (依赖: {', '.join(node.depends_on)})" if node.depends_on else ""
            lines.append(f"- {node.id}: {node.skill} {node.params}{deps}")
        return "\n".join(lines)

    def _is_complete_plan(self, dag: TaskDAG, ctx: PlanningContext) -> bool:
        """Check if a rule DAG is complete (doesn't need LLM enhancement).

        Args:
            dag: The DAG to check.
            ctx: Planning context.

        Returns:
            True if the DAG is complete.
        """
        # A direct response is always complete
        if len(dag.nodes) == 1:
            node = list(dag.nodes.values())[0]
            if node.skill == "chat" and "message" in node.params:
                return True

        # If hybrid mode, never consider complete (always try LLM)
        if ctx.planning_mode == PlanningMode.HYBRID:
            return False

        # For LLM_FULL mode, rule DAG is just a starting point
        return False

    def _parse_response(
        self,
        response: str,
        ctx: PlanningContext,
    ) -> Optional[TaskDAG]:
        """Parse LLM response into a TaskDAG.

        Args:
            response: Raw LLM response text.
            ctx: Planning context.

        Returns:
            Parsed TaskDAG or None if parsing failed.
        """
        try:
            data = self._extract_json(response)

            # Check for "continue" mode (keep existing plan)
            if data.get("mode") == "continue":
                if ctx.rule_dag:
                    return ctx.rule_dag
                return None

            # Direct response
            if data.get("mode") == "direct":
                direct_text = data.get("response", "")
                return TaskDAG.create_simple("chat", {"message": direct_text})

            # DAG mode
            tasks = []
            for task_data in data.get("tasks", []):
                task = {
                    "id": task_data.get("id", f"t{len(tasks)+1}"),
                    "skill": task_data.get("skill", "chat"),
                    "params": task_data.get("params", {}),
                    "depends_on": task_data.get("depends_on", []),
                    "is_checkpoint": task_data.get("is_checkpoint", False),
                    "source": TaskSource.LLM.value,
                }
                tasks.append(task)

            if not tasks:
                return None

            # Auto-mark checkpoints
            self._auto_mark_checkpoints(tasks)

            return TaskDAG.create(ctx.goal, tasks)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse LLM response: {e}")
            return None

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON from text that may contain other content.

        Args:
            text: Text potentially containing JSON.

        Returns:
            Parsed JSON as dictionary.

        Raises:
            json.JSONDecodeError: If no valid JSON found.
        """
        text = text.strip()

        # Try direct parse
        if text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass

        # Try markdown code block
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find JSON object
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        raise json.JSONDecodeError("No JSON found", text, 0)

    def _auto_mark_checkpoints(self, tasks: List[Dict[str, Any]]) -> None:
        """Auto-mark tasks as checkpoints based on heuristics.

        Args:
            tasks: List of task dictionaries to modify in-place.
        """
        checkpoint_skills = {"search", "web_search", "rag_search"}

        # Build dependency count
        downstream_count: Dict[str, int] = {}
        for task in tasks:
            for dep_id in task.get("depends_on", []):
                downstream_count[dep_id] = downstream_count.get(dep_id, 0) + 1

        for task in tasks:
            task_id = task.get("id", "")
            skill = task.get("skill", "")

            if skill in checkpoint_skills:
                task["is_checkpoint"] = True
            elif downstream_count.get(task_id, 0) >= 2:
                task["is_checkpoint"] = True

    def _create_fallback_dag(self, ctx: PlanningContext) -> TaskDAG:
        """Create a fallback DAG when planning fails.

        Args:
            ctx: Planning context.

        Returns:
            Simple chat DAG as fallback.
        """
        return TaskDAG.create_simple("chat", {"message": ctx.goal})
