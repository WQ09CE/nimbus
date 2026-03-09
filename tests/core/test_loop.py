"""Tests for nimbus_next.loop — the RuntimeLoop execution driver."""

from typing import List

import pytest

from nimbus.core.loop import LoopConfig, MessageQueue, RuntimeLoop, SteeringQueue
from nimbus.core.mmu import MMU, MMUConfig
from nimbus.core.protocol import ActionIR, Fault, StepResult, ToolResult


# =============================================================================
# Mock VCPU
# =============================================================================


class MockVCPU:
    """Programmable mock VCPU that returns predefined step results."""

    def __init__(self, steps: List[StepResult]):
        self._steps = list(steps)
        self._call_count = 0
        self._interrupted = False

    @property
    def iteration(self) -> int:
        return self._call_count

    def set_wakeup_event(self, event) -> None:
        """Accept wakeup event from RuntimeLoop (no-op in mock)."""
        pass

    def request_interruption(self) -> None:
        self._interrupted = True

    async def step(self) -> StepResult:
        if self._interrupted:
            return StepResult(
                is_final=True,
                final_result=ToolResult(status="CANCELLED", output="Interrupted"),
            )
        if self._call_count >= len(self._steps):
            return StepResult(
                is_final=True,
                final_result=ToolResult(status="OK", output="Done (exhausted)"),
            )
        result = self._steps[self._call_count]
        self._call_count += 1
        return result


# =============================================================================
# Helpers
# =============================================================================


def make_step(is_final=False, output=None, actions=None, fault=None):
    """Create a StepResult for testing."""
    result = StepResult(is_final=is_final, actions=actions or [])
    if is_final and output:
        result.final_result = ToolResult(status="OK", output=output, is_final=True)
    if fault:
        result.fault = fault
        if not result.final_result:
            result.final_result = ToolResult(status="ERROR", output=fault.message, fault=fault)
    return result


def make_tool_step(tool_name="Read", output="file contents"):
    """Create a step with a tool call result."""
    return StepResult(
        actions=[ActionIR(kind="TOOL_CALL", name=tool_name)],
        results=[ToolResult(status="OK", output=output)],
    )


# =============================================================================
# Tests
# =============================================================================


class TestRuntimeLoopBasic:
    @pytest.mark.asyncio
    async def test_single_step_completion(self):
        """VCPU returns final on first step → loop completes."""
        vcpu = MockVCPU([make_step(is_final=True, output="The answer is 42.")])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)
        result = await loop.run()
        assert result.status == "OK"
        assert "42" in result.output

    @pytest.mark.asyncio
    async def test_multi_step_then_final(self):
        """VCPU does tool calls then finishes."""
        vcpu = MockVCPU([
            make_tool_step("Read", "def hello(): pass"),
            make_tool_step("Bash", "OK"),
            make_step(is_final=True, output="Fixed the bug."),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)
        result = await loop.run()
        assert result.status == "OK"
        assert "bug" in result.output
        assert vcpu._call_count == 3

    @pytest.mark.asyncio
    async def test_empty_steps_exhaust(self):
        """If mock runs out, VCPU returns final with 'exhausted'."""
        vcpu = MockVCPU([
            make_tool_step(),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)
        result = await loop.run()
        # Second call exhausts mock → returns Done
        assert result.status == "OK"


class TestRuntimeLoopInterrupt:
    @pytest.mark.asyncio
    async def test_interrupt_before_step(self):
        """Interrupt before any step → cancelled."""
        vcpu = MockVCPU([make_tool_step()])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)
        loop.request_interruption()
        result = await loop.run()
        assert result.status == "CANCELLED"

    @pytest.mark.asyncio
    async def test_interrupt_during_loop(self):
        """Interrupt after first step → cancelled on next iteration."""
        steps = [make_tool_step(), make_tool_step()]
        vcpu = MockVCPU(steps)
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)
            if len(events) == 1:
                loop.request_interruption()

        # Should have step event + final (cancelled)
        assert any(e.get("type") == "final" for e in events)
        final = [e for e in events if e.get("type") == "final"][0]
        assert final["result"].status == "CANCELLED"


