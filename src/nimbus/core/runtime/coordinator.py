"""Replan coordination with running tasks.

This module provides the ReplanCoordinator class which handles the complex
coordination needed when replanning during task execution:

1. Detecting meaningful plan changes (avoid unnecessary replans)
2. Cancelling conflicting running tasks
3. Merging completed results into new plan
4. Resolving ID conflicts using generation-based naming
5. Recording replan history for debugging

Example:
    ```python
    coordinator = ReplanCoordinator()

    # Register running task
    coordinator.register_task(task_id, async_task, cancel_token)

    # Request replan
    merged_dag = await coordinator.request_replan(
        current_dag=dag,
        new_dag=new_dag,
        trigger="checkpoint",
        trigger_task_id="t1",
    )

    if merged_dag:
        # Use new merged plan
        dag = merged_dag
    ```
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from ..types import TaskDAG, TaskNode, TaskStatus, ReplanRecord
from .cancellation import CancellationToken

if TYPE_CHECKING:
    from ..planner.legacy import AdaptivePlanner


@dataclass
class CoordinatorConfig:
    """Configuration for replan coordinator.

    Attributes:
        cancel_timeout: Max seconds to wait for task cancellation.
        force_cancel_on_timeout: Force cancel tasks that don't respond.
        preserve_completed: Keep completed task results in new DAG.
        preserve_running: Keep running tasks in new DAG (don't cancel).
        require_meaningful_change: Only replan if plan actually changed.
        change_threshold: Minimum change ratio to trigger replan.
    """

    cancel_timeout: float = 5.0
    force_cancel_on_timeout: bool = True
    preserve_completed: bool = True
    preserve_running: bool = False
    require_meaningful_change: bool = True
    change_threshold: float = 0.1


class ReplanCoordinator:
    """Coordinates replanning with running tasks.

    Handles the complex coordination needed during replanning:
    - Detecting meaningful plan changes
    - Cancelling conflicting running tasks
    - Merging completed results into new plan
    - Resolving ID conflicts using generation-based naming

    Attributes:
        config: Configuration for coordinator behavior.

    Example:
        ```python
        coordinator = ReplanCoordinator()

        # During task execution, register tasks
        token = CancellationToken()
        async_task = asyncio.create_task(execute_task())
        coordinator.register_task("t1", async_task, token)

        # When replan is needed
        new_dag = await coordinator.request_replan(
            current_dag=current_dag,
            new_dag=proposed_dag,
            trigger="checkpoint",
        )
        ```
    """

    def __init__(
        self,
        config: Optional[CoordinatorConfig] = None,
    ):
        """Initialize replan coordinator.

        Args:
            config: Configuration for coordinator behavior.
        """
        self.config = config or CoordinatorConfig()
        self._active_tasks: Dict[str, asyncio.Task[Any]] = {}
        self._cancel_tokens: Dict[str, CancellationToken] = {}
        self._lock = asyncio.Lock()
        self._paused = False

    def register_task(
        self,
        task_id: str,
        async_task: asyncio.Task[Any],
        cancel_token: CancellationToken,
    ) -> None:
        """Register a running task for coordination.

        Registered tasks can be cancelled during replan if they conflict
        with the new plan. Tasks should check their cancel token periodically.

        Args:
            task_id: Unique identifier for the task.
            async_task: The asyncio.Task object.
            cancel_token: Token the task checks for cancellation.

        Example:
            ```python
            token = CancellationToken()

            async def task_fn():
                while not token.is_cancelled():
                    await do_work()

            async_task = asyncio.create_task(task_fn())
            coordinator.register_task("t1", async_task, token)
            ```
        """
        self._active_tasks[task_id] = async_task
        self._cancel_tokens[task_id] = cancel_token

    def unregister_task(self, task_id: str) -> None:
        """Unregister a completed/cancelled task.

        Should be called after a task completes or is cancelled to
        clean up tracking data.

        Args:
            task_id: ID of the task to unregister.
        """
        self._active_tasks.pop(task_id, None)
        self._cancel_tokens.pop(task_id, None)

    async def request_replan(
        self,
        current_dag: TaskDAG,
        new_dag: TaskDAG,
        trigger: str,
        trigger_task_id: Optional[str] = None,
    ) -> Optional[TaskDAG]:
        """Request replan with proper coordination.

        This is the main entry point for replanning. It:
        1. Checks if plan actually changed (meaningful change)
        2. Cancels conflicting running tasks
        3. Merges completed results into new DAG
        4. Resolves ID conflicts
        5. Records replan in history

        Args:
            current_dag: The DAG currently being executed.
            new_dag: The proposed new DAG from replanning.
            trigger: What triggered the replan ("checkpoint", "failure", "manual").
            trigger_task_id: ID of the task that triggered replan.

        Returns:
            Merged DAG if replan accepted, None to continue current plan.

        Example:
            ```python
            new_dag = await planner.replan(request, context, skills)

            if new_dag:
                merged = await coordinator.request_replan(
                    current_dag=current_dag,
                    new_dag=new_dag,
                    trigger="checkpoint",
                    trigger_task_id="t1",
                )
                if merged:
                    current_dag = merged
            ```
        """
        async with self._lock:
            # Step 1: Check if plan actually changed
            if self.config.require_meaningful_change:
                if not self.is_meaningful_change(current_dag, new_dag):
                    return None

            # Step 2: Pause scheduling during replan
            self.pause_scheduling()

            try:
                # Step 3: Cancel conflicting tasks
                cancelled_ids = await self.cancel_conflicting_tasks(
                    current_dag, new_dag
                )

                # Step 4: Merge completed results
                merged_dag = self.merge_results(current_dag, new_dag)

                # Step 5: Resolve ID conflicts
                self.resolve_id_conflicts(merged_dag)

                # Step 6: Record replan
                record = self._create_replan_record(
                    old_dag=current_dag,
                    new_dag=merged_dag,
                    trigger=trigger,
                    trigger_task_id=trigger_task_id,
                    cancelled=cancelled_ids,
                )
                merged_dag.replan_history.append(record)

                return merged_dag

            finally:
                # Always resume scheduling
                self.resume_scheduling()

    def is_meaningful_change(
        self,
        old_dag: TaskDAG,
        new_dag: TaskDAG,
    ) -> bool:
        """Check if replan represents meaningful change.

        Compares the pending tasks in old DAG with new DAG to detect
        if there's an actual meaningful difference that warrants
        replanning.

        Checks:
        1. Different number of pending tasks
        2. Different task signatures (skill + params)
        3. Different dependency structure

        Args:
            old_dag: Current DAG being executed.
            new_dag: Proposed new DAG.

        Returns:
            True if the change is meaningful and worth replanning.
        """
        # Get pending tasks from old DAG
        old_pending = [
            node for node in old_dag.nodes.values()
            if node.status == TaskStatus.PENDING
        ]

        # Get all tasks from new DAG
        new_tasks = list(new_dag.nodes.values())

        # Check 1: Different number of tasks
        if len(old_pending) != len(new_tasks):
            return True

        # Check 2: Different task signatures
        old_signatures = {self._task_signature(n) for n in old_pending}
        new_signatures = {self._task_signature(n) for n in new_tasks}

        if old_signatures != new_signatures:
            return True

        # Check 3: Check change threshold
        if len(old_pending) > 0:
            # Calculate signature overlap
            overlap = len(old_signatures & new_signatures)
            change_ratio = 1.0 - (overlap / len(old_signatures))
            if change_ratio >= self.config.change_threshold:
                return True

        # Check 4: Different dependency structure
        old_deps = self._get_dependency_structure(old_dag, pending_only=True)
        new_deps = self._get_dependency_structure(new_dag, pending_only=False)

        if old_deps != new_deps:
            return True

        return False

    async def cancel_conflicting_tasks(
        self,
        old_dag: TaskDAG,
        new_dag: TaskDAG,
    ) -> List[str]:
        """Cancel tasks that conflict with new plan.

        Identifies running tasks that are not in the new plan and
        requests their cancellation. Waits up to cancel_timeout for
        tasks to respond.

        Args:
            old_dag: Current DAG with running tasks.
            new_dag: Proposed new DAG.

        Returns:
            List of task IDs that were cancelled.
        """
        cancelled: List[str] = []

        # Get signatures of new tasks
        new_signatures = {
            self._task_signature(node)
            for node in new_dag.nodes.values()
        }

        # Find running tasks not in new plan
        for task_id, node in old_dag.nodes.items():
            if node.status != TaskStatus.RUNNING:
                continue

            # Skip if preserving running tasks
            if self.config.preserve_running:
                continue

            # Check if this task is in new plan (by signature)
            task_sig = self._task_signature(node)
            if task_sig in new_signatures:
                continue

            # Request cancellation
            if task_id in self._cancel_tokens:
                token = self._cancel_tokens[task_id]
                token.cancel(f"replan: task not in new plan")
                cancelled.append(task_id)

        # Wait for cancellations
        if cancelled:
            await self._wait_for_cancellations(cancelled)

        return cancelled

    async def _wait_for_cancellations(self, task_ids: List[str]) -> None:
        """Wait for cancelled tasks to complete.

        Args:
            task_ids: List of task IDs to wait for.
        """
        tasks_to_wait = [
            self._active_tasks[tid]
            for tid in task_ids
            if tid in self._active_tasks
        ]

        if not tasks_to_wait:
            return

        try:
            await asyncio.wait(
                tasks_to_wait,
                timeout=self.config.cancel_timeout,
            )
        except Exception:
            pass

        # Force cancel if needed
        if self.config.force_cancel_on_timeout:
            for task_id in task_ids:
                if task_id in self._active_tasks:
                    task = self._active_tasks[task_id]
                    if not task.done():
                        task.cancel()

    def merge_results(
        self,
        old_dag: TaskDAG,
        new_dag: TaskDAG,
    ) -> TaskDAG:
        """Merge completed results from old DAG into new DAG.

        Copies results from completed tasks in old DAG to matching
        tasks in new DAG (matched by signature). This preserves work
        already done.

        Args:
            old_dag: Current DAG with completed results.
            new_dag: New DAG to merge results into.

        Returns:
            New DAG with merged results (modifies new_dag in place).
        """
        if not self.config.preserve_completed:
            return new_dag

        # Build signature -> completed node map from old DAG
        completed_by_sig: Dict[str, TaskNode] = {}
        for node in old_dag.nodes.values():
            if node.status == TaskStatus.COMPLETED:
                sig = self._task_signature(node)
                completed_by_sig[sig] = node

        # Apply completed results to new DAG
        for node in new_dag.nodes.values():
            sig = self._task_signature(node)
            if sig in completed_by_sig:
                old_node = completed_by_sig[sig]
                node.status = TaskStatus.COMPLETED
                node.result = old_node.result
                node.started_at = old_node.started_at
                node.finished_at = old_node.finished_at

        # Update dependencies: remove dependencies on completed tasks
        for node in new_dag.nodes.values():
            if node.status == TaskStatus.PENDING:
                # Filter out completed dependencies
                node.depends_on = [
                    dep_id for dep_id in node.depends_on
                    if dep_id in new_dag.nodes
                    and new_dag.nodes[dep_id].status != TaskStatus.COMPLETED
                ]

        return new_dag

    def resolve_id_conflicts(self, dag: TaskDAG) -> None:
        """Resolve ID conflicts using generation-based naming.

        Increments the generation counter for tasks with conflicting IDs
        and updates all references. This ensures unique IDs across replans.

        Args:
            dag: DAG to resolve conflicts in (modified in place).
        """
        # Find tasks that need generation updates
        seen_base_ids: Dict[str, int] = {}

        for node in dag.nodes.values():
            base_id, current_gen = self._parse_task_id(node.id)

            if base_id in seen_base_ids:
                # Conflict detected - increment generation
                new_gen = max(seen_base_ids[base_id], current_gen) + 1
                old_id = node.id
                new_id = f"{base_id}_g{new_gen}" if new_gen > 0 else base_id

                # Update node
                node.id = new_id
                node.generation = new_gen

                # Update references in other nodes
                for other_node in dag.nodes.values():
                    other_node.depends_on = [
                        new_id if dep_id == old_id else dep_id
                        for dep_id in other_node.depends_on
                    ]

                seen_base_ids[base_id] = new_gen
            else:
                seen_base_ids[base_id] = current_gen

        # Rebuild nodes dict with updated IDs
        new_nodes = {node.id: node for node in dag.nodes.values()}
        dag.nodes = new_nodes

    def _parse_task_id(self, task_id: str) -> tuple[str, int]:
        """Parse task ID into base ID and generation.

        Args:
            task_id: Task ID like "t1" or "t1_g2".

        Returns:
            Tuple of (base_id, generation).
        """
        match = re.match(r"^(.+?)_g(\d+)$", task_id)
        if match:
            return match.group(1), int(match.group(2))
        return task_id, 0

    def pause_scheduling(self) -> None:
        """Pause new task scheduling during replan.

        The executor should check is_paused() before starting new tasks.
        """
        self._paused = True

    def resume_scheduling(self) -> None:
        """Resume task scheduling after replan."""
        self._paused = False

    def is_paused(self) -> bool:
        """Check if scheduling is paused.

        Returns:
            True if scheduling is paused, False otherwise.
        """
        return self._paused

    def _task_signature(self, node: TaskNode) -> str:
        """Generate stable signature for task comparison.

        Uses the TaskNode's built-in get_signature() method which
        hashes the skill and params.

        Args:
            node: TaskNode to generate signature for.

        Returns:
            Signature string for comparison.
        """
        return node.get_signature()

    def _get_dependency_structure(
        self,
        dag: TaskDAG,
        pending_only: bool = False,
    ) -> Set[tuple[str, str]]:
        """Get dependency structure as set of (sig, dep_sig) tuples.

        Args:
            dag: DAG to extract structure from.
            pending_only: If True, only include pending tasks.

        Returns:
            Set of (task_signature, dependency_signature) tuples.
        """
        deps: Set[tuple[str, str]] = set()

        for node in dag.nodes.values():
            if pending_only and node.status != TaskStatus.PENDING:
                continue

            node_sig = self._task_signature(node)
            for dep_id in node.depends_on:
                if dep_id in dag.nodes:
                    dep_sig = self._task_signature(dag.nodes[dep_id])
                    deps.add((node_sig, dep_sig))

        return deps

    def _create_replan_record(
        self,
        old_dag: TaskDAG,
        new_dag: TaskDAG,
        trigger: str,
        trigger_task_id: Optional[str],
        cancelled: List[str],
    ) -> ReplanRecord:
        """Create a record of this replan event.

        Args:
            old_dag: The original DAG.
            new_dag: The new DAG after replan.
            trigger: What triggered the replan.
            trigger_task_id: ID of triggering task.
            cancelled: List of cancelled task IDs.

        Returns:
            ReplanRecord documenting this replan.
        """
        # Find added tasks (in new but not in old by signature)
        old_sigs = {self._task_signature(n) for n in old_dag.nodes.values()}
        new_sigs = {self._task_signature(n) for n in new_dag.nodes.values()}

        added_sigs = new_sigs - old_sigs
        added_ids = [
            node.id for node in new_dag.nodes.values()
            if self._task_signature(node) in added_sigs
        ]

        return ReplanRecord(
            timestamp=datetime.now(),
            trigger=trigger,
            trigger_task_id=trigger_task_id,
            old_task_count=len(old_dag.nodes),
            new_task_count=len(new_dag.nodes),
            tasks_cancelled=cancelled,
            tasks_added=added_ids,
            reason=f"Replan triggered by {trigger}"
            + (f" at task {trigger_task_id}" if trigger_task_id else ""),
        )

    def get_active_task_ids(self) -> List[str]:
        """Get list of currently active task IDs.

        Returns:
            List of task IDs that are currently registered.
        """
        return list(self._active_tasks.keys())

    def get_replan_count(self, dag: TaskDAG) -> int:
        """Get number of replans for a DAG.

        Args:
            dag: DAG to check.

        Returns:
            Number of replans recorded in history.
        """
        return len(dag.replan_history)
