"""
Atomic states for the Nimbus vCPU FSM.

These classes encapsulate the logic for each stage of the Think-Act-Observe loop
and dictating strict transitions.
"""

import asyncio
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
            iteration_count=self.iteration_count,
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

    def restore_from_snapshot(self, model: Any) -> None:
        self.iteration_count = model.iteration_count
        self.max_iterations = model.max_iterations
        self.consecutive_thoughts = model.consecutive_thoughts
        self.consecutive_errors = model.consecutive_errors
        self.consecutive_empty_responses = model.consecutive_empty_responses
        self.compaction_count = model.compaction_count
        self.max_compactions = model.max_compactions
        self.tool_failure_counts = dict(model.tool_failure_counts)
        self.path_not_found_count = model.path_not_found_count
        self.doom_loop_count = model.doom_loop_count

class StateInit(VCPUState):
    """Initializes the VCPU step and loads necessary context."""
    
    @property
    def name(self) -> str:
        return "INIT"

    async def execute(self, ctx: FSMContext) -> VCPUState:
        logger.debug(f"[vCPU] State: {self.name} - Init step {ctx.state.iteration_count + 1}")
        
        try:
            # Reset per-step active variables
            ctx.current_actions = []
            ctx.pending_error = None
            ctx.pending_parse_error = None
            ctx.final_result = None
            
            return StateReasoning()
        except Exception as e:
            logger.exception("FSM Initialization failed")
            ctx.pending_error = e
            return StateErrorRecovery()


class StateReasoning(VCPUState):
    """The ALU execution phase. Calls the LLM and parses the response."""
    
    @property
    def name(self) -> str:
        return "REASONING"

    async def execute(self, ctx: FSMContext) -> VCPUState:
        logger.debug(f"[vCPU] State: {self.name}")
        
        ctx.state.increment_iteration()
        
        # 1. JIT Context Assembly happens in ALU/Adapter Layer now
        # VCPU simply passes the MMU reference.
        
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
                mmu=ctx.mmu,
                tools=ctx.tools,
                on_chunk=_on_chunk
            )
        except Exception as e:
            logger.exception("ALU execution failed")
            ctx.pending_error = e
            return StateErrorRecovery()
            
        # 4. Decode Response directly (No Middleware Pipeline)
        try:
            actions: List[ActionIR] = ctx.decoder.decode_response(
                response=response,
                text_is_final=ctx.manifest.text_is_final if ctx.manifest else True,
                role=ctx.manifest.role if ctx.manifest else None,
                model_features=ctx.manifest.features if ctx.manifest else None
            )
            ctx.current_actions = actions
        except Exception as e:
            logger.error(f"Failed to parse ALU response: {e}")
            # Save the assistant's raw response before error recovery
            if hasattr(response, 'content') and response.content:
                ctx.mmu.add_assistant_message(response.content)
            ctx.pending_parse_error = f"Failed to decode response: {str(e)}. Please output valid format."
            return StateErrorRecovery()

        if not actions:
            # Save the assistant's response to MMU so it isn't lost.
            # Without this, error recovery would inject an orphan message
            # with no preceding assistant turn.
            if hasattr(response, 'content') and response.content:
                ctx.mmu.add_assistant_message(response.content)
            # Handle Empty Response (Agent broke protocol and output nothing useful)
            ctx.pending_parse_error = "You returned an empty response or invalid format. You MUST use a tool or return a final answer."
            return StateErrorRecovery()
            
        # Route based on Actions
        has_tool_call = False
        for action in actions:
            if action.kind in ("RETURN", "REPLY"):
                raw = action.args.get("result", action.args.get("value", action.args.get("text")))
                ctx.final_result = raw if raw is not None else str(action.args)
                
                # Persist the final response to MMU since we are bypassing StateObservation.
                # IMPORTANT: Do NOT persist tool_calls here! Control-flow tools like
                # SubmitResult/return_result are routed as RETURN by the decoder and
                # never executed via Gate, so there will be no matching tool_result
                # message. Saving tool_calls without tool_results breaks the web-ui
                # (it shows the tool call as forever "running") and violates the
                # OpenAI message ordering contract (assistant+tool_calls must be
                # followed by tool result messages).
                if hasattr(response, "content") and response.content:
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


