"""
Checkpoint Manager - Handles session checkpointing and restoration.

Extracted from VCPU to follow single responsibility principle.
"""

import time
from typing import Any, Dict

from nimbus.core.memory.mmu import MMU
from nimbus.core.persistence import SessionCheckpointModel
from nimbus.core.runtime.states import FSMExecutionState


class CheckpointManager:
    """
    Manages session checkpoints for VCPU state persistence.

    Checkpoints capture:
    - Execution state (iteration, errors, etc.)
    - Memory snapshot (conversation history)
    - Metadata (timestamp, reason)
    """

    def __init__(self, state: FSMExecutionState, mmu: MMU):
        """
        Initialize CheckpointManager.

        Args:
            state: The execution state to checkpoint
            mmu: Memory management unit to checkpoint
        """
        self._state = state
        self._mmu = mmu

    def create(
        self,
        session_id: str,
        reason: str = "periodic",
    ) -> SessionCheckpointModel:
        """
        Create a full session checkpoint.

        Args:
            session_id: Current session ID
            reason: Reason for checkpoint (periodic/interruption/error)

        Returns:
            SessionCheckpointModel (Pydantic model)
        """
        exec_snapshot = self._state.create_snapshot()
        mem_snapshot = self._mmu.create_snapshot()

        return SessionCheckpointModel(
            session_id=session_id,
            timestamp=time.time(),
            step_index=self._state.iteration_count,
            execution_state=exec_snapshot,
            memory_snapshot=mem_snapshot,
            reason=reason,
            can_resume=(self._state.iteration_count < self._state.max_iterations),
        )

    def restore(self, checkpoint: SessionCheckpointModel) -> None:
        """
        Restore session state from checkpoint.

        Args:
            checkpoint: SessionCheckpointModel to restore from
        """
        if hasattr(self._state, "restore_from_snapshot"):
            self._state.restore_from_snapshot(checkpoint.execution_state)
        if hasattr(self._mmu, "restore_from_snapshot"):
            self._mmu.restore_from_snapshot(checkpoint.memory_snapshot)

    def get_state_dict(self) -> Dict[str, Any]:
        """
        Get current state as dictionary for debugging.

        Returns:
            Dictionary containing state information
        """
        return {
            **self._state.to_dict(),
            "stack_depth": self._mmu.stack_depth,
            "mmu_state": self._mmu.get_state(),
        }
