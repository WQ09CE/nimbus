"""Tests for the fault-semantics table and its VCPU enforcement.

The table (core/fault_semantics.py) is the normative spec for what survives
each fault class; these tests pin (a) the table's internal invariants and
(b) the display/history convergence enforcement in VCPU: streamed text the
user already saw must land in history, marked via meta — never lost, never
recognized by content.
"""

import asyncio
from typing import List, Optional

import pytest

from nimbus.core.decoder import InstructionDecoder
from nimbus.core.fault_semantics import (
    INTERRUPT_MARKER,
    ORIGIN_INTERRUPTED,
    SEMANTICS,
    FaultClass,
    marked_partial_text,
)
from nimbus.core.mmu import MMU, PinnedContext
from nimbus.core.protocol import ActionIR, Fault, ToolResult
from nimbus.core.vcpu import VCPU, VCPUConfig

# =============================================================================
# Table invariants
# =============================================================================


class TestSemanticsTable:
    def test_every_fault_class_has_a_row(self):
        from typing import get_args
        for cls in get_args(FaultClass):
            assert cls in SEMANTICS, f"missing row for fault class {cls}"
        assert set(SEMANTICS) == set(get_args(FaultClass))

    def test_interrupted_reason_reserved_for_synthetic_repair(self):
        """'interrupted' on turn/end must only come from crash/fork synthesis
        (session_log docstring reservation) — never from a live-loop row."""
        for cls, policy in SEMANTICS.items():
            if policy.turn_end_reason == "interrupted":
                assert cls in ("crash", "fork_cut"), (
                    f"{cls} may not use the reserved 'interrupted' reason"
                )

    def test_retry_paths_leave_no_trace(self):
        """Silent retry and steering preemption re-issue the request; keeping
        anything would replay half-formed state to the model (hax EV_RETRY)."""
        for cls in ("provider_retry", "steering_preempt"):
            policy = SEMANTICS[cls]
            assert policy.keep_streamed_text == "none"
            assert policy.close_open_calls == "none"
            assert policy.turn_end_reason == ""  # turn stays open
            assert policy.resumability == "retry"

    def test_graded_closers_only_for_dead_process_cuts(self):
        """Live paths answer their own batches (cancelled); graded closers
        (OUTCOME_UNKNOWN/NOT_STARTED) exist only where nobody was alive to
        answer: crash repair and fork cuts."""
        for cls, policy in SEMANTICS.items():
            if policy.close_open_calls == "graded":
                assert cls in ("crash", "fork_cut")

    def test_marked_partial_text(self):
        assert marked_partial_text("") is None
        assert marked_partial_text("   \n") is None
        assert marked_partial_text("Hello wor") == f"Hello wor\n{INTERRUPT_MARKER}"


# =============================================================================
# VCPU enforcement — display/history convergence
# =============================================================================


class StreamingFailALU:
    """Streams chunks via on_chunk, then raises. Optionally succeeds later."""

    def __init__(self, chunks: List[str], error: Exception,
                 succeed_after: Optional[int] = None,
                 final_content: str = "Recovered answer."):
        self._chunks = chunks
        self._error = error
        self._succeed_after = succeed_after
        self._final_content = final_content
        self.calls = 0

    async def chat(self, messages, tools, on_chunk=None):
        self.calls += 1
        if self._succeed_after is not None and self.calls > self._succeed_after:
            class _Resp:
                content = self._final_content
                tool_calls = None
            if on_chunk:
                on_chunk(self._final_content)
            return _Resp()
        for c in self._chunks:
            if on_chunk:
                on_chunk(c)
        raise self._error


class HangingALU:
    """Streams chunks then hangs until cancelled."""

    def __init__(self, chunks: List[str]):
        self._chunks = chunks

    async def chat(self, messages, tools, on_chunk=None):
        for c in self._chunks:
            if on_chunk:
                on_chunk(c)
        await asyncio.sleep(3600)


class NullGate:
    async def syscall_tool(self, action: ActionIR, timeout=None) -> ToolResult:
        return ToolResult(status="OK", output="ok")


def make_vcpu(alu, wakeup: Optional[asyncio.Event] = None, max_errors: int = 1):
    mmu = MMU()
    mmu.set_pinned(PinnedContext(system_rules="Be helpful."))
    mmu.add_user_message("Do the task.")
    config = VCPUConfig(
        max_iterations=50, llm_call_timeout=5.0,
        max_consecutive_errors=max_errors,
    )
    vcpu = VCPU(alu, InstructionDecoder(), NullGate(), mmu, tools=[], config=config)
    if wakeup is not None:
        vcpu._wakeup_event = wakeup
    return vcpu, mmu


def marked_messages(mmu: MMU):
    return [
        m for m in mmu._messages
        if m.role == "assistant" and m.meta.get("origin") == ORIGIN_INTERRUPTED
    ]


