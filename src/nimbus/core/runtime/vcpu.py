"""
Nimbus v2 Virtual CPU (vCPU) - The Core Execution Engine

The vCPU implements the Think-Act-Observe loop:

    while not done:
        1. Think: LLM generates response (ALU)
        2. Decode: Parse into ActionIR (Decoder)
        3. Execute: Execute via Gate (Syscall)
        4. Observe: Update MMU memory (Memory)

Architecture:
    ┌─────────────────────────────────────────────────────────┐
    │                        vCPU                             │
    │  ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐  │
    │  │   ALU   │ → │ Decoder │ → │  Gate   │ → │   MMU   │  │
    │  │  (LLM)  │   │ (Parse) │   │ (Exec)  │   │ (Memory)│  │
    │  └─────────┘   └─────────┘   └─────────┘   └─────────┘  │
    │       ↑                                          │      │
    │       └──────────────────────────────────────────┘      │
    └─────────────────────────────────────────────────────────┘

Key Responsibilities:
- Orchestrate the Think-Act-Observe loop
- Handle all ActionKinds (TOOL_CALL, SUB_CALL, RETURN, etc.)
- Manage iteration limits and timeouts
- Emit events for observability
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Tuple

from nimbus.core.memory.mmu import MMU
from nimbus.core.protocol import ActionIR, Event, Fault, ToolResult
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.os.gate import KernelGate

# =============================================================================
# Tool Call Optimization Constants (Learned from opencode)
# =============================================================================

# Tools that modify state and should trigger "call return_result" hint
# After successful execution of these tools, we inject a hint to remind
# the LLM to call return_result if the task is complete.
TERMINAL_TOOLS = {"Edit", "Write", "Bash"}

# Doom loop detection threshold (from opencode's processor.ts)
# If the same tool is called with identical arguments this many times
# consecutively, we detect it as an infinite loop and force termination.
DOOM_LOOP_THRESHOLD = 3

# Tool name case mapping for auto-repair (from opencode's llm.ts)
# LLMs sometimes call tools with wrong casing (e.g., "read" instead of "Read")
# This mapping allows automatic correction.
TOOL_NAME_CANONICAL: Dict[str, str] = {
    "read": "Read",
    "glob": "Glob",
    "grep": "Grep",
    "bash": "Bash",
    "kill": "Kill",
    "write": "Write",
    "edit": "Edit",
    "return_result": "return_result",
    # Add canonical forms as well (no-op repair)
    "Read": "Read",
    "Glob": "Glob",
    "Grep": "Grep",
    "Bash": "Bash",
    "Kill": "Kill",
    "Write": "Write",
    "Edit": "Edit",
}

# =============================================================================
# LLM Client Protocol
# =============================================================================

class LLMResponse(Protocol):
    """Protocol for LLM response objects."""

    @property
    def content(self) -> Optional[str]:
        """Text content from the response."""
        ...

    @property
    def tool_calls(self) -> Optional[List[Any]]:
        """Tool calls from the response."""
        ...


class LLMClient(Protocol):
    """
    Protocol for LLM clients (ALU).

    This defines the interface that any LLM provider must implement
    to be used with the vCPU.
    """

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """
        Send messages to the LLM and get a response.

        Args:
            messages: List of message dicts (OpenAI format)
            tools: Optional list of tool definitions

        Returns:
            LLMResponse with content and/or tool_calls
        """
        ...


# =============================================================================
# vCPU Configuration
# =============================================================================

@dataclass
class VCPUConfig:
    """
    Configuration for vCPU.

    Attributes:
        max_iterations: Maximum Think-Act-Observe cycles before compaction
        default_timeout: Default timeout for tool execution (seconds)
        max_consecutive_thoughts: Max thoughts before forcing action
        max_sub_call_depth: Maximum recursion depth for SUB_CALLs
        emit_step_events: Whether to emit step lifecycle events
        compact_on_limit: Whether to compact memory when hitting iteration limit
        max_compactions: Maximum compactions before stopping (prevents infinite loops)
    """
    max_iterations: int = 50
    default_timeout: float = 60.0
    max_consecutive_thoughts: int = 1  # Auto-return on first text-only response
    max_sub_call_depth: int = 10
    emit_step_events: bool = True
    compact_on_limit: bool = True  # NEW: Trigger compaction instead of stopping
    max_compactions: int = 10  # NEW: Max compactions (10 x 50 = 500 iterations max)


# =============================================================================
# Step Result
# =============================================================================

@dataclass
class StepResult:
    """
    Result of a single vCPU step.

    Attributes:
        actions: ActionIR instructions produced in this step
        results: ToolResults from executing actions
        is_final: Whether this step produced a final result
        final_result: The final result if is_final is True
        fault: Any fault that occurred during the step
        timing_ms: Timing breakdown for the step
    """
    actions: List[ActionIR] = field(default_factory=list)
    results: List[ToolResult] = field(default_factory=list)
    is_final: bool = False
    final_result: Optional[Any] = None
    fault: Optional[Fault] = None
    timing_ms: Dict[str, int] = field(default_factory=dict)


# =============================================================================
# Virtual CPU
# =============================================================================

class VCPU:
    """
    Virtual CPU - The Core Execution Engine.

    The vCPU orchestrates the Think-Act-Observe loop, coordinating between
    the LLM (ALU), Decoder, Gate, and MMU to execute agent tasks.

    Example:
        vcpu = VCPU(
            alu=llm_client,
            decoder=InstructionDecoder(),
            gate=kernel_gate,
            mmu=mmu,
            config=VCPUConfig()
        )

        # Execute a goal
        result = await vcpu.execute("Find all Python files in src/")

        # Or step through manually
        while True:
            step = await vcpu.step()
            if step.is_final:
                break
    """

    def __init__(
        self,
        alu: LLMClient,
        decoder: InstructionDecoder,
        gate: KernelGate,
        mmu: MMU,
        config: Optional[VCPUConfig] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        Initialize the vCPU.

        Args:
            alu: LLM client for generating responses
            decoder: Instruction decoder for parsing LLM output
            gate: Kernel gate for executing syscalls
            mmu: Memory management unit for context
            config: vCPU configuration
            tools: Tool definitions for LLM
        """
        self.alu = alu
        self.decoder = decoder
        self.gate = gate
        self.mmu = mmu
        self.config = config or VCPUConfig()
        self.tools = tools or []

        # Execution state
        self._iteration = 0
        self._consecutive_thoughts = 0
        self._is_running = False
        self._is_done = False
        self._final_result: Optional[ToolResult] = None
        
        # Compaction state (NEW)
        self._compaction_count = 0
        self._compaction_callback: Optional[Callable[[], Awaitable[bool]]] = None

        # Doom loop detection (learned from opencode)
        # Tracks recent tool calls as (tool_name, args_json) tuples
        self._recent_tool_calls: List[tuple] = []
        self._doom_loop_count = 0
        self._consecutive_errors = 0

    # =========================================================================
    # Main Execution Loop
    # =========================================================================

    async def execute(self, goal: str) -> ToolResult:
        """
        Execute the main Think-Act-Observe loop until completion.

        This is the primary entry point for running a goal to completion.

        Args:
            goal: The goal to achieve

        Returns:
            ToolResult with the final result or error
        """
        # Initialize execution state
        self._reset()
        self._is_running = True

        # Add goal as user message
        self.mmu.add_user_message(goal)
        self._emit_event("STEP_STARTED", {"goal": goal, "iteration": 0})

        try:
            while not self._is_done:
                # Check iteration limit - trigger compaction instead of stopping
                if self._iteration >= self.config.max_iterations:
                    # Check if we've hit max compactions
                    if self._compaction_count >= self.config.max_compactions:
                        fault = Fault(
                            domain="RESOURCE",
                            code="BUDGET_EXCEEDED",
                            message=f"Exceeded maximum iterations ({self.config.max_iterations} x {self.config.max_compactions} compactions)",
                            retryable=False,
                            context={
                                "max_iterations": self.config.max_iterations,
                                "compactions": self._compaction_count,
                            }
                        )
                        return ToolResult(status="ERROR", fault=fault)
                    
                    # Try to compact memory and continue
                    if self.config.compact_on_limit:
                        compacted = await self._do_compaction()
                        if compacted:
                            # Reset iteration counter and continue
                            logger.info(
                                f"🗜️ Compaction #{self._compaction_count} complete, "
                                f"resetting iteration counter (was {self._iteration})"
                            )
                            self._iteration = 0
                            continue
                    
                    # Compaction disabled or failed - stop
                    fault = Fault(
                        domain="RESOURCE",
                        code="BUDGET_EXCEEDED",
                        message=f"Exceeded maximum iterations ({self.config.max_iterations})",
                        retryable=False,
                        context={"max_iterations": self.config.max_iterations}
                    )
                    return ToolResult(status="ERROR", fault=fault)

                # Execute one step
                step_result = await self.step()

                if step_result.fault:
                    self._consecutive_errors += 1
                    
                    # Track doom loops specifically
                    if step_result.fault.code == "DOOM_LOOP":
                        self._doom_loop_count += 1
                    
                    # Graceful termination conditions (checked BEFORE retryable check):
                    # - Too many consecutive errors (any type)
                    # - Any doom loop (even first one triggers graceful report)
                    # - Too many total doom loops
                    should_graceful_terminate = (
                        self._consecutive_errors >= 5 or 
                        self._doom_loop_count >= 1 or  # First doom loop triggers graceful termination
                        step_result.fault.code == "DOOM_LOOP"
                    )
                    
                    if should_graceful_terminate:
                        # Let LLM generate a natural response about the failure
                        graceful_response = await self._generate_llm_failure_response(
                            goal=goal,
                            fault=step_result.fault,
                            iterations=self._iteration,
                        )
                        return ToolResult(
                            status="OK",  # Report as OK with explanation, not ERROR
                            output=graceful_response,
                            is_final=True
                        )
                    
                    # Propagate non-retryable faults (but not DOOM_LOOP, handled above)
                    if not step_result.fault.retryable:
                        return ToolResult(status="ERROR", fault=step_result.fault)
                    
                    # For retryable faults, add error to memory and continue
                    self.mmu.add_assistant_message(
                        f"[Error] {step_result.fault.message}. Retrying..."
                    )
                else:
                    # Reset consecutive error counter on success
                    self._consecutive_errors = 0

                if step_result.is_final:
                    return step_result.final_result or ToolResult(
                        status="OK",
                        output="Task completed",
                        is_final=True
                    )

        except asyncio.CancelledError:
            return ToolResult(
                status="CANCELLED",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message="Execution was cancelled",
                    retryable=True
                )
            )
        except Exception as e:
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message=str(e),
                    retryable=False,
                    context={"exception_type": type(e).__name__}
                )
            )
        finally:
            self._is_running = False

        # Should not reach here
        return ToolResult(status="OK", output="Execution completed", is_final=True)

    async def step(self) -> StepResult:
        """
        Execute a single Think-Act-Observe cycle.

        This is useful for step-by-step execution or debugging.

        Returns:
            StepResult with actions, results, and status
        """
        self._iteration += 1
        step_result = StepResult()
        start_time = time.time_ns()

        self._emit_event("STEP_STARTED", {"iteration": self._iteration})

        from nimbus.core.logging import get_logger
        logger = get_logger("kernel.vcpu")

        try:
            # 1. THINK: Get LLM response
            logger.info(f"Thinking... (Iteration {self._iteration})")
            think_start = time.time_ns()
            messages = self.mmu.assemble_context()
            
            # Log tool availability
            tools_to_pass = self.tools if self.tools else None
            if tools_to_pass:
                tool_names = [t.get('function', {}).get('name', '?') for t in tools_to_pass]
                logger.info(f"🔧 Passing {len(tools_to_pass)} tools to LLM: {tool_names}")
            else:
                logger.warning(f"⚠️ No tools available in VCPU.tools! LLM will not be able to call tools.")
            
            response = await self.alu.chat(messages, tools=tools_to_pass)
            think_duration = (time.time_ns() - think_start) // 1_000_000
            step_result.timing_ms["think"] = think_duration
            
            tool_calls_count = len(response.tool_calls) if response.tool_calls else 0
            content_preview = (response.content[:100] + "...") if response.content and len(response.content) > 100 else response.content
            logger.info(f"Thought complete ({think_duration}ms) | Tool Calls: {tool_calls_count} | Content: {content_preview or '(no content)'}")

            # 2. DECODE: Parse into ActionIR
            decode_start = time.time_ns()
            try:
                actions = self.decoder.decode(
                    content=response.content,
                    tool_calls=response.tool_calls
                )
            except Fault as f:
                step_result.fault = f
                step_result.timing_ms["total"] = (time.time_ns() - start_time) // 1_000_000
                return step_result
            step_result.actions = actions
            step_result.timing_ms["decode"] = (time.time_ns() - decode_start) // 1_000_000

            # IMPORTANT: Add assistant message with tool_calls to memory BEFORE executing tools
            # This is required by OpenAI/OpenRouter API format:
            # 1. user message
            # 2. assistant message (with tool_calls)  <-- We add this here
            # 3. tool message (with tool_call_id)     <-- Added by _handle_tool_call
            #
            # Without this, the API will reject the request because tool results
            # reference tool_call_ids that don't exist in the conversation.
            if response.tool_calls:
                # Convert tool_calls to OpenAI format for storage
                tool_calls_for_storage = []
                for tc in response.tool_calls:
                    # Handle both object-style and dict-style tool calls
                    if hasattr(tc, 'id'):
                        tool_calls_for_storage.append({
                            "id": tc.id,
                            "type": getattr(tc, 'type', 'function'),
                            "function": {
                                "name": tc.function.name if hasattr(tc, 'function') else tc.get('function', {}).get('name', ''),
                                "arguments": tc.function.arguments if hasattr(tc, 'function') else tc.get('function', {}).get('arguments', '{}')
                            }
                        })
                    else:
                        # Already a dict
                        tool_calls_for_storage.append(tc)

                self.mmu.add_assistant_with_tool_calls(
                    content=response.content,
                    tool_calls=tool_calls_for_storage
                )

            # Emit action events
            for action in actions:
                self._emit_event("ACTION_EMITTED", {
                    "action_id": action.id,
                    "kind": action.kind,
                    "name": action.name
                })
                
                # Log action plan
                if action.kind == "TOOL_CALL":
                    # Create a summarized args string for logging
                    args_summary = json.dumps(action.args)
                    if len(args_summary) > 200:
                        args_summary = args_summary[:197] + "..."
                    logger.info(f"Plan: Call tool '{action.name}' with args: {args_summary}")
                elif action.kind == "THOUGHT":
                    pass # Already logged thought above/below
                else:
                    logger.info(f"Plan: Action {action.kind} ({action.name})")

            # 3. EXECUTE: Handle each action
            exec_start = time.time_ns()
            for action in actions:
                logger.debug(f"Executing action: {action.kind} - {action.name}")
                result = await self._execute_action(action)
                logger.debug(f"Action result: status={result.status}, fault={result.fault}")
                step_result.results.append(result)

                # Check for final result
                if result.is_final:
                    step_result.is_final = True
                    step_result.final_result = result
                    self._is_done = True
                    break

                # Check for non-retryable fault
                if result.fault and not result.fault.retryable:
                    step_result.fault = result.fault
                    break

            step_result.timing_ms["execute"] = (time.time_ns() - exec_start) // 1_000_000

        except Exception as e:
            step_result.fault = Fault(
                domain="KERNEL",
                code="SYSTEM_ERROR",
                message=str(e),
                retryable=False,
                context={"exception_type": type(e).__name__}
            )

        step_result.timing_ms["total"] = (time.time_ns() - start_time) // 1_000_000
        return step_result

    # =========================================================================
    # Action Handlers
    # =========================================================================

    async def _execute_action(self, action: ActionIR) -> ToolResult:
        """
        Execute a single ActionIR instruction.

        Routes to the appropriate handler based on action kind.

        Args:
            action: The ActionIR to execute

        Returns:
            ToolResult from execution
        """
        handlers = {
            "TOOL_CALL": self._handle_tool_call,
            "SUB_CALL": self._handle_sub_call,
            "RETURN": self._handle_return,
            "THOUGHT": self._handle_thought,
            "POST_IPC": self._handle_post_ipc,
            "REQUEST_REPLAN": self._handle_request_replan,
            "CANCEL": self._handle_cancel,
        }

        handler = handlers.get(action.kind)
        if handler is None:
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="ILL_INSTRUCTION",
                    message=f"Unknown action kind: {action.kind}",
                    retryable=False
                )
            )

        try:
            return await handler(action)
        except Exception as e:
            from nimbus.core.logging import get_logger
            logger = get_logger("kernel.vcpu")
            logger.error(f"Exception in handler for {action.kind}/{action.name}: {e}", exc_info=True)
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="HANDLER_ERROR",
                    message=f"Handler error: {e}",
                    retryable=False
                )
            )

    async def _handle_tool_call(self, action: ActionIR) -> ToolResult:
        """
        Handle TOOL_CALL action via Gate.

        Executes the tool through the kernel gate with permission checking
        and timeout enforcement.

        Features:
        - Tool name auto-repair (learned from opencode): Fixes common LLM
          casing errors like "read" -> "Read".
        - File edit history tracking (improved from opencode): Detects when
          LLM tries to re-apply the same edit that already succeeded.
        - Doom loop detection (learned from opencode): Detects when the same
          tool is called with identical arguments multiple times consecutively.
        - Terminal tool hints: Reminds LLM to call return_result after
          state-modifying operations (Edit, Write, Bash).
        """
        # Auto-repair tool name if needed (learned from opencode's llm.ts)
        # Only repair built-in tools; custom tools pass through unchanged
        original_name = action.name
        canonical_name = TOOL_NAME_CANONICAL.get(action.name.lower())

        if canonical_name and canonical_name != action.name:
            # Log the repair for built-in tools
            self._emit_event("TOOL_NAME_REPAIRED", {
                "original": action.name,
                "repaired": canonical_name
            })
            # Create a new action with the corrected name
            action = ActionIR(
                kind=action.kind,
                name=canonical_name,
                id=action.id,
                args=action.args,
                meta=action.meta,
            )
        # Note: Custom tools (not in TOOL_NAME_CANONICAL) are allowed to pass through.
        # The gate will handle unknown tool errors if the tool doesn't exist.

        # Check for doom loop BEFORE executing (learned from opencode)
        args_json = json.dumps(action.args, sort_keys=True)
        current_call = (action.name, args_json)

        # Track this call
        self._recent_tool_calls.append(current_call)

        # Keep only the last DOOM_LOOP_THRESHOLD calls
        if len(self._recent_tool_calls) > DOOM_LOOP_THRESHOLD:
            self._recent_tool_calls = self._recent_tool_calls[-DOOM_LOOP_THRESHOLD:]

        # Check if all recent calls are identical (doom loop detected)
        if len(self._recent_tool_calls) == DOOM_LOOP_THRESHOLD:
            if all(call == current_call for call in self._recent_tool_calls):
                # Doom loop detected! 
                self._emit_event("DOOM_LOOP_DETECTED", {
                    "tool": action.name,
                    "args": action.args,
                    "consecutive_count": DOOM_LOOP_THRESHOLD
                })

                # Clear the recent calls to allow recovery
                self._recent_tool_calls.clear()

                # Provide tool-specific guidance for recovery
                guidance = self._get_doom_loop_guidance(action.name)

                # IMPROVED: Return a recoverable error that instructs LLM to adapt
                # Instead of is_final=True (which terminates immediately), we give
                # LLM one more chance to recover by using return_result properly
                return ToolResult(
                    status="ERROR",
                    output=(
                        f"[Operation Failed] The {action.name} operation failed after multiple attempts.\n\n"
                        f"What happened: The same operation was tried {DOOM_LOOP_THRESHOLD} times without success.\n\n"
                        f"Recovery guidance:\n{guidance}\n\n"
                        f"IMPORTANT: Please call return_result now to report what you were trying to do "
                        f"and what obstacle you encountered. Do NOT retry the same operation."
                    ),
                    is_final=False,  # Give LLM a chance to gracefully report
                    fault=Fault(
                        domain="RUNTIME",
                        code="DOOM_LOOP",
                        message=f"Operation failed: {action.name} unsuccessful after {DOOM_LOOP_THRESHOLD} attempts",
                        retryable=False
                    )
                )

        # Execute the tool
        result = await self.gate.syscall_tool(action, timeout_sec=self.config.default_timeout)

        # Update memory with tool result
        output_str = str(result.output) if result.output is not None else ""
        if result.fault:
            output_str = f"[Error] {result.fault.message}"

        # Inject hint for terminal tools on success - append to tool result
        # This reminds LLM to call return_result after state-modifying operations
        if action.name in TERMINAL_TOOLS and result.status == "OK":
            output_str += (
                "\n\n[IMPORTANT] Operation completed successfully. "
                "If your task is complete, call return_result immediately with a summary. "
                "Do NOT call more tools to verify - trust the success message above."
            )

        self.mmu.add_tool_result(
            tool_call_id=action.id,
            name=action.name,
            content=output_str
        )

        # Reset consecutive thoughts counter on tool call
        self._consecutive_thoughts = 0

        # Clear doom loop tracker on successful different tool call
        # (only keep tracking if we just detected a potential loop start)
        if result.status == "OK" and len(self._recent_tool_calls) > 1:
            # If this call is different from the previous one, reset tracker
            if len(self._recent_tool_calls) >= 2 and self._recent_tool_calls[-1] != self._recent_tool_calls[-2]:
                self._recent_tool_calls = [current_call]

        return result

    async def _handle_sub_call(self, action: ActionIR) -> ToolResult:
        """
        Handle SUB_CALL action by pushing a new frame.

        Creates a new stack frame for the subprocess and recursively
        executes the subgoal.
        """
        # Check recursion depth
        if self.mmu.stack_depth >= self.config.max_sub_call_depth:
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="RESOURCE",
                    code="BUDGET_EXCEEDED",
                    message=f"Maximum sub-call depth ({self.config.max_sub_call_depth}) exceeded",
                    retryable=False
                )
            )

        # Get the goal from action
        goal = action.args.get("goal", action.name)

        # Push new frame
        frame_id = self.mmu.push_frame(goal, meta={"action_id": action.id})

        self._emit_event("PROC_SPAWNED", {
            "frame_id": frame_id,
            "goal": goal,
            "depth": self.mmu.stack_depth
        })

        # Add goal as user message in new frame
        self.mmu.add_user_message(goal)

        # Continue execution in this frame (recursive Think-Act-Observe)
        # The execution will continue until a RETURN is encountered
        return ToolResult(status="OK", output=f"Started subtask: {goal}")

    async def _handle_return(self, action: ActionIR) -> ToolResult:
        """
        Handle RETURN action by popping the current frame.

        Returns the result to the parent frame or finalizes execution
        if at root frame.
        """
        result = action.args.get("result", action.args.get("output", ""))

        if self.mmu.is_root_frame:
            # At root frame - this is the final result
            self._emit_event("PROC_FINISHED", {
                "result": str(result)[:200],  # Truncate for event
                "is_final": True
            })

            return ToolResult(
                status="OK",
                output=result,
                is_final=True
            )
        else:
            # Pop frame and return to parent
            self.mmu.pop_frame(result)

            self._emit_event("PROC_FINISHED", {
                "result": str(result)[:200],
                "is_final": False
            })

            return ToolResult(status="OK", output=f"Subtask completed: {result}")

    async def _handle_thought(self, action: ActionIR) -> ToolResult:
        """
        Handle THOUGHT action by recording to memory.

        Thoughts are internal reasoning that don't produce side effects.

        Auto-return: If consecutive thoughts reach the limit, treat the last
        thought as a final result. This handles cases where the LLM responds
        with text instead of calling return_result.
        """
        thought_text = action.args.get("text", "")

        # Add thought as assistant message
        self.mmu.add_assistant_message(thought_text)

        # Track consecutive thoughts
        self._consecutive_thoughts += 1
        
        from nimbus.core.logging import get_logger
        logger = get_logger("kernel.vcpu")
        logger.info(f"Thought processed. Count: {self._consecutive_thoughts}/{self.config.max_consecutive_thoughts}")

        # Check if this thought looks like a final answer (heuristic)
        # If it doesn't suggest using tools, and it's long enough, it might be an answer
        # This is a bit aggressive but helps with LLMs that refuse to call return_result
        is_likely_answer = (
            len(thought_text) > 10 and 
            "tool" not in thought_text.lower() and 
            "call" not in thought_text.lower()
        )

        # Auto-return if too many consecutive thoughts without tool calls
        # This is a safety net for LLMs that don't follow instructions
        if self._consecutive_thoughts >= self.config.max_consecutive_thoughts:
            self._emit_event("AUTO_RETURN", {
                "reason": "max_consecutive_thoughts",
                "thought_count": self._consecutive_thoughts,
                "result": thought_text[:200]
            })
            self._consecutive_thoughts = 0
            
            # If we are auto-returning, we should reset the thought counter
            # so we don't get into a loop of auto-returns if the user continues chatting
            
            # Treat the last thought as final result
            return ToolResult(
                status="OK",
                output=thought_text,
                is_final=True
            )

        return ToolResult(status="OK", output="Thought recorded")

    async def _handle_post_ipc(self, action: ActionIR) -> ToolResult:
        """
        Handle POST_IPC action by publishing to the IPC bus.

        Publishes a reference to the IPC bus for cross-process communication.
        """
        channel = action.args.get("channel", "default")
        key = action.args.get("key", action.id)
        value_ref = action.args.get("value_ref", "")
        meta = action.args.get("meta", {})

        self.gate.post_ipc(channel, key, value_ref, meta)

        return ToolResult(status="OK", output=f"Published to {channel}:{key}")

    async def _handle_request_replan(self, action: ActionIR) -> ToolResult:
        """
        Handle REQUEST_REPLAN action by signaling the scheduler.

        Requests the kernel scheduler to replan the current DAG.
        """
        reason = action.args.get("reason", {})
        if isinstance(reason, str):
            reason = {"message": reason}

        self.gate.request_replan(reason)

        return ToolResult(status="OK", output="Replan requested")

    async def _handle_cancel(self, action: ActionIR) -> ToolResult:
        """
        Handle CANCEL action by marking execution as done.

        Cancels the current execution and returns with cancelled status.
        """
        reason = action.args.get("reason", "Cancelled by agent")
        self._is_done = True

        return ToolResult(
            status="CANCELLED",
            output=reason,
            is_final=True,
            fault=Fault(
                domain="KERNEL",
                code="SYSTEM_ERROR",
                message=reason,
                retryable=False
            )
        )

    # =========================================================================
    # Compaction
    # =========================================================================
    
    def set_compaction_callback(
        self, callback: Callable[[], Awaitable[bool]]
    ) -> None:
        """
        Set a callback for memory compaction.
        
        The callback should:
        1. Summarize and compress the MMU's context
        2. Return True if compaction succeeded, False otherwise
        
        This is typically set by AgentOS to use the CompactionEngine.
        
        Args:
            callback: Async function that performs compaction
        """
        self._compaction_callback = callback
    
    async def _do_compaction(self) -> bool:
        """
        Trigger memory compaction.
        
        If a callback is set, use it. Otherwise, use MMU's built-in compression.
        
        Returns:
            True if compaction succeeded
        """
        self._compaction_count += 1
        
        self._emit_event("COMPACTION_START", {
            "iteration": self._iteration,
            "compaction_count": self._compaction_count,
        })
        
        try:
            if self._compaction_callback:
                # Use external compaction (e.g., AgentOS CompactionEngine)
                success = await self._compaction_callback()
            else:
                # Use MMU's built-in compression
                success = self._compact_mmu()
            
            self._emit_event("COMPACTION_END", {
                "success": success,
                "compaction_count": self._compaction_count,
            })
            
            return success
            
        except Exception as e:
            logger.error(f"Compaction failed: {e}")
            self._emit_event("COMPACTION_END", {
                "success": False,
                "error": str(e),
            })
            return False
    
    def _compact_mmu(self) -> bool:
        """
        Use MMU's built-in compression as fallback.
        
        This generates a simple summary of older messages and replaces them.
        """
        try:
            # Get all messages from current frame
            if not self.mmu._stack:
                return False
            
            frame = self.mmu._stack[-1]
            messages = frame.messages
            
            if len(messages) < self.mmu.config.keep_recent_messages * 2:
                # Not enough messages to compress
                return False
            
            # Keep recent messages, summarize older ones
            keep_count = self.mmu.config.keep_recent_messages
            older = messages[:-keep_count]
            recent = messages[-keep_count:]
            
            # Generate summary using MMU's summarizer
            summary = self.mmu._summarize_messages(older)
            
            # Create summary message
            from nimbus.core.memory.context import Message
            summary_msg = Message(
                role="system",
                content=f"[Context Summary - {len(older)} messages compressed]\n{summary}",
                meta={"compressed": True, "original_count": len(older)}
            )
            
            # Replace messages in frame
            frame.messages = [summary_msg] + recent
            
            logger.info(
                f"MMU compaction: {len(older)} messages → 1 summary, "
                f"kept {len(recent)} recent"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"MMU compaction failed: {e}")
            return False

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _reset(self) -> None:
        """Reset execution state for a new run."""
        self._iteration = 0
        self._consecutive_thoughts = 0
        self._is_running = False
        self._is_done = False
        self._final_result = None
        self._recent_tool_calls = []  # Reset doom loop tracker
        self._doom_loop_count = 0  # Track how many doom loops occurred
        self._consecutive_errors = 0  # Track consecutive errors
        self._compaction_count = 0  # Reset compaction counter

    async def _generate_llm_failure_response(
        self,
        goal: str,
        fault: Fault,
        iterations: int,
    ) -> str:
        """Let LLM generate a natural response about the failure."""
        try:
            # Build context about what happened
            recent_errors = []
            for msg in self.mmu.assemble_context()[-6:]:  # Last few messages
                content = msg.get("content", "")
                if isinstance(content, str) and ("error" in content.lower() or "failed" in content.lower()):
                    recent_errors.append(content[:200])
            
            error_context = "\n".join(recent_errors[-3:]) if recent_errors else fault.message
            
            # Create a focused prompt for the LLM
            prompt = f"""You tried to help the user with this task but encountered repeated failures.

User's request: {goal}

What went wrong: {fault.message}

Error details: {error_context}

Generate a brief, friendly response (2-3 sentences) that:
1. Acknowledges what you tried to do
2. Explains why it didn't work (in simple terms)
3. Optionally suggests what the user could try or ask for instead

Be conversational and helpful, not robotic. Don't use bullet points or formatting."""

            # Call LLM without tools to get a simple text response
            response = await self.alu.chat(
                [{"role": "user", "content": prompt}],
                tools=None  # No tools, just generate text
            )
            
            if response.content:
                return response.content.strip()
            else:
                # Fallback to template if LLM returns empty
                return self._generate_graceful_failure_report(goal, fault, iterations)
                
        except Exception as e:
            # If LLM call fails, use template fallback
            from nimbus.core.logging import get_logger
            logger = get_logger("kernel.vcpu")
            logger.warning(f"Failed to generate LLM failure response: {e}")
            return self._generate_graceful_failure_report(goal, fault, iterations)

    def _generate_graceful_failure_report(
        self, 
        goal: str, 
        fault: Fault,
        iterations: int,
    ) -> str:
        """Generate a natural, conversational failure report."""
        
        # Extract key info from fault context
        context = fault.context or {}
        tool_name = context.get("tool", "")
        
        # Check for common error patterns and generate natural responses
        error_msg = fault.message.lower()
        
        # File not found
        if "not found" in error_msg or "no such file" in error_msg:
            # Try to extract filename from the error
            import re
            file_match = re.search(r'[\'"]?([^\'"]+\.\w+)[\'"]?', fault.message)
            filename = file_match.group(1) if file_match else "the file"
            return (
                f"I couldn't find `{filename}`. "
                f"The file might not exist, or the path could be incorrect. "
                f"Would you like me to search for similar files?"
            )
        
        # Permission denied
        if "permission" in error_msg or "access denied" in error_msg:
            return (
                f"I don't have permission to access that resource. "
                f"You may need to check the file permissions or run with elevated privileges."
            )
        
        # Timeout
        if fault.code == "TIMEOUT":
            return (
                f"The operation took too long and timed out. "
                f"This might be because the resource is slow or unavailable. "
                f"Would you like me to try a different approach?"
            )
        
        # Doom loop (repeated failures)
        if fault.code == "DOOM_LOOP":
            # Try to give context-specific advice
            if "Read" in str(context.get("tool", "")):
                return (
                    f"I tried to read the file multiple times but it doesn't seem to exist. "
                    f"Would you like me to list the files in that directory to help find the right one?"
                )
            elif "Edit" in str(context.get("tool", "")):
                return (
                    f"I couldn't make the edit - the text I was looking for might have changed. "
                    f"Would you like me to show you the current file content?"
                )
            else:
                return (
                    f"I tried this operation several times without success. "
                    f"The approach I was using doesn't seem to be working. "
                    f"Could you provide more details or suggest an alternative approach?"
                )
        
        # Generic fallback - still conversational
        return (
            f"I ran into some trouble completing this task. "
            f"Error: {fault.message}. "
            f"Let me know if you'd like me to try a different approach."
        )

    def _get_doom_loop_guidance(self, tool_name: str) -> str:
        """Get tool-specific guidance for recovering from a doom loop.

        This provides actionable advice based on the tool that triggered
        the infinite loop, helping the LLM understand how to proceed.
        """
        guidance_map = {
            "Edit": (
                "EDIT TOOL GUIDANCE:\n"
                "1. Use the Read tool FIRST to see the current file content\n"
                "2. Common failure reasons:\n"
                "   - The old_string does not match the file content exactly\n"
                "   - The file was already modified by a previous successful edit\n"
                "   - Whitespace or indentation mismatch\n"
                "   - The text appears multiple times (need more context)\n"
                "3. Recovery steps:\n"
                "   - Read the file to get the current state\n"
                "   - If the change you wanted is already there, move on\n"
                "   - If you need a different edit, use text from the fresh Read\n"
                "4. If your task is complete, call return_result immediately"
            ),
            "Write": (
                "WRITE TOOL GUIDANCE:\n"
                "- If Write is failing repeatedly, the file path may be invalid\n"
                "- Check if the directory exists using Glob or Bash\n"
                "- Ensure you have permission to write to this location\n"
                "- Consider using a different approach if Write keeps failing"
            ),
            "Bash": (
                "BASH TOOL GUIDANCE:\n"
                "- The same command is failing repeatedly\n"
                "- Check if the command syntax is correct\n"
                "- Verify required dependencies are installed\n"
                "- Try a different approach to achieve the same goal"
            ),
            "Read": (
                "READ TOOL GUIDANCE:\n"
                "- The file may not exist at the specified path\n"
                "- Use Glob to search for the correct file path\n"
                "- Check if the path is relative vs absolute"
            ),
            "Glob": (
                "GLOB TOOL GUIDANCE:\n"
                "- The pattern may not match any files\n"
                "- Try a broader pattern (e.g., **/*.py instead of specific path)\n"
                "- Verify the search directory is correct"
            ),
            "Grep": (
                "GREP TOOL GUIDANCE:\n"
                "- The search pattern may not exist in any files\n"
                "- Try a simpler or broader search pattern\n"
                "- Check if the path/directory is correct"
            ),
        }

        return guidance_map.get(tool_name, (
            f"GENERAL GUIDANCE:\n"
            f"- The {tool_name} tool is failing with the same arguments\n"
            f"- Review the error message from previous attempts\n"
            f"- Try a different approach or different arguments\n"
            f"- If stuck, call return_result to report the issue"
        ))

    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emit an event if event emission is enabled."""
        if not self.config.emit_step_events:
            return

        if self.gate.events:
            self.gate.events.emit(Event(
                type=event_type,  # type: ignore
                pid=self.gate.pid,
                data=data
            ))

    # =========================================================================
    # State Accessors
    # =========================================================================

    @property
    def iteration(self) -> int:
        """Get current iteration count."""
        return self._iteration

    @property
    def is_running(self) -> bool:
        """Check if vCPU is currently running."""
        return self._is_running

    @property
    def is_done(self) -> bool:
        """Check if execution is complete."""
        return self._is_done

    def get_state(self) -> Dict[str, Any]:
        """Get vCPU state for debugging/checkpointing."""
        return {
            "iteration": self._iteration,
            "consecutive_thoughts": self._consecutive_thoughts,
            "is_running": self._is_running,
            "is_done": self._is_done,
            "stack_depth": self.mmu.stack_depth,
            "mmu_state": self.mmu.get_state(),
        }
