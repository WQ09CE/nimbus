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
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from nimbus.v2.core.memory.mmu import MMU
from nimbus.v2.core.protocol import ActionIR, Event, Fault, ToolResult
from nimbus.v2.core.runtime.decoder import InstructionDecoder
from nimbus.v2.os.gate import KernelGate

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
        max_iterations: Maximum Think-Act-Observe cycles
        default_timeout: Default timeout for tool execution (seconds)
        max_consecutive_thoughts: Max thoughts before forcing action
        max_sub_call_depth: Maximum recursion depth for SUB_CALLs
        emit_step_events: Whether to emit step lifecycle events
    """
    max_iterations: int = 50
    default_timeout: float = 60.0
    max_consecutive_thoughts: int = 5
    max_sub_call_depth: int = 10
    emit_step_events: bool = True


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
                # Check iteration limit
                if self._iteration >= self.config.max_iterations:
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
                    # Propagate non-retryable faults
                    if not step_result.fault.retryable:
                        return ToolResult(status="ERROR", fault=step_result.fault)
                    # For retryable faults, add error to memory and continue
                    self.mmu.add_assistant_message(
                        f"[Error] {step_result.fault.message}. Retrying..."
                    )

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

        try:
            # 1. THINK: Get LLM response
            think_start = time.time_ns()
            messages = self.mmu.assemble_context()
            response = await self.alu.chat(messages, tools=self.tools if self.tools else None)
            step_result.timing_ms["think"] = (time.time_ns() - think_start) // 1_000_000

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

            # Emit action events
            for action in actions:
                self._emit_event("ACTION_EMITTED", {
                    "action_id": action.id,
                    "kind": action.kind,
                    "name": action.name
                })

            # 3. EXECUTE: Handle each action
            exec_start = time.time_ns()
            for action in actions:
                result = await self._execute_action(action)
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

        return await handler(action)

    async def _handle_tool_call(self, action: ActionIR) -> ToolResult:
        """
        Handle TOOL_CALL action via Gate.

        Executes the tool through the kernel gate with permission checking
        and timeout enforcement.
        """
        result = await self.gate.syscall_tool(action, timeout_sec=self.config.default_timeout)

        # Update memory with tool result
        output_str = str(result.output) if result.output is not None else ""
        if result.fault:
            output_str = f"[Error] {result.fault.message}"

        self.mmu.add_tool_result(
            tool_call_id=action.id,
            name=action.name,
            content=output_str
        )

        # Reset consecutive thoughts counter on tool call
        self._consecutive_thoughts = 0

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
        """
        thought_text = action.args.get("text", "")

        # Add thought as assistant message
        self.mmu.add_assistant_message(thought_text)

        # Track consecutive thoughts
        self._consecutive_thoughts += 1

        # Warn if too many consecutive thoughts
        if self._consecutive_thoughts >= self.config.max_consecutive_thoughts:
            # Add a nudge to take action
            self.mmu.add_user_message(
                "[System] You've been thinking for a while. "
                "Please take an action or return a result."
            )
            self._consecutive_thoughts = 0

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
    # Helper Methods
    # =========================================================================

    def _reset(self) -> None:
        """Reset execution state for a new run."""
        self._iteration = 0
        self._consecutive_thoughts = 0
        self._is_running = False
        self._is_done = False
        self._final_result = None

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
