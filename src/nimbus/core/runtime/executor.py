"""Async Runtime for parallel DAG execution.

This module provides the AsyncRuntime class which executes TaskDAGs
in parallel with support for:

- Parallel task execution with concurrency control
- Timeout and retry handling
- Checkpoint persistence
- Tool registry integration
- Optional ReplanCoordinator for dynamic replanning

Example:
    ```python
    runtime = AsyncRuntime(
        skills={"search": search_skill, "summarize": summarize_skill},
        config=RuntimeConfig(max_concurrent=5),
    )

    result = await runtime.execute_dag(dag)
    print(f"Completed: {result.stats.completed}")
    ```
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Coroutine, Dict, Optional, TYPE_CHECKING

from ..types import (
    TaskDAG,
    TaskNode,
    TaskStatus,
    RuntimeConfig,
    ExecutionResult,
    ExecutionStats,
)
from ..logging import get_agent_logger
from .cancellation import CancellationToken
from .coordinator import ReplanCoordinator

if TYPE_CHECKING:
    from ..checkpoint import CheckpointSaver
    from nimbus.tools import ToolRegistry

# Type alias for skill functions
SkillFunc = Callable[..., Coroutine[Any, Any, Any]]


class AsyncRuntime:
    """Executes TaskDAG in parallel with timeout and retry support.

    Supports optional checkpoint persistence for durable execution.
    Also supports ToolRegistry for code exploration tools (Read, Glob, Grep).
    Optionally integrates with ReplanCoordinator for dynamic replanning.

    Attributes:
        skills: Dictionary mapping skill names to async functions.
        config: Runtime configuration (timeout, retries, etc.).
        checkpointer: Optional checkpoint saver for durable execution.
        tool_registry: Optional tool registry for code tools.
        workspace: Workspace directory for tool sandbox validation.
        coordinator: Optional replan coordinator for dynamic replanning.

    Example:
        ```python
        # Basic usage
        runtime = AsyncRuntime(skills={"chat": chat_skill})
        result = await runtime.execute_dag(dag)

        # With coordinator for replanning
        coordinator = ReplanCoordinator()
        runtime = AsyncRuntime(
            skills={"search": search_skill},
            coordinator=coordinator,
        )
        ```
    """

    def __init__(
        self,
        skills: Optional[Dict[str, SkillFunc]] = None,
        config: Optional[RuntimeConfig] = None,
        checkpointer: Optional["CheckpointSaver"] = None,
        tool_registry: Optional["ToolRegistry"] = None,
        workspace: Optional[Path] = None,
        coordinator: Optional[ReplanCoordinator] = None,
    ):
        """Initialize async runtime.

        Args:
            skills: Dictionary mapping skill names to async functions.
            config: Runtime configuration (timeout, retries, etc.).
            checkpointer: Optional checkpoint saver for durable execution.
            tool_registry: Optional tool registry for code tools (Read, Glob, Grep).
            workspace: Optional workspace directory for tool sandbox validation.
            coordinator: Optional replan coordinator for dynamic replanning.
        """
        self.skills: Dict[str, SkillFunc] = skills or {}
        self.config = config or RuntimeConfig()
        self.checkpointer = checkpointer
        self.tool_registry = tool_registry
        self.workspace = workspace or Path.cwd()
        self.coordinator = coordinator
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._task_tokens: Dict[str, CancellationToken] = {}

    def register_skill(self, name: str, func: SkillFunc) -> None:
        """Register a skill function.

        Args:
            name: Skill name for routing.
            func: Async function implementing the skill.
        """
        self.skills[name] = func

    def get_skill_names(self) -> set:
        """Get set of registered skill names.

        Includes both registered skills and tools from the tool registry.
        """
        names = set(self.skills.keys())
        if self.tool_registry:
            names.update(self.tool_registry.list_tools())
        return names

    async def execute_dag(
        self,
        dag: TaskDAG,
        resume: bool = True,
    ) -> ExecutionResult:
        """Execute entire DAG with parallel task execution.

        Handles edge cases:
        - Empty DAG (no tasks)
        - All tasks failing
        - Partial failures with graceful degradation

        When a coordinator is configured, tasks are registered for
        potential cancellation during replanning.

        Args:
            dag: TaskDAG to execute.
            resume: If True and checkpointer is configured, attempt to resume
                    from the latest checkpoint for this DAG.

        Returns:
            ExecutionResult with all results and statistics.
        """
        start_time = datetime.now()
        log = get_agent_logger("runtime", task_id=dag.id)

        # Try to resume from checkpoint if enabled
        if resume and self.checkpointer:
            checkpoint = self.checkpointer.load(dag.id)
            if checkpoint:
                completed_count = checkpoint.completed_count
                log.info(
                    f"Resumed from checkpoint, skipping {completed_count} completed nodes"
                )
                dag = checkpoint

        # Handle empty DAG edge case
        if len(dag.nodes) == 0:
            log.warning("Empty DAG received, returning immediately")
            return ExecutionResult(
                dag_id=dag.id,
                status="success",
                results={},
                errors={},
                duration_ms=0,
                stats=ExecutionStats(
                    total_tasks=0,
                    completed=0,
                    failed=0,
                    skipped=0,
                    total_duration_ms=0,
                    parallel_efficiency=1.0,
                ),
            )

        # Initialize semaphore for concurrency control
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)

        log.info(
            f"Starting DAG execution: {dag.goal[:50]}...",
            extra={"total_tasks": len(dag.nodes)},
        )

        # Execute until all tasks are in terminal state
        iteration_count = 0
        max_iterations = len(dag.nodes) * 10  # Safety limit

        while not dag.is_completed():
            iteration_count += 1

            # Safety check to prevent infinite loops
            if iteration_count > max_iterations:
                log.error(
                    f"DAG execution exceeded max iterations ({max_iterations}), "
                    "marking remaining tasks as failed"
                )
                for node in dag.nodes.values():
                    if node.status == TaskStatus.PENDING:
                        node.status = TaskStatus.FAILED
                        node.error = "Execution timeout: max iterations exceeded"
                break

            # Check if coordinator has paused scheduling
            if self.coordinator and self.coordinator.is_paused():
                await asyncio.sleep(0.1)
                continue

            ready_tasks = dag.get_ready_tasks()

            if not ready_tasks:
                # Check if there are still running tasks
                running_count = sum(
                    1 for n in dag.nodes.values()
                    if n.status == TaskStatus.RUNNING
                )
                if running_count == 0 and not dag.is_completed():
                    # Deadlock detected - no ready tasks and no running tasks
                    log.error("Deadlock detected in DAG execution")
                    for node in dag.nodes.values():
                        if node.status == TaskStatus.PENDING:
                            node.status = TaskStatus.FAILED
                            node.error = "Deadlock: unreachable due to missing dependencies"
                    break

                # No ready tasks but not completed - wait for running tasks
                await asyncio.sleep(0.05)
                continue

            # Execute ready tasks in parallel
            log.debug(f"Executing {len(ready_tasks)} ready tasks in parallel")

            async with asyncio.TaskGroup() as tg:
                for task_node in ready_tasks:
                    tg.create_task(self._execute_task(task_node, dag))

        # Calculate results
        end_time = datetime.now()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Collect statistics
        completed = sum(1 for n in dag.nodes.values() if n.status == TaskStatus.COMPLETED)
        failed = sum(1 for n in dag.nodes.values() if n.status == TaskStatus.FAILED)
        skipped = sum(1 for n in dag.nodes.values() if n.status == TaskStatus.SKIPPED)

        # Calculate parallel efficiency
        serial_time = sum(n.duration_ms or 0 for n in dag.nodes.values())
        efficiency = serial_time / duration_ms if duration_ms > 0 else 0.0

        stats = ExecutionStats(
            total_tasks=len(dag.nodes),
            completed=completed,
            failed=failed,
            skipped=skipped,
            total_duration_ms=duration_ms,
            parallel_efficiency=efficiency,
        )

        # Determine overall status
        if failed == 0 and skipped == 0:
            status = "success"
        elif completed > 0:
            status = "partial"
        else:
            status = "failed"

        log.info(
            f"DAG execution completed: status={status}, "
            f"completed={completed}, failed={failed}, skipped={skipped}, "
            f"duration={duration_ms}ms"
        )

        return ExecutionResult(
            dag_id=dag.id,
            status=status,
            results=dag.get_results(),
            errors=dag.get_errors(),
            duration_ms=duration_ms,
            stats=stats,
        )

    async def _execute_task(self, task: TaskNode, dag: TaskDAG) -> None:
        """Execute a single task with retry support.

        When a coordinator is configured, registers the task for
        potential cancellation and checks the cancel token periodically.

        Args:
            task: TaskNode to execute.
            dag: Parent DAG (for marking downstream on failure).
        """
        log = get_agent_logger("runtime", task_id=task.id)

        # Create cancellation token for this task
        cancel_token = CancellationToken()
        self._task_tokens[task.id] = cancel_token

        # Register with coordinator if available
        current_async_task: Optional[asyncio.Task[Any]] = None
        if self.coordinator:
            current_async_task = asyncio.current_task()
            if current_async_task:
                self.coordinator.register_task(task.id, current_async_task, cancel_token)

        try:
            async with self._semaphore:  # type: ignore
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now()

                log.info(f"Task started: skill={task.skill}")

                for attempt in range(self.config.max_retries + 1):
                    # Check for cancellation before each attempt
                    if cancel_token.is_cancelled():
                        task.status = TaskStatus.FAILED
                        task.error = f"Cancelled: {cancel_token.reason}"
                        task.finished_at = datetime.now()
                        log.info(f"Task cancelled: {cancel_token.reason}")
                        return

                    try:
                        result = await self._execute_with_timeout(task, cancel_token)

                        # Check cancellation after execution
                        if cancel_token.is_cancelled():
                            task.status = TaskStatus.FAILED
                            task.error = f"Cancelled: {cancel_token.reason}"
                            task.finished_at = datetime.now()
                            return

                        task.status = TaskStatus.COMPLETED
                        task.result = result
                        task.finished_at = datetime.now()

                        log.success(
                            f"Task completed: skill={task.skill}, "
                            f"duration={task.duration_ms}ms"
                        )

                        # Save checkpoint after successful completion
                        self._save_checkpoint(dag, log)
                        return

                    except asyncio.CancelledError:
                        # Handle asyncio cancellation
                        task.status = TaskStatus.FAILED
                        task.error = "Cancelled by coordinator"
                        task.finished_at = datetime.now()
                        raise

                    except asyncio.TimeoutError:
                        error_msg = f"Timeout after {self.config.default_timeout}s"
                        log.warning(f"Task timeout (attempt {attempt + 1}): {error_msg}")

                        if attempt < self.config.max_retries:
                            await asyncio.sleep(self.config.retry_delay)
                            continue

                        task.status = TaskStatus.FAILED
                        task.error = error_msg
                        task.finished_at = datetime.now()

                    except Exception as e:
                        error_msg = str(e)
                        log.warning(
                            f"Task error (attempt {attempt + 1}): {error_msg}"
                        )

                        if attempt < self.config.max_retries and self._is_retryable(e):
                            await asyncio.sleep(self.config.retry_delay)
                            continue

                        task.status = TaskStatus.FAILED
                        task.error = error_msg
                        task.finished_at = datetime.now()

                # Task failed - mark downstream as skipped
                if task.status == TaskStatus.FAILED:
                    log.error(f"Task failed: skill={task.skill}, error={task.error}")
                    dag.mark_downstream_skipped(task.id)
                    # Save checkpoint after failure too
                    self._save_checkpoint(dag, log)

        finally:
            # Clean up
            self._task_tokens.pop(task.id, None)
            if self.coordinator:
                self.coordinator.unregister_task(task.id)

    def _save_checkpoint(self, dag: TaskDAG, log: Any) -> None:
        """Save checkpoint if checkpointer is configured.

        Args:
            dag: The DAG to checkpoint.
            log: Logger instance.
        """
        if self.checkpointer:
            try:
                checkpoint_id = self.checkpointer.save(dag)
                log.debug(f"Checkpoint saved: {checkpoint_id}")
            except Exception as e:
                log.warning(f"Failed to save checkpoint: {e}")

    async def _execute_with_timeout(
        self,
        task: TaskNode,
        cancel_token: Optional[CancellationToken] = None,
    ) -> Any:
        """Execute task with timeout.

        First checks if the task is a registered tool, then falls back to skills.

        Args:
            task: TaskNode to execute.
            cancel_token: Optional cancellation token for cooperative cancellation.

        Returns:
            Result from tool or skill execution.

        Raises:
            asyncio.TimeoutError: If execution exceeds timeout.
            ValueError: If neither tool nor skill is registered.
        """
        # First check if it's a tool
        if self.tool_registry and task.skill in self.tool_registry:
            return await asyncio.wait_for(
                self.tool_registry.execute(
                    task.skill,
                    task.params,
                    workspace=self.workspace,
                ),
                timeout=self.config.default_timeout,
            )

        # Otherwise check skills
        skill_func = self.skills.get(task.skill)
        if not skill_func:
            raise ValueError(f"Unknown skill or tool: {task.skill}")

        return await asyncio.wait_for(
            skill_func(**task.params),
            timeout=self.config.default_timeout,
        )

    def _is_retryable(self, error: Exception) -> bool:
        """Check if an error is retryable.

        Args:
            error: Exception that occurred.

        Returns:
            True if the error should trigger a retry.
        """
        retryable_types = (
            asyncio.TimeoutError,
            ConnectionError,
            OSError,
        )
        return isinstance(error, retryable_types)

    def get_cancel_token(self, task_id: str) -> Optional[CancellationToken]:
        """Get the cancellation token for a running task.

        Args:
            task_id: ID of the task.

        Returns:
            CancellationToken if task is running, None otherwise.
        """
        return self._task_tokens.get(task_id)

    async def execute_stream(
        self, dag: TaskDAG
    ) -> AsyncIterator[Dict[str, Any]]:
        """Execute DAG with streaming status updates.

        Args:
            dag: TaskDAG to execute.

        Yields:
            Status dicts for UI consumption.
        """
        yield {
            "type": "dag_start",
            "dag_id": dag.id,
            "goal": dag.goal,
            "total_tasks": len(dag.nodes),
        }

        # Track which tasks we've reported
        reported_started: set[str] = set()
        reported_completed: set[str] = set()

        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)

        # Create task execution futures
        pending_futures: Dict[str, asyncio.Task[Any]] = {}

        while not dag.is_completed():
            # Check if coordinator has paused scheduling
            if self.coordinator and self.coordinator.is_paused():
                await asyncio.sleep(0.1)
                continue

            # Start ready tasks
            ready_tasks = dag.get_ready_tasks()

            for task in ready_tasks:
                if task.id not in pending_futures and task.id not in reported_started:
                    reported_started.add(task.id)
                    yield {
                        "type": "task_start",
                        "task_id": task.id,
                        "skill": task.skill,
                        "params": task.params,
                    }
                    future = asyncio.create_task(self._execute_task(task, dag))
                    pending_futures[task.id] = future

            # Wait a bit for tasks to complete
            if pending_futures:
                await asyncio.sleep(0.01)

            # Check for completed tasks
            completed_ids = []
            for task_id, future in pending_futures.items():
                if future.done():
                    completed_ids.append(task_id)

            # Report completed tasks
            for task_id in completed_ids:
                del pending_futures[task_id]
                task = dag.nodes[task_id]

                if task_id not in reported_completed:
                    reported_completed.add(task_id)

                    if task.status == TaskStatus.COMPLETED:
                        yield {
                            "type": "task_done",
                            "task_id": task_id,
                            "skill": task.skill,
                            "result": task.result,
                            "duration_ms": task.duration_ms,
                        }
                    elif task.status == TaskStatus.FAILED:
                        yield {
                            "type": "task_failed",
                            "task_id": task_id,
                            "skill": task.skill,
                            "error": task.error,
                        }

            # Report skipped tasks
            for task in dag.nodes.values():
                if task.status == TaskStatus.SKIPPED and task.id not in reported_completed:
                    reported_completed.add(task.id)
                    yield {
                        "type": "task_skipped",
                        "task_id": task.id,
                        "skill": task.skill,
                        "reason": task.error,
                    }

        # Final summary
        completed = sum(1 for n in dag.nodes.values() if n.status == TaskStatus.COMPLETED)
        failed = sum(1 for n in dag.nodes.values() if n.status == TaskStatus.FAILED)
        skipped = sum(1 for n in dag.nodes.values() if n.status == TaskStatus.SKIPPED)

        yield {
            "type": "dag_complete",
            "dag_id": dag.id,
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "results": dag.get_results(),
        }