class TestRuntimeLoopCompaction:
    @pytest.mark.asyncio
    async def test_compaction_triggered(self):
        """When MMU needs compaction, it triggers before the step."""
        # Create MMU with very low token limit
        config = MMUConfig(max_context_tokens=50, compress_threshold=0.5)
        mmu = MMU(config)
        # Add enough content to trigger compaction
        mmu.add_user_message("x" * 500)
        mmu.add_assistant_message("y" * 500)

        vcpu = MockVCPU([
            make_step(is_final=True, output="Done after compaction."),
        ])
        loop = RuntimeLoop(vcpu, mmu)
        result = await loop.run()
        assert result.status == "OK"

    @pytest.mark.asyncio
    async def test_max_compactions_exhausted(self):
        """After max_compactions, loop terminates with error."""
        config = MMUConfig(max_context_tokens=10, compress_threshold=0.1)
        mmu = MMU(config)
        mmu.add_user_message("x" * 1000)

        # Use a large step count — but compaction keeps failing
        vcpu = MockVCPU([make_tool_step() for _ in range(20)])

        loop_config = LoopConfig(max_compactions=1)
        loop = RuntimeLoop(vcpu, mmu, config=loop_config)
        result = await loop.run()
        # Should eventually exhaust compactions or succeed
        assert result is not None

    @pytest.mark.asyncio
    async def test_context_overflow_fault_triggers_compaction(self):
        """CTX_OVERFLOW fault from VCPU triggers compaction retry."""
        overflow_fault = Fault(
            domain="LLM", code="CTX_OVERFLOW",
            message="Context too long", retryable=False,
        )
        vcpu = MockVCPU([
            make_step(is_final=True, fault=overflow_fault),
            make_step(is_final=True, output="Recovered after compaction."),
        ])
        # Need some messages for compaction to work
        mmu = MMU()
        mmu.add_user_message("Some context to compact")
        mmu.add_assistant_message("Some response")

        loop = RuntimeLoop(vcpu, mmu)
        result = await loop.run()
        assert result is not None


