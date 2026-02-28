"""
Atomic states for the Nimbus vCPU FSM.

These classes encapsulate the logic for each stage of the Think-Act-Observe loop
and dictating strict transitions.
"""

import json
import logging
import traceback
from typing import Any, Dict, List, Optional, Tuple

from nimbus.core.protocol import ActionIR, Fault, ToolResult
from nimbus.core.runtime.fsm import FSMContext, VCPUState

from dataclasses import dataclass, field

logger = logging.getLogger("kernel.vcpu.states")


@dataclass
class FSMExecutionState:
    """
    vCPU Execution State for FSM.
    
    Centralizes execution tracking for the Finite State Machine.
    Replaces the legacy while-loop flags with strict iteration/error counters.
    """
    iteration_count: int = 0
    max_iterations: int = 50
    consecutive_thoughts: int = 0
    consecutive_errors: int = 0
    consecutive_empty_responses: int = 0
    compaction_count: int = 0
    max_compactions: int = 1
    tool_failure_counts: Dict[str, int] = field(default_factory=dict)
    max_tool_failures: int = 6
    path_not_found_count: int = 0
    doom_loop_count: int = 0

    def reset(self) -> None:
        self.iteration_count = 0
        self.consecutive_thoughts = 0
        self.consecutive_errors = 0
        self.consecutive_empty_responses = 0
        self.compaction_count = 0
        self.tool_failure_counts.clear()
        self.path_not_found_count = 0
        self.doom_loop_count = 0

    def increment_iteration(self) -> int:
        self.iteration_count += 1
        return self.iteration_count

    def on_thought(self) -> int:
        self.consecutive_thoughts += 1
        return self.consecutive_thoughts

    def on_action(self) -> None:
        self.consecutive_thoughts = 0

    def on_tool_success(self, tool_name: str) -> None:
        self.consecutive_errors = 0
        self.tool_failure_counts[tool_name] = 0

    def on_tool_failure(self, tool_name: str) -> int:
        self.consecutive_errors += 1
        self.tool_failure_counts[tool_name] = self.tool_failure_counts.get(tool_name, 0) + 1
        return self.tool_failure_counts[tool_name]

    def is_tool_failing_too_much(self, tool_name: str) -> bool:
        return self.tool_failure_counts.get(tool_name, 0) >= self.max_tool_failures

    def on_empty_response(self) -> int:
        self.consecutive_empty_responses += 1
        return self.consecutive_empty_responses

    def on_valid_response(self) -> None:
        self.consecutive_empty_responses = 0

    @classmethod
    def from_config(
        cls,
        max_iterations: int = 50,
        max_compactions: int = 10,
        max_tool_failures: int = 6,
    ) -> "FSMExecutionState":
        return cls(
            max_iterations=max_iterations,
            max_compactions=max_compactions,
            max_tool_failures=max_tool_failures,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration_count,
            "max_iterations": self.max_iterations,
            "consecutive_thoughts": self.consecutive_thoughts,
            "consecutive_errors": self.consecutive_errors,
            "consecutive_empty_responses": self.consecutive_empty_responses,
            "compaction_count": self.compaction_count,
            "max_compactions": self.max_compactions,
            "tool_failure_counts": dict(self.tool_failure_counts),
            "path_not_found_count": self.path_not_found_count,
            "doom_loop_count": self.doom_loop_count,
        }

    def create_snapshot(self) -> Any:
        from nimbus.core.persistence import FSMExecutionStateModel
        return FSMExecutionStateModel(
            iteration=self.iteration_count,
            max_iterations=self.max_iterations,
            consecutive_thoughts=self.consecutive_thoughts,
            consecutive_errors=self.consecutive_errors,
            consecutive_empty_responses=self.consecutive_empty_responses,
            compaction_count=self.compaction_count,
            max_compactions=self.max_compactions,
            tool_failure_counts=dict(self.tool_failure_counts),
            path_not_found_count=self.path_not_found_count,
            doom_loop_count=self.doom_loop_count,
        )

