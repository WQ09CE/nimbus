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
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol

from nimbus.core.memory.mmu import MMU
from nimbus.core.models.manifest import ModelManifest, get_model_manifest
from nimbus.core.persistence import SessionCheckpointModel
from nimbus.core.protocol import ActionIR, Event, Fault, ToolResult
from nimbus.core.runtime.checkpoint_manager import CheckpointManager
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.doom_loop import DoomLoopDetector
from nimbus.core.runtime.edit_fuse import EditFuse
from nimbus.core.runtime.empty_result_handler import EmptyResultHandler
from nimbus.core.runtime.error_handler import ErrorHandlerRegistry
from nimbus.core.runtime.execution_state import ExecutionState
from nimbus.core.runtime.failure_reporter import FailureReporter
from nimbus.core.runtime.pipeline import ResponsePipeline
from nimbus.core.runtime.recovery_executor import RecoveryContext, RecoveryExecutor
from nimbus.core.runtime.tracer import TraceManager
from nimbus.os.gate import KernelGate

# =============================================================================
# Tool Call Optimization Constants (Learned from opencode)
# =============================================================================

# Tools that modify state and may indicate task progress.
# State-modifying tools that change files/system state.
# Note: We no longer inject continuation hints after these tools.
# The LLM decides whether to verify based on the user's original request.
STATE_MODIFYING_TOOLS = {"Edit", "Write"}

# Doom loop detection threshold (from opencode's processor.ts)
# If the same tool is called with identical arguments this many times
# consecutively, we detect it as an infinite loop and force termination.
DOOM_LOOP_THRESHOLD = 3