def _persist_tool_turn(ctx: FSMContext) -> None:
    """
    Save the current assistant tool_use + tool_results to MMU.

    Ensures tool_use/tool_result pairing integrity so the conversation
    history is always well-formed.  Called by:
      - StateObservation (normal flow)
      - StateActionExecution (before error recovery, to prevent orphan
        tool_results that crash the Anthropic API)
    """
    tool_actions = [a for a in ctx.current_actions if a.kind in ("TOOL_CALL", "SUB_CALL")]
    if not tool_actions:
        return

    # Extract non-blocking thought text
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

    # Write tool results — every tool_use MUST have a matching tool_result
    for action in ctx.current_actions:
        if action.kind in ("TOOL_CALL", "SUB_CALL"):
            result = getattr(action, 'result', None)
            if result:
                content = result.output if hasattr(result, "output") else str(result)
            else:
                content = "[Tool was not executed due to earlier error]"
            ctx.mmu.add_tool_result(
                tool_call_id=action.id,
                name=action.name,
                content=content,
                tool_args=action.args if result else None,
            )


class StateActionExecution(VCPUState):
    """Executes the proposed Actions via the KernelGate."""
    
    @property
    def name(self) -> str:
        return "ACTION_EXECUTION"

    async def execute(self, ctx: FSMContext) -> VCPUState:
        logger.debug(f"[vCPU] State: {self.name} - Executing {len(ctx.current_actions)} actions")
        
        # 1. Filter executable actions (skip THOUGHT)
        executable_actions = [
            action for action in ctx.current_actions
            if action.kind in ("TOOL_CALL", "SUB_CALL")
        ]
        
        # 2. Serial path: 0 or 1 executable action — no concurrency overhead
        if len(executable_actions) <= 1:
            for action in executable_actions:
                logger.info(f"⚙️  [vCPU] Executing Tool: {action.name}")
                try:
                    if ctx.config.dry_run:
                        logger.info(f"🌵 [Dry-Run] Simulating tool: {action.name}")
                        result = ToolResult(
                            status="OK",
                            output=f"[Dry-Run] Successfully simulated execution of {action.name} with args {action.args}"
                        )
                    else:
                        result = await ctx.gate.syscall_tool(action)
                    
                    if not hasattr(action, 'result'):
                        action.result = result
                    ctx.current_results.append(result)
                except Exception as e:
                    logger.warning(f"Tool {action.name} failed with Exception: {e}")
                    result = ToolResult(
                        status="ERROR",
                        output=f"Tool failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
                    )
                    action.result = result
                    ctx.current_results.append(result)
                    # Persist assistant tool_use + tool_results before error
                    # recovery so the conversation stays well-formed.
                    _persist_tool_turn(ctx)
                    ctx.pending_error = e
                    return StateErrorRecovery()
            return StateObservation()
        
        # 3. Concurrent path: 2+ executable actions
        tool_names = [action.name for action in executable_actions]
        logger.info(f"⚡ [vCPU] Executing {len(executable_actions)} tools concurrently: {tool_names}")
        
        async def _execute_one(action):
            """Execute a single action with independent error handling."""
            try:
                if ctx.config.dry_run:
                    logger.info(f"🌵 [Dry-Run] Simulating tool concurrently: {action.name}")
                    result = ToolResult(
                        status="OK",
                        output=f"[Dry-Run] Successfully simulated execution of {action.name} with args {action.args}"
                    )
                else:
                    result = await ctx.gate.syscall_tool(action)
                
                if not hasattr(action, 'result'):
                    action.result = result
                return result, None
            except Exception as e:
                logger.warning(f"Tool {action.name} failed with Exception: {e}")
                result = ToolResult(
                    status="ERROR",
                    output=f"Tool failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
                )
                action.result = result
                return result, e
        
        # Launch all concurrently and collect results in original order
        outcomes = await asyncio.gather(*[
            _execute_one(action) for action in executable_actions
        ])
        
        # 4. Collect results in order and check for errors
        first_error = None
        for result, error in outcomes:
            ctx.current_results.append(result)
            if error is not None and first_error is None:
                first_error = error
        
        # 5. If any action failed, record and enter error recovery
        if first_error is not None:
            # Persist assistant tool_use + tool_results (including errors)
            # before error recovery so the conversation stays well-formed.
            _persist_tool_turn(ctx)
            ctx.pending_error = first_error
            return StateErrorRecovery()
        
        return StateObservation()


