"""
RuntimeLoop — The outer execution driver.

Drives VCPU.step() in a loop, handling:
1. Context overflow → trigger compaction and retry
2. Iteration limit → optional compaction or graceful termination
3. Interrupt signals → clean shutdown
4. Event streaming → yield step events for UI/debugging

Why separate from VCPU?
VCPU handles a single Think-Act-Observe cycle. The RuntimeLoop handles
the *lifecycle*: when to stop, when to compact, when to yield to the user.
This separation keeps VCPU testable without async complexity.
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
        """Run the loop, yielding events at each step."""
        async for event in self._loop():
            yield event

    # --- Core loop ---

    async def _loop(self) -> AsyncIterator[Dict[str, Any]]:
        """The heart of the RuntimeLoop.

        Drives VCPU.step() and handles lifecycle concerns:
        - Context overflow triggers compaction
        - Iteration limits trigger compaction or termination
        - Interrupts cause clean shutdown
        """
        while True:
            # Check interrupt
            if self._interrupted:
                result = ToolResult(status="CANCELLED", output="Execution interrupted.", is_final=True)
                yield {"type": "final", "result": result}
                return

            # Check if context needs compaction before next step
            if self.mmu.needs_compaction():
                compacted = await self._try_compaction()
                if not compacted:
                    # Can't compact anymore — force termination
                    result = ToolResult(
                        status="ERROR",
                        output="Context window exhausted after max compactions.",
                        fault=Fault(domain="RESOURCE", code="CTX_OVERFLOW",
                                    message="Context exhausted", retryable=False),
                    )
                    yield {"type": "final", "result": result}
                    return

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

            # Yield step event
            yield self._step_event(step_result, elapsed_ms)

            # Handle context overflow fault (retry after compaction)
            if step_result.fault and step_result.fault.code == "CTX_OVERFLOW":
                compacted = await self._try_compaction()
                if compacted:
                    # Reset VCPU state to allow retry
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

    # --- Compaction ---

    async def _try_compaction(self) -> bool:
        """Attempt to compact the context. Returns True if successful."""
        if self._compaction_count >= self.config.max_compactions:
            logger.warning("Max compactions (%d) reached", self.config.max_compactions)
            return False

        if self._steps_since_compaction < self.config.compaction_cooldown:
            logger.warning(
                "Compaction cooldown: %d steps since last (min %d)",
                self._steps_since_compaction, self.config.compaction_cooldown,
            )
            # Allow it anyway if we really need it (context is full)
            pass

        logger.info("Compacting context (attempt %d/%d)",
                     self._compaction_count + 1, self.config.max_compactions)

        summary = await self.mmu.archive_and_reset()
        if summary:
            self._compaction_count += 1
            self._steps_since_compaction = 0
            self._emit("CONTEXT_COMPACTED", {"summary_len": len(summary)})
            return True

        return False

    # --- Events ---

    def _step_event(self, step: StepResult, elapsed_ms: int) -> Dict[str, Any]:
        """Convert a StepResult into a stream event."""
        event: Dict[str, Any] = {
            "type": "step",
            "iteration": self.vcpu.iteration,
            "elapsed_ms": elapsed_ms,
            "is_final": step.is_final,
        }

        # Include actions
        if step.actions:
            event["actions"] = [
                {"kind": a.kind, "name": a.name}
                for a in step.actions
            ]

        # Include tool results
        if step.results:
            event["results"] = [
                {"status": r.status, "output_preview": str(r.output)[:200]}
                for r in step.results
            ]

        # Include final result
        if step.is_final and step.final_result:
            event["final_output"] = str(step.final_result.output)[:500]

        return event

    def _emit(self, event_type: str, data: Dict) -> None:
        if self._event_cb:
            self._event_cb(Event(type=event_type, pid="loop", data=data))
