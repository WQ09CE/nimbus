"""
RuntimeLoop -- The outer execution driver.

Drives VCPU.step() in a loop, handling:
1. Context overflow -> trigger compaction and retry
2. Iteration limit -> optional compaction or graceful termination
3. Interrupt signals -> clean shutdown with partial results
4. Event streaming -> fine-grained events for reactive UI (pi-style)
5. Steering queue -> inject user messages between tool calls (pi-style)
6. Follow-up queue -> re-enter the loop after agent finishes (pi-style)
7. Abort -> hard stop with process group kill

Why separate from VCPU?
VCPU handles a single Think-Act-Observe cycle. The RuntimeLoop handles
the *lifecycle*: when to stop, when to compact, when to yield to the user.
This separation keeps VCPU testable without async complexity.

Design notes (pi-coding-agent influence):
- Steering queue: messages injected while agent is executing tools.
  Checked between each tool call -- skips remaining tools.
- Follow-up queue: messages sent after agent finishes. Re-enters the loop.
- Partial results: pi returns partial content on abort (stopReason === 'aborted').
  We track accumulated results so interruption never loses work.
- Fine-grained events: pi emits text_delta, tool_call events as async iterators.
  We emit similar typed events for reactive UI binding.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from nimbus.adapters.types import TokenUsage

from .protocol import Event, Fault, StepResult, ToolResult
from .queues import (  # noqa: F401  (re-exported for compat)
    FollowUpQueue,
    MessageQueue,
    SteeringQueue,
)
from .session_log import SessionLog
from .storage import SessionStorage

logger = logging.getLogger("nimbus.loop")


class _AuxiliaryChatInterrupted(BaseException):
    """Internal control-flow signal for an interrupted auxiliary LLM call.

    This deliberately derives from BaseException: MMU's summarizer fallback catches
    ordinary Exceptions, but abort/steering must not be mistaken for a failed
    summarizer and converted into a successful deterministic compaction.
    """


# Pi-style retryable error classification (cross-provider)
_RETRYABLE_ERROR_RE = re.compile(
    r"overloaded|rate.?limit|too many requests|429|500|502|503|504|"
    r"service.?unavailable|server error|internal error|connection.?error|"
    r"connection.?refused|other side closed|fetch failed|upstream.?connect|"
    r"reset before headers|terminated|retry delay",
    re.IGNORECASE,
)

# Context overflow detection (pi-style comprehensive patterns)
# Should trigger compaction, NOT retry.
#
# Provider-specific patterns (from pi-coding-agent overflow.ts):
# - Anthropic: "prompt is too long: 213462 tokens > 200000 maximum"
# - OpenAI: "Your input exceeds the context window of this model"
# - Google: "The input token count (1196265) exceeds the maximum number of tokens allowed (1048575)"
# - xAI: "This model's maximum prompt length is 131072 but the request contains 537812 tokens"
# - Groq: "Please reduce the length of the messages or completion"
# - OpenRouter: "This endpoint's maximum context length is X tokens. However, you requested about Y tokens"
# - llama.cpp: "the request exceeds the available context size, try increasing it"
# - LM Studio: "tokens to keep from the initial prompt is greater than the context length"
# - GitHub Copilot: "prompt token count of X exceeds the limit of Y"
# - MiniMax: "invalid params, context window exceeds limit"
# - Kimi: "Your request exceeded model token limit: X (requested: Y)"
# - Cerebras/Mistral: Returns "400/413 status code (no body)" - handled below
_OVERFLOW_ERROR_RE = re.compile(
    r"prompt is too long|"                       # Anthropic
    r"input is too long for requested model|"     # Amazon Bedrock
    r"exceeds the context window|"               # OpenAI (Completions & Responses API)
    r"input token count.*exceeds the maximum|"   # Google (Gemini)
    r"maximum prompt length is \d+|"             # xAI (Grok)
    r"reduce the length of the messages|"        # Groq
    r"maximum context length is \d+ tokens|"     # OpenRouter (all backends)
    r"exceeds the limit of \d+|"                 # GitHub Copilot
    r"exceeds the available context size|"       # llama.cpp server
    r"greater than the context length|"          # LM Studio
    r"context window exceeds limit|"             # MiniMax
    r"exceeded model token limit|"               # Kimi For Coding
    r"context.*(length|limit|window|too long|exceeded)|"
    r"maximum.*(context|token)|token.*(limit|exceeded|maximum)|"
    r"too.?long|prompt.*(too|exceeds)|input.*(too|exceeds)|"
    r"request too large|content_too_large|string_above_max_length|"
    r"context[_ ]length[_ ]exceeded|"            # Generic fallback
    r"too many tokens|"                          # Generic fallback
    r"token limit exceeded",                     # Generic fallback
    re.IGNORECASE,
)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class LoopConfig:
    max_compactions: int = 100  # Absolute runaway ceiling (NOT a normal limit)
    max_unproductive_compactions: int = 2  # Consecutive no-progress → genuine exhaustion
    compaction_cooldown: int = 5  # Min steps between compactions
    yield_interval: float = 0.0  # Yield control between steps (for cooperative scheduling)


# =============================================================================
# RuntimeLoop
# =============================================================================


class RuntimeLoop:
    """Drives VCPU step-by-step until completion.

    Usage:
        loop = RuntimeLoop(vcpu, mmu)
        result = await loop.run()
        # or
        async for event in loop.stream():
            print(event)

    Steering (pi-style):
        loop.steering_queue.steer("Change approach")
        # -> skips remaining tools in current step, injects message

    Follow-up (pi-style):
        loop.followup_queue.follow_up("Now also fix tests")
        # -> after agent finishes, re-enters the loop

    Message queue (backward compat):
        loop.message_queue.enqueue("Also check tests")

    Abort:
        loop.abort()
        await loop.wait_for_idle()

    Partial results on abort:
        loop.request_interruption()
        # loop.partial_results contains all results accumulated so far
    """

    def __init__(
        self,
        vcpu: Any,  # VCPU instance
        mmu: Any,  # MMU instance
        config: Optional[LoopConfig] = None,
        event_callback: Optional[Callable[[Event], None]] = None,
        adapter: Any = None,  # LLM adapter for summarization
        steering_queue: Optional[SteeringQueue] = None,
        followup_queue: Optional[FollowUpQueue] = None,
        abort_event: Optional[asyncio.Event] = None,
        session_id: Optional[str] = None,
        storage: Optional[SessionStorage] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.vcpu = vcpu
        self.mmu = mmu
        self.config = config or LoopConfig()
        self._event_cb = event_callback
        self._adapter = adapter

        self.session_id = session_id or uuid.uuid4().hex
        self.storage = storage or SessionStorage()
        self.metadata = metadata or {}

        # Phase 2 event log: the authoritative record of the session's message
        # surface (load_session derives from it; the snapshot is metadata +
        # fallback). open() continues an existing file — seq and turn numbers
        # resume, and a crashed tail is repaired in place before new turns.
        # Mock storages without base_dir get an in-memory log.
        log_dir = getattr(self.storage, "base_dir", None)
        if log_dir is not None:
            self.session_log = SessionLog.open(log_dir / f"{self.session_id}.jsonl")
        else:
            self.session_log = SessionLog(None)
        self._turn = self.session_log.last_turn
        self._step_in_turn = 0
        self._turn_open = False
        self._step_open = False
        if hasattr(self.mmu, "event_sink"):
            self.mmu.event_sink = self.session_log.append

        self._compaction_count = 0
        self._unproductive_compactions = 0
        self._steps_since_compaction = 0
        self._interrupted = False
        self._retry_count = 0
        self._max_retries = 3
        self._base_retry_delay = 2.0  # seconds

        # Pi-style steering + wakeup for LLM call interruption
        self._wakeup_event = asyncio.Event()
        self.steering_queue = steering_queue or SteeringQueue(self._wakeup_event)
        self.followup_queue = followup_queue or FollowUpQueue()

        # Backward-compatible message_queue facade
        self.message_queue = MessageQueue(self.steering_queue)

        # Wire wakeup event to VCPU for LLM call interruption
        if hasattr(self.vcpu, "set_wakeup_event"):
            self.vcpu.set_wakeup_event(self._wakeup_event)

        # Abort support
        self._abort_event = abort_event or asyncio.Event()

        # Idle tracking (pi-style waitForIdle)
        self._idle_event = asyncio.Event()
        self._idle_event.set()  # Start idle
        self._running = False

        # Partial result tracking (pi-style abort recovery)
        self.partial_results: List[ToolResult] = []

        # Pending steering messages to inject at top of next loop iteration
        self._pending_steering: List[str] = []

        # Stall detection: weaker models can keep calling tools instead of
        # finishing — either the exact same call (re-writing an identical file)
        # or the same tool with reworded args (re-spawning a sub-agent whose
        # result they already have). Track both: exact-signature repeats and the
        # consecutive same-tool streak (args may vary).
        self._last_tool_sig: Optional[str] = None
        self._tool_repeat_count = 0
        self._last_tool_name: Optional[str] = None
        self._same_tool_streak = 0
        # In contract mode, give a stalled sub-agent one forced submit_result
        # nudge before terminating, so its structured deliverable isn't lost.
        self._stall_forced_submit = False

        # Cumulative token usage across all LLM calls in this loop run (pi-style)
        self._cumulative_usage = TokenUsage()

    def request_interruption(self) -> None:
        """Signal the loop to stop after the current step."""
        self._interrupted = True
        self.vcpu.request_interruption()
        # Wake the VCPU's LLM-call race so an in-flight generation is
        # cancelled NOW — without this, a stop request waits for the full
        # LLM stream to finish (observed: interrupt timing out twice while
        # a sub-agent's model call ran on). The inner loop checks
        # _interrupted before consuming steering, so a set wakeup with an
        # empty queue is safe.
        self._wakeup_event.set()

    def abort(self) -> None:
        """Hard stop -- cancel everything including running bash processes."""
        self._interrupted = True
        self._abort_event.set()
        self.vcpu.request_interruption()
        # Same as request_interruption: cut the in-flight LLM call. The
        # sub-agent abort watcher calls this on the CHILD loop too, so a
        # parent abort now propagates all the way into a child's model call.
        self._wakeup_event.set()

    async def wait_for_idle(self) -> None:
        """Wait until the loop finishes. Used after abort()."""
        if not self._running:
            return
        await self._idle_event.wait()

    # --- Sync run (returns final result) ---

    async def run(self) -> ToolResult:
        """Run the loop until completion. Returns the final ToolResult."""
        final_result = None
        try:
            async for event in self._loop():
                if event.get("type") == "final":
                    final_result = event["result"]
        finally:
            # Backstop: if _loop raised mid-turn, close the bracket. No-op
            # when the turn already closed with its own reason.
            self._turn_end("aborted", backstop=True)
            self.session_log.flush()

        return final_result or ToolResult(
            status="ERROR", output="Loop ended without result.",
        )

    # --- Streaming run (yields events) ---

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        """Run the loop, yielding fine-grained events at each step."""
        self._running = True
        self._idle_event.clear()
        try:
            async for event in self._loop():
                yield event
        finally:
            self._running = False
            self._idle_event.set()
            # Backstop: an abandoned/failed generator closes its open turn as
            # aborted; no-op when the turn already closed with its own reason.
            self._turn_end("aborted", backstop=True)
            self.session_log.flush()

    # --- Turn/step brackets (Phase 0 event log) ---

    def _turn_start(self) -> None:
        self._turn += 1
        self._step_in_turn = 0
        self._turn_open = True
        self._step_open = False
        self.session_log.append("turn/start", {"turn": self._turn})

    def _turn_end(self, kind: str, **detail: Any) -> None:
        """Close the open turn with a reason. Idempotent — every exit path
        closes with its own reason; the entry-point backstops close abandoned
        generators as 'aborted' and no-op when the turn is already closed.
        'interrupted' is reserved for crash-repair synthesis; never pass it here.

        Closes an open step first: a cancellation (BaseException) can escape
        the step bracket's `except Exception`, and turn/end with an open step
        would violate the log invariant.
        """
        if not self._turn_open:
            return
        if self._step_open:
            self._step_open = False
            self.session_log.append(
                "step/end", {"turn": self._turn, "step": self._step_in_turn}
            )
        self._turn_open = False
        self.session_log.append(
            "turn/end", {"turn": self._turn, "reason": {"kind": kind, **detail}}
        )

    # --- Core loop (pi-style two-loop structure) ---

    async def _loop(self) -> AsyncIterator[Dict[str, Any]]:
        """The heart of the RuntimeLoop.

        Two-loop structure (pi-coding-agent style):
        - OUTER: follow-up loop -- re-enters after agent finishes if follow-ups exist
        - INNER: step loop -- drives VCPU.step() with steering injection

        Handles lifecycle concerns:
        - Steering messages injected between steps (skip remaining tools)
        - Context overflow triggers compaction
        - Iteration limits trigger compaction or termination
        - Interrupts cause clean shutdown with partial results
        - Follow-up messages re-enter the loop after completion
        """
        while True:  # OUTER: follow-up loop
            self._turn_start()
            while True:  # INNER: step loop
                # Check interrupt -- return partial results (pi-style)
                if self._interrupted:
                    partial_output = self._collect_partial_output()
                    result = ToolResult(
                        status="CANCELLED",
                        output=partial_output,
                        ui_detail={"partial_results_count": len(self.partial_results)},
                        is_final=True,
                    )
                    self._emit("INTERRUPTED", {
                        "partial_results_count": len(self.partial_results),
                    })
                    self._save_core_dump("suspended")
                    self._turn_end("aborted")
                    yield {"type": "interrupted", "result": result, "partial_results": self.partial_results}
                    yield {"type": "final", "result": result}
                    return

                # Inject steering messages from last step (pi-style)
                for msg in self._pending_steering:
                    self.mmu.add_user_message(msg)
                    yield {"type": "steering_injected", "content": msg}
                self._pending_steering = []

                # Also drain any queued messages not yet consumed by VCPU steering
                # (backward compat: messages queued before loop starts)
                queued = self.steering_queue.drain_all()
                for msg in queued:
                    self.mmu.add_user_message(msg)
                    yield {"type": "message_queued", "content": msg}

                # Check if context needs compaction before next step
                if self.mmu.needs_compaction():
                    try:
                        summary = await self._try_compaction()
                    except _AuxiliaryChatInterrupted:
                        # Abort/steering interrupted only the helper call. Return to
                        # the loop boundary so cancellation wins or steering drains;
                        # this is not context exhaustion.
                        continue
                    if not summary:
                        result = ToolResult(
                            status="ERROR",
                            output="Context window exhausted after max compactions.",
                            fault=Fault(domain="RESOURCE", code="CTX_OVERFLOW",
                                        message="Context exhausted", retryable=False),
                        )
                        self._save_core_dump("error")
                        self._turn_end("error", cause="context-exhausted")
                        yield {"type": "final", "result": result}
                        return
                    yield {"type": "context_compacted", "compaction_count": self._compaction_count, "summary": summary}

                # ---- Execute one VCPU step ----
                t0 = time.monotonic()
                self._step_in_turn += 1
                self._step_open = True
                self.session_log.append(
                    "step/start", {"turn": self._turn, "step": self._step_in_turn}
                )
                try:
                    step_result = await self.vcpu.step()
                except Exception as e:
                    logger.exception("Unexpected error in VCPU step")
                    self._step_open = False
                    self.session_log.append(
                        "step/end", {"turn": self._turn, "step": self._step_in_turn}
                    )
                    result = ToolResult(
                        status="ERROR", output=f"Runtime error: {e}",
                        fault=Fault(domain="KERNEL", code="SYSTEM_ERROR",
                                    message=str(e), retryable=False),
                    )
                    self._save_core_dump("error")
                    self._turn_end("error")
                    yield {"type": "final", "result": result}
                    return
                self._step_open = False
                self.session_log.append(
                    "step/end", {"turn": self._turn, "step": self._step_in_turn}
                )

                elapsed_ms = int((time.monotonic() - t0) * 1000)
                self._steps_since_compaction += 1
                if not step_result.fault:
                    self._retry_count = 0  # Reset on success

                # Accumulate token usage (pi-style)
                if step_result.usage is not None:
                    self._cumulative_usage += step_result.usage
                    # We NO LONGER update MMU usage here because VCPU already updated it
                    # before adding the massive tool results.
                    yield {
                        "type": "usage_update",
                        "step_usage": step_result.usage.to_dict(),
                        "cumulative_usage": self._cumulative_usage.to_dict(),
                        "context_window": {
                            "current": self.mmu.estimate_tokens(),
                            "maximum": self.mmu.config.max_context_tokens
                        }
                    }

                # Track partial results (pi-style abort recovery)
                for r in step_result.results:
                    self.partial_results.append(r)

                # Collect steering messages from step result (pi-style)
                if step_result.steering_messages:
                    self._pending_steering = step_result.steering_messages
                    # Don't mark as final -- loop continues with steering
                    step_result.is_final = False

                # Yield fine-grained events (pi-style)
                for event in self._step_events(step_result, elapsed_ms):
                    yield event

                # Handle context overflow fault (retry after compaction)
                if step_result.fault and step_result.fault.code == "CTX_OVERFLOW":
                    try:
                        summary = await self._try_compaction()
                    except _AuxiliaryChatInterrupted:
                        continue
                    if summary:
                        yield {"type": "context_compacted", "compaction_count": self._compaction_count, "summary": summary}
                        step_result.is_final = False
                        continue
                    else:
                        self._turn_end("error", cause="context-overflow")
                        yield {"type": "final", "result": step_result.final_result}
                        return

                # Handle iteration budget exceeded (retry after compaction + counter reset)
                if step_result.fault and step_result.fault.code == "BUDGET_EXCEEDED":
                    try:
                        summary = await self._try_compaction()
                    except _AuxiliaryChatInterrupted:
                        continue
                    if summary:
                        # Reset VCPU iteration counter after successful compaction
                        if hasattr(self.vcpu, '_exec'):
                            self.vcpu._exec.iteration = 0
                            self.vcpu._exec.consecutive_thoughts = 0
                        # Re-arm the countdown for the fresh budget, and tell the
                        # model its earlier messages were compacted — otherwise it
                        # may "finish" by pointing at analysis no longer in context.
                        if hasattr(self.vcpu, '_countdown_warning_sent'):
                            self.vcpu._countdown_warning_sent = False
                        self.mmu.add_system_message(
                            "Context was compacted after hitting the iteration limit: "
                            "earlier messages are now summarized. Any deliverable must "
                            "be restated in full when you finish — do not refer to "
                            "content 'above'. If the task asked for files, verify they "
                            "are written."
                        )
                        yield {"type": "context_compacted", "compaction_count": self._compaction_count, "summary": summary}
                        step_result.is_final = False
                        continue
                    else:
                        # Compaction failed -- hard stop
                        self._turn_end("max-iterations")
                        yield {"type": "final", "result": step_result.final_result}
                        return

                # Handle retryable faults with exponential backoff (pi-style)
                if step_result.fault and step_result.fault.retryable and step_result.fault.code != "BUDGET_EXCEEDED":
                    if self._retry_count < self._max_retries:
                        self._retry_count += 1
                        delay = min(self._base_retry_delay * (2 ** (self._retry_count - 1)), 60.0)
                        logger.warning(
                            "Retryable error (attempt %d/%d), backing off %.1fs: %s",
                            self._retry_count, self._max_retries, delay, step_result.fault.message,
                        )
                        # Remove error assistant message from MMU to avoid confusing LLM on retry
                        if hasattr(self.mmu, '_messages') and self.mmu._messages:
                            last = self.mmu._messages[-1]
                            if hasattr(last, 'role') and last.role == 'assistant':
                                self.mmu._messages.pop()
                                logger.debug("Removed error assistant message before retry")
                        await asyncio.sleep(delay)
                        step_result.is_final = False
                        yield {"type": "retry", "attempt": self._retry_count, "delay": delay}
                        continue
                    else:
                        logger.error("Max retries (%d) exhausted: %s", self._max_retries, step_result.fault.message)

                # ---- Stall detection ----
                # A weak model can keep calling tools instead of finishing. Two
                # distinct signals, treated very differently:
                #   • exact-signature repeat (identical tool + identical args,
                #     succeeding over and over) — high confidence it is spinning.
                #     This is the ONLY signal allowed to force termination.
                #   • same-tool streak with VARYING args — NOT a stall on its own:
                #     a reader legitimately reads many different files. This earns
                #     only an occasional gentle nudge, never a kill.
                if not step_result.is_final and not step_result.fault:
                    sig = self._tool_call_signature(step_result)
                    succeeded = bool(step_result.results) and all(
                        r.status == "OK" for r in step_result.results
                    )
                    if sig and succeeded:
                        tool_name = sig.split("::", 1)[0]
                        self._tool_repeat_count = (
                            self._tool_repeat_count + 1 if sig == self._last_tool_sig else 0
                        )
                        self._same_tool_streak = (
                            self._same_tool_streak + 1 if tool_name == self._last_tool_name else 1
                        )
                        self._last_tool_sig = sig
                        self._last_tool_name = tool_name

                        if self._tool_repeat_count >= 2:
                            # 3rd identical call → genuine spin. In contract mode a
                            # sub-agent must exit via submit_result; give it one firm
                            # nudge to do so before we force a tool-free completion,
                            # so its structured deliverable isn't silently dropped.
                            if self._in_contract_mode() and not self._stall_forced_submit:
                                self._stall_forced_submit = True
                                self._tool_repeat_count = 0
                                self.mmu.add_user_message(
                                    "⚠️ You keep repeating the same successful tool call. "
                                    "Stop. You already have the result. Call submit_result "
                                    "now with your findings to finish — this is the only "
                                    "way to deliver your answer."
                                )
                                yield {"type": "stall_nudge", "signature": sig}
                            else:
                                summary = await self._final_summary()
                                # An auxiliary summary can be interrupted by either
                                # abort or steering.  Do not convert that wakeup into
                                # a stall-completed result; return to the loop so abort
                                # wins or queued steering is injected normally.
                                if self._wakeup_event.is_set():
                                    self._tool_repeat_count = 0
                                    self._last_tool_sig = None
                                    continue
                                last = step_result.results[-1].output if step_result.results else None
                                last_clean = str(last).split("\n\n[WARNING")[0].strip() if last else ""
                                if not summary:
                                    summary = last_clean or "Task completed."
                                elif last_clean and last_clean not in summary:
                                    # Truth gate: the summary is a model
                                    # paraphrase; the tool output is ground
                                    # truth. Append it whenever it is not
                                    # already contained — gating on summary
                                    # LENGTH lost the actual answer whenever a
                                    # verbose model wrote a long wrong summary
                                    # (found by eval: hello-tool).
                                    summary = f"{summary}\n\n[Last tool output]\n{last_clean[:2000]}"
                                result = ToolResult(
                                    status="OK", output=summary, is_final=True,
                                    ui_detail={"terminated": "tool_call_stall"},
                                )
                                self._save_core_dump("completed")
                                self._turn_end("completed", stalled=True)
                                yield {"type": "stall_terminated", "signature": sig}
                                yield {"type": "final", "result": result}
                                return
                        elif self._tool_repeat_count == 1:
                            # 2nd identical call → re-anchor on the goal and point at
                            # the next step. Goal-directed, not a generic "stop":
                            # unsticks weak models (Write done → run it with Bash;
                            # sub-agent answered → report the result).
                            goal = getattr(self.mmu, "_goal", "") or "the stated goal"
                            self.mmu.add_user_message(
                                "⚠️ You already completed this step successfully — the "
                                "tool call returned a result. Do NOT repeat it. Re-read "
                                f"the GOAL:\n{goal}\n\nUse the results you already have. "
                                "Perform the NEXT uncompleted step now (if the goal asks "
                                "you to run or test something, call the Bash tool). If "
                                "every part of the goal is already done, stop calling "
                                "tools and reply with a brief summary of what you found."
                            )
                            yield {"type": "stall_nudge", "signature": sig}
                        elif self._same_tool_streak and self._same_tool_streak % 8 == 0:
                            # Long run of one tool with varying args — legitimate, but
                            # check in: a gentle reminder that doesn't tell it to stop,
                            # only to consider whether it has enough to finish.
                            self.mmu.add_user_message(
                                f"You've called {tool_name} many times. If you still need "
                                "more data, continue — but if you already have enough to "
                                "satisfy the goal, stop calling tools and reply with a "
                                "summary of what you found."
                            )
                            yield {"type": "stall_nudge", "signature": sig}
                    else:
                        self._last_tool_sig = None
                        self._last_tool_name = None
                        self._tool_repeat_count = 0
                        self._same_tool_streak = 0
                        self._stall_forced_submit = False

                # Check if done (inner loop)
                if step_result.is_final:
                    break  # exit inner loop, check follow-up

                # Cooperative yield
                if self.config.yield_interval > 0:
                    await asyncio.sleep(self.config.yield_interval)

            # OUTER: Check follow-up queue
            follow_ups = self.followup_queue.drain()
            if follow_ups:
                # Close this turn before injecting: the follow-up messages
                # belong to the NEXT turn (they land between the brackets as
                # log-only prelude, claimed by the turn/start that follows).
                self._turn_end("completed")
                for msg in follow_ups:
                    self.mmu.add_user_message(msg)
                    yield {"type": "followup_injected", "content": msg}
                continue  # re-enter inner loop

            # No follow-ups -- done
            self._save_core_dump("completed")
            self._turn_end("completed")
            yield {"type": "final", "result": step_result.final_result}
            return

    # --- Core Dump (pi-style minimalism) ---

    def _save_core_dump(self, status: str) -> None:
        """Serialize the complete agent state to disk (Core Dump)."""
        messages = [m.to_dict() for m in self.mmu._messages]

        # Pull vcpu state (Registers) -- defensive for mock VCPUs in tests
        vcpu_state = {}
        exec_ctx = getattr(self.vcpu, "_exec", None)
        if exec_ctx:
            vcpu_state = {
                "iteration": getattr(exec_ctx, "iteration", 0),
                "consecutive_thoughts": getattr(exec_ctx, "consecutive_thoughts", 0),
                "consecutive_errors": getattr(exec_ctx, "consecutive_errors", 0),
            }

        # Serialize config info
        vcpu_config = {}
        if hasattr(self.vcpu, "config"):
            cfg = self.vcpu.config
            vcpu_config = {
                "max_iterations": getattr(cfg, "max_iterations", 200),
                "max_consecutive_thoughts": getattr(cfg, "max_consecutive_thoughts", 8),
                "max_consecutive_errors": getattr(cfg, "max_consecutive_errors", 3),
            }

        # Persist MMU critical state (global_summary + goal survive compaction)
        mmu_state = {
            "global_summary": self.mmu._global_summary,
            "goal": self.mmu._goal,
            "plan": getattr(self.mmu, "_plan", ""),
        }

        # Merge mmu_state into metadata so it persists across restarts
        metadata = dict(self.metadata) if self.metadata else {}
        metadata["mmu_state"] = mmu_state

        # Persist cumulative token usage — without it a session's cache hit
        # rate and cost are unrecoverable after the fact (the audit could not
        # answer "what was the hit rate of that 46-minute session").
        if hasattr(self._cumulative_usage, "to_dict"):
            metadata["cumulative_usage"] = self._cumulative_usage.to_dict()

        # Preserve llm_config from metadata (set at session creation)
        # so it doesn't get lost when vcpu_config is overwritten with runtime state
        llm_config = metadata.get("llm_config", {})

        try:
            self.storage.save_session(
                session_id=self.session_id,
                status=status,
                messages=messages,
                vcpu_state=vcpu_state,
                vcpu_config=vcpu_config,
                llm_config=llm_config,
                metadata=metadata,
            )
            logger.info(f"Core Dump saved for session {self.session_id} ({status})")
        except Exception as e:
            logger.error(f"Failed to save Core Dump for session {self.session_id}: {e}")

    # --- Partial result collection (pi-style) ---

    async def _chat_auxiliary(self, messages: List[Dict[str, Any]]) -> Any:
        """Run a tool-free helper chat with the same lifecycle guarantees as VCPU.

        Wakeup is shared by steering and interruption.  We cancel promptly in
        either case; the normal loop then gives interruption priority, or drains
        and injects steering on its next iteration.  This avoids consuming a
        steering message here and preserves the existing queue semantics.
        """
        if self._interrupted or self._abort_event.is_set():
            raise _AuxiliaryChatInterrupted()

        timeout = float(getattr(getattr(self.vcpu, "config", None), "llm_call_timeout", 300.0))
        chat_task = asyncio.create_task(self._adapter.chat(messages, []))
        wakeup_task = asyncio.create_task(self._wakeup_event.wait())
        done, pending = await asyncio.wait(
            (chat_task, wakeup_task), timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        if wakeup_task in done or self._interrupted or self._abort_event.is_set():
            if not chat_task.done():
                chat_task.cancel()
                await asyncio.gather(chat_task, return_exceptions=True)
            raise _AuxiliaryChatInterrupted()
        if chat_task not in done:
            chat_task.cancel()
            await asyncio.gather(chat_task, return_exceptions=True)
            raise asyncio.TimeoutError("Auxiliary LLM call timed out")
        return chat_task.result()

    async def _final_summary(self) -> Optional[str]:
        """Force a brief, tool-free final answer from the model. Used when the
        agent has stalled re-issuing an identical successful tool call: replay the
        full context with NO tools so the model must reply with text, not act.
        Passes a pre-assembled message list (not the MMU) — the adapter only
        accepts a list or re-assembles, and the latter path rejects kwargs."""
        try:
            messages = list(self.mmu.assemble_context())
        except Exception:
            messages = []
        messages.append({
            "role": "user",
            "content": "The task appears complete. Do NOT call any tools. Reply with the "
                       "concrete final answer the task asked for — state the specific "
                       "result or value (e.g. the exact number, the command output, or "
                       "the file contents) explicitly, not just that you finished.",
        })
        try:
            resp = await self._chat_auxiliary(messages)
            return (resp.content or "").strip() or None
        except _AuxiliaryChatInterrupted:
            return None
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("Final-summary call failed: %s", e)
            return None

    def _in_contract_mode(self) -> bool:
        """Whether this loop drives a contract-mode sub-agent (must exit via
        submit_result). Defensive: tolerate mock VCPUs without a config."""
        cfg = getattr(self.vcpu, "config", None)
        return bool(getattr(cfg, "contract_mode", False))

    @staticmethod
    def _tool_call_signature(step_result: StepResult) -> Optional[str]:
        """Canonical signature for a step that is exactly ONE tool call and
        nothing else (no REPLY/text). Returns None otherwise — multi-tool or
        text steps represent genuine progress and never count as a stall."""
        tool_actions = [a for a in step_result.actions if a.kind == "TOOL_CALL"]
        if len(tool_actions) != 1:
            return None
        if any(a.kind in ("REPLY", "RETURN") for a in step_result.actions):
            return None
        a = tool_actions[0]
        try:
            args = json.dumps(a.args, sort_keys=True, default=str)
        except Exception:
            args = str(a.args)
        return f"{a.name}::{args}"

    def _collect_partial_output(self) -> str:
        """Collect all partial outputs into a summary string."""
        if not self.partial_results:
            return "Execution interrupted. No results collected yet."
        parts = []
        for i, r in enumerate(self.partial_results, 1):
            preview = str(r.output)[:200] if r.output else "(no output)"
            parts.append(f"[{i}] {r.status}: {preview}")
        return f"Execution interrupted. Partial results ({len(parts)} tool calls):\n" + "\n".join(parts)

    # --- Compaction ---

    async def _llm_summarize(self, system_prompt: str, user_prompt: str) -> str:
        """Call the LLM adapter for context summarization.

        Uses the adapter's chat() interface with system + user messages,
        no tools. Collects all text from the response.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        # Pass messages list as first positional arg (not keyword).
        # DirectAdapter.chat(mmu, tools) → stream(mmu) handles isinstance(mmu, list).
        response = await self._chat_auxiliary(messages)
        return response.content or ""

    async def _try_compaction(self) -> Optional[str]:
        """Attempt to compact the context. Returns summary if successful, None otherwise.

        Auxiliary interruption is control flow, not compaction failure: propagate it
        to the loop call sites, which resume normal abort/steering handling instead
        of manufacturing CTX_OVERFLOW.

        A long agentic run legitimately compacts many times, so the limit is NOT a
        fixed total — that turned a healthy long task into a spurious CTX_OVERFLOW
        once compaction actually started firing. Instead we bail only on genuine
        exhaustion: when compaction repeatedly fails to reduce the backlog (e.g. a
        single message larger than the window). max_compactions remains a high
        absolute runaway ceiling, not a normal operating limit.
        """
        if self._compaction_count >= self.config.max_compactions:
            logger.warning(
                "Absolute compaction ceiling (%d) reached — runaway guard",
                self.config.max_compactions,
            )
            return None

        logger.info("Compacting context (attempt #%d)", self._compaction_count + 1)

        tokens_before = self.mmu.estimate_tokens()
        summarizer = self._llm_summarize if self._adapter else None
        summary = await self.mmu.archive_and_reset(summarizer=summarizer)
        if not summary:
            return None

        self._compaction_count += 1
        self._steps_since_compaction = 0

        # Productivity check: did this compaction meaningfully shrink the backlog?
        # Genuine exhaustion shows up as consecutive no-progress compactions; bail
        # only then, not on a fixed count.
        tokens_after = self.mmu.estimate_tokens()
        if tokens_after < tokens_before * 0.95:
            self._unproductive_compactions = 0
        else:
            self._unproductive_compactions += 1
            logger.warning(
                "Compaction made little progress (%d -> %d tokens), %d/%d consecutive",
                tokens_before, tokens_after,
                self._unproductive_compactions, self.config.max_unproductive_compactions,
            )
            if self._unproductive_compactions >= self.config.max_unproductive_compactions:
                logger.error("Context genuinely exhausted: compaction cannot reduce the backlog further")
                return None

        self._emit("CONTEXT_COMPACTED", {"summary_len": len(summary)})
        return summary

    # --- Fine-grained Events (pi-style) ---

    def _step_events(self, step: StepResult, elapsed_ms: int) -> List[Dict[str, Any]]:
        """Convert a StepResult into fine-grained stream events.

        Pi emits individual events for text deltas, tool call starts/results.
        We emit events in chronological order: text -> tool_call -> tool_result
        paired per tool, so the UI can render them interleaved.
        """
        events: List[Dict[str, Any]] = []

        # Build a result lookup by action index for pairing
        result_by_idx: Dict[int, Any] = {}
        tool_action_idx = 0
        for i, action in enumerate(step.actions):
            if action.kind == "TOOL_CALL":
                if tool_action_idx < len(step.results):
                    result_by_idx[i] = step.results[tool_action_idx]
                    tool_action_idx += 1

        # Emit events in action order: text and tool pairs interleaved
        for i, action in enumerate(step.actions):
            if action.kind == "TOOL_CALL":
                # Emit tool_call_start
                events.append({
                    "type": "tool_call_start",
                    "tool": action.name,
                    "args": {k: str(v) for k, v in action.args.items()},
                    "call_id": action.id,
                })
                # Immediately pair with tool_call_done (result)
                result = result_by_idx.get(i)
                if result:
                    event: Dict[str, Any] = {
                        "type": "tool_call_done",
                        "tool": action.name,
                        "call_id": action.id,
                        "status": result.status,
                        "output_preview": str(result.output)[:200] if result.output else None,
                    }
                    if result.ui_detail:
                        event["ui_detail"] = result.ui_detail
                    events.append(event)
            elif action.kind in ("REPLY", "RETURN"):
                text = action.args.get("text", action.args.get("result", ""))
                events.append({
                    "type": "text_delta",
                    "content": text,
                    "is_final": True,
                })
            elif action.kind == "THOUGHT":
                text = action.args.get("text", "")
                events.append({
                    "type": "text_delta",
                    "content": text,
                    "is_final": False,
                })

        # Emit step summary
        events.append({
            "type": "step",
            "iteration": self.vcpu.iteration,
            "elapsed_ms": elapsed_ms,
            "is_final": step.is_final,
            "action_count": len(step.actions),
            "result_count": len(step.results),
        })

        # Final result -- only emit if no REPLY/RETURN action already emitted text
        has_reply = any(a.kind in ("REPLY", "RETURN") for a in step.actions)
        if step.is_final and step.final_result and not has_reply:
            events.append({
                "type": "text_delta",
                "content": str(step.final_result.output)[:500] if step.final_result.output else "",
                "is_final": True,
            })

        return events

    def _emit(self, event_type: str, data: Dict) -> None:
        if self._event_cb:
            self._event_cb(Event(type=event_type, pid="loop", data=data))
