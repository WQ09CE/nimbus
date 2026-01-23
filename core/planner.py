"""Planners for creating execution plans using LLM.

This module provides:
- SimplePlanner: Basic sequential planning
- DAGPlanner: Parallel task DAG planning
- AdaptivePlanner: Dynamic re-planning based on execution results
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Set

from .types import Plan, Task, TaskType, TaskDAG, TaskNode, TaskStatus
from .logging import get_logger

logger = get_logger("planner")


class LLMClient(Protocol):
    """Protocol for LLM client interface."""

    async def complete(self, prompt: str) -> str:
        """Generate completion for the given prompt."""
        ...


PLANNING_PROMPT = """你是一个任务规划器。根据用户目标，输出 JSON 格式的执行计划。

【重要】只输出 JSON，不要输出任何其他内容，不要有解释或说明。

可用技能: {skills}

上下文:
{context}

用户目标: {goal}

【输出格式】

简单对话（问候、闲聊、不需要技能）:
{{"mode": "direct", "response": "你的回复"}}

需要执行技能的任务:
{{"mode": "multi_step", "tasks": [{{"type": "类型", "skill": "技能名", "params": {{参数}}}}]}}

【示例1】用户说"你好"
{{"mode": "direct", "response": "你好！有什么可以帮你的吗？"}}

【示例2】用户说"搜索 Python 教程"
{{"mode": "multi_step", "tasks": [{{"type": "search", "skill": "search", "params": {{"query": "Python 教程"}}}}]}}

【示例3】用户说"总结这段文字"
{{"mode": "multi_step", "tasks": [{{"type": "analyze", "skill": "summarize", "params": {{"text": "待总结文本"}}}}]}}

JSON:"""


class SimplePlanner:
    """Plans task execution using LLM."""

    def __init__(self, llm_client: LLMClient):
        """Initialize planner with LLM client.

        Args:
            llm_client: Client with async complete(prompt) method.
        """
        self.llm_client = llm_client

    async def create_plan(
        self,
        goal: str,
        context: str,
        available_skills: List[str],
    ) -> Plan:
        """Create an execution plan for the given goal.

        Args:
            goal: User's input/goal.
            context: Conversation context.
            available_skills: List of available skill names.

        Returns:
            Plan with either direct response or tasks to execute.
        """
        prompt = PLANNING_PROMPT.format(
            skills=", ".join(available_skills) if available_skills else "chat",
            context=context or "No prior context.",
            goal=goal,
        )

        response = await self.llm_client.complete(prompt)
        return self._parse_response(response)

    def _parse_response(self, response: str) -> Plan:
        """Parse LLM response into a Plan.

        Args:
            response: Raw LLM response text.

        Returns:
            Parsed Plan object.
        """
        try:
            data = self._extract_json(response)

            if data.get("mode") == "direct":
                return Plan.direct(data.get("response", ""))

            # Multi-step mode
            tasks = []
            for i, task_data in enumerate(data.get("tasks", [])):
                task_type = TaskType(task_data.get("type", "chat"))
                task = Task(
                    id=f"task_{uuid.uuid4().hex[:8]}",
                    type=task_type,
                    skill=task_data.get("skill", "chat"),
                    params=task_data.get("params", {}),
                )
                tasks.append(task)

            if not tasks:
                # Fallback to direct if no tasks parsed
                return Plan.direct("I'm not sure how to help with that.")

            return Plan.multi_step(tasks)

        except (json.JSONDecodeError, KeyError, ValueError):
            # Fallback: treat response as direct answer
            return Plan.direct(response.strip())

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

        # Try direct parse first
        if text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Try to fix and parse
                fixed = self._fix_json(text)
                if fixed:
                    return fixed

        # Try to find JSON in markdown code block
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except json.JSONDecodeError:
                fixed = self._fix_json(code_block.group(1))
                if fixed:
                    return fixed

        # Try to find nested JSON object (handles arrays inside)
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                fixed = self._fix_json(json_match.group())
                if fixed:
                    return fixed

        raise json.JSONDecodeError("No JSON found", text, 0)

    def _fix_json(self, text: str) -> Optional[Dict[str, Any]]:
        """Attempt to fix common JSON formatting issues.

        Args:
            text: Potentially malformed JSON string.

        Returns:
            Parsed JSON dict if fixable, None otherwise.
        """
        fixed = text.strip()

        # Remove trailing content after the JSON object
        brace_count = 0
        end_pos = 0
        for i, c in enumerate(fixed):
            if c == "{":
                brace_count += 1
            elif c == "}":
                brace_count -= 1
                if brace_count == 0:
                    end_pos = i + 1
                    break
        if end_pos > 0:
            fixed = fixed[:end_pos]

        # Fix trailing commas before ] or }
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)

        # Fix missing quotes around keys (simple cases)
        fixed = re.sub(r"(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:", r'\1"\2":', fixed)

        # Fix single quotes to double quotes
        # Only if no double quotes are present in values
        if "'" in fixed and fixed.count('"') < fixed.count("'"):
            fixed = fixed.replace("'", '"')

        # Try to parse the fixed JSON
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return None


# =============================================================================
# DAG Planner (Phase 2)
# =============================================================================

DAG_PLANNING_PROMPT = """你是一个任务规划器。根据用户目标，生成一个可并行执行的任务 DAG（有向无环图）。