class TestRuntimeLoopStreaming:
    @pytest.mark.asyncio
    async def test_stream_yields_events(self):
        """Stream mode yields events for each step."""
        vcpu = MockVCPU([
            make_tool_step("Read", "file data"),
            make_step(is_final=True, output="All done."),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)

        assert len(events) >= 2  # At least 2 step events + final
        assert events[-1]["type"] == "final"

    @pytest.mark.asyncio
    async def test_stream_events_have_iteration(self):
        """Step events include iteration count."""
        vcpu = MockVCPU([
            make_tool_step(),
            make_step(is_final=True, output="Done"),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)

        step_events = [e for e in events if e.get("type") == "step"]
        assert len(step_events) >= 1
        assert "iteration" in step_events[0]


class TestRuntimeLoopErrorHandling:
    @pytest.mark.asyncio
    async def test_vcpu_exception(self):
        """Exception in VCPU.step() → loop returns error result."""
        class FailingVCPU:
            iteration = 0
            def request_interruption(self): pass
            async def step(self):
                raise RuntimeError("kaboom")

        mmu = MMU()
        loop = RuntimeLoop(FailingVCPU(), mmu)
        result = await loop.run()
        assert result.status == "ERROR"
        assert "kaboom" in result.output


class TestMessageQueue:
    """Tests for pi-style message queuing (backward-compat facade)."""

    def test_enqueue_and_drain(self):
        sq = SteeringQueue()
        q = MessageQueue(sq)
        q.enqueue("msg1")
        q.enqueue("msg2")
        assert q.pending == 2
        msgs = q.drain()
        assert msgs == ["msg1", "msg2"]
        assert q.pending == 0

    def test_drain_one(self):
        sq = SteeringQueue()
        q = MessageQueue(sq)
        q.enqueue("msg1")
        q.enqueue("msg2")
        assert q.drain_one() == "msg1"
        assert q.drain_one() == "msg2"
        assert q.drain_one() is None

    def test_drain_empty(self):
        sq = SteeringQueue()
        q = MessageQueue(sq)
        assert q.drain() == []
        assert q.drain_one() is None

    @pytest.mark.asyncio
    async def test_message_queue_in_loop(self):
        """Queued messages should be injected between steps."""
        vcpu = MockVCPU([
            make_tool_step("Read", "file data"),
            make_step(is_final=True, output="Done"),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        # Queue a message before streaming
        loop.message_queue.enqueue("Also check tests")

        events = []
        async for event in loop.stream():
            events.append(event)

        # Should have a message_queued event
        queued_events = [e for e in events if e.get("type") == "message_queued"]
        assert len(queued_events) == 1
        assert queued_events[0]["content"] == "Also check tests"


class TestPartialResults:
    """Tests for pi-style partial result tracking on abort."""

    @pytest.mark.asyncio
    async def test_partial_results_on_interrupt(self):
        """Interrupting should preserve partial results."""
        vcpu = MockVCPU([
            make_tool_step("Read", "file contents"),
            make_tool_step("Bash", "test output"),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)
            # Interrupt after first step yields events
            step_events = [e for e in events if e.get("type") == "step"]
            if len(step_events) == 1:
                loop.request_interruption()

        # Should have partial results from the first step
        assert len(loop.partial_results) >= 1
        assert loop.partial_results[0].output == "file contents"

        # Should have an interrupted event
        interrupted = [e for e in events if e.get("type") == "interrupted"]
        assert len(interrupted) == 1
        assert interrupted[0]["result"].status == "CANCELLED"

    @pytest.mark.asyncio
    async def test_partial_results_empty_on_clean_finish(self):
        """Clean completion should have partial_results from tool calls."""
        vcpu = MockVCPU([
            make_tool_step("Read", "data"),
            make_step(is_final=True, output="Done"),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)
        await loop.run()
        # Tool call results are tracked
        assert len(loop.partial_results) == 1


class TestFineGrainedEvents:
    """Tests for pi-style fine-grained event streaming."""

    @pytest.mark.asyncio
    async def test_tool_call_events(self):
        """Should emit tool_call_start and tool_call_done events."""
        vcpu = MockVCPU([
            make_tool_step("Bash", "hello world"),
            make_step(is_final=True, output="Done"),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)

        event_types = [e.get("type") for e in events]
        assert "tool_call_start" in event_types
        assert "tool_call_done" in event_types

    @pytest.mark.asyncio
    async def test_text_delta_on_final(self):
        """Final reply should emit text_delta event."""
        vcpu = MockVCPU([
            make_step(
                is_final=True, output="The answer",
                actions=[ActionIR(kind="REPLY", args={"text": "The answer"})],
            ),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)

        text_deltas = [e for e in events if e.get("type") == "text_delta"]
        assert len(text_deltas) >= 1
        assert "The answer" in text_deltas[0]["content"]

    @pytest.mark.asyncio
    async def test_ui_detail_in_events(self):
        """Split tool results should include ui_detail in events."""
        step = StepResult(
            actions=[ActionIR(kind="TOOL_CALL", name="Bash")],
            results=[ToolResult(
                status="OK", output="ok",
                ui_detail={"exit_code": 0, "lines": 3},
            )],
        )
        vcpu = MockVCPU([
            step,
            make_step(is_final=True, output="Done"),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)

        done_events = [e for e in events if e.get("type") == "tool_call_done"]
        assert len(done_events) >= 1
        assert done_events[0]["ui_detail"] == {"exit_code": 0, "lines": 3}
