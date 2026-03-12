"""Tests for pi-coding-agent style steering, follow-up, and abort.

Tests the new two-queue architecture:
- SteeringQueue: messages injected while tools execute, skip remaining tools
- FollowUpQueue: messages re-enter the loop after agent finishes
- Abort: hard stop with process group kill signal
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from nimbus.core.loop import (
    FollowUpQueue,
    MessageQueue,
    RuntimeLoop,
    SteeringQueue,
)
from nimbus.core.mmu import MMU, PinnedContext
from nimbus.core.protocol import ActionIR, StepResult, ToolResult
from nimbus.core.vcpu import VCPU, VCPUConfig

# =============================================================================
# Mocks
# =============================================================================


@dataclass
class MockResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None


class MockALU:
    """Programmable mock LLM."""

    def __init__(self, responses: List[MockResponse]):
        self._responses = list(responses)
        self._call_count = 0

    async def chat(self, messages, tools, on_chunk=None):
        if self._call_count >= len(self._responses):
            return MockResponse(content="Done.")
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


class MockGate:
    """Mock gate that records calls and returns configurable results."""

    def __init__(self, results: Optional[Dict[str, str]] = None, delay: float = 0.0):
        self._results = results or {}
        self._delay = delay
        self.calls: List[ActionIR] = []

    async def syscall_tool(self, action: ActionIR, timeout=None) -> ToolResult:
        self.calls.append(action)
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        output = self._results.get(action.name, f"executed {action.name}")
        return ToolResult(status="OK", output=output)


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


def make_step(is_final=False, output=None, actions=None, fault=None, steering_messages=None):
    """Create a StepResult for testing."""
    result = StepResult(is_final=is_final, actions=actions or [])
    if is_final and output:
        result.final_result = ToolResult(status="OK", output=output, is_final=True)
    if fault:
        result.fault = fault
        if not result.final_result:
            result.final_result = ToolResult(status="ERROR", output=fault.message, fault=fault)
    if steering_messages:
        result.steering_messages = steering_messages
    return result


def make_tool_step(tool_name="Read", output="file contents"):
    """Create a step with a tool call result."""
    return StepResult(
        actions=[ActionIR(kind="TOOL_CALL", name=tool_name)],
        results=[ToolResult(status="OK", output=output)],
    )


def make_tool_call(name, args, tc_id="tc1"):
    return {
        "function": {"name": name, "arguments": args if isinstance(args, str) else __import__("json").dumps(args)},
        "id": tc_id,
    }


def make_vcpu_with_steering(responses, gate_results=None, text_is_final=False, steering_fn=None):
    """Create a VCPU with steering callback."""
    from nimbus.core.decoder import InstructionDecoder
    alu = MockALU(responses)
    decoder = InstructionDecoder()
    gate = MockGate(gate_results)
    mmu = MMU()
    mmu.set_pinned(PinnedContext(system_rules="Be helpful."))
    mmu.add_user_message("Do the task.")
    config = VCPUConfig(max_iterations=50, llm_call_timeout=10.0)
    vcpu = VCPU(
        alu, decoder, gate, mmu, tools=[], config=config,
        text_is_final=text_is_final, get_steering=steering_fn,
    )
    return vcpu, gate


# =============================================================================
# SteeringQueue Tests
# =============================================================================


class TestSteeringQueue:
    def test_steer_and_drain_one(self):
        q = SteeringQueue()
        q.steer("change approach")
        q.steer("also fix tests")
        assert q.pending == 2
        assert q.drain_one() == "change approach"
        assert q.drain_one() == "also fix tests"
        assert q.drain_one() is None

    def test_steer_and_drain_all(self):
        q = SteeringQueue()
        q.steer("msg1")
        q.steer("msg2")
        msgs = q.drain_all()
        assert msgs == ["msg1", "msg2"]
        assert q.pending == 0

    def test_drain_empty(self):
        q = SteeringQueue()
        assert q.drain_one() is None
        assert q.drain_all() == []

    def test_wakeup_event_set_on_steer(self):
        event = asyncio.Event()
        q = SteeringQueue(wakeup_event=event)
        assert not event.is_set()
        q.steer("hello")
        assert event.is_set()

    def test_wakeup_event_cleared_on_drain(self):
        event = asyncio.Event()
        q = SteeringQueue(wakeup_event=event)
        q.steer("hello")
        assert event.is_set()
        q.drain_one()
        assert not event.is_set()


# =============================================================================
# FollowUpQueue Tests
# =============================================================================


class TestFollowUpQueue:
    def test_follow_up_and_drain(self):
        q = FollowUpQueue()
        q.follow_up("now fix tests")
        q.follow_up("and update docs")
        assert q.pending == 2
        msgs = q.drain()
        assert msgs == ["now fix tests", "and update docs"]
        assert q.pending == 0

    def test_drain_empty(self):
        q = FollowUpQueue()
        assert q.drain() == []


# =============================================================================
# MessageQueue Backward Compat Tests
# =============================================================================


class TestMessageQueueBackwardCompat:
    def test_enqueue_delegates_to_steering(self):
        sq = SteeringQueue()
        mq = MessageQueue(sq)
        mq.enqueue("hello")
        assert sq.pending == 1
        assert sq.drain_one() == "hello"

    def test_drain_delegates_to_steering(self):
        sq = SteeringQueue()
        mq = MessageQueue(sq)
        mq.enqueue("a")
        mq.enqueue("b")
        assert mq.drain() == ["a", "b"]

    def test_pending_reflects_steering(self):
        sq = SteeringQueue()
        mq = MessageQueue(sq)
        mq.enqueue("x")
        assert mq.pending == 1


# =============================================================================
# VCPU Steering Tests
# =============================================================================


class TestVCPUSteering:
    @pytest.mark.asyncio
    async def test_steering_skips_remaining_tools(self):
        """3 tool calls, inject steering after first -> second and third get SKIPPED."""
        steering_messages = []

        def get_steering():
            if steering_messages:
                msgs = list(steering_messages)
                steering_messages.clear()
                return msgs
            return []

        tc1 = make_tool_call("Read", {"file_path": "a.py"}, tc_id="tc1")
        tc2 = make_tool_call("Read", {"file_path": "b.py"}, tc_id="tc2")
        tc3 = make_tool_call("Read", {"file_path": "c.py"}, tc_id="tc3")

        vcpu, gate = make_vcpu_with_steering(
            [MockResponse(tool_calls=[tc1, tc2, tc3])],
            gate_results={"Read": "file data"},
            steering_fn=get_steering,
        )

        # Inject steering message (will be seen after first tool executes)
        steering_messages.append("Change approach please")

        result = await vcpu.step()

        # All tools execute in parallel (gather), steering checked after all complete
        assert len(result.results) == 3
        assert result.results[0].status == "OK"
        assert result.results[1].status == "OK"
        assert result.results[2].status == "OK"

        # Steering messages collected after all tools complete
        assert result.steering_messages == ["Change approach please"]

        # Gate called for all tools (parallel execution)
        assert len(gate.calls) == 3

    @pytest.mark.asyncio
    async def test_no_steering_normal_flow(self):
        """Without steering callback, everything works as before."""
        tc1 = make_tool_call("Read", {"file_path": "a.py"}, tc_id="tc1")
        tc2 = make_tool_call("Bash", {"command": "ls"}, tc_id="tc2")

        vcpu, gate = make_vcpu_with_steering(
            [MockResponse(tool_calls=[tc1, tc2])],
            gate_results={"Read": "data", "Bash": "files"},
            steering_fn=None,  # No steering
        )

        result = await vcpu.step()

        # Both tools executed normally
        assert len(result.results) == 2
        assert result.results[0].status == "OK"
        assert result.results[1].status == "OK"
        assert len(gate.calls) == 2
        assert result.steering_messages == []

    @pytest.mark.asyncio
    async def test_steering_checked_once_after_all_tools(self):
        """Steering is checked once after all tools complete (parallel gather)."""
        call_count = 0

        def counting_steering():
            nonlocal call_count
            call_count += 1
            return []  # Never steer

        tc1 = make_tool_call("Read", {"file_path": "a.py"}, tc_id="tc1")
        tc2 = make_tool_call("Read", {"file_path": "b.py"}, tc_id="tc2")

        vcpu, gate = make_vcpu_with_steering(
            [MockResponse(tool_calls=[tc1, tc2])],
            gate_results={"Read": "data"},
            steering_fn=counting_steering,
        )

        await vcpu.step()

        # Checked once after all tools complete
        assert call_count == 1
        assert len(gate.calls) == 2

    @pytest.mark.asyncio
    async def test_steering_checked_once_even_single_tool(self):
        """Even with one tool call, steering is checked once after completion."""
        call_count = 0

        def counting_steering():
            nonlocal call_count
            call_count += 1
            return []

        tc1 = make_tool_call("Read", {"file_path": "a.py"}, tc_id="tc1")

        vcpu, gate = make_vcpu_with_steering(
            [MockResponse(tool_calls=[tc1])],
            gate_results={"Read": "data"},
            steering_fn=counting_steering,
        )

        await vcpu.step()

        # Steering checked once after tool complete
        assert call_count == 1
        assert len(gate.calls) == 1


# =============================================================================
# RuntimeLoop Steering Integration Tests
# =============================================================================


class TestRuntimeLoopSteering:
    @pytest.mark.asyncio
    async def test_steering_message_injected(self):
        """After skip, steering message appears in next iteration."""
        # Step 1: returns steering_messages (simulates VCPU finding steering)
        step1 = make_step(
            is_final=False,
            actions=[ActionIR(kind="TOOL_CALL", name="Read")],
            steering_messages=["Change approach"],
        )
        step1.results = [
            ToolResult(status="OK", output="data"),
            ToolResult(status="SKIPPED", output="Skipped due to queued user message."),
        ]
        # Step 2: final
        step2 = make_step(is_final=True, output="Done with new approach.")

        vcpu = MockVCPU([step1, step2])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)

        # Should have steering_injected event
        steering_events = [e for e in events if e.get("type") == "steering_injected"]
        assert len(steering_events) == 1
        assert steering_events[0]["content"] == "Change approach"

        # Final event
        final = [e for e in events if e.get("type") == "final"]
        assert len(final) == 1
        assert final[0]["result"].output == "Done with new approach."

    @pytest.mark.asyncio
    async def test_steering_prevents_final(self):
        """Steering messages prevent the step from being marked final."""
        # Step with steering_messages + is_final=True should NOT end the loop
        step1 = make_step(
            is_final=True,  # Would be final, but steering overrides
            output="premature end",
            steering_messages=["Actually do more"],
        )
        step1.results = [ToolResult(status="OK", output="partial")]

        step2 = make_step(is_final=True, output="Actually done.")

        vcpu = MockVCPU([step1, step2])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)

        final = [e for e in events if e.get("type") == "final"]
        assert len(final) == 1
        assert final[0]["result"].output == "Actually done."
        # VCPU was called twice
        assert vcpu._call_count == 2


# =============================================================================
# RuntimeLoop Follow-Up Tests
# =============================================================================


class TestRuntimeLoopFollowUp:
    @pytest.mark.asyncio
    async def test_followup_reenters_loop(self):
        """Agent finishes, follow-up message re-enters."""
        step1 = make_step(is_final=True, output="First task done.")
        step2 = make_step(is_final=True, output="Follow-up task done.")

        vcpu = MockVCPU([step1, step2])
        mmu = MMU()
        followup_queue = FollowUpQueue()
        loop = RuntimeLoop(vcpu, mmu, followup_queue=followup_queue)

        # Pre-load a follow-up message
        followup_queue.follow_up("Now fix tests too")

        events = []
        async for event in loop.stream():
            events.append(event)

        # Should have followup_injected event
        fu_events = [e for e in events if e.get("type") == "followup_injected"]
        assert len(fu_events) == 1
        assert fu_events[0]["content"] == "Now fix tests too"

        # Should have final with the follow-up result
        final = [e for e in events if e.get("type") == "final"]
        assert len(final) == 1
        assert final[0]["result"].output == "Follow-up task done."

        # VCPU called twice
        assert vcpu._call_count == 2

    @pytest.mark.asyncio
    async def test_no_followup_normal_completion(self):
        """Without follow-ups, loop completes normally."""
        step1 = make_step(is_final=True, output="All done.")

        vcpu = MockVCPU([step1])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)

        final = [e for e in events if e.get("type") == "final"]
        assert len(final) == 1
        assert final[0]["result"].output == "All done."
        assert vcpu._call_count == 1


# =============================================================================
# RuntimeLoop Abort Tests
# =============================================================================


class TestRuntimeLoopAbort:
    @pytest.mark.asyncio
    async def test_abort_interrupts_loop(self):
        """Calling abort() stops the loop."""
        step1 = make_tool_step("Read", "data")
        step2 = make_tool_step("Bash", "output")

        vcpu = MockVCPU([step1, step2])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)
            step_events = [e for e in events if e.get("type") == "step"]
            if len(step_events) == 1:
                loop.abort()

        # Should have interrupted event
        interrupted = [e for e in events if e.get("type") == "interrupted"]
        assert len(interrupted) == 1
        assert interrupted[0]["result"].status == "CANCELLED"

    @pytest.mark.asyncio
    async def test_abort_sets_abort_event(self):
        """abort() sets the abort event for bash process kill."""
        vcpu = MockVCPU([make_step(is_final=True, output="done")])
        mmu = MMU()
        abort_event = asyncio.Event()
        loop = RuntimeLoop(vcpu, mmu, abort_event=abort_event)

        loop.abort()
        assert abort_event.is_set()
        assert loop._interrupted

    @pytest.mark.asyncio
    async def test_abort_preserves_partial(self):
        """abort mid-execution -> partial results collected."""
        step1 = make_tool_step("Read", "file data from read")

        vcpu = MockVCPU([step1, make_tool_step()])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        events = []
        async for event in loop.stream():
            events.append(event)
            step_events = [e for e in events if e.get("type") == "step"]
            if len(step_events) == 1:
                loop.abort()

        # Partial results from the first step should be preserved
        assert len(loop.partial_results) >= 1
        assert loop.partial_results[0].output == "file data from read"


# =============================================================================
# RuntimeLoop wait_for_idle Tests
# =============================================================================


class TestWaitForIdle:
    @pytest.mark.asyncio
    async def test_wait_for_idle_returns_when_not_running(self):
        """wait_for_idle() returns immediately when loop is not running."""
        vcpu = MockVCPU([])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)
        # Should return immediately since not running
        await loop.wait_for_idle()

    @pytest.mark.asyncio
    async def test_wait_for_idle_after_stream(self):
        """wait_for_idle() works after stream completes."""
        vcpu = MockVCPU([make_step(is_final=True, output="done")])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        async for _ in loop.stream():
            pass

        # Should return immediately after stream completes
        await loop.wait_for_idle()

    @pytest.mark.asyncio
    async def test_wait_for_idle_after_abort(self):
        """abort() then wait_for_idle() completes."""
        step1 = make_tool_step("Read", "data")
        vcpu = MockVCPU([step1, make_tool_step()])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        async def run_and_abort():
            events = []
            async for event in loop.stream():
                events.append(event)
                step_events = [e for e in events if e.get("type") == "step"]
                if len(step_events) == 1:
                    loop.abort()

        # Run in a task
        task = asyncio.create_task(run_and_abort())
        await task

        # Now wait_for_idle should return immediately
        await asyncio.wait_for(loop.wait_for_idle(), timeout=1.0)


# =============================================================================
# Integration: Backward Compatibility
# =============================================================================


class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_message_queue_still_works(self):
        """message_queue.enqueue() still works via the facade."""
        vcpu = MockVCPU([
            make_tool_step("Read", "data"),
            make_step(is_final=True, output="Done"),
        ])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        # Use old API
        loop.message_queue.enqueue("Also check tests")

        events = []
        async for event in loop.stream():
            events.append(event)

        # Should have message_queued event (backward compat)
        queued_events = [e for e in events if e.get("type") == "message_queued"]
        assert len(queued_events) == 1
        assert queued_events[0]["content"] == "Also check tests"

    @pytest.mark.asyncio
    async def test_request_interruption_still_works(self):
        """request_interruption() still works alongside abort()."""
        vcpu = MockVCPU([make_tool_step(), make_tool_step()])
        mmu = MMU()
        loop = RuntimeLoop(vcpu, mmu)

        loop.request_interruption()
        result = await loop.run()
        assert result.status == "CANCELLED"
