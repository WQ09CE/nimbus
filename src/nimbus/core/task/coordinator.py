"""SubagentReplanCoordinator for task-level replanning.

This module provides coordination for replanning at the subagent task level,
handling:
- Pause/resume of task scheduling during replan
- Replan request handling
- History tracking

Example:
    >>> from nimbus.core.task.coordinator import SubagentReplanCoordinator
    >>>
    >>> coordinator = SubagentReplanCoordinator()
    >>> await coordinator.request_replan(failed_node, dag, "Connection error")
    >>> if coordinator.is_paused():
    ...     # Wait for replan to complete
    ...     await asyncio.sleep(0.1)
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .types import (
    SubagentNode,
    SubagentDAG,
    SubagentReplanRecord,
    SubagentStatus,
)

if TYPE_CHECKING:
    pass


class SubagentReplanCoordinator:
    """Coordinates replanning at the subagent task level.

    Unlike the tool-level ReplanCoordinator, this handles replanning
    at a higher abstraction level where entire subagent tasks may need
    to be reconsidered.

    Attributes:
        max_replan_attempts: Maximum number of replan attempts.
    """

    def __init__(self, max_replan_attempts: int = 3):
        """Initialize coordinator.

        Args:
            max_replan_attempts: Maximum replan attempts before giving up.
        """
        self.max_replan_attempts = max_replan_attempts
        self._paused = False
        self._lock = asyncio.Lock()
        self._replan_count = 0

    def is_paused(self) -> bool:
        """Check if scheduling is paused for replan.

        Returns:
            True if scheduling should be paused.
        """
        return self._paused

    def pause(self) -> None:
        """Pause task scheduling during replan."""
        self._paused = True

    def resume(self) -> None:
        """Resume task scheduling after replan."""
        self._paused = False

    async def request_replan(
        self,
        failed_node: SubagentNode,
        dag: SubagentDAG,
        error: str,
    ) -> bool:
        """Request a replan due to subagent failure.

        This method:
        1. Pauses scheduling
        2. Records the replan event
        3. Determines if replan is viable
        4. Resumes scheduling (caller handles actual replan)

        Args:
            failed_node: The SubagentNode that failed.
            dag: The SubagentDAG being executed.
            error: Error message from the failure.

        Returns:
            True if replan was requested, False if replan is not viable.
        """
        async with self._lock:
            # Check if we've exceeded max replan attempts
            if self._replan_count >= self.max_replan_attempts:
                return False

            # Pause scheduling
            self.pause()

            try:
                # Record the replan request
                self._replan_count += 1

                # Create replan record
                record = SubagentReplanRecord(
                    timestamp=datetime.now(),
                    trigger="failure",
                    trigger_node_id=failed_node.id,
                    old_node_count=len(dag.nodes),
                    new_node_count=len(dag.nodes),  # Will be updated if replan happens
                    nodes_cancelled=[],
                    nodes_added=[],
                    reason=f"Subagent {failed_node.id} ({failed_node.subagent_type.value}) failed: {error}",
                )
                dag.replan_history.append(record)

                return True

            finally:
                # Resume scheduling (caller will handle actual replan)
                self.resume()

    def get_replan_count(self) -> int:
        """Get number of replan requests made.

        Returns:
            Number of replans requested.
        """
        return self._replan_count

    def reset(self) -> None:
        """Reset coordinator state."""
        self._paused = False
        self._replan_count = 0

    def can_replan(self) -> bool:
        """Check if another replan is allowed.

        Returns:
            True if replan count is below max attempts.
        """
        return self._replan_count < self.max_replan_attempts
