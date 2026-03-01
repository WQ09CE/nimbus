"""
Nimbus v2 Virtual CPU (vCPU) - The Core Execution Engine (FSM Refactored)

The vCPU implements the Think-Act-Observe loop via a Finite State Machine:
    INIT -> REASONING -> ACTION_EXECUTION -> OBSERVATION -> (Back to REASONING or INIT)

Key Responsibilities:
- Orchestrate the State Machine
- Expose run() interface for AgentOS
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

from nimbus.core.memory.mmu import MMU
from nimbus.core.models.manifest import ModelManifest
from nimbus.core.persistence import SessionCheckpointModel
from nimbus.core.protocol import ToolResult
from nimbus.core.runtime.checkpoint_manager import CheckpointManager
from nimbus.core.runtime.decoder import BaseDecoder, DefaultDecoder
from nimbus.core.runtime.fsm import FSMContext, VCPUState
from nimbus.core.runtime.pipeline import ResponsePipeline
from nimbus.core.runtime.states import StateInit, StateCompleted, StateReasoning, StateObservation, FSMExecutionState, StateErrorRecovery
from nimbus.core.runtime.config import VCPUConfig
from nimbus.core.runtime.tracer import TraceManager

# Tool Call Optimization Constants
STATE_MODIFYING_TOOLS = {"Edit", "Write"}

# VCPUConfig imported from nimbus.core.runtime.config

class VCPU:
    """
    Virtual CPU - The FSM Execution Engine.
    """

    def __init__(
        self,
        alu: Any,
        decoder: BaseDecoder,
        gate: Any,
        mmu: MMU,
        config: Optional[VCPUConfig] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "default_session",
        manifest: Optional[ModelManifest] = None,
    ):
        # Hardware Components
        self.alu = alu
        self.gate = gate
        self.mmu = mmu
        self.decoder = decoder or DefaultDecoder()
        
        # Configuration
        self.config = config or VCPUConfig()
        self.tools = tools or []
        self.session_id = session_id
        self.manifest = manifest or ModelManifest(name="default_model", provider="unknown")
        
        # OS Controls
        self.signals = {"soft_timeout": False, "hard_timeout": False}
        self.tracer = TraceManager(session_id=session_id)

        # FSM State "Register"
        # Represents objective execution counts rather than controlling flow
        self._state = FSMExecutionState.from_config(
            max_iterations=self.config.max_iterations
        )
        self._is_active: bool = False  # Legacy flag for AgentOS compatibility
        
        # Pipeline 
        self.pipeline = ResponsePipeline(features=self.manifest.features, role=self.manifest.role)

        # Checkpoint Manager
        self._checkpoint_manager = CheckpointManager(
            state=self._state,
            mmu=self.mmu
        )

        # FSM Iteration State
        self._fsm_ctx: Optional[FSMContext] = None
        self._current_state: Optional[VCPUState] = None

    def request_pause(self) -> None:
        """Request the vCPU to pause execution (placeholder for AgentOS signal)."""
        pass

    def request_interruption(self) -> None:
        """Request interruption (trigger soft_timeout)."""
        self.signals["soft_timeout"] = True

    def _reset(self) -> None:
        """Reset the vCPU state for a new interaction turn."""
        self._state = FSMExecutionState.from_config(
            max_iterations=self.config.max_iterations
        )
        self._is_active = False
        self.signals.clear()
        self._checkpoint_manager.state = self._state
        self._fsm_ctx = None
        self._current_state = None

    async def step(self) -> "StepResult":
        """
        Drive the FSM forward by exactly one logical step (Think -> Act -> Observe).
        Yields control back to the OS between full iterations.
        """
        import logging
        logger = logging.getLogger("kernel.vcpu")
        
        # 1. Initialize FSM Session if brand new
        if self._fsm_ctx is None:
            self._fsm_ctx = FSMContext(
                mmu=self.mmu,
                gate=self.gate,
                alu=self.alu,
                decoder=self.decoder,
                pipeline=self.pipeline,
                config=self.config,
                tools=self.tools,
                state=self._state
            )
            self._current_state = StateInit()
            self._is_active = True
            
        from nimbus.core.protocol import StepResult
        step_result = StepResult()

        try:
            # 2. Check for early termination signals from OS 
            if self.signals.get("soft_timeout") or self.signals.get("hard_timeout"):
                logger.warning("vCPU received timeout signal in FSM step.")
                step_result.is_final = True
                step_result.fault = self._fsm_ctx.fault
                if self._fsm_ctx.final_result is not None:
                    # An explicit termination signal was raised during error recovery or observation bounds checking
                    self._current_state = StateCompleted()
                
                # Pre-execution transition check
                # This check is for the state *after* the timeout, which is StateCompleted or the current state.
                # The instruction implies validating a transition *to* a state.
                # Given the context, if a timeout occurs, the FSM is effectively transitioning to a "halted" state.
                # The most appropriate check here would be if the current state allows for an abrupt termination.
                # However, the provided snippet checks against "INIT".
                # Assuming the intent is to ensure the FSM can transition to a completed state from the current state.
                # For now, applying the change as provided, but noting the potential logical mismatch.
                if self._current_state.name != "INIT":
                    from nimbus.core.runtime.fsm import VALID_TRANSITIONS
                    # This condition checks if the *current* state is a valid transition *from* INIT.
                    # This seems incorrect for a timeout scenario.
                    # A more logical check might be:
                    # if "COMPLETED" not in VALID_TRANSITIONS.get(self._current_state.name, []):
                    #     raise RuntimeError(f"Invalid FSM Transition: {self._current_state.name} -> COMPLETED on timeout")
                    # However, following the user's explicit instruction for the provided code.
                    if self._current_state.name not in VALID_TRANSITIONS.get("INIT", []):
                        raise RuntimeError(f"Invalid FSM Transition: INIT -> {self._current_state.name}")
                self._is_active = False
                return step_result

            # 3. Fast-forward simple setup states (Init, Completed, ErrorRecovery)
            while isinstance(self._current_state, StateInit) or isinstance(self._current_state, StateCompleted) or isinstance(self._current_state, StateErrorRecovery):
                if isinstance(self._current_state, StateCompleted):
                    step_result.is_final = True
                    step_result.fault = self._fsm_ctx.fault
                    f_res = self._fsm_ctx.final_result
                    step_result.final_result = f_res if isinstance(f_res, ToolResult) else ToolResult(status="OK", output=f_res, is_final=True)
                    self._is_active = False
                    return step_result
                
                self._current_state = await self._current_state.execute(self._fsm_ctx)

            # 4. We are now either in Reasoning or Observation. 
            # A logical "step" out to AgentOS consumes Reasoning -> ActionExecution -> Observation -> (back to Reasoning)
            
            # Phase A: Think/Reason
            if isinstance(self._current_state, StateReasoning):
                next_state = await self._current_state.execute(self._fsm_ctx)
                from nimbus.core.runtime.fsm import VALID_TRANSITIONS
                if next_state.name not in VALID_TRANSITIONS.get(self._current_state.name, []):
                     raise RuntimeError(f"Invalid FSM Transition: {self._current_state.name} -> {next_state.name}")
                self._current_state = next_state
                
            # A complete iteration outputs the actions derived
            step_result.actions = list(self._fsm_ctx.current_actions)
                
            # Phase B: Act (if actions generated)
            if self._current_state.__class__.__name__ == "StateActionExecution":
                next_state = await self._current_state.execute(self._fsm_ctx)
                from nimbus.core.runtime.fsm import VALID_TRANSITIONS
                if next_state.name not in VALID_TRANSITIONS.get(self._current_state.name, []):
                     raise RuntimeError(f"Invalid FSM Transition: {self._current_state.name} -> {next_state.name}")
                self._current_state = next_state
                
            # Pop in execution results for the agentOS stream wrapper
            step_result.results = list(self._fsm_ctx.current_results)
            
            # Phase C: Observe (Write memory and evaluate loop limits)
            if isinstance(self._current_state, StateObservation):
                next_state = await self._current_state.execute(self._fsm_ctx)
                from nimbus.core.runtime.fsm import VALID_TRANSITIONS
                if next_state.name not in VALID_TRANSITIONS.get(self._current_state.name, []):
                     raise RuntimeError(f"Invalid FSM Transition: {self._current_state.name} -> {next_state.name}")
                self._current_state = next_state
                
            return step_result

        except asyncio.CancelledError:
            logger.warning("vCPU Execution Cancelled.")
            step_result.is_final = True
            step_result.final_result = ToolResult(status="CANCELLED", is_final=True)
            self._is_active = False
            return step_result
        except Exception as e:
            logger.exception("Catastrophic VCPU Failure")
            step_result.is_final = True
            step_result.final_result = ToolResult(status="ERROR", is_final=True, output=f"FSM Crashed: {e}")
            self._is_active = False
            return step_result

    async def run(self, goal: str) -> ToolResult:
        """
        Convenience wrapper to execute the FSM sequentially until completion.
        (Used primarily by MVP scripts and simple CLI integrations).

        Args:
            goal: The main objective text

        Returns:
            Final ToolResult containing the output or fault
        """
        import logging
        logger = logging.getLogger("kernel.vcpu")
        logger.info(f"🚀 Starting FSM vCPU task run wrapper. Iteration budget: {self.config.max_iterations}")

        # Ensure goal is documented in Memory if brand new frame
        if len(self.mmu.current_frame.messages) == 0:
            self.mmu.add_user_message(goal)

        final_res = None
        while self.is_running and not self.is_done:
            step_res = await self.step()
            if step_res.is_final:
                final_res = step_res.final_result
                break
                
        return final_res or ToolResult(status="OK", output="Task abruptly concluded without final result.")

    async def _prepare_goal_for_pinning(self, goal: str) -> str:
        """
        Prepare goal for pinning.

        If goal is short enough, use as-is.
        If too long, use LLM to summarize while preserving language.

        Args:
            goal: The original user goal

        Returns:
            Goal suitable for pinning (original or summarized)
        """
        if len(goal) <= self.config.goal_max_length:
            return goal

        # Goal is too long, summarize with LLM
        import logging
        logger = logging.getLogger("kernel.vcpu")

        try:
            # Detect user's language for the prompt
            has_chinese = any("\u4e00" <= c <= "\u9fff" for c in goal)

            if has_chinese:
                prompt = f"""请用一句话总结用户的核心请求（保持中文，不超过100字）：

