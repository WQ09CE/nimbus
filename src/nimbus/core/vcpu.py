"""
VCPU (Virtual CPU) — The Think-Act-Observe execution engine.

Implements the core agent loop as a Finite State Machine:
  INIT → REASONING → ACTION_EXECUTION → OBSERVATION → (back to INIT or COMPLETED)

Why FSM instead of a simple while loop?
- Each state transition is explicit and validated
- Interruption is clean (check between states)
- Execution is observable (emit events at transitions)
- Error recovery is a first-class state, not an ad-hoc catch

This is the "brain" that ties together:
- ALU (LLM) for thinking
- Decoder for translating LLM output to ActionIR
- Gate for executing tools
- MMU for managing context
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from .protocol import ActionIR, Fault, StepResult, ToolResult

logger = logging.getLogger("nimbus.vcpu")

# Catches the narrate-instead-of-act failure mode: a tool-call-less reply that
# only ANNOUNCES an upcoming action and then stops. Small models do this a lot
# ("Next Action: Spawn researcher", "我将调用 Grep…", "I am now spawning…").
# Matched against the message TAIL, because a genuine final answer doesn't END
# on an unfulfilled plan, whereas a premature stop does.
_NARRATE_TAIL_CHARS = 320
_ANNOUNCE_PATTERNS = [
    # intent + action-verb + tool/agent noun in proximity
    re.compile(
        r"(我将|我会|我现在|我打算|接下来我?|让我|马上|稍后我?|准备|现在就|"
        r"i['’ ]?(?:will|'ll|m going to|am going to|am about to)|let me|going to|about to|now i)"
        r"[^。.!?\n]{0,40}"
        r"(调用|使用|运行|执行|跑|生成|创建|分派|派|启动|开始|spawn|call|use|run|invoke|"
        r"execute|dispatch|launch|create|generate|proceed)"
        r"[^。.!?\n]{0,40}"
        r"(工具|命令|脚本|子?(?:智能体|代理)|agent|researcher|worker|reader|tool|bash|grep|"
        r"glob|read|write|edit|spawn_agent|command|script)",
        re.IGNORECASE,
    ),
    # explicit "next action" / 下一步 execution markers (strong premature-stop signal)
    re.compile(r"(next\s+action|下一步(行动|动作|操作)?|下一个动作|接下来(我)?(要|将|需要|准备))", re.IGNORECASE),
    # "spawning the X agent" / 分派/启动 … agent
    re.compile(
        r"(spawn(ing|ed)?|dispatch(ing)?|分派|派遣|启动)\s*[a-z一-鿿_ ]{0,24}"
        r"(agent|researcher|worker|reader|子?智能体|子?代理)",
        re.IGNORECASE,
    ),
]


def _announces_unfulfilled_tool(text: str) -> bool:
    """True if a tool-call-less reply ENDS by announcing an unfulfilled action."""
    if not text:
        return False
    tail = text[-_NARRATE_TAIL_CHARS:]
    return any(p.search(tail) for p in _ANNOUNCE_PATTERNS)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class VCPUConfig:
    max_iterations: int = 200
    max_consecutive_thoughts: int = 8
    max_consecutive_errors: int = 3
    llm_call_timeout: float = 300.0
    contract_mode: bool = False  # Sub-agent only: pure text → THOUGHT, must exit via submit_result


# =============================================================================
# Protocols — what the VCPU expects from its collaborators
# =============================================================================


class ALUProtocol(Protocol):
    """The LLM adapter (Arithmetic Logic Unit)."""
    async def chat(self, messages: List[Dict], tools: List[Dict], on_chunk: Optional[Callable[[str], None]] = None) -> Any: ...


class GateProtocol(Protocol):
    """The tool execution gate."""
    async def syscall_tool(self, action: ActionIR, timeout: Optional[float] = None) -> ToolResult: ...


class DecoderProtocol(Protocol):
    """The instruction decoder."""
    def decode(self, content: Optional[str], tool_calls: Optional[List], text_is_final: bool = True) -> List[ActionIR]: ...


# =============================================================================
# Execution State (counters)
# =============================================================================


@dataclass
class ExecutionState:
    iteration: int = 0
    consecutive_thoughts: int = 0
    consecutive_errors: int = 0

    def on_action(self) -> None:
        self.consecutive_thoughts = 0
        self.consecutive_errors = 0

    def on_thought(self) -> int:
        self.consecutive_thoughts += 1
        return self.consecutive_thoughts

    def on_error(self) -> int:
        self.consecutive_errors += 1
        return self.consecutive_errors


# =============================================================================
# VCPU — The FSM engine
# =============================================================================


class VCPU:
    """Virtual CPU implementing Think-Act-Observe via FSM."""

    def __init__(
        self,
        alu: ALUProtocol,
        decoder: DecoderProtocol,
        gate: GateProtocol,
        mmu: Any,  # MMU instance
        tools: List[Dict[str, Any]],
        config: Optional[VCPUConfig] = None,
        text_is_final: bool = True,
        get_steering: Optional[Callable[[], List[str]]] = None,
        initial_state: Optional[Dict[str, int]] = None,
        on_text_delta: Optional[Callable[[str], None]] = None,
    ):
        self.alu = alu
        self.decoder = decoder
        self.gate = gate
        self.mmu = mmu
        self.tools = tools
        self.config = config or VCPUConfig()
        self.text_is_final = text_is_final

        self._exec = ExecutionState()
        if initial_state:
            self._exec.iteration = initial_state.get("iteration", 0)
            self._exec.consecutive_thoughts = initial_state.get("consecutive_thoughts", 0)
            self._exec.consecutive_errors = initial_state.get("consecutive_errors", 0)

        self._interrupted = False
        self._wakeup_event: Optional[asyncio.Event] = None
        self._get_steering = get_steering
        self._on_text_delta = on_text_delta
        self._countdown_warning_sent = False

    def set_wakeup_event(self, event: asyncio.Event) -> None:
        """Receive a wakeup event from the RuntimeLoop to enable graceful steering."""
        self._wakeup_event = event

    def request_interruption(self) -> None:
        self._interrupted = True

    @property
    def iteration(self) -> int:
        return self._exec.iteration

    async def step(self) -> StepResult:
        """Drive the FSM forward by one complete Think-Act-Observe cycle.

        Returns a StepResult. If is_final=True, the agent is done.
        """
        result = StepResult()

        # Check interruption
        if self._interrupted:
            result.is_final = True
            result.final_result = ToolResult(
                status="CANCELLED", output="Execution interrupted.", is_final=True,
            )
            return result

        # Check iteration limit
        self._exec.iteration += 1
        if self._exec.iteration > self.config.max_iterations:
            # Soft limit: signal the loop to attempt compaction, not a hard error
            logger.warning(
                "Iteration %d exceeds max_iterations (%d), signaling compaction",
                self._exec.iteration, self.config.max_iterations,
            )
            result.is_final = True
            result.fault = Fault(
                domain="RESOURCE", code="BUDGET_EXCEEDED",
                message=f"Max iterations ({self.config.max_iterations}) reached.",
                retryable=True,  # Changed: let loop try compaction
            )
            result.final_result = ToolResult(
                status="ERROR",
                output=f"Max iterations ({self.config.max_iterations}) reached. Attempting recovery...",
                fault=result.fault,
                is_final=True,
            )
            return result
        elif self._exec.iteration == int(self.config.max_iterations * 0.8):
            logger.warning(
                "Approaching iteration limit: %d / %d",
                self._exec.iteration, self.config.max_iterations,
            )

        # Countdown steering: inject one-time warning near iteration limit
        if (
            self.config.contract_mode
            and not self._countdown_warning_sent
            and self._exec.iteration >= int(self.config.max_iterations * 0.85)
        ):
            remaining = self.config.max_iterations - self._exec.iteration
            self.mmu.add_system_message(
                f"⚠️ You only have {remaining} steps left. "
                "Immediately write all findings to Scratchpad and call submit_result to deliver your results."
            )
            self._countdown_warning_sent = True

        # ---- THINK (Reasoning) ----
        try:
            messages = self.mmu.assemble_context()
            chat_coro = self.alu.chat(messages, self.tools, on_chunk=self._on_text_delta)

            if self._wakeup_event:
                # Race LLM call against wakeup event (steering message arrived).
                # If wakeup fires during LLM call: cancel, return empty step (non-final).
                # The loop will inject the steering message and re-run step().
                chat_task = asyncio.create_task(chat_coro)
                wakeup_task = asyncio.create_task(self._wakeup_event.wait())
                done, pending = await asyncio.wait(
                    [chat_task, wakeup_task],
                    timeout=self.config.llm_call_timeout,
                    return_when=asyncio.FIRST_COMPLETED
                )

                # Always cancel pending tasks to prevent orphan task leaks
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

                if self._wakeup_event.is_set():
                    if not chat_task.done():
                        chat_task.cancel()
                        try:
                            await chat_task
                        except asyncio.CancelledError:
                            pass
                    # Don't add system message -- the loop will inject the steering
                    # message as a user message at the top of the next iteration.
                    result.actions = []
                    return result

                if chat_task not in done:
                    chat_task.cancel()
                    raise asyncio.TimeoutError()

                response = chat_task.result()
            else:
                response = await asyncio.wait_for(
                    chat_coro,
                    timeout=self.config.llm_call_timeout,
                )
        except asyncio.TimeoutError:
            return self._error_step(result, "LLM call timed out", retryable=True)
        except asyncio.CancelledError:
            raise
        except Fault as f:
            errs = self._exec.on_error()
            if f.retryable:
                # Silent retry: don't pollute LLM context with transient errors
                # (server disconnects, rate limits, etc.)
                delay = min(2 ** errs, 30)  # exponential backoff, max 30s
                logger.warning(
                    "[VCPU] Retryable LLM error (attempt %d): %s — retrying in %.0fs",
                    errs, f.message, delay,
                )
                await asyncio.sleep(delay)
                if errs >= self.config.max_consecutive_errors:
                    # Too many retries — surface the error
                    self.mmu.add_system_message(f"[LLM Error] {f.message} (after {errs} retries)")
                    return self._error_step(result, f"Too many LLM stream errors: {f.message}")
                return result  # non-final, will retry silently
            else:
                # Non-retryable: inform the LLM so it can adapt
                self.mmu.add_system_message(f"[LLM Error] {f.message}")
                if errs >= self.config.max_consecutive_errors:
                    return self._error_step(result, f"Too many LLM stream errors: {f.message}")
                return result  # non-final, will retry
        except Exception as e:
            return self._error_step(result, f"LLM error: {e}")

        # ---- DECODE ----
        content = getattr(response, "content", None)
        tool_calls = getattr(response, "tool_calls", None)

        # Forward LLM token usage to the loop (pi-style)
        result.usage = getattr(response, "usage", None)

        try:
            actions = self.decoder.decode(
                content, tool_calls,
                text_is_final=self.text_is_final,
                contract_mode=self.config.contract_mode,
            )
        except Fault as f:
            # Hallucination or parse error — inject feedback and retry
            self.mmu.add_system_message(f"[Decoder Error] {f.message}")
            errs = self._exec.on_error()
            if errs >= self.config.max_consecutive_errors:
                return self._error_step(result, f"Too many decode errors: {f.message}")
            return result  # non-final, will retry

        if not actions:
            # Diagnostic: capture exactly what the model produced that decoded to
            # nothing, so we can tell missed-tool-call-format from reasoning/refusal.
            logger.warning(
                "[VCPU] Empty decode (iter=%d): content=%r tool_calls=%r",
                self._exec.iteration, (str(content)[:600] if content else content), tool_calls,
            )
            self.mmu.add_system_message("Empty response. You MUST call a tool or return a final answer.")
            errs = self._exec.on_error()
            # Enforce the consecutive-error budget here too (other error paths do).
            # Without this the loop spins on empty responses until max_iterations.
            if errs >= self.config.max_consecutive_errors:
                return self._error_step(
                    result,
                    f"Model produced {errs} consecutive empty responses "
                    "(no tool call or final answer).",
                )
            return result

        result.actions = actions

        # ---- ROUTE: RETURN/REPLY → done ----
        has_tool_action = any(a.kind == "TOOL_CALL" for a in actions)
        for action in actions:
            if action.kind in ("RETURN", "REPLY"):
                text = action.args.get("text", action.args.get("result", ""))

                # narrate-not-act guard: a plain-text REPLY that only ANNOUNCES a
                # tool action ("I'll run bash…") but emits no tool call is the
                # model planning instead of acting. Nudge it to actually act
                # rather than accepting the plan as the final answer. Bounded by
                # max_consecutive_errors (RETURN = explicit final is never nudged).
                if (
                    action.kind == "REPLY"
                    and not has_tool_action
                    and _announces_unfulfilled_tool(content or text)
                ):
                    errs = self._exec.on_error()
                    if errs < self.config.max_consecutive_errors:
                        if content or text:
                            self.mmu.add_assistant_message(content or text)
                        self.mmu.add_system_message(
                            "You described a tool action but did not call any tool. "
                            "If you intend to act, emit the tool call now. Only give a "
                            "plain final answer if no tool is actually needed."
                        )
                        return result  # non-final, retry

                # Persist to MMU
                if content:
                    self.mmu.add_assistant_message(content)
                elif text:
                    self.mmu.add_assistant_message(text)
                if result.usage is not None and hasattr(self.mmu, 'set_last_usage'):
                    self.mmu.set_last_usage(result.usage)
                result.is_final = True
                result.final_result = ToolResult(status="OK", output=text, is_final=True)
                return result

        # ---- ACT (execute tool calls) ----
        tool_actions = [a for a in actions if a.kind == "TOOL_CALL"]
        thought_text = None
        for a in actions:
            if a.kind == "THOUGHT":
                thought_text = a.args.get("text", "")

        if tool_actions:
            self._exec.on_action()

            # Persist assistant message with tool_calls
            tc_dicts = [{
                "id": a.id, "type": "function",
                "function": {"name": a.name, "arguments": json.dumps(a.args)},
            } for a in tool_actions]
            self.mmu.add_assistant_with_tool_calls(thought_text, tc_dicts)
            if result.usage is not None and hasattr(self.mmu, 'set_last_usage'):
                self.mmu.set_last_usage(result.usage)

            # Concurrent execution: run all tool calls in parallel via gather,
            # then write results back to MMU in original order.
            if self._interrupted:
                for action in tool_actions:
                    skip = ToolResult(status="CANCELLED", output="Execution interrupted.")
                    result.results.append(skip)
                    self.mmu.add_tool_result(action.id, action.name, skip.output)
            else:
                async def _exec_one(action: ActionIR) -> ToolResult:
                    if self._interrupted:
                        return ToolResult(status="CANCELLED", output="Execution interrupted.")
                    return await self.gate.syscall_tool(action)

                tool_results = await asyncio.gather(
                    *[_exec_one(a) for a in tool_actions]
                )

                # Write results back to MMU in order (preserves conversation sequence)
                # Pi-style dual result: output → LLM context, ui_detail → UI rendering
                for action, tool_result in zip(tool_actions, tool_results):
                    result.results.append(tool_result)
                    self.mmu.add_tool_result(
                        action.id, action.name, str(tool_result.output),
                        ui_detail=tool_result.ui_detail,
                    )
                    
                    # Intercept submit_result: Immediately terminate VCPU loop
                    if action.name == "submit_result" and tool_result.status == "OK":
                        result.is_final = True
                        result.final_result = ToolResult(
                            status="OK",
                            output=tool_result.output,
                            ui_detail=tool_result.ui_detail,
                            is_final=True,
                        )

                # Check for steering messages after all tools complete
                if self._get_steering:
                    steering = self._get_steering()
                    if steering:
                        result.steering_messages = steering
        else:
            # Pure thought — no tool calls
            count = self._exec.on_thought()
            if thought_text:
                self.mmu.add_assistant_message(thought_text)
            
            if result.usage is not None and hasattr(self.mmu, 'set_last_usage'):
                self.mmu.set_last_usage(result.usage)

            if count >= self.config.max_consecutive_thoughts:
                result.is_final = True
                result.final_result = ToolResult(
                    status="OK",
                    output=thought_text or "Agent stopped after too many thoughts without action.",
                    is_final=True,
                )
                return result

        return result

    def _error_step(self, result: StepResult, message: str, retryable: bool = False) -> StepResult:
        result.is_final = True
        result.fault = Fault(domain="LLM", code="SYSTEM_ERROR", message=message, retryable=retryable)
        result.final_result = ToolResult(
            status="ERROR", output=message, fault=result.fault, is_final=True,
        )
        return result