class StateInit(VCPUState):
    """Initializes the VCPU step and loads necessary context."""
    
    @property
    def name(self) -> str:
        return "INIT"

    async def execute(self, ctx: FSMContext) -> VCPUState:
        logger.debug(f"[vCPU] State: {self.name} - Init step {ctx.state.iteration_count + 1}")
        
        # Reset per-step active variables
        ctx.pipeline.reset()
        ctx.current_actions = []
        ctx.pending_error = None
        ctx.pending_parse_error = None
        ctx.final_result = None
        
        return StateReasoning()


class StateReasoning(VCPUState):
    """The ALU execution phase. Calls the LLM and parses the response."""
    
    @property
    def name(self) -> str:
        return "REASONING"

    async def execute(self, ctx: FSMContext) -> VCPUState:
        logger.debug(f"[vCPU] State: {self.name}")
        
        ctx.state.increment_iteration()
        
        # 1. Fetch context from MMU
        context = ctx.mmu.assemble_context()
        messages = context
        # TODO: adapter logic if needed
        
        # 3. Call LLM
        try:
            logger.info(f"🧠 [vCPU] ALU Thinking (Iter {ctx.state.iteration_count})...")
            
            # Emit step_started event so frontend knows to commit previous message blocks
            if ctx.gate and hasattr(ctx.gate, "events") and ctx.gate.events:
                from nimbus.core.protocol import Event
                ctx.gate.events.emit(Event(
                    type="step_started",
                    pid=ctx.gate.pid,
                    data={"iteration": ctx.state.iteration_count}
                ))
                
            def _on_chunk(chunk: str) -> None:
                if ctx.gate and hasattr(ctx.gate, "events") and ctx.gate.events:
                    from nimbus.core.protocol import Event
                    ctx.gate.events.emit(Event(
                        type="thinking",
                        pid=ctx.gate.pid,
                        data={"chunk": chunk}
                    ))
            
            response = await ctx.alu.chat(
                messages=messages,
                tools=ctx.tools,
                on_chunk=_on_chunk
            )
        except Exception as e:
            logger.exception("ALU execution failed")
            ctx.pending_error = e
            return StateErrorRecovery()
            
        # 4. Decode Response (Middleware Pipeline)
        try:
            actions: List[ActionIR] = ctx.pipeline.process_response(response, ctx.decoder)
            ctx.current_actions = actions
        except Exception as e:
            logger.error(f"Failed to parse ALU response: {e}")
            ctx.pending_parse_error = f"Failed to decode response: {str(e)}. Please output valid format."
            return StateErrorRecovery()

        if not actions:
            # Handle Empty Response (Agent broke protocol and output nothing useful)
            ctx.pending_parse_error = "You returned an empty response or invalid format. You MUST use a tool or return a final answer."
            return StateErrorRecovery()
            
        # Route based on Actions
        has_tool_call = False
        for action in actions:
            if action.kind in ("RETURN", "REPLY"):
                ctx.final_result = action.args.get("result", action.args.get("value", action.args.get("text", action.args)))
                
                # Persist the final response to MMU since we are bypassing StateObservation
                if hasattr(response, "tool_calls") and response.tool_calls:
                    ctx.mmu.add_assistant_with_tool_calls(getattr(response, "content", ""), response.tool_calls)
                elif hasattr(response, "content") and response.content:
                    ctx.mmu.add_assistant_message(response.content)
                else:
                    ctx.mmu.add_assistant_message(str(ctx.final_result))
                    
                return StateCompleted()
            elif action.kind in ("TOOL_CALL", "SUB_CALL"):
                has_tool_call = True
                
        if has_tool_call:
            return StateActionExecution()
            
        # If it's just a THOUGHT without any tools, what's next?
        # Typically LLMs should be forced to take an action. 
        # If they just output THOUGHT, we treat it as an empty action cycle.
        return StateObservation()