用户原文：
{goal[:1000]}...

一句话总结："""
            else:
                prompt = f"""Summarize the user's core request in one sentence (max 100 chars):

Original:
{goal[:1000]}...

One sentence summary:"""

            # Use LLM to summarize
            messages = [{"role": "user", "content": prompt}]
            
            # Use appropriate completion method depending on ALU implementation
            if hasattr(self.alu, "complete"):
                response = await self.alu.complete(messages, tools=[])
            else:
                response = await self.alu.chat(messages, tools=[])

            if response.content:
                summary = response.content.strip()
                # Ensure summary is actually shorter
                if len(summary) < len(goal):
                    logger.info(f"Goal summarized: {len(goal)} → {len(summary)} chars")
                    return summary
        except Exception as e:
            logger.warning(f"Failed to summarize goal for pinning: {e}")
            
        return goal[:self.config.goal_max_length] + "..."

    # =========================================================================
    # State Accessors
    # =========================================================================

    @property
    def iteration(self) -> int:
        return self._state.iteration_count

    @property
    def is_running(self) -> bool:
        # Compatibility facade for AgentOS. VCPU is considered 'running'
        # if it hasn't completely halted.
        if self._current_state is None:
            # First tick hasn't started, but AgentOS thinks it's running 
            return self._is_active
        return self._is_active and not isinstance(self._current_state, StateCompleted)
        
    @property
    def is_done(self) -> bool:
        # Counterpart to is_running
        if self._current_state is None:
             return False
        return isinstance(self._current_state, StateCompleted)

    def get_state(self) -> Dict[str, Any]:
        return self._state.to_dict()

    # =========================================================================
    # Persistence
    # =========================================================================

    def create_checkpoint(self, session_id: str, reason: str = "periodic") -> SessionCheckpointModel:
        return self._checkpoint_manager.create(session_id, reason)

    def restore_from_checkpoint(self, checkpoint: SessionCheckpointModel) -> None:
        self._checkpoint_manager.restore(checkpoint)