【重要】只输出 JSON，不要输出任何其他内容。

## 可用技能
{skills}

## 上下文
{context}

## 用户目标
{goal}

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

## 示例

示例1：用户说"你好"
{{"mode": "direct", "response": "你好！有什么可以帮你的吗？"}}

示例2：用户说"搜索 AI 趋势，然后写个总结"
{{
  "mode": "dag",
  "tasks": [
    {{"id": "t1", "skill": "search", "params": {{"query": "AI 趋势 2025"}}, "depends_on": []}},
    {{"id": "t2", "skill": "summarize", "params": {{"source": "t1"}}, "depends_on": ["t1"]}}
  ]
}}

示例3：用户说"同时搜索 Python 和 Rust 教程"
{{
  "mode": "dag",
  "tasks": [
    {{"id": "t1", "skill": "search", "params": {{"query": "Python 教程"}}, "depends_on": []}},
    {{"id": "t2", "skill": "search", "params": {{"query": "Rust 教程"}}, "depends_on": []}}
  ]
}}

JSON:"""


class DAGPlanner:
    """Plans task execution as a DAG using LLM."""

    def __init__(self, llm_client: LLMClient):
        """Initialize DAG planner with LLM client.

        Args:
            llm_client: Client with async complete(prompt) method.
        """
        self.llm_client = llm_client

    async def create_plan(
        self,
        goal: str,
        context: str,
        available_skills: Set[str],
    ) -> TaskDAG:
        """Create a DAG execution plan for the given goal.

        Args:
            goal: User's input/goal.
            context: Conversation context.
            available_skills: Set of available skill names.

        Returns:
            TaskDAG with tasks and dependencies.
        """
        skills_desc = self._format_skills(available_skills)

        prompt = DAG_PLANNING_PROMPT.format(
            skills=skills_desc,
            context=context or "无上下文",
            goal=goal,
        )

        logger.debug(f"Planning for goal: {goal[:50]}...")

        response = await self.llm_client.complete(prompt)
        dag = self._parse_response(response, goal, available_skills)

        # Validate and fix the DAG
        errors = self.validate_dag(dag, available_skills)
        if errors:
            logger.warning(f"DAG validation errors: {errors}")
            # Fallback to simple chat
            return TaskDAG.create_simple("chat", {"message": goal})

        logger.info(f"Created DAG with {len(dag.nodes)} tasks")
        return dag

    def _format_skills(self, skills: Set[str]) -> str:
        """Format available skills for the prompt."""
        if not skills:
            return "chat (默认对话)"

        return ", ".join(sorted(skills))

    def _parse_response(
        self,
        response: str,
        goal: str,
        available_skills: Set[str],
    ) -> TaskDAG:
        """Parse LLM response into a TaskDAG.

        Args:
            response: Raw LLM response text.
            goal: Original user goal.
            available_skills: Set of available skills.

        Returns:
            Parsed TaskDAG object.
        """
        try:
            data = self._extract_json(response)

            if data.get("mode") == "direct":
                # Direct response - create simple chat DAG
                direct_text = data.get("response", "")
                return TaskDAG.create_simple("chat", {"message": direct_text})

            # DAG mode - parse tasks with dependencies
            tasks = []
            for task_data in data.get("tasks", []):
                task = {
                    "id": task_data.get("id", f"t{len(tasks)+1}"),
                    "skill": task_data.get("skill", "chat"),
                    "params": task_data.get("params", {}),
                    "depends_on": task_data.get("depends_on", []),
                    "is_checkpoint": task_data.get("is_checkpoint", False),
                }
                tasks.append(task)

            if not tasks:
                # Fallback to direct response
                return TaskDAG.create_simple("chat", {"message": goal})

            # Auto-mark checkpoints
            self._auto_mark_checkpoints(tasks)

            return TaskDAG.create(goal, tasks)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse DAG response: {e}")
            # Fallback: return LLM's raw response as direct result
            # (rather than re-processing the goal)
            return TaskDAG.create_simple("chat", {"message": response})

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON from text (reuse SimplePlanner logic)."""
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

    def validate_dag(
        self,
        dag: TaskDAG,
        available_skills: Set[str],
    ) -> List[str]:
        """Validate DAG for correctness.

        Args:
            dag: TaskDAG to validate.
            available_skills: Set of available skill names.

        Returns:
            List of error messages (empty if valid).
        """
        errors = []

        # 1. Check skill existence
        for node in dag.nodes.values():
            if node.skill not in available_skills and node.skill != "chat":
                errors.append(f"Unknown skill '{node.skill}' in task {node.id}")

        # 2. Check dependency existence
        for node in dag.nodes.values():
            for dep_id in node.depends_on:
                if dep_id not in dag.nodes:
                    errors.append(
                        f"Task {node.id} depends on non-existent task {dep_id}"
                    )

        # 3. Check for cycles (topological sort)
        if not self._is_acyclic(dag):
            errors.append("DAG contains a cycle")

        return errors

    def _is_acyclic(self, dag: TaskDAG) -> bool:
        """Check if DAG is acyclic using Kahn's algorithm.

        Args:
            dag: TaskDAG to check.

        Returns:
            True if acyclic, False if contains cycle.
        """
        # Calculate in-degrees
        in_degree = {node_id: 0 for node_id in dag.nodes}
        for node in dag.nodes.values():
            for dep_id in node.depends_on:
                if dep_id in dag.nodes:
                    # dep -> node, so node's in-degree increases
                    pass  # We count the reverse
            # Actually count: this node depends on deps
            # So we need out-edges from deps
        # Rebuild: for each node, count how many depend on it
        out_edges = {node_id: [] for node_id in dag.nodes}
        for node in dag.nodes.values():
            for dep_id in node.depends_on:
                if dep_id in out_edges:
                    out_edges[dep_id].append(node.id)
                    in_degree[node.id] = in_degree.get(node.id, 0) + 1

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

        return visited == len(dag.nodes)

    def _auto_mark_checkpoints(self, tasks: List[Dict[str, Any]]) -> None:
        """Auto-mark tasks as checkpoints based on heuristics.

        Checkpoints trigger re-planning evaluation. Tasks are marked as
        checkpoints if:
        1. They are search-type skills (search, web_search, rag_search)
        2. They have 2+ downstream dependencies

        Args:
            tasks: List of task dictionaries to modify in-place.
        """
        # Skills that should always be checkpoints
        checkpoint_skills = {"search", "web_search", "rag_search"}

        # Build dependency count map (how many tasks depend on each task)
        downstream_count: Dict[str, int] = {}
        for task in tasks:
            for dep_id in task.get("depends_on", []):
                downstream_count[dep_id] = downstream_count.get(dep_id, 0) + 1

        # Mark checkpoints
        for task in tasks:
            task_id = task.get("id", "")
            skill = task.get("skill", "")

            # Rule 1: Search-type skills
            if skill in checkpoint_skills:
                task["is_checkpoint"] = True
                logger.debug(f"Auto-marked {task_id} as checkpoint (search skill)")

            # Rule 2: 2+ downstream dependencies
            elif downstream_count.get(task_id, 0) >= 2:
                task["is_checkpoint"] = True
                logger.debug(f"Auto-marked {task_id} as checkpoint (2+ dependents)")