class StateActionExecution(VCPUState):
    """Executes the proposed Actions via the KernelGate."""
    
    @property
    def name(self) -> str:
        return "ACTION_EXECUTION"

    async def execute(self, ctx: FSMContext) -> VCPUState:
        logger.debug(f"[vCPU] State: {self.name} - Executing {len(ctx.current_actions)} actions")
        
        for action in ctx.current_actions:
             if action.kind == "THOUGHT":
                 # Thought is non-blocking, we just write it to MMU later
                 continue
                 
             if action.kind in ("TOOL_CALL", "SUB_CALL"):
                 logger.info(f"⚙️  [vCPU] Executing Tool: {action.name}")
                 try:
                     # Execute via Gate
                     result = await ctx.gate.syscall_tool(action)
                     
                     # Store in action context to be written to MMU later
                     if not hasattr(action, 'result'):
                         action.result = result
                     ctx.current_results.append(result)
                         
                 except Exception as e:
                     logger.warning(f"Tool {action.name} failed with Exception: {e}")
                     # In a strict FSM, a tool failure interrupts the current cycle.
                     # We record the failure and bounce to Recovery.
                     result = ToolResult(
                         status="ERROR",
                         output=f"Tool failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
                     )
                     action.result = result
                     ctx.current_results.append(result)
                     ctx.pending_error = e
                     return StateErrorRecovery()
                     
        return StateObservation()


class StateObservation(VCPUState):
    """Writes results to the MMU and checks global limits."""
    
    @property
    def name(self) -> str:
        return "OBSERVATION"

    async def execute(self, ctx: FSMContext) -> VCPUState:
        logger.debug(f"[vCPU] State: {self.name} - Writing to MMU")
        
        # 1. First, record the assistant's tool calls batch if any exist
        tool_actions = [a for a in ctx.current_actions if a.kind in ("TOOL_CALL", "SUB_CALL")]
        if tool_actions:
            # Extract non-blocking thought text to include as content in the
            # same assistant message (Anthropic API requires tool_result
            # immediately after tool_use — a separate assistant message in
            # between would break the contract).
            thought_text = None
            for a in ctx.current_actions:
                if a.kind == "THOUGHT" and a.meta and a.meta.get("non_blocking"):
                    thought_text = a.args.get("text", "")
                    break

            tool_calls = []
            for action in tool_actions:
                tool_calls.append({
                    "id": action.id,
                    "type": "function",
                    "function": {
                        "name": action.name,
                        "arguments": json.dumps(action.args) if isinstance(action.args, dict) else action.args
                    }
                })
            ctx.mmu.add_assistant_with_tool_calls(thought_text, tool_calls)

            # 2a. Write tool results immediately after tool_use (no intervening messages)
            for action in ctx.current_actions:
                if action.kind in ("TOOL_CALL", "SUB_CALL"):
                    result = getattr(action, 'result', None)
                    content = result.output if hasattr(result, "output") else str(result)
                    ctx.mmu.add_tool_result(
                        tool_call_id=action.id,
                        name=action.name,
                        content=content,
                        tool_args=action.args
                    )
        else:
            # 2b. No tool calls — write standalone thoughts
            for action in ctx.current_actions:
                if action.kind == "THOUGHT":
                    ctx.mmu.add_assistant_message(action.args.get('text', ''))

        # Reset consecutive errors on successful observation (tools executed without exception)
        if tool_actions:
            ctx.state.consecutive_errors = 0

        # Doom Loop Detection
        from nimbus.core.runtime.doom_loop import DoomLoopDetector

        if not hasattr(ctx, '_doom_loop_detector'):
            ctx._doom_loop_detector = DoomLoopDetector(threshold=3)

        for action in tool_actions:
            doom_result = ctx._doom_loop_detector.check(action.name, action.args)
            if doom_result.is_loop:
                logger.warning(
                    f"Doom loop detected for tool '{action.name}': {doom_result.guidance}"
                )
                ctx.state.doom_loop_count += 1

                if ctx.state.doom_loop_count >= 2:
                    # Two doom loop warnings -> force terminate
                    ctx.final_result = ToolResult(
                        status="ERROR",
                        output=f"Agent terminated due to repeated doom loop on tool '{action.name}'",
                        fault=Fault(
                            domain="AGENT",
                            code="DOOM_LOOP",
                            message=doom_result.guidance,
                        ),
                        is_final=True,
                    )
                    # Clear observed actions before returning
                    ctx.current_actions = []
                    ctx.current_results = []
                    return StateCompleted()

                # First warning: inject guidance and continue
                ctx.mmu.add_tool_result(
                    tool_call_id=None,
                    name="doom_loop_warning",
                    content=f"[Doom Loop Warning] {doom_result.guidance}",
                )

        # Clear observed actions
        ctx.current_actions = []
        ctx.current_results = []

        # Check Watchdog / Limits
        if ctx.state.iteration_count >= ctx.config.max_iterations:
             logger.error("vCPU max iterations reached.")
             ctx.final_result = ToolResult(
                 status="ERROR",
                 output=f"Max iterations ({ctx.config.max_iterations}) reached.",
                 fault=Fault(domain="RESOURCE", code="BUDGET_EXCEEDED", message="Max iterations limit.", retryable=False),
                 is_final=True
             )
             return StateCompleted()

        return StateInit()


