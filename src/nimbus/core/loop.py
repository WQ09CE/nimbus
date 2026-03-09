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
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional
import uuid

from .protocol import Event, Fault, StepResult, ToolResult
from .storage import SessionStorage

logger = logging.getLogger("nimbus.loop")


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class LoopConfig:
    max_compactions: int = 3  # Max times we can compact before giving up
    compaction_cooldown: int = 5  # Min steps between compactions
    yield_interval: float = 0.0  # Yield control between steps (for cooperative scheduling)


# =============================================================================
# Steering Queue (pi-style: checked between tool calls)
# =============================================================================


class SteeringQueue:
    """Messages injected while agent is executing tools.

    Checked between each tool call -- skips remaining tools.
    Pi-coding-agent uses a callback after each turn to ask for queued messages,
    supporting two modes: one-at-a-time or all-at-once.
    """

    def __init__(self, wakeup_event: Optional[asyncio.Event] = None) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._wakeup_event = wakeup_event

    def steer(self, message: str) -> None:
        """Add a steering message (thread-safe via asyncio.Queue).

        This triggers the wakeup event to interrupt the current LLM call
        if one is in progress.
        """
        self._queue.put_nowait(message)
        if self._wakeup_event:
            self._wakeup_event.set()

    def drain_one(self) -> Optional[str]:
        """Drain one message at a time (default mode)."""
        try:
            msg = self._queue.get_nowait()
            if self._wakeup_event and self._queue.empty():
                self._wakeup_event.clear()
            return msg
        except asyncio.QueueEmpty:
            return None

    def drain_all(self) -> List[str]:
        """Drain all queued messages at once."""
        messages: List[str] = []
        while not self._queue.empty():
            try:
                messages.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if self._wakeup_event and self._queue.empty():
            self._wakeup_event.clear()
        return messages

    @property
    def pending(self) -> int:
        return self._queue.qsize()


# =============================================================================
# Follow-Up Queue (pi-style: re-enters loop after agent finishes)
# =============================================================================


