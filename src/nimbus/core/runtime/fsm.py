"""
Nimbus vCPU Finite State Machine (FSM) Engine.

This module defines the core FSM protocol and context used by the vCPU
to orchestrate the Think-Act-Observe loop reliably.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol

from nimbus.core.memory.mmu import MMU
from nimbus.core.protocol import ActionIR
# Avoid circular import by referencing FSMExecutionState
import nimbus.core.runtime.states as _states
from nimbus.core.runtime.pipeline import ResponsePipeline
from nimbus.core.runtime.config import VCPUConfig

logger = logging.getLogger("kernel.vcpu.fsm")


class FSMContext:
    """
    The shared context passed between all states in the vCPU FSM.
    Provides access to the MMU, Gate, Decoder pipeline, and tracks the execution state.
    """

    def __init__(
        self,
        mmu: MMU,
        gate: Any,  # KernelGate (using Any to avoid circular import for now)
        alu: Any,   # LLMClient
        decoder: Any, # BaseDecoder
        pipeline: ResponsePipeline,
        config: VCPUConfig,
        tools: List[Dict[str, Any]],
        state: '_states.FSMExecutionState',
    ):
        self.mmu = mmu
        self.gate = gate
        self.alu = alu
        self.decoder = decoder
        self.pipeline = pipeline
        self.config = config
        self.tools = tools
        self.state = state

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
        
        Args:
            ctx: The shared FSM context containing all necessary dependencies and state.
            
        Returns:
            The next VCPUState instance to transition to.
        """
        ...