class TestVCPUPreservation:
    @pytest.mark.asyncio
    async def test_terminal_error_preserves_marked_text(self):
        """provider_error row: streamed text survives, marked via meta."""
        alu = StreamingFailALU(["Hello ", "wor"], RuntimeError("boom"))
        vcpu, mmu = make_vcpu(alu)
        result = await vcpu.step()
        assert result.is_final and result.final_result.status == "ERROR"
        kept = marked_messages(mmu)
        assert len(kept) == 1
        assert kept[0].content == f"Hello wor\n{INTERRUPT_MARKER}"

    @pytest.mark.asyncio
    async def test_empty_stream_terminal_error_adds_nothing(self):
        alu = StreamingFailALU([], RuntimeError("boom"))
        vcpu, mmu = make_vcpu(alu)
        result = await vcpu.step()
        assert result.is_final
        assert marked_messages(mmu) == []

    @pytest.mark.asyncio
    async def test_retry_clears_buffer_no_duplication(self):
        """provider_retry row: a retried attempt streams from scratch; the
        dead attempt's chunks must not leak into history or the next step."""
        alu = StreamingFailALU(
            ["half-str"],
            Fault(domain="LLM", code="RATE_LIMIT", message="429", retryable=True),
            succeed_after=1,
        )
        vcpu, mmu = make_vcpu(alu, max_errors=5)
        r1 = await vcpu.step()   # retryable failure → non-final, silent
        assert not r1.is_final
        assert marked_messages(mmu) == []
        r2 = await vcpu.step()   # succeeds
        assert r2.is_final
        # No marked remnants, and the dead chunk is nowhere in history.
        assert marked_messages(mmu) == []
        assert all("half-str" not in (m.content or "") for m in mmu._messages)

    @pytest.mark.asyncio
    async def test_user_interrupt_mid_stream_preserves_marked_text(self):
        """user_interrupt row: wakeup+interrupt mid-stream keeps marked text."""
        wakeup = asyncio.Event()
        alu = HangingALU(["Partial thought"])
        vcpu, mmu = make_vcpu(alu, wakeup=wakeup)

        async def interrupt_soon():
            await asyncio.sleep(0.05)
            vcpu.request_interruption()
            wakeup.set()

        task = asyncio.create_task(interrupt_soon())
        result = await vcpu.step()
        await task
        assert not result.actions  # empty step; loop's interrupt check takes over
        kept = marked_messages(mmu)
        assert len(kept) == 1
        assert kept[0].content == f"Partial thought\n{INTERRUPT_MARKER}"

    @pytest.mark.asyncio
    async def test_preserve_streamed_text_direct(self):
        """Loop-facing API for retries-exhausted: preserves once, then no-op."""
        alu = StreamingFailALU(
            ["half "],
            Fault(domain="LLM", code="RATE_LIMIT", message="429", retryable=True),
        )
        vcpu, mmu = make_vcpu(alu, max_errors=5)
        r = await vcpu.step()  # retryable → keeps nothing, buffer still holds
        assert not r.is_final
        assert vcpu.preserve_streamed_text() is True
        kept = marked_messages(mmu)
        assert len(kept) == 1 and kept[0].content == f"half \n{INTERRUPT_MARKER}"
        assert vcpu.preserve_streamed_text() is False  # buffer cleared

    @pytest.mark.asyncio
    async def test_steering_preempt_mid_stream_keeps_nothing(self):
        """steering_preempt row: wakeup WITHOUT interrupt drops the half-stream
        (the re-issued request regenerates it)."""
        wakeup = asyncio.Event()
        alu = HangingALU(["Partial thought"])
        vcpu, mmu = make_vcpu(alu, wakeup=wakeup)

        async def steer_soon():
            await asyncio.sleep(0.05)
            wakeup.set()  # steering only — no interruption

        task = asyncio.create_task(steer_soon())
        result = await vcpu.step()
        await task
        assert not result.actions
        assert marked_messages(mmu) == []
        assert all("Partial thought" not in (m.content or "") for m in mmu._messages)


# =============================================================================
# Write-path arbitration (MMU single write gate)
# =============================================================================


class TestWritePathArbitration:
    def test_restore_messages_bypasses_log_by_name(self):
        """The named restore bypass replaces the surface without notifying —
        these messages came FROM the log; re-logging would duplicate."""
        mmu = MMU()
        seen = []
        mmu.event_sink = lambda t, d: seen.append(t)
        mmu.restore_messages([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "meta": {"x": 1}},
        ])
        assert mmu.message_count == 2
        assert seen == []  # no notifications
        view = mmu.messages_view()
        assert view[1].meta == {"x": 1}

    def test_live_appends_notify(self):
        mmu = MMU()
        seen = []
        mmu.event_sink = lambda t, d: seen.append(t)
        mmu.add_user_message("q")
        mmu.add_assistant_message("a")
        assert seen == ["user/message", "assistant/message"]

    def test_messages_view_is_a_copy(self):
        mmu = MMU()
        mmu.add_user_message("q")
        view = mmu.messages_view()
        view.clear()
        assert mmu.message_count == 1