class FollowUpQueue:
    """Messages sent after agent finishes. Re-enters the loop."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def follow_up(self, message: str) -> None:
        """Add a follow-up message."""
        self._queue.put_nowait(message)

    def drain(self) -> List[str]:
        """Drain all queued follow-up messages."""
        messages: List[str] = []
        while not self._queue.empty():
            try:
                messages.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    @property
    def pending(self) -> int:
        return self._queue.qsize()


# =============================================================================
# MessageQueue (backward-compatible facade)
# =============================================================================


class MessageQueue:
    """Backward-compatible facade over SteeringQueue.

    Existing code (e.g., session.py) uses message_queue.enqueue().
    This delegates to the steering queue transparently.
    """

    def __init__(self, steering_queue: SteeringQueue) -> None:
        self._steering = steering_queue

    def enqueue(self, message: str) -> None:
        """Add a message to the steering queue (backward compat)."""
        self._steering.steer(message)

    def drain(self) -> List[str]:
        """Drain all queued messages at once."""
        return self._steering.drain_all()

    def drain_one(self) -> Optional[str]:
        """Drain one message at a time."""
        return self._steering.drain_one()

    @property
    def pending(self) -> int:
        return self._steering.pending


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

        self._compaction_count = 0
        self._steps_since_compaction = 0
        self._interrupted = False

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

    def request_interruption(self) -> None:
        """Signal the loop to stop after the current step."""
        self._interrupted = True
        self.vcpu.request_interruption()

    def abort(self) -> None:
        """Hard stop -- cancel everything including running bash processes."""
        self._interrupted = True
        self._abort_event.set()
        self.vcpu.request_interruption()

    async def wait_for_idle(self) -> None:
        """Wait until the loop finishes. Used after abort()."""
        if not self._running:
            return
        await self._idle_event.wait()

    # --- Sync run (returns final result) ---

    async def run(self) -> ToolResult:
        """Run the loop until completion. Returns the final ToolResult."""
        final_result = None
        async for event in self._loop():
            if event.get("type") == "final":
                final_result = event["result"]

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
                    summary = await self._try_compaction()
                    if not summary:
                        result = ToolResult(
                            status="ERROR",
                            output="Context window exhausted after max compactions.",
                            fault=Fault(domain="RESOURCE", code="CTX_OVERFLOW",
                                        message="Context exhausted", retryable=False),
                        )
                        self._save_core_dump("error")
                        yield {"type": "final", "result": result}
                        return
                    yield {"type": "context_compacted", "compaction_count": self._compaction_count, "summary": summary}

                # ---- Execute one VCPU step ----
                t0 = time.monotonic()
                try:
                    step_result = await self.vcpu.step()
                except Exception as e:
                    logger.exception("Unexpected error in VCPU step")
                    result = ToolResult(
                        status="ERROR", output=f"Runtime error: {e}",
                        fault=Fault(domain="KERNEL", code="SYSTEM_ERROR",
                                    message=str(e), retryable=False),
                    )
                    self._save_core_dump("error")
                    yield {"type": "final", "result": result}
                    return

                elapsed_ms = int((time.monotonic() - t0) * 1000)
                self._steps_since_compaction += 1

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
                    summary = await self._try_compaction()
                    if summary:
                        yield {"type": "context_compacted", "compaction_count": self._compaction_count, "summary": summary}
                        step_result.is_final = False
                        continue
                    else:
                        yield {"type": "final", "result": step_result.final_result}
                        return

                # Check if done (inner loop)
                if step_result.is_final:
                    break  # exit inner loop, check follow-up

                # Cooperative yield
                if self.config.yield_interval > 0:
                    await asyncio.sleep(self.config.yield_interval)

            # OUTER: Check follow-up queue
            follow_ups = self.followup_queue.drain()
            if follow_ups:
                for msg in follow_ups:
                    self.mmu.add_user_message(msg)
                    yield {"type": "followup_injected", "content": msg}
                continue  # re-enter inner loop

            # No follow-ups -- done
            self._save_core_dump("completed")
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
                "max_iterations": getattr(cfg, "max_iterations", 50),
                "max_consecutive_thoughts": getattr(cfg, "max_consecutive_thoughts", 8),
                "max_consecutive_errors": getattr(cfg, "max_consecutive_errors", 3),
            }

        # Persist MMU critical state (global_summary + goal survive compaction)
        mmu_state = {
            "global_summary": self.mmu._global_summary,
            "goal": self.mmu._goal,
        }

        # Merge mmu_state into metadata so it persists across restarts
        metadata = dict(self.metadata) if self.metadata else {}
        metadata["mmu_state"] = mmu_state

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
        # The adapter's chat() returns an LLMResponse with .content
        response = await self._adapter.chat(messages=messages, tools=[])
        return response.content or ""

    async def _try_compaction(self) -> Optional[str]:
        """Attempt to compact the context. Returns summary if successful, None otherwise."""
        if self._compaction_count >= self.config.max_compactions:
            logger.warning("Max compactions (%d) reached", self.config.max_compactions)
            return None

        if self._steps_since_compaction < self.config.compaction_cooldown:
            logger.warning(
                "Compaction cooldown: %d steps since last (min %d)",
                self._steps_since_compaction, self.config.compaction_cooldown,
            )
            pass

        logger.info("Compacting context (attempt %d/%d)",
                     self._compaction_count + 1, self.config.max_compactions)

        summarizer = self._llm_summarize if self._adapter else None
        summary = await self.mmu.archive_and_reset(summarizer=summarizer)
        if summary:
            self._compaction_count += 1
            self._steps_since_compaction = 0
            self._emit("CONTEXT_COMPACTED", {"summary_len": len(summary)})
            return summary

        return None

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
                    "args_preview": {k: str(v)[:100] for k, v in action.args.items()},
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