class StateObservation(VCPUState):
    """Writes results to the MMU and checks global limits."""
    
    @property
    def name(self) -> str:
        return "OBSERVATION"

    async def execute(self, ctx: FSMContext) -> VCPUState:
        logger.debug(f"[vCPU] State: {self.name} - Writing to MMU")
        
        # 1. Persist assistant tool_use + tool_results via shared helper
        tool_actions = [a for a in ctx.current_actions if a.kind in ("TOOL_CALL", "SUB_CALL")]
        if tool_actions:
            _persist_tool_turn(ctx)
        else:
            # 2b. No tool calls — write standalone thoughts
            for action in ctx.current_actions:
                if action.kind == "THOUGHT":
                    ctx.mmu.add_assistant_message(action.args.get('text', ''))

        # Track consecutive thoughts vs productive actions.
        # This prevents infinite THOUGHT loops where the LLM keeps emitting
        # text without ever calling a tool or returning a final answer.
        if tool_actions:
            ctx.state.on_action()  # resets consecutive_thoughts to 0
            ctx.state.consecutive_errors = 0
        else:
            ctx.state.on_thought()  # increments consecutive_thoughts
            max_thoughts = getattr(ctx.config, "max_consecutive_thoughts", 8)
            if ctx.state.consecutive_thoughts >= max_thoughts:
                logger.warning(
                    f"Agent exceeded max consecutive thoughts ({max_thoughts}). "
                    f"Forcing termination to prevent infinite loop."
                )
                # Gather the last thought as the final result
                last_text = ""
                for action in ctx.current_actions:
                    if action.kind == "THOUGHT":
                        last_text = action.args.get("text", "")
                ctx.final_result = last_text or "Agent terminated after too many consecutive thoughts without action."
                ctx.current_actions = []
                ctx.current_results = []
                return StateCompleted()

        # Checked by KernelGate before execution.
        
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

        # Compaction is no longer handled by the FSM state machine.
        # It will be handled "Just-In-Time" by a Context Pipeline before the ALU call.

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

        # Limit consecutive error recovery loops to prevent Token/Budget runaway
        MAX_CONSECUTIVE_ERRORS = 3
        ctx.state.consecutive_errors += 1

        if ctx.state.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            logger.warning(
                f"Fatal: {ctx.state.consecutive_errors} consecutive errors. "
                f"Aborting to prevent infinite error loop and token drain."
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
            
        # Exponential Backoff to throttle API rate limits during tight doom loops (1.5^N seconds)
        import asyncio
        backoff_seconds = min(30.0, 1.5 ** ctx.state.consecutive_errors)
        logger.info(f"[vCPU] Applying {backoff_seconds:.2f}s exponential backoff due to consecutive errors...")
        await asyncio.sleep(backoff_seconds)

        # Inject error feedback as a user message so the LLM sees it.
        # IMPORTANT: We must NOT use add_tool_result() here because there
        # is no preceding assistant tool_use to pair with — that would
        # create an orphan tool_result that crashes the Anthropic API
        # ("unexpected tool_use_id found in tool_result blocks").
        if error_msg:
            ctx.mmu.add_user_message(f"[System Error] {error_msg}")

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
