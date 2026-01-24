"""Simple executor for running planned tasks."""

from typing import Any, AsyncIterator, Callable, Coroutine, Dict, List, Optional

from .types import Plan, Task

# Type alias for skill functions
SkillFunc = Callable[..., Coroutine[Any, Any, Any]]


class ExecutionStatus:
    """Status update during execution."""

    def __init__(self, task_id: str, status: str, result: Any = None):
        self.task_id = task_id
        self.status = status  # "started", "completed", "failed"
        self.result = result


class SimpleExecutor:
    """Executes plans by running tasks through skills."""

    def __init__(self, skills: Optional[Dict[str, SkillFunc]] = None):
        """Initialize executor with optional skill registry.

        Args:
            skills: Dictionary mapping skill names to async functions.
        """
        self.skills: Dict[str, SkillFunc] = skills or {}

    def register_skill(self, name: str, func: SkillFunc) -> None:
        """Register a skill function.

        Args:
            name: Skill name for routing.
            func: Async function implementing the skill.
        """
        self.skills[name] = func

    def get_skill_names(self) -> List[str]:
        """Get list of registered skill names."""
        return list(self.skills.keys())

    async def execute(self, plan: Plan) -> List[Any]:
        """Execute a plan and return results.

        Args:
            plan: Plan containing tasks to execute.

        Returns:
            List of results from each task.
        """
        results = []
        async for status in self.execute_with_status(plan):
            if status.status == "completed":
                results.append(status.result)
            elif status.status == "failed":
                results.append(None)
        return results

    async def execute_with_status(
        self, plan: Plan
    ) -> AsyncIterator[ExecutionStatus]:
        """Execute plan with real-time status updates.

        Args:
            plan: Plan containing tasks to execute.

        Yields:
            ExecutionStatus updates for each task.
        """
        if plan.is_direct():
            return

        for task in plan.tasks:
            yield ExecutionStatus(task.id, "started")

            try:
                result = await self._execute_task(task)
                task.result = result
                yield ExecutionStatus(task.id, "completed", result)
            except Exception as e:
                task.error = str(e)
                yield ExecutionStatus(task.id, "failed", str(e))

    async def execute_stream(
        self, plan: Plan
    ) -> AsyncIterator[Dict[str, Any]]:
        """Execute plan with streaming status updates.

        Yields structured status dicts for UI consumption.

        Args:
            plan: Plan containing tasks to execute.

        Yields:
            Status dicts with type and content fields.
        """
        if plan.is_direct():
            yield {
                "type": "direct",
                "content": plan.direct_response or "",
            }
            return

        for task in plan.tasks:
            yield {
                "type": "task_start",
                "task_id": task.id,
                "skill": task.skill,
                "params": task.params,
            }

            try:
                result = await self._execute_task(task)
                task.result = result
                yield {
                    "type": "task_done",
                    "task_id": task.id,
                    "skill": task.skill,
                    "result": result,
                }
            except Exception as e:
                task.error = str(e)
                yield {
                    "type": "error",
                    "task_id": task.id,
                    "skill": task.skill,
                    "error": str(e),
                }

    async def _execute_task(self, task: Task) -> Any:
        """Execute a single task.

        Args:
            task: Task to execute.

        Returns:
            Result from the skill function.

        Raises:
            ValueError: If skill is not registered.
        """
        skill_func = self.skills.get(task.skill)
        if not skill_func:
            raise ValueError(f"Unknown skill: {task.skill}")

        return await skill_func(**task.params)