# Tool name case mapping for auto-repair (from opencode's llm.ts)
# LLMs sometimes call tools with wrong casing (e.g., "read" instead of "Read")
# This mapping allows automatic correction.
TOOL_NAME_CANONICAL: Dict[str, str] = {
    "read": "Read",
    "bash": "Bash",
    "kill": "Kill",
    "write": "Write",
    "edit": "Edit",
    "return_result": "return_result",
    "submitresult": "SubmitResult",
    "submit_result": "SubmitResult",
    "SubmitResult": "SubmitResult",
    # Add canonical forms as well (no-op repair)
    "Read": "Read",
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
        max_consecutive_thoughts: Text-only responses before treating as final (1 = immediate stop)
        max_sub_call_depth: Maximum recursion depth for SUB_CALLs
        emit_step_events: Whether to emit step lifecycle events
        compact_on_limit: Whether to compact memory when hitting iteration limit
        max_compactions: Maximum compactions before stopping (prevents infinite loops)
    """

    max_iterations: int = 50
    default_timeout: float = 60.0
    max_consecutive_thoughts: int = 2  # 2 = default to 2 thoughts before stopping
    max_sub_call_depth: int = 10
    emit_step_events: bool = True
    compact_on_limit: bool = True  # NEW: Trigger compaction instead of stopping
    max_compactions: int = 100  # NEW: Max compactions (increased for infinite context)
    # Goal pinning
    pin_goal: bool = True  # Pin user goal to survive compaction
    goal_max_length: int = 500  # Summarize goal if longer than this
    # LLM call watchdog
    llm_call_timeout: float = 300.0  # Single LLM call timeout (seconds), prevents streaming hang
    # Tracing
    enable_tracing: bool = True  # Enable structural trace logs


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
        session_id: str = "default_session",
        manifest: Optional[ModelManifest] = None,
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
            session_id: ID for tracing and persistence
            manifest: Model capabilities manifest
        """
        self.alu = alu
        self.decoder = decoder
        self.gate = gate
        self.mmu = mmu
        self.config = config or VCPUConfig()
        self.tools = tools or []
        self.session_id = session_id

        # Initialize Model Capability Pipeline
        self.manifest = manifest or get_model_manifest("default")
        self.pipeline = ResponsePipeline(self.manifest.features, role=self.manifest.role)

        # self._message_queue removed in Phase 1 Refactor

        # Tracing
        _trace_workspace = getattr(mmu, "nimfs_workspace", None) or "."
        self.tracer = TraceManager(session_id, workspace=_trace_workspace) if self.config.enable_tracing else None

        # Centralized execution state (refactored from 15+ instance variables)
        self._state = ExecutionState.from_config(
            max_iterations=self.config.max_iterations,
            max_compactions=self.config.max_compactions,
            max_tool_failures=6,
        )

        # Compaction callback (external)
        self._compaction_callback: Optional[Callable[[], Awaitable[bool]]] = None

        # Extracted components (single responsibility)
        self._doom_detector = DoomLoopDetector(threshold=DOOM_LOOP_THRESHOLD)
        self._edit_fuse = EditFuse(max_failures_per_file=4)
        self._error_registry = ErrorHandlerRegistry()
        self._recovery_executor = RecoveryExecutor(
            tool_executor=self.gate.syscall_tool,
            error_registry=self._error_registry,
        )
        self._failure_reporter = FailureReporter(alu)
        self._checkpoint_manager = CheckpointManager(self._state, self.mmu)
        self._empty_result_handler = EmptyResultHandler(
            state=self._state,
            error_registry=self._error_registry,
            max_tool_failures=self._state.max_tool_failures,
        )

        # Legacy compatibility properties (will be removed in future)
        self._max_consecutive_empty = 5  # Stop after 5 consecutive empty responses

        # External signals dict (shared with AgentOS process for soft-timeout)
        self.signals: Dict[str, Any] = {}

    def request_pause(self) -> None:
        """Request the vCPU to pause execution at the next safe point."""
        self._state.interruption_requested = True
        from nimbus.core.logging import get_logger

        get_logger("kernel.vcpu").info("Interruption requested for next step.")

    def _collect_partial_results(self) -> Optional[str]:
        """Collect any partial results produced during this session (for soft-timeout)."""
        # Check for Write tool calls that succeeded
        written_files = []
        for msg in self.mmu.current_frame.messages:
            if msg.role == "tool":
                content = msg.content or ""
                if isinstance(content, str) and "Successfully wrote" in content and " to " in content:
                    path = content.split(" to ")[-1].strip()
                    written_files.append(path)

        if written_files:
            return (
                "(specialist timed out but produced partial output)\n\n"
                "Files written before timeout:\n" +
                "\n".join(f"  - {f}" for f in written_files)
            )

        # Fall back to last substantial assistant message
        for msg in reversed(self.mmu.current_frame.messages):
            if msg.role == "assistant" and msg.content:
                text = str(msg.content)
                if len(text) > 200:
                    return f"(specialist timed out, last output fragment):\n\n{text[:2000]}"

        return None

    def inject_message(self, content: str) -> None:
        """
        Inject a user message into the running execution loop.
        """
        # NO-OP: Functionality moved to AgentOS.inject_message
        # This method is deprecated and will be removed in future versions.
        # It's kept here just in case some legacy code calls it,
        # but it won't actually do anything in the new AgentOS loop.
        pass

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
        self._state.is_running = True

        # Pin goal to ensure it survives compaction
        if self.config.pin_goal:
            pinned_goal = await self._prepare_goal_for_pinning(goal)
            self.mmu.pin_user_goal(pinned_goal)

        # Add goal as user message (always use original)
        self.mmu.add_user_message(goal)
        self._emit_event("STEP_STARTED", {"goal": goal, "iteration": 0})

        try:
            while not self._state.is_done:
                # Check interruption request
                if self._state.interruption_requested:
                    self._emit_event("INTERRUPTION_HANDLED", {"iteration": self._state.iteration})
                    return ToolResult(
                        status="CANCELLED",
                        fault=Fault(
                            domain="RUNTIME",
                            code="INTERRUPTED",
                            message="Execution interrupted by user request",
                            retryable=True,  # Can resume
                        ),
                    )

                # Check iteration limit - trigger compaction instead of stopping
                if self._state.iteration >= self.config.max_iterations:
                    # Check if we've hit max compactions
                    if self._state.compaction_count >= self.config.max_compactions:
                        fault = Fault(
                            domain="RESOURCE",
                            code="BUDGET_EXCEEDED",
                            message=f"Exceeded maximum iterations ({self.config.max_iterations} x {self.config.max_compactions} compactions)",
                            retryable=False,
                            context={
                                "max_iterations": self.config.max_iterations,
                                "compactions": self._state.compaction_count,
                            },
                        )
                        return ToolResult(status="ERROR", fault=fault)

                    # Try to compact memory and continue
                    if self.config.compact_on_limit:
                        compacted = await self._do_compaction()
                        if compacted:
                            # Reset iteration counter and continue
                            from nimbus.core.logging import get_logger

                            get_logger("kernel.vcpu").info(
                                f"🗜️ Compaction #{self._state.compaction_count} complete, "
                                f"resetting iteration counter (was {self._state.iteration})"
                            )
                            self._state.iteration = 0
                            continue

                    # Compaction disabled or failed - stop
                    fault = Fault(
                        domain="RESOURCE",
                        code="BUDGET_EXCEEDED",
                        message=f"Exceeded maximum iterations ({self.config.max_iterations})",
                        retryable=False,
                        context={"max_iterations": self.config.max_iterations},
                    )
                    return ToolResult(status="ERROR", fault=fault)

                # Execute one step
                step_result = await self.step()

                if step_result.fault:
                    self._state.consecutive_errors += 1

                    # Graceful termination conditions (checked BEFORE retryable check):
                    # - Too many consecutive errors (any type)
                    # - Any doom loop (even first one triggers graceful report)
                    # - Too many total doom loops
                    # - Empty response loop (LLM stuck/confused)
                    should_graceful_terminate = (
                        self._state.consecutive_errors >= 5
                        or self._state.doom_loop_count
                        >= 2  # Second doom loop triggers graceful termination
                        or step_result.fault.code == "EMPTY_RESPONSE_LOOP"  # LLM stuck
                    )

                    if should_graceful_terminate:
                        # Let LLM generate a natural response about the failure
                        graceful_response = await self._generate_llm_failure_response(
                            goal=goal,
                            fault=step_result.fault,
                            iterations=self._state.iteration,
                        )
                        return ToolResult(
                            status="OK",  # Report as OK with explanation, not ERROR
                            output=graceful_response,
                            is_final=True,
                        )

                    # Propagate non-retryable faults (but not DOOM_LOOP, handled above)
                    if not step_result.fault.retryable:
                        return ToolResult(status="ERROR", fault=step_result.fault)

                    # For retryable faults, add error to memory and continue
                    self.mmu.add_assistant_message(
                        f"[Error] {step_result.fault.message}. Retrying...",
                        # Mark as ephemeral so it disappears after successful retry
                        # Actually MMU.add_assistant_message doesn't support meta kwarg yet in signature
                        # We need to check or update MMU signature, or use add_message directly
                    )
                    # Let's fix this by using add_message directly if needed or updating MMU wrapper
                    # For now, let's look at how add_assistant_message is implemented.
                    # It just calls current_frame.add_assistant_message(content) which creates Message

                    # Since we can't easily change the signature of add_assistant_message everywhere safely right now,
                    # let's access the last message and tag it.
                    if self.mmu.current_frame.messages:
                        self.mmu.current_frame.messages[-1].meta["ephemeral"] = True
                else:
                    # Reset consecutive error counter on success
                    self._state.consecutive_errors = 0

                # Check execution result
                if step_result.is_final:
                    final_result = step_result.final_result or ToolResult(
                        status="OK", output="Task completed", is_final=True
                    )

                    # Add completion marker to history to prevent context bleeding
                    # This tells the LLM that the previous goal is DONE.
                    result_preview = str(final_result.output)[:100].replace("\n", " ")
                    self.mmu.add_system_message(f"✓ Task completed. Result: {result_preview}...")

                    return final_result

        except asyncio.CancelledError:
            return ToolResult(
                status="CANCELLED",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message="Execution was cancelled",
                    retryable=True,
                ),
            )
        except Exception as e:
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message=str(e),
                    retryable=False,
                    context={"exception_type": type(e).__name__},
                ),
            )
        finally:
            self._state.is_running = False

        # Should not reach here
        return ToolResult(status="OK", output="Execution completed", is_final=True)

    async def step(self) -> StepResult:
        """
        Execute a single Think-Act-Observe cycle.

        This is useful for step-by-step execution or debugging.

        Returns:
            StepResult with actions, results, and status
        """
        # Start Trace
        if self.tracer:
            self.tracer.start_step(self._state.iteration + 1)

        # Check soft_timeout signal — finalize immediately with partial results
        if self.signals.get("soft_timeout"):
            from nimbus.core.logging import get_logger as _gl
            _gl("kernel.vcpu").warning("vCPU received soft_timeout signal, collecting partial results...")
            partial_output = self._collect_partial_results()
            res = StepResult(
                is_final=True,
                final_result=partial_output or "(specialist timed out before producing output)",
                fault=Fault(
                    domain="VCPU",
                    code="SOFT_TIMEOUT",
                    message="Specialist soft-timed out, returning partial results",
                ),
            )
            if self.tracer:
                self.tracer.record_fault(res.fault)
                self.tracer.finish_step()
            return res

        # Check interruption request at step start (Legacy support for vCPU-driven interrupt)
        # In Phase 1, AgentOS handles this before calling step(), but we keep this as safety net.
        if self._state.interruption_requested:
            self._emit_event("INTERRUPTION_HANDLED", {"iteration": self._state.iteration})
            res = StepResult(
                is_final=True,
                fault=Fault(
                    domain="RUNTIME",
                    code="INTERRUPTED",
                    message="Execution interrupted by user request",
                    retryable=True,
                ),
            )
            if self.tracer: self.tracer.finish_step()
            return res

        self._state.iteration += 1
        step_result = StepResult()
        start_time = time.time_ns()

        self._emit_event("STEP_STARTED", {"iteration": self._state.iteration})

        from nimbus.core.logging import get_logger

        logger = get_logger("kernel.vcpu")

        # Check if memory needs compaction BEFORE assembling context
        # Return a special Fault for AgentOS to handle
        if self.mmu.needs_compression():
            current_tokens = self.mmu.estimate_tokens()
            threshold = int(
                self.mmu.config.max_context_tokens * self.mmu.config.compress_threshold
            )
            logger.warning(
                f"🧠 Context overflow: {current_tokens} tokens exceeds threshold ({threshold}/{self.mmu.config.max_context_tokens})"
            )
            res = StepResult(
                is_final=False,
                fault=Fault(
                    domain="MEMORY",
                    code="CONTEXT_OVERFLOW",
                    message=f"Context overflow: {current_tokens} tokens exceeds threshold ({threshold}/{self.mmu.config.max_context_tokens}). Compaction required.",
                    retryable=True,  # Can retry after compaction
                    context={
                        "current_tokens": current_tokens,
                        "threshold": threshold,
                        "max_tokens": self.mmu.config.max_context_tokens,
                    },
                ),
            )
            if self.tracer:
                self.tracer.record_fault(res.fault)
                self.tracer.finish_step()
            return res

        try:
            # 1. THINK: Get LLM response
            logger.info(f"Thinking... (Iteration {self._state.iteration})")
            think_start = time.time_ns()

            # Reset pipeline for new turn
            self.pipeline.reset()

            messages = self.mmu.assemble_context()

            # TRACE: Record Context
            if self.tracer:
                pinned_t = self.mmu.get_pinned().token_estimate() if self.mmu.get_pinned() else 0
                # Rough estimate for frame tokens
                frame_t = self.mmu.estimate_tokens() - pinned_t
                self.tracer.record_context(messages, pinned_tokens=pinned_t, frame_tokens=frame_t)

            # Debug: Dump full context to file if NIMBUS_DUMP_CONTEXT is set
            import os

            if os.environ.get("NIMBUS_DUMP_CONTEXT"):
                self._dump_context_to_file(messages, self._state.iteration)

            # Enhanced logging: Show context summary
            msg_count = len(messages)
            last_msgs = messages[-3:] if len(messages) >= 3 else messages
            logger.debug(f"📋 Context: {msg_count} messages, last {len(last_msgs)}:")
            for i, msg in enumerate(last_msgs):
                role = msg.get("role", "?")
                content = msg.get("content", "")
                if isinstance(content, str):
                    preview = content[:200] + "..." if len(content) > 200 else content
                    preview = preview.replace("\n", "\\n")
                else:
                    preview = f"[{type(content).__name__}]"
                tool_calls = msg.get("tool_calls", [])
                tc_info = f" | {len(tool_calls)} tool_calls" if tool_calls else ""
                logger.debug(f"  [{role}]{tc_info}: {preview}")

            # Log tool availability
            tools_to_pass = self.tools if self.tools else None
            if tools_to_pass:
                tool_names = [t.get("function", {}).get("name", "?") for t in tools_to_pass]
                logger.info(f"🔧 Passing {len(tools_to_pass)} tools to LLM: {tool_names}")
            else:
                logger.warning(
                    "⚠️ No tools available in VCPU.tools! LLM will not be able to call tools."
                )

            # Callback for streaming thinking process
            def on_think_chunk(chunk: str):
                processed = self.pipeline.process_chunk(chunk)
                if processed and not self._state.suppress_streaming:
                    self._emit_event("THINKING", {"content": processed})

            try:
                response = await asyncio.wait_for(
                    self.alu.chat(messages, tools=tools_to_pass, on_chunk=on_think_chunk),
                    timeout=self.config.llm_call_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"LLM single-call timeout after {self.config.llm_call_timeout}s "
                    f"(iteration {self._state.iteration})"
                )
                # Treat as empty response -- will trigger thought handling / poke
                from nimbus.adapters.types import VcpuLLMResponse
                response = VcpuLLMResponse(content="", tool_calls=[])

            # Reset suppress flag after receiving response
            self._state.suppress_streaming = False

            # CLEANUP EPHEMERAL MESSAGES
            # Once the LLM has responded, the previous ephemeral hints (errors/retries)
            # have served their purpose and should be removed to keep context clean.
            cleaned_count = self.mmu.cleanup_ephemeral_messages()
            if cleaned_count > 0:
                logger.debug(f"🧹 Cleaned {cleaned_count} ephemeral messages from history.")

            # TRACE: Record LLM Response
            if self.tracer:
                self.tracer.record_llm_response(response.content, response.tool_calls)

            think_duration = (time.time_ns() - think_start) // 1_000_000
            step_result.timing_ms["think"] = think_duration

            tool_calls_count = len(response.tool_calls) if response.tool_calls else 0
            content_preview = (
                (response.content[:200] + "...")
                if response.content and len(response.content) > 200
                else response.content
            )

            # Enhanced logging: Show full response details
            if tool_calls_count == 0 and not response.content:
                # Empty response - this is suspicious!
                self._state.consecutive_empty_responses += 1
                logger.warning(
                    f"⚠️ EMPTY RESPONSE #{self._state.consecutive_empty_responses} from LLM (Iteration {self._state.iteration}) "
                    f"- no content, no tool calls!"
                )
                
                # Active Intervention: Poke the model if it's silent
                # This helps wake up models (like Gemini) that get stuck after context injection
                if self._state.consecutive_empty_responses >= 1:
                     poke_msg = "[System] Your last response was empty. Please continue with the task."
                     self.mmu.add_user_message(poke_msg)
                     if self.mmu.current_frame.messages:
                         self.mmu.current_frame.messages[-1].meta["ephemeral"] = True
                     logger.info("👉 Poked silent model with system message")

                logger.warning(
                    "   This may indicate: context too long, task too hard, or LLM confusion"
                )

                # Check if we've hit too many consecutive empty responses
                if self._state.consecutive_empty_responses >= self._max_consecutive_empty:
                    logger.error(
                        f"🛑 STOPPING: {self._state.consecutive_empty_responses} consecutive empty responses. "
                        f"LLM appears to be stuck or confused."
                    )
                    step_result.fault = Fault(
                        domain="RUNTIME",
                        code="EMPTY_RESPONSE_LOOP",
                        message=f"LLM returned {self._state.consecutive_empty_responses} consecutive empty responses",
                        retryable=False,
                        context={
                            "consecutive_empty": self._state.consecutive_empty_responses,
                            "iteration": self._state.iteration,
                        },
                    )
                    step_result.timing_ms["total"] = (time.time_ns() - start_time) // 1_000_000
                    if self.tracer:
                        self.tracer.record_fault(step_result.fault)
                        self.tracer.finish_step()
                    return step_result
            else:
                # Reset counter on non-empty response
                self._state.consecutive_empty_responses = 0
                logger.info(
                    f"Thought complete ({think_duration}ms) | Tool Calls: {tool_calls_count} | Content: {content_preview or '(no content)'}"
                )

            # Log tool calls details
            if response.tool_calls:
                for tc in response.tool_calls:
                    if hasattr(tc, "function"):
                        name = tc.function.name
                        args = tc.function.arguments[:100] if tc.function.arguments else "{}"
                    else:
                        name = tc.get("function", {}).get("name", "?")
                        args = str(tc.get("function", {}).get("arguments", "{}"))[:100]
                    logger.debug(f"  🔧 Tool: {name}({args})")

            # 2. PIPELINE & DECODE: Process response through middleware
            decode_start = time.time_ns()
            raw_content = response.content  # Snapshot for change detection

            try:
                actions = self.pipeline.process_response(response, self.decoder)
            except Fault as f:
                # If it's a hallucination fault from decoder (fallback), handle it
                if f.code == "ILL_INSTRUCTION":
                    # Defensive measure: handles genuine model hallucinations
                    # (e.g. text-based tool simulation like "[Called tool...]").
                    # Note: The main cause of Gemini "[Historical context:]" pattern
                    # was fixed in pi-ai-server.ts (correct provider/model/api metadata).
                    # This handler is kept as a safety net for edge cases.
                    self._state.hallucination_count = getattr(self._state, 'hallucination_count', 0) + 1
                    max_hallucinations = 3
                    if self._state.hallucination_count >= max_hallucinations:
                        logger.error(
                            f"🛑 Hallucination limit ({max_hallucinations}) reached. "
                            f"Model cannot stop hallucinating. Forcing completion."
                        )
                        step_result.is_final = True
                        step_result.final_result = ToolResult(
                            status="ERROR",
                            output="抱歉，模型多次尝试均未能正确调用工具。请尝试：\n"
                                   "1. 重新描述你的请求\n"
                                   "2. 切换到其他模型（如 Claude 或 GPT）\n"
                                   "3. 将复杂任务拆分为更小的步骤",
                            is_final=True,
                        )
                        self._state.is_done = True
                        step_result.timing_ms["total"] = (time.time_ns() - start_time) // 1_000_000
                        return step_result

                    # Inject a user-role correction (no fake assistant message)
                    self.mmu.add_user_message(
                        "[System] INVALID RESPONSE — you wrote a tool call as text instead of "
                        "using the function calling API. Your next response MUST contain an "
                        "actual function call (tool_call), not a text description. "
                        "Continue with the task where you left off."
                    )
                    if self.mmu.current_frame.messages:
                        self.mmu.current_frame.messages[-1].meta["ephemeral"] = True

                    logger.warning(
                        f"🛡️ Hallucination #{self._state.hallucination_count}/{max_hallucinations}: "
                        f"injected correction"
                    )
                step_result.fault = f
                step_result.timing_ms["total"] = (time.time_ns() - start_time) // 1_000_000
                if self.tracer:
                    self.tracer.record_fault(f)
                    self.tracer.finish_step()
                return step_result

            # Check if pipeline filtered everything (Hallucination Guard)
            # If actions empty, raw_content existed, and response.content is now None/Empty
            if not actions and raw_content and not response.content and not response.tool_calls:
                 logger.warning("🛡️ Pipeline stripped all content (Hallucination detected). Injecting hint.")
                 self.mmu.add_user_message(
                     "[System] Your response contained invalid text-formatted tool calls. "
                     "Use the function calling API directly."
                 )
                 if self.mmu.current_frame.messages:
                     self.mmu.current_frame.messages[-1].meta["ephemeral"] = True

                 # We consume this step as a correction step
                 step_result.timing_ms["decode"] = (time.time_ns() - decode_start) // 1_000_000
                 step_result.timing_ms["total"] = (time.time_ns() - start_time) // 1_000_000
                 return step_result

            step_result.actions = actions
            step_result.timing_ms["decode"] = (time.time_ns() - decode_start) // 1_000_000

            # TRACE: Record Actions
            if self.tracer:
                self.tracer.record_actions(actions)

            # 3. MEMORY UPDATE
            # Determine if this was a split response (Thought + Tool)
            is_split = False
            if self.manifest.features.split_mixed_responses:
                # Check structure: First action is THOUGHT, and there are TOOL_CALLs
                has_thought = len(actions) > 0 and actions[0].kind == "THOUGHT"
                has_tools = any(a.kind == "TOOL_CALL" for a in actions)
                is_split = has_thought and has_tools

            if is_split:
                 # Standard split response: Text + Tool Calls
                 # We combine them into a single assistant message to keep context clean
                 thought_text = actions[0].args.get("text", "")
                 self.mmu.add_assistant_with_tool_calls(content=thought_text, tool_calls=response.tool_calls)

            elif response.tool_calls:
                 # Standard Tool Call (or stripped content)
                 self.mmu.add_assistant_with_tool_calls(content=response.content, tool_calls=response.tool_calls)

            elif response.content:
                 # Standard Content
                 self.mmu.add_assistant_message(response.content)

            # Emit action events
            for action in actions:
                self._emit_event(
                    "ACTION_EMITTED",
                    {"action_id": action.id, "kind": action.kind, "name": action.name},
                )

                # Log action plan
                if action.kind == "TOOL_CALL":
                    # Create a summarized args string for logging
                    args_summary = json.dumps(action.args)
                    if len(args_summary) > 200:
                        args_summary = args_summary[:197] + "..."
                    logger.info(f"Plan: Call tool '{action.name}' with args: {args_summary}")
                elif action.kind == "THOUGHT":
                    pass  # Already logged thought above/below
                else:
                    logger.info(f"Plan: Action {action.kind} ({action.name})")

            # 3. EXECUTE: Execute actions in parallel
            exec_start = time.time_ns()

            # We execute all actions concurrently to maximize performance.
            # This follows the "parallel tool calls" paradigm (e.g. OpenAI).
            # Order is preserved in the results list.
            if actions:
                logger.debug(f"Executing {len(actions)} actions in parallel...")
                results = await asyncio.gather(
                    *(self._execute_action(action) for action in actions),
                    return_exceptions=True
                )

                for i, result in enumerate(results):
                    # Handle exceptions (programmatic errors in _execute_action)
                    if isinstance(result, BaseException):
                        # Treat as system error
                        logger.error(f"Action execution failed: {result}", exc_info=result)
                        fault_result = ToolResult(
                            status="ERROR",
                            fault=Fault(
                                domain="KERNEL",
                                code="SYSTEM_ERROR",
                                message=str(result),
                                retryable=False
                            )
                        )
                        step_result.results.append(fault_result)
                        step_result.fault = fault_result.fault
                    else:
                        # Normal ToolResult
                        # Apply smart truncation to prevent context explosion
                        # Skip truncation for SubmitResult (is_final=True) — let the
                        # specialist_tools NimFS offload handle large final outputs
                        # so no data is permanently lost.
                        is_final = getattr(result, 'is_final', False)
                        if (not is_final
                                and result.output
                                and isinstance(result.output, str)
                                and len(result.output) > 100_000):
                            total_len = len(result.output)
                            head = result.output[:50_000]
                            tail = result.output[-50_000:]
                            result.output = f"{head}\n\n... [Output truncated, {total_len - 100_000} characters hidden. If you need the full content, use specific tools to read segments.] ...\n\n{tail}"
                            logger.info(f"✂️ Truncated long output from {total_len} to {len(result.output)} chars")

                        step_result.results.append(result)

                        # Check for final result (priority to first one found)
                        if result.is_final and not step_result.is_final:
                            step_result.is_final = True
                            step_result.final_result = result
                            self._state.is_done = True

                        # Check for non-retryable fault
                        if result.fault and not result.fault.retryable and not step_result.fault:
                            step_result.fault = result.fault

            step_result.timing_ms["execute"] = (time.time_ns() - exec_start) // 1_000_000

            # TRACE: Record Results
            if self.tracer:
                self.tracer.record_results(step_result.results)
                if step_result.fault:
                    self.tracer.record_fault(step_result.fault)

        except Exception as e:
            # Check for context overflow in exception message
            error_msg = str(e)
            if "prompt is too long" in error_msg or "context_length_exceeded" in error_msg:
                 # Parse actual token count from API error message
                 # Try multiple error message formats from different providers
                 _actual_tokens = None
                 # Anthropic: "prompt is too long: 200393 tokens > 200000 maximum"
                 _match = re.search(r'(\d+)\s*tokens?\s*>', error_msg)
                 if _match:
                     _actual_tokens = int(_match.group(1))
                 # OpenAI: "maximum context length is 128000 tokens... resulted in 150000 tokens"
                 if not _actual_tokens:
                     _match = re.search(r'resulted?\s+in\s+(\d+)\s*tokens', error_msg)
                     if _match:
                         _actual_tokens = int(_match.group(1))
                 # Generic: find the largest number followed by "token(s)"
                 if not _actual_tokens:
                     _all = re.findall(r'(\d+)\s*tokens?', error_msg)
                     if _all:
                         _actual_tokens = max(int(x) for x in _all)
                 # Fallback to estimate
                 if not _actual_tokens:
                     _actual_tokens = self.mmu.estimate_tokens()
                     logger.warning(f"Could not parse token count from error, using estimate: {_actual_tokens}")
                 step_result.fault = Fault(
                    domain="MEMORY",
                    code="CONTEXT_OVERFLOW",
                    message=error_msg,
                    retryable=True, # Allow AgentOS to retry after compaction
                    context={
                        "error": error_msg,
                        "current_tokens": _actual_tokens,
                        "threshold": self.mmu.config.max_context_tokens,
                    }
                 )
            else:
                 step_result.fault = Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message=error_msg,
                    retryable=False,
                    context={"exception_type": type(e).__name__},
                )
            if self.tracer:
                self.tracer.record_fault(step_result.fault)

        step_result.timing_ms["total"] = (time.time_ns() - start_time) // 1_000_000

        # TRACE: Finish Step
        if self.tracer:
            self.tracer.finish_step()

        return step_result

    # =========================================================================
    # State Persistence
    # =========================================================================

    def create_checkpoint(
        self, session_id: str, reason: str = "periodic"
    ) -> SessionCheckpointModel:
        """
        Create a full session checkpoint (vCPU + MMU).

        This captures the exact state of execution between steps.

        Args:
            session_id: Session ID
            reason: Reason for checkpoint (periodic/interruption/error)

        Returns:
            SessionCheckpointModel (Pydantic model)
        """
        return self._checkpoint_manager.create(session_id, reason)

    def restore_from_checkpoint(self, checkpoint: SessionCheckpointModel) -> None:
        """
        Restore session state from checkpoint.

        Args:
            checkpoint: SessionCheckpointModel to restore from
        """
        self._checkpoint_manager.restore(checkpoint)

    # =========================================================================
    # Action Handlers
    # =========================================================================

    @property
    def _compaction_count(self) -> int:
        return self._state.compaction_count

    @property
    def _doom_loop_count(self) -> int:
        """Read-only property delegating to DoomLoopDetector.loop_count."""
        return self._doom_detector.loop_count

    async def _handle_sub_call(self, action: ActionIR) -> ToolResult:
        """Handle SUB_CALL action (Simulated)."""
        return ToolResult(status="OK", output="Subroutine called (simulated)")

    def _is_stale_thought(self, current: str, previous: str, threshold: float = 0.8) -> bool:
        """Detect near-duplicate thoughts using character-level overlap."""
        if not current or not previous:
            return False
        # Quick check: identical
        if current.strip() == previous.strip():
            return True
        # Length-based heuristic: very different lengths = likely different content
        ratio = min(len(current), len(previous)) / max(len(current), len(previous))
        if ratio < 0.5:
            return False
        # Prefix match: same start = likely same thought loop
        prefix_len = min(200, len(current), len(previous))
        if current[:prefix_len] == previous[:prefix_len]:
            return True
        return False

    def _detect_promise_gate(self, text: str) -> bool:
        """Detect if the LLM is making promises to act without actually calling tools.

        Promise Gate: LLM outputs text like "Now let me write..." or "I'll create the file..."
        but doesn't include any tool calls. Intentionally permissive — false positives only
        cause a stronger poke, while false negatives let the model spin.
        """
        if not text or len(text) < 20:
            return False

        text_lower = text.lower()

        # English promise patterns
        promise_patterns_en = [
            "let me ",
            "i'll ",
            "i will ",
            "now i ",
            "now let",
            "now write",
            "now create",
            "let's ",
            "going to ",
            "about to ",
            "proceed to ",
            "next i ",
            "writing the ",
            "creating the ",
        ]

        # Chinese promise patterns
        promise_patterns_zh = [
            "让我",
            "我来",
            "我将",
            "现在",
            "接下来",
            "下面",
            "开始写",
            "开始创建",
            "马上",
        ]

        for pattern in promise_patterns_en + promise_patterns_zh:
            if pattern in text_lower:
                return True

        return False

    async def _handle_thought(self, action: ActionIR) -> ToolResult:
        """Handle THOUGHT action.

        Design (Asymmetric):
        - Frontend agents (orchestrator, standard, chat): Text = final answer → stop.
        - Backend specialists (explorer, implementer, etc.): Text is just CoT thinking.
          They MUST call SubmitResult to finish. A poke reminds them to use tools.
        """
        from nimbus.core.logging import get_logger
        logger = get_logger("kernel.vcpu")
        role = getattr(self.manifest, 'role', 'unknown')
        logger.info(f"VCPU _handle_thought. Role: {role}, Action: {action.kind}")

        # 1. Pipeline splitting (non-blocking) - from MixedResponseSplitter
        # These are thought fragments that accompany tool calls, NOT final answers
        if action.meta and action.meta.get("non_blocking"):
             return ToolResult(
                 status="OK",
                 output=action.args.get("text"),
                 is_final=False
             )

        # 2. Frontend roles: text output = final answer (chat with user)
        FRONTEND_ROLES = {"orchestrator", "standard", "chat"}
        if role in FRONTEND_ROLES:
            return await self._handle_return(action)

        # 2.5 Staleness detection — near-duplicate thoughts → immediate return
        current_text = action.args.get("text", "")
        if self._state.pending_thought_text:
            if self._is_stale_thought(current_text, self._state.pending_thought_text):
                logger.info(
                    f"THOUGHT stale (duplicate detected), forcing RETURN "
                    f"(consecutive={self._state.consecutive_thoughts})"
                )
                return await self._handle_return(action)

        # 3. Backend specialists: text is just thinking, not a final answer.
        #    They must call SubmitResult to properly finish.
        thought_count = self._state.on_thought()

        # Frame-aware limits: child frames (specialists) get more CoT room
        base_max = self.config.max_consecutive_thoughts
        if self.mmu.stack_depth > 1:
            max_thoughts = max(base_max, 4)
        else:
            max_thoughts = base_max

        logger.info(
            f"VCPU backend thought #{thought_count}/{max_thoughts} from {role} (depth={self.mmu.stack_depth})"
        )

        if thought_count >= max_thoughts:
            # Safety valve: force return after max consecutive thoughts
            logger.warning(
                f"VCPU: {role} hit max consecutive thoughts ({max_thoughts}), forcing return"
            )
            return await self._handle_return(action)

        # --- Promise Gate Detection & Escalating Poke ---
        # Poke: remind the specialist to use tools or SubmitResult.
        # MUST be injected as "system" role — if injected as "user", the LLM
        # treats it as a new user request and tries to "answer" it with more text.
        text_len = len(current_text)
        is_promise_gate = self._detect_promise_gate(current_text)

        if is_promise_gate:
            logger.warning(
                f"VCPU: Promise Gate detected from {role} "
                f"(thought #{thought_count}): '{current_text[:100]}...'"
            )

        # Escalating poke based on consecutive thoughts + promise gate
        if thought_count >= 2 and is_promise_gate:
            # Level 3: Critical - about to force return
            poke_msg = (
                "CRITICAL: You have now said you would take action {n} times without "
                "actually calling any tool. Your text output is DISCARDED — only tool "
                "calls produce results. You MUST call a tool in your VERY NEXT response "
                "or you will be terminated. If your task is complete, call SubmitResult. "
                "If you need to write a file, call Write. DO NOT output any more text "
                "without a tool call."
            ).format(n=thought_count)
        elif is_promise_gate or (text_len > 150 and role in ("architect", "implementer")):
            # Level 2: Strong warning
            poke_msg = (
                f"STOP. You just output {text_len} characters of text without calling any tool. "
                f"This text will be DISCARDED. You MUST use the Write tool to save content to a file. "
                f"Call Write(file_path='your/path.md', content='...') NOW, then SubmitResult."
            )
        else:
            # Level 1: Gentle poke
            poke_msg = self.manifest.features.poke_message

        self.mmu.add_system_message(poke_msg)

        return ToolResult(
            status="OK",
            output=action.args.get("text", ""),
            is_final=False,
        )

    async def _handle_reply(self, action: ActionIR) -> ToolResult:
        """Handle REPLY action — role-aware.

        Frontend roles: REPLY = final answer → terminate.
        Backend specialists: REPLY should not directly terminate;
        redirect to THOUGHT path for continuation poke.
        """
        role = getattr(self.manifest, 'role', 'unknown')
        BACKEND_ROLES = {"explorer", "implementer", "architect", "tester", "executor"}

        if role in BACKEND_ROLES:
            # Backend specialist's REPLY must not terminate directly
            # Redirect to THOUGHT path which applies continuation poke
            from nimbus.core.logging import get_logger
            get_logger("kernel.vcpu").warning(
                f"Backend '{role}' produced REPLY → redirecting to THOUGHT path"
            )
            return await self._handle_thought(action)

        # Frontend role: REPLY = final answer
        return await self._handle_return(action)

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
            "RETURN": self._handle_return,
            "REPLY": self._handle_reply,
            # Treat THOUGHT as implicit RETURN (Natural conversation)
            "THOUGHT": self._handle_thought,
            "SUB_CALL": self._handle_sub_call,
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
                    retryable=False,
                ),
            )

        try:
            result = await handler(action)
            # Note: Error recovery for TOOL_CALL is handled inside _handle_tool_call
            # BEFORE adding to memory, so failed attempts don't pollute context
            return result
        except Exception as e:
            from nimbus.core.logging import get_logger

            logger = get_logger("kernel.vcpu")
            logger.error(
                f"Exception in handler for {action.kind}/{action.name}: {e}", exc_info=True
            )
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="HANDLER_ERROR",
                    message=f"Handler error: {e}",
                    retryable=False,
                ),
            )

    async def _handle_tool_error(
        self, action: ActionIR, result: ToolResult
    ) -> Optional[ToolResult]:
        """
        Smart Error Handler: 使用注册的 error handlers 尝试恢复工具调用错误。

        类似操作系统的错误处理机制：
        - Tool 层只抛出错误（如 ENOENT）
        - Error Handler Registry 根据错误类型决定恢复策略

        恢复策略类型（由 ErrorHandlerRegistry 管理）：
        1. inject_hint: 注入提示消息给 LLM
        2. auto_tool: 自动执行恢复工具（如 ls 列目录）
        3. modify_args: 修改参数后重试
        4. skip: 不干预，让 LLM 自己处理

        Args:
            action: 失败的 ActionIR
            result: 包含错误信息的 ToolResult

        Returns:
            恢复后的 ToolResult，如果无法恢复则返回 None
        """
        fault = result.fault
        if not fault:
            return None

        # 使用 ErrorHandlerRegistry 决定恢复策略
        recovery = await self._error_registry.handle_error(
            fault_message=fault.message,
            tool_name=action.name,
            args=action.args,
            workspace=None,  # TODO: 获取 workspace 路径
        )

        if recovery is None:
            return None

        # 使用 RecoveryExecutor 执行恢复动作
        ctx = RecoveryContext(
            original_action=action,
            original_result=result,
            default_timeout=self.config.default_timeout,
        )
        return await self._recovery_executor.execute(recovery, ctx)

    async def _handle_empty_result(
        self, action: ActionIR, result: ToolResult
    ) -> Optional[ToolResult]:
        """
        处理"成功但无结果"的情况。

        Delegates to EmptyResultHandler for basic handling,
        then performs recovery actions if needed.
        """
        from nimbus.core.logging import get_logger

        logger = get_logger("kernel.vcpu.error_handler")

        # Use EmptyResultHandler for basic checks and hard stop
        hard_stop = self._empty_result_handler.handle(action, result)
        if hard_stop:
            return hard_stop

        # If not a no-match situation, we're done
        if not self._empty_result_handler.is_no_match(action, result):
            return None

        # Use error handler for recovery
        recovery = await self._error_registry.handle_error(
            fault_message="No matches found",
            tool_name=action.name,
            args=action.args,
            workspace=None,
        )

        if recovery and recovery.action_type == "auto_tool":
            if recovery.auto_tool and recovery.auto_args:
                await self._execute_auto_recovery(action, recovery, logger)

        elif recovery and recovery.action_type == "inject_hint" and recovery.hint:
            self.mmu.add_system_message(recovery.hint)
            if self.mmu.current_frame.messages:
                self.mmu.current_frame.messages[-1].meta["ephemeral"] = True
            logger.info(f"🔧 Hint injected for no-match: {recovery.hint[:80]}...")

        return None

    async def _execute_auto_recovery(self, action: ActionIR, recovery, logger) -> None:
        """Execute auto-recovery tool and inject results."""
        logger.info(f"🔧 Auto-executing recovery for no-match: {recovery.auto_tool}")

        recovery_action = ActionIR(
            kind="TOOL_CALL",
            name=recovery.auto_tool,
            id=f"recovery_{action.id}",
            args=recovery.auto_args,
            meta={"recovery_for": action.name},
        )

        recovery_result = await self.gate.syscall_tool(
            recovery_action, timeout_sec=self.config.default_timeout
        )

        # Combine hint and result
        combined = ""
        if recovery.hint:
            combined += recovery.hint + "\n\n"
        if recovery_result.output:
            combined += str(recovery_result.output)

        if combined:
            self.mmu.add_system_message(combined)
            if self.mmu.current_frame.messages:
                self.mmu.current_frame.messages[-1].meta["ephemeral"] = True
            logger.info(f"🔧 Recovery hint injected for {action.name}")

    async def _handle_tool_call(self, action: ActionIR) -> ToolResult:
        """
        Handle TOOL_CALL action via Gate.

        Executes the tool through the kernel gate with permission checking
        and timeout enforcement.

        Features:
        - SubmitResult interception: Pseudo-tool for specialist task completion.
        - Tool name auto-repair (learned from opencode): Fixes common LLM
          casing errors like "read" -> "Read".
        - File edit history tracking (improved from opencode): Detects when
          LLM tries to re-apply the same edit that already succeeded.
        - Doom loop detection (learned from opencode): Detects when the same
          tool is called with identical arguments multiple times consecutively.
        - Terminal tool hints: Reminds LLM to finish the task after
          state-modifying operations (Edit, Write, Bash).
        """
        # ── SubmitResult pseudo-tool: intercept before anything else ──
        if action.name == "SubmitResult":
            result_text = (
                action.args.get("result")
                or action.args.get("arguments", {}).get("result", "")
            )
            from nimbus.core.logging import get_logger
            get_logger("kernel.vcpu").info(
                f"SubmitResult called by {getattr(self.manifest, 'role', '?')}: "
                f"{result_text[:120]}..."
            )
            # Record it in MMU so the parent can see the tool call/result pair
            self.mmu.add_assistant_with_tool_calls(
                content=None,
                tool_calls=[{"id": action.id, "function": {"name": "SubmitResult", "arguments": action.args}}],
            )
            self.mmu.add_tool_result(
                tool_call_id=action.id, name="SubmitResult", content=result_text
            )
            return ToolResult(status="OK", output=result_text, is_final=True)

        # Auto-repair tool name if needed (learned from opencode's llm.ts)
        # Only repair built-in tools; custom tools pass through unchanged
        canonical_name = TOOL_NAME_CANONICAL.get(action.name.lower())

        if canonical_name and canonical_name != action.name:
            # Log the repair for built-in tools
            self._emit_event(
                "TOOL_NAME_REPAIRED", {"original": action.name, "repaired": canonical_name}
            )
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

        # EditFuse: 检查该文件是否已熔断
        if action.name == "Edit":
            _fuse_file = action.args.get("file_path", "")
            _fuse_block = self._edit_fuse.check_before_edit(_fuse_file)
            if _fuse_block is not None:
                return _fuse_block

        # Execute the tool (gate handles param validation internally)
        result = await self.gate.syscall_tool(action)

        # EditFuse: 记录成功/失败
        if action.name == "Edit":
            _fuse_file = action.args.get("file_path", "")
            if result.status == "OK":
                self._edit_fuse.on_edit_success(_fuse_file)
            elif result.fault:
                self._edit_fuse.on_edit_failure(_fuse_file)

        # Check for doom loop AFTER executing
        # This way, param validation failures from gate don't pollute doom detection.
        # We only track actual execution attempts (including gate-level validation errors).
        doom_result = self._doom_detector.check(action.name, action.args)

        if doom_result.is_loop:
            self._state.doom_loop_count += 1

            self._emit_event(
                "DOOM_LOOP_DETECTED",
                {
                    "tool": action.name,
                    "args": action.args,
                    "consecutive_count": doom_result.consecutive_count,
                    "doom_loop_count": self._state.doom_loop_count,
                },
            )

            if self._state.doom_loop_count == 1:
                # First doom loop: warn but allow recovery
                from nimbus.core.logging import get_logger
                logger = get_logger("kernel.vcpu")
                logger.warning(
                    f"Doom loop detected (1st time) for {action.name}, "
                    f"injecting recovery hint and continuing"
                )
                doom_output = (
                    f"⚠️ DOOM LOOP DETECTED: You've made {doom_result.consecutive_count} identical failing "
                    f"calls to {action.name}. STOP and change your approach.\n\n"
                    f"{doom_result.guidance or ''}\n\n"
                    f"Read the file again before retrying."
                )
                # Write tool_result to MMU before returning — required to keep
                # the assistant tool_use / user tool_result pairing intact.
                self.mmu.add_tool_result(tool_call_id=action.id, name=action.name, content=doom_output)
                return ToolResult(
                    status="ERROR",
                    output=doom_output,
                    is_final=False,
                    fault=Fault(
                        domain="RUNTIME",
                        code="DOOM_LOOP",
                        message=f"Operation failed: {action.name} unsuccessful after {doom_result.consecutive_count} attempts (recoverable, 1st occurrence)",
                        retryable=True,  # Allow recovery on first doom loop
                    ),
                )
            else:
                # Second+ doom loop: non-recoverable
                doom_output = self._failure_reporter.format_doom_loop_error(
                    tool_name=action.name,
                    threshold=doom_result.consecutive_count,
                    guidance=doom_result.guidance or "",
                )
                # Write tool_result to MMU before returning — required to keep
                # the assistant tool_use / user tool_result pairing intact.
                self.mmu.add_tool_result(tool_call_id=action.id, name=action.name, content=doom_output)
                return ToolResult(
                    status="ERROR",
                    output=doom_output,
                    is_final=False,
                    fault=Fault(
                        domain="RUNTIME",
                        code="DOOM_LOOP",
                        message=f"Operation failed: {action.name} unsuccessful after {doom_result.consecutive_count} attempts (2nd doom loop, non-recoverable)",
                        retryable=False,
                    ),
                )

        # Try error recovery BEFORE adding to memory (so failed attempts don't pollute context)
        if result.fault:
            recovered = await self._handle_tool_error(action, result)
            if recovered is not None:
                result = recovered  # Use recovered result instead
        else:
            # Handle "successful but empty" results (e.g., search with no matches)
            # These are OK status but still need recovery hints
            empty_override = await self._handle_empty_result(action, result)
            if empty_override is not None:
                result = empty_override  # Use error result to force behavior change

        # Update memory with tool result
        # Logic update: Prioritize result.output if available (even for errors),
        # as it might contain enhanced error messages/hints from recovery.
        output_str = str(result.output) if result.output is not None else ""

        # Only fallback to raw fault message if output is empty
        if result.fault and not output_str:
            output_str = f"[Error] {result.fault.message}"

        # Note: We no longer inject continuation hints after Edit/Write.
        # The old hint ("consider testing with Bash before finishing") caused
        # the LLM to make unnecessary extra tool calls even when the task was done.
        # If verification is needed, the LLM should decide on its own based on
        # the user's original request, not because of an injected hint.

        self.mmu.add_tool_result(tool_call_id=action.id, name=action.name, content=output_str)

        # Reset consecutive thoughts counter on tool call
        self._state.on_action()
        self._state.on_productive_action(action.name)

        # DoomLoopDetector handles its own state management internally

        return result

    async def _handle_return(self, action: ActionIR) -> ToolResult:
        """
        Handle RETURN action (and implicit RETURN via THOUGHT).

        Returns the result and finalizes execution.
        """
        from nimbus.core.logging import get_logger
        logger = get_logger("kernel.vcpu")

        # Support various argument names for flexibility
        # 'result'/'output': from explicit RETURN tool call
        # 'content'/'text': from implicit THOUGHT action
        result = action.args.get(
            "result",
            action.args.get("output", action.args.get("content", action.args.get("text", ""))),
        )

        # Note: Hallucination detection is handled upstream in decoder._check_hallucination()
        # with proper short/long text heuristics. No duplicate check here — it caused
        # false positives when the model legitimately discussed tool patterns in its response.

        # Layer 2: if post-poke THOUGHT was suppressed, use the pre-poke text.
        # The pre-poke text was already streamed via THINKING events in iteration 1,
        # so keep streamed=True to prevent stream_chat from re-emitting it.
        streamed = action.kind == "THOUGHT"
        if self._state.pending_thought_text and action.kind == "THOUGHT":
            result = self._state.pending_thought_text
            self._state.pending_thought_text = ""
            # streamed stays True — text was already sent via THINKING events

        self._emit_event(
            "PROC_FINISHED",
            {
                "result": str(result)[:200],  # Truncate for event
                "is_final": True,
            },
        )
        self._emit_event("STEP_COMPLETED", {"status": "success", "is_final": True})

        return ToolResult(
            status="OK",
            output=result,
            is_final=True,
            meta={"streamed": streamed},
        )

    # _handle_thought: continuation poke (A) + productive work tracking (B)

    async def _handle_post_ipc(self, action: ActionIR) -> ToolResult:
        """
        Handle POST_IPC action (placeholder).

        Note: IPC functionality was removed as YAGNI. This handler exists
        for compatibility but does nothing. Re-implement if actually needed.
        """
        channel = action.args.get("channel", "default")
        key = action.args.get("key", action.id)

        # Log but don't execute (IPC was removed)
        from nimbus.core.logging import get_logger

        logger = get_logger("kernel.vcpu")
        logger.debug(f"POST_IPC called (no-op): {channel}:{key}")

        return ToolResult(status="OK", output=f"IPC not implemented: {channel}:{key}")

    async def _handle_request_replan(self, action: ActionIR) -> ToolResult:
        """
        Handle REQUEST_REPLAN action (placeholder).

        Note: Replan functionality was removed as YAGNI. This handler exists
        for compatibility but does nothing. Re-implement if actually needed.
        """
        reason = action.args.get("reason", {})

        # Log but don't execute (replan was removed)
        from nimbus.core.logging import get_logger

        logger = get_logger("kernel.vcpu")
        logger.debug(f"REQUEST_REPLAN called (no-op): {reason}")

        return ToolResult(status="OK", output="Replan not implemented")

    async def _handle_cancel(self, action: ActionIR) -> ToolResult:
        """
        Handle CANCEL action by marking execution as done.

        Cancels the current execution and returns with cancelled status.
        """
        reason = action.args.get("reason", "Cancelled by agent")
        self._state.is_done = True

        return ToolResult(
            status="CANCELLED",
            output=reason,
            is_final=True,
            fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message=reason, retryable=False),
        )

    # =========================================================================
    # Compaction
    # =========================================================================

    def set_compaction_callback(self, callback: Callable[[], Awaitable[bool]]) -> None:
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
        self._state.compaction_count += 1

        self._emit_event(
            "COMPACTION_START",
            {
                "iteration": self._state.iteration,
                "compaction_count": self._state.compaction_count,
            },
        )

        try:
            if self._compaction_callback:
                # Use external compaction (e.g., AgentOS CompactionEngine)
                success = await self._compaction_callback()
            else:
                # Use MMU's built-in compression (now async)
                success = await self._compact_mmu()

            self._emit_event(
                "COMPACTION_END",
                {
                    "success": success,
                    "compaction_count": self._state.compaction_count,
                },
            )

            return success

        except Exception as e:
            from nimbus.core.logging import get_logger

            get_logger("kernel.vcpu").error(f"Compaction failed: {e}")
            self._emit_event(
                "COMPACTION_END",
                {
                    "success": False,
                    "error": str(e),
                },
            )
            return False

    async def _compact_mmu(self) -> bool:
        """
        Use MMU's built-in compression (Archive & Reset).

        New Strategy (v2):
        Instead of summarizing in-memory (which degrades quality), we
        ARCHIVE the current context to disk and RESET the frame.

        This effectively gives us "Infinite Context" via file storage.
        """
        from nimbus.core.logging import get_logger

        logger = get_logger("kernel.vcpu")

        try:
            # 1. Get session_id for file organization
            session_id = "unknown"
            # Try to get from ALU client
            if hasattr(self.alu, "_client") and hasattr(self.alu._client, "session_id"):
                session_id = self.alu._client.session_id

            # 2. Archive and Reset
            archive_path = await self.mmu.archive_and_reset(session_id)

            if archive_path:
                logger.info(f"🗄️ Memory compaction successful: Context archived to {archive_path}")
                return True

            # Fallback: If archiving failed (e.g. no messages), just return True to allow reset
            logger.warning(
                "Memory archiving skipped (no messages?), but proceeding with cycle reset"
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
        # Reset centralized state
        self._state.reset()

        # Reset extracted components
        self._doom_detector.reset()
        self._error_registry.reset()



    async def _dump_context_to_file(self, messages: List[Dict[str, Any]], iteration: int) -> None:
        """Dump current context messages to a JSON file for debugging."""
        try:
            from pathlib import Path

            from nimbus.utils.timeutil import local_now_str

            log_dir = Path(".logs/context")
            log_dir.mkdir(parents=True, exist_ok=True)

            timestamp = local_now_str("%Y%m%d_%H%M%S")
            filename = log_dir / f"context_{timestamp}_iter{iteration:03d}.json"

            with open(filename, "w", encoding="utf-8") as f:
                json.dump(messages, f, indent=2, ensure_ascii=False)

            from nimbus.core.logging import get_logger

            logger = get_logger("kernel.vcpu")
            logger.info(f"📝 Context dumped to {filename}")

        except Exception as e:
            from nimbus.core.logging import get_logger

            logger = get_logger("kernel.vcpu")
            logger.error(f"Failed to dump context: {e}")

    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Emit a lifecycle event.

        Events are used for:
        - Observability (logging)
        - External hooks (UI updates)
        - Debugging
        """
        if event_type == "STEP_STARTED":
            # Don't log step start if disabled
            if not self.config.emit_step_events:
                return

        # Emit to gate (which handles broadcasting)
        # Note: In v1, VCPU emitted directly. In v2, we route via Gate/AgentOS.
        # Here we just use the gate's event stream if available.
        if hasattr(self.gate, "event_stream") and self.gate.event_stream:
            self.gate.event_stream.emit(
                Event(
                    type=event_type,
                    pid=self.gate.pid,
                    data=data,
                )
            )

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
        from nimbus.core.logging import get_logger

        logger = get_logger("kernel.vcpu")

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
            response = await self.alu.chat(messages, tools=[])

            if response.content:
                summary = response.content.strip()
                # Ensure summary is actually shorter
                if len(summary) < len(goal):
                    logger.info(f"Goal summarized: {len(goal)} → {len(summary)} chars")
                    return summary

            # Fallback: truncate
            logger.warning("Goal summarization failed, using truncation")
            return goal[: self.config.goal_max_length] + "..."

        except Exception as e:
            logger.error(f"Goal summarization error: {e}")
            return goal[: self.config.goal_max_length] + "..."

    async def _generate_llm_failure_response(
        self,
        goal: str,
        fault: Fault,
        iterations: int,
    ) -> str:
        """Let LLM generate a natural response about the failure."""
        from nimbus.core.runtime.failure_reporter import FailureContext

        ctx = FailureContext(
            goal=goal,
            fault_code=fault.code,
            fault_message=fault.message,
            iterations=iterations,
        )
        return await self._failure_reporter.generate_report(ctx)

    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emit an event if event emission is enabled."""
        if not self.config.emit_step_events:
            return

        if self.gate.events:
            self.gate.events.emit(
                Event(
                    type=event_type,  # type: ignore
                    pid=self.gate.pid,
                    data=data,
                )
            )

    # =========================================================================
    # State Accessors (delegating to ExecutionState)
    # =========================================================================

    @property
    def iteration(self) -> int:
        """Get current iteration count."""
        return self._state.iteration

    @property
    def is_running(self) -> bool:
        """Check if vCPU is currently running."""
        return self._state.is_running

    @property
    def is_done(self) -> bool:
        """Check if execution is complete."""
        return self._state.is_done

    def get_state(self) -> Dict[str, Any]:
        """Get vCPU state for debugging/checkpointing."""
        state = self._checkpoint_manager.get_state_dict()
        state["doom_loop_count"] = self._doom_detector.loop_count
        return state

    def _dump_context_to_file(self, messages: List[Dict[str, Any]], iteration: int) -> None:
        """
        Dump full context to a JSON file for debugging.

        Enabled by setting NIMBUS_DUMP_CONTEXT environment variable.
        Files are written to .logs/context/ directory.
        """
        import json
        from pathlib import Path

        from nimbus.utils.timeutil import local_now_str, utcnow

        dump_dir = Path(".logs/context")
        dump_dir.mkdir(parents=True, exist_ok=True)

        timestamp = local_now_str("%Y%m%d_%H%M%S")
        filename = dump_dir / f"context_{timestamp}_iter{iteration:03d}.json"

        # Prepare dump data
        dump_data = {
            "timestamp": utcnow().isoformat(),
            "iteration": iteration,
            "message_count": len(messages),
            "messages": messages,
            "state": {
                "consecutive_thoughts": self._state.consecutive_thoughts,
                "is_running": self._state.is_running,
                "is_done": self._state.is_done,
                "stack_depth": self.mmu.stack_depth,
            },
        }

        def _safe_default(obj):
            """Fallback serialiser for non-JSON-serialisable objects (e.g. mock dataclasses)."""
            if hasattr(obj, "__dict__"):
                return obj.__dict__
            return str(obj)

        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(dump_data, f, ensure_ascii=False, indent=2, default=_safe_default)
        except Exception:
            # If serialization still fails, write a repr fallback
            with open(filename, "w", encoding="utf-8") as f:
                f.write(repr(dump_data))

        from nimbus.core.logging import get_logger

        logger = get_logger("kernel.vcpu")
        logger.info(f"📝 Context dumped to {filename}")