class StateErrorRecovery(VCPUState):
    """Interrupt Handler. Injects error messages back into the context."""
    
    @property
    def name(self) -> str:
        return "ERROR_RECOVERY"

    async def execute(self, ctx: FSMContext) -> VCPUState:
        logger.warning(f"[vCPU] State: {self.name}")

        # Determine Error Details
        error_msg = ""
        if ctx.pending_parse_error:
            error_msg = ctx.pending_parse_error
        elif ctx.pending_error:
            error_msg = f"System Error during execution: {str(ctx.pending_error)}"

        logger.error(f"Recovering from: {error_msg}")

        # Track consecutive errors and bail out if too many
        MAX_CONSECUTIVE_ERRORS = 5
        ctx.state.consecutive_errors += 1

        if ctx.state.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            logger.warning(
                f"Fatal: {ctx.state.consecutive_errors} consecutive errors. "
                f"Aborting to prevent infinite error loop."
            )
            ctx.final_result = ToolResult(
                status="ERROR",
                output=f"Agent terminated after {ctx.state.consecutive_errors} consecutive errors. "
                       f"Last error: {error_msg[:500]}",
                fault=Fault(
                    domain="RESOURCE",
                    code="MAX_ERRORS_EXCEEDED",
                    message=f"Exceeded {MAX_CONSECUTIVE_ERRORS} consecutive errors",
                ),
                is_final=True,
            )
            return StateCompleted()

        # Inject standard error back to MMU so the LLM sees it
        if error_msg:
             # Create a mock ActionIR to represent the system error interaction
             mock_sys_action = ActionIR(
                 kind="SYSTEM_ERROR",
                 name="error_handler",
                 args={"message": error_msg}
             )
             ctx.mmu.add_tool_result(
                 tool_call_id=mock_sys_action.id,
                 name="error_handler",
                 content=error_msg,
             )

        # Bounce back to Reasoning to let the LLM fix it
        return StateInit()


class StateCompleted(VCPUState):
    """Terminal state of the vCPU."""
    
    @property
    def name(self) -> str:
        return "COMPLETED"

    async def execute(self, ctx: FSMContext) -> VCPUState:
        logger.info(f"[vCPU] State: {self.name} - Finished execution")
        # In a real runner, we detect this class type and break the loop.
        return self
