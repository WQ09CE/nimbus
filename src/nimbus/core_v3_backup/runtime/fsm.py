"""
Nimbus vCPU Finite State Machine (FSM) Engine.

This module defines the core FSM protocol and context used by the vCPU
to orchestrate the Think-Act-Observe loop reliably.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Protocol

from nimbus.core.memory.mmu import MMU
from nimbus.core.protocol import ActionIR
# Avoid circular import by referencing FSMExecutionState
import nimbus.core.runtime.states as _states
from nimbus.core.runtime.config import VCPUConfig
logger = logging.getLogger("kernel.vcpu.fsm")

class SyscallGateProtocol(Protocol):
    async def syscall_tool(self, action: ActionIR) -> Any:
        ...

class ALUProtocol(Protocol):
    async def chat(self, mmu: MMU, tools: List[Dict[str, Any]], on_chunk: Any = None) -> Any:
        ...

class DecoderProtocol(Protocol):
    def decode(self, text: str) -> List[ActionIR]:
        ...


class FSMContext:
    """
    The shared context passed between all states in the vCPU FSM.
    Provides access to the MMU, Gate, Decoder pipeline, and tracks the execution state.
    """

    def __init__(
        self,
        mmu: MMU,
        gate: SyscallGateProtocol,
        alu: ALUProtocol,
        decoder: DecoderProtocol,
        config: VCPUConfig,
        tools: List[Dict[str, Any]],
        state: '_states.FSMExecutionState',
        transform_context_hook: Optional[Callable[[FSMContext], Any]] = None,
        interrupt_event: Optional[Any] = None,
        manifest: Any = None,
    ):
        self.mmu = mmu
        self.gate = gate
        self.alu = alu
        self.decoder = decoder
        self.config = config
        self.tools = tools
        self.state = state
        self.transform_context_hook = transform_context_hook
        self.interrupt_event = interrupt_event
        self.manifest = manifest

        # Temporary registers for FSM transitions
        self.current_actions: List[ActionIR] = []
        self.current_results: List[Any] = []
        self.final_result: Optional[Any] = None
        
        # Track pending errors for the ERROR_RECOVERY state
        self.pending_error: Optional[Exception] = None
        self.pending_parse_error: Optional[str] = None
        self.fault: Optional[Any] = None


class VCPUState(Protocol):
    """
    Protocol for an atomic state in the vCPU FSM.
    """

    @property
    def name(self) -> str:
        """Name of the state (e.g., 'INIT', 'REASONING')."""
        ...

    async def execute(self, ctx: FSMContext) -> VCPUState:
        """
        Execute the logic for this state and return the next state to transition to.
        
        """
        ...


# Legal State Transition Matrix
# Restricts FSM jumps to prevent runaway hallucinations or logical corruption
VALID_TRANSITIONS: Dict[str, List[str]] = {
    "INIT": ["REASONING", "ERROR_RECOVERY"],
    "REASONING": ["ACTION_EXECUTION", "OBSERVATION", "ERROR_RECOVERY", "COMPLETED"],
    "ACTION_EXECUTION": ["OBSERVATION", "ERROR_RECOVERY"],
    "OBSERVATION": ["INIT", "COMPLETED"],
    "ERROR_RECOVERY": ["INIT", "COMPLETED"]
}