# =============================================================================
# Re-planning Support (Phase 3)
# =============================================================================

class ReplanningStrategy(Enum):
    """Strategies for when to trigger re-planning."""
    NONE = "none"                    # No re-planning
    ON_FAILURE = "on_failure"        # Re-plan when a task fails
    ON_CHECKPOINT = "on_checkpoint"  # Re-plan at checkpoint tasks
    ALWAYS = "always"                # Re-plan after every task


@dataclass
class ReplanRequest:
    """Request for re-planning based on execution progress.

    Attributes:
        original_goal: The user's original goal/request.
        completed_tasks: Dict mapping task_id to result for completed tasks.
        remaining_tasks: List of task IDs that haven't started yet.
        reason: Reason for re-planning request.
        failed_task_id: ID of failed task (if reason is "task_failed").
        failed_error: Error message from failed task.
        checkpoint_task_id: ID of checkpoint task (if reason is "checkpoint_reached").
        checkpoint_result: Result from checkpoint task.
    """
    original_goal: str
    completed_tasks: Dict[str, Any]
    remaining_tasks: List[str]
    reason: str  # "checkpoint_reached" | "task_failed" | "manual"
    failed_task_id: Optional[str] = None
    failed_error: Optional[str] = None
    checkpoint_task_id: Optional[str] = None
    checkpoint_result: Optional[Any] = None

    def get_context_summary(self) -> str:
        """Generate a context summary for the LLM.

        Returns:
            Formatted string summarizing completed work.
        """
        parts = [f"Original goal: {self.original_goal}"]

        if self.completed_tasks:
            parts.append("\nCompleted tasks:")
            for task_id, result in self.completed_tasks.items():
                result_preview = str(result)[:200] if result else "(no result)"
                parts.append(f"  - {task_id}: {result_preview}")

        if self.remaining_tasks:
            parts.append(f"\nRemaining tasks: {', '.join(self.remaining_tasks)}")

        if self.reason == "task_failed" and self.failed_task_id:
            parts.append(f"\nFailed task: {self.failed_task_id}")
            parts.append(f"Error: {self.failed_error}")

        if self.reason == "checkpoint_reached" and self.checkpoint_task_id:
            parts.append(f"\nCheckpoint reached: {self.checkpoint_task_id}")

        return "\n".join(parts)


