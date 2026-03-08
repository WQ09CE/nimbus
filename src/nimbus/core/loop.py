"""
RuntimeLoop — The outer execution driver.

Drives VCPU.step() in a loop, handling:
1. Context overflow → trigger compaction and retry
2. Iteration limit → optional compaction or graceful termination
3. Interrupt signals → clean shutdown with partial results
4. Event streaming → fine-grained events for reactive UI (pi-style)
5. Message queuing → inject user messages while agent is working

Why separate from VCPU?
VCPU handles a single Think-Act-Observe cycle. The RuntimeLoop handles
the *lifecycle*: when to stop, when to compact, when to yield to the user.
This separation keeps VCPU testable without async complexity.

Design notes (pi-coding-agent influence):
- Message queue: pi uses a callback after each turn to ask for queued messages.
  We use an asyncio.Queue for the same effect — messages injected between steps.
- Partial results: pi returns partial content on abort (stopReason === 'aborted').
  We track accumulated results so interruption never loses work.
- Fine-grained events: pi emits text_delta, tool_call events as async iterators.
  We emit similar typed events for reactive UI binding.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from .protocol import Event, Fault, StepResult, ToolResult

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
# Message Queue (pi-style)
# =============================================================================


class MessageQueue:
    """Queue for injecting user messages while the agent is working.

    Pi-coding-agent uses a callback after each turn to ask for queued messages,
    supporting two modes: one-at-a-time or all-at-once. We implement the same
    via asyncio.Queue with a drain method.
    """

    def __init__(self, wakeup_event: Optional[asyncio.Event] = None) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._wakeup_event = wakeup_event

    def enqueue(self, message: str) -> None:
        """Add a message to the queue (thread-safe via asyncio.Queue)."""
        self._queue.put_nowait(message)
        if self._wakeup_event:
            self._wakeup_event.set()

    def drain(self) -> List[str]:
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

    def drain_one(self) -> Optional[str]:
        """Drain one message at a time."""
        try:
            msg = self._queue.get_nowait()
            if self._wakeup_event and self._queue.empty():
                self._wakeup_event.clear()
            return msg
        except asyncio.QueueEmpty:
            return None

    @property
    def pending(self) -> int:
        return self._queue.qsize()


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

    Message queuing (pi-style):
        loop = RuntimeLoop(vcpu, mmu)
        loop.message_queue.enqueue("Also check the tests")
        async for event in loop.stream():
            ...

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
    ):
        self.vcpu = vcpu
        self.mmu = mmu
        self.config = config or LoopConfig()
        self._event_cb = event_callback

        self._compaction_count = 0
        self._steps_since_compaction = 0
        self._interrupted = False

        # Pi-style message queue + Graceful steering interrupt
        self._wakeup_event = asyncio.Event()
        self.message_queue = MessageQueue(self._wakeup_event)
        if hasattr(self.vcpu, "set_wakeup_event"):
            self.vcpu.set_wakeup_event(self._wakeup_event)

        # Partial result tracking (pi-style abort recovery)
        self.partial_results: List[ToolResult] = []

    def request_interruption(self) -> None:
        """Signal the loop to stop after the current step."""
        self._interrupted = True
        self.vcpu.request_interruption()

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
        async for event in self._loop():
            yield event

    # --- Core loop ---

    async def _loop(self) -> AsyncIterator[Dict[str, Any]]:
        """The heart of the RuntimeLoop.

        Drives VCPU.step() and handles lifecycle concerns:
        - Context overflow triggers compaction
        - Iteration limits trigger compaction or termination
        - Interrupts cause clean shutdown with partial results
        - Queued messages are injected between steps
        """
        while True:
            # Check interrupt — return partial results (pi-style)
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
                yield {"type": "interrupted", "result": result, "partial_results": self.partial_results}
                yield {"type": "final", "result": result}
                return

            # Inject queued messages (pi-style message queuing)
            queued = self.message_queue.drain()
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
                yield {"type": "final", "result": result}
                return

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            self._steps_since_compaction += 1

            # Track partial results (pi-style abort recovery)
            for r in step_result.results:
                self.partial_results.append(r)

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

            # Check if done
            if step_result.is_final:
                yield {"type": "final", "result": step_result.final_result}
                return

            # Cooperative yield
            if self.config.yield_interval > 0:
                await asyncio.sleep(self.config.yield_interval)

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

        summary = await self.mmu.archive_and_reset()
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
        We do the same instead of bundling everything into one 'step' event.
        """
        events: List[Dict[str, Any]] = []

        # Emit per-action events
        for action in step.actions:
            if action.kind == "TOOL_CALL":
                events.append({
                    "type": "tool_call_start",
                    "tool": action.name,
                    "args_preview": {k: str(v)[:100] for k, v in action.args.items()},
                    "call_id": action.id,
                })
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

        # Emit per-result events (with split ui_detail)
        for i, result in enumerate(step.results):
            tool_name = step.actions[i].name if i < len(step.actions) else "unknown"
            event: Dict[str, Any] = {
                "type": "tool_call_done",
                "tool": tool_name,
                "status": result.status,
                "output_preview": str(result.output)[:200] if result.output else None,
            }
            # Include ui_detail if present (split tool result)
            if result.ui_detail:
                event["ui_detail"] = result.ui_detail
            events.append(event)

        # Emit step summary
        events.append({
            "type": "step",
            "iteration": self.vcpu.iteration,
            "elapsed_ms": elapsed_ms,
            "is_final": step.is_final,
            "action_count": len(step.actions),
            "result_count": len(step.results),
        })

        # Final result
        if step.is_final and step.final_result:
            events.append({
                "type": "text_delta",
                "content": str(step.final_result.output)[:500] if step.final_result.output else "",
                "is_final": True,
            })

        return events

    def _emit(self, event_type: str, data: Dict) -> None:
        if self._event_cb:
            self._event_cb(Event(type=event_type, pid="loop", data=data))