REPLAN_PROMPT = """你是一个智能任务规划器，需要根据已完成的任务结果决定是否调整计划。

## 当前状态
{context_summary}

## 可用技能
{skills}

## 决策

分析已完成任务的结果，决定：
1. 继续执行原计划 (mode: "continue")
2. 调整计划 (mode: "replan")

【重要】只输出 JSON，不要有其他内容。

## 输出格式

继续原计划:
{{"mode": "continue", "reason": "原因说明"}}

调整计划（如果搜索结果建议不同方向、发现新信息、或原计划不再适用）:
{{
  "mode": "replan",
  "reason": "调整原因",
  "tasks": [
    {{"id": "t1", "skill": "skill_name", "params": {{}}, "depends_on": []}}
  ]
}}

JSON:"""


class AdaptivePlanner(DAGPlanner):
    """DAG Planner with dynamic re-planning capabilities.

    Extends DAGPlanner to support:
    - Re-planning at checkpoint tasks
    - Re-planning after failures
    - Context-aware plan adjustments

    Example:
        ```python
        planner = AdaptivePlanner(llm_client, strategy=ReplanningStrategy.ON_CHECKPOINT)

        # Initial plan
        dag = await planner.create_plan(goal, context, skills)

        # After search completes, check if re-planning needed
        request = ReplanRequest(
            original_goal=goal,
            completed_tasks={"t1": search_results},
            remaining_tasks=["t2", "t3"],
            reason="checkpoint_reached",
            checkpoint_task_id="t1",
            checkpoint_result=search_results,
        )

        new_dag = await planner.replan(request, context, skills)
        if new_dag:
            # Use new plan
            dag = new_dag
        ```
    """

    def __init__(
        self,
        llm_client: LLMClient,
        strategy: ReplanningStrategy = ReplanningStrategy.ON_CHECKPOINT,
    ):
        """Initialize adaptive planner.

        Args:
            llm_client: LLM client for planning.
            strategy: When to trigger re-planning.
        """
        super().__init__(llm_client)
        self.strategy = strategy

    async def replan(
        self,
        request: ReplanRequest,
        context: str,
        available_skills: Set[str],
    ) -> Optional[TaskDAG]:
        """Evaluate and potentially create a new plan.

        Args:
            request: ReplanRequest with current execution state.
            context: Additional context (conversation history, etc).
            available_skills: Set of available skill names.

        Returns:
            New TaskDAG if re-planning is needed, None to continue.
        """
        # Check if re-planning is enabled for this reason
        if not self._should_replan(request):
            logger.debug(f"Skipping replan: strategy={self.strategy}, reason={request.reason}")
            return None

        logger.info(f"Evaluating replan: reason={request.reason}")

        # Build context summary
        context_summary = request.get_context_summary()
        if context:
            context_summary = f"{context}\n\n{context_summary}"

        # Ask LLM whether to replan
        prompt = REPLAN_PROMPT.format(
            context_summary=context_summary,
            skills=", ".join(sorted(available_skills)),
        )

        response = await self.llm_client.complete(prompt)

        try:
            data = self._extract_json(response)

            if data.get("mode") == "continue":
                logger.info(f"Continuing original plan: {data.get('reason', '')}")
                return None

            if data.get("mode") == "replan":
                logger.info(f"Re-planning: {data.get('reason', '')}")

                # Parse new tasks
                tasks = []
                for task_data in data.get("tasks", []):
                    task = {
                        "id": task_data.get("id", f"t{len(tasks)+1}"),
                        "skill": task_data.get("skill", "chat"),
                        "params": task_data.get("params", {}),
                        "depends_on": task_data.get("depends_on", []),
                        "is_checkpoint": task_data.get("is_checkpoint", False),
                    }
                    tasks.append(task)

                if not tasks:
                    logger.warning("Replan returned no tasks")
                    return None

                # Auto-mark checkpoints
                self._auto_mark_checkpoints(tasks)

                new_dag = TaskDAG.create(request.original_goal, tasks)

                # Validate
                errors = self.validate_dag(new_dag, available_skills)
                if errors:
                    logger.warning(f"Replan DAG validation errors: {errors}")
                    return None

                return new_dag

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse replan response: {e}")

        return None

    def _should_replan(self, request: ReplanRequest) -> bool:
        """Check if re-planning should be attempted.

        Args:
            request: ReplanRequest to evaluate.

        Returns:
            True if re-planning should be attempted.
        """
        if self.strategy == ReplanningStrategy.NONE:
            return False

        if self.strategy == ReplanningStrategy.ALWAYS:
            return True

        if self.strategy == ReplanningStrategy.ON_FAILURE:
            return request.reason == "task_failed"

        if self.strategy == ReplanningStrategy.ON_CHECKPOINT:
            return request.reason in ("checkpoint_reached", "task_failed")

        return False

    def should_evaluate_checkpoint(self, task: TaskNode) -> bool:
        """Check if a completed task should trigger checkpoint evaluation.

        Args:
            task: Completed TaskNode.

        Returns:
            True if checkpoint evaluation should run.
        """
        if self.strategy == ReplanningStrategy.NONE:
            return False

        if self.strategy == ReplanningStrategy.ALWAYS:
            return True

        if self.strategy in (ReplanningStrategy.ON_CHECKPOINT, ReplanningStrategy.ON_FAILURE):
            return task.is_checkpoint

        return False

    def create_checkpoint_request(
        self,
        dag: TaskDAG,
        checkpoint_task: TaskNode,
    ) -> ReplanRequest:
        """Create a ReplanRequest for a checkpoint task.

        Args:
            dag: Current TaskDAG.
            checkpoint_task: The checkpoint task that just completed.

        Returns:
            ReplanRequest for re-planning evaluation.
        """
        # Collect completed tasks
        completed = {
            task_id: node.result
            for task_id, node in dag.nodes.items()
            if node.status == TaskStatus.COMPLETED
        }

        # Find remaining tasks
        remaining = [
            task_id
            for task_id, node in dag.nodes.items()
            if node.status == TaskStatus.PENDING
        ]

        return ReplanRequest(
            original_goal=dag.goal,
            completed_tasks=completed,
            remaining_tasks=remaining,
            reason="checkpoint_reached",
            checkpoint_task_id=checkpoint_task.id,
            checkpoint_result=checkpoint_task.result,
        )

    def create_failure_request(
        self,
        dag: TaskDAG,
        failed_task: TaskNode,
    ) -> ReplanRequest:
        """Create a ReplanRequest for a failed task.

        Args:
            dag: Current TaskDAG.
            failed_task: The task that failed.

        Returns:
            ReplanRequest for re-planning evaluation.
        """
        # Collect completed tasks
        completed = {
            task_id: node.result
            for task_id, node in dag.nodes.items()
            if node.status == TaskStatus.COMPLETED
        }

        # Find remaining tasks (excluding failed and skipped)
        remaining = [
            task_id
            for task_id, node in dag.nodes.items()
            if node.status == TaskStatus.PENDING
        ]

        return ReplanRequest(
            original_goal=dag.goal,
            completed_tasks=completed,
            remaining_tasks=remaining,
            reason="task_failed",
            failed_task_id=failed_task.id,
            failed_error=failed_task.error,
        )
