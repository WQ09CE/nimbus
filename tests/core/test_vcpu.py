"""Tests for nimbus_next.vcpu — the Think-Act-Observe engine."""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from nimbus.core.decoder import InstructionDecoder
from nimbus.core.mmu import MMU, PinnedContext
from nimbus.core.protocol import ActionIR, ToolResult
from nimbus.core.vcpu import VCPU, VCPUConfig


# =============================================================================
# Mock ALU (LLM)
# =============================================================================


@dataclass
class MockResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None


class MockALU:
    """Programmable mock LLM that returns predefined responses."""

    def __init__(self, responses: List[MockResponse]):
        self._responses = list(responses)
        self._call_count = 0

    async def chat(self, messages, tools, on_chunk=None):
        if self._call_count >= len(self._responses):
            return MockResponse(content="Done.")
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


# =============================================================================
# Mock Gate
# =============================================================================


class MockGate:
    def __init__(self, results: Optional[Dict[str, str]] = None):
        self._results = results or {}
        self.calls: List[ActionIR] = []

    async def syscall_tool(self, action: ActionIR, timeout=None) -> ToolResult:
        self.calls.append(action)
        output = self._results.get(action.name, f"executed {action.name}")
        return ToolResult(status="OK", output=output)


# =============================================================================
# Helper
# =============================================================================

def make_tool_call(name, args, tc_id="tc1"):
    return {
        "function": {"name": name, "arguments": args if isinstance(args, str) else __import__("json").dumps(args)},
        "id": tc_id,
    }


def make_vcpu(responses, gate_results=None, text_is_final=False, max_iter=50):
    alu = MockALU(responses)
    decoder = InstructionDecoder()
    gate = MockGate(gate_results)
    mmu = MMU()
    mmu.set_pinned(PinnedContext(system_rules="Be helpful."))
    mmu.add_user_message("Do the task.")
    config = VCPUConfig(max_iterations=max_iter, llm_call_timeout=10.0)
    return VCPU(alu, decoder, gate, mmu, tools=[], config=config, text_is_final=text_is_final), gate


# =============================================================================
# Tests
# =============================================================================


class TestVCPUBasicFlow:
    @pytest.mark.asyncio
    async def test_direct_reply(self):
        """LLM returns text with text_is_final=True → REPLY → done."""
        vcpu, _ = make_vcpu([MockResponse(content="The answer is 42.")], text_is_final=True)
        result = await vcpu.step()
        assert result.is_final
        assert "42" in result.final_result.output

    @pytest.mark.asyncio
    async def test_tool_call_flow(self):
        """LLM calls a tool → Gate executes → result in MMU."""
        tc = make_tool_call("Read", {"file_path": "/tmp/x"})
        vcpu, gate = make_vcpu(
            [MockResponse(tool_calls=[tc])],
            gate_results={"Read": "file contents here"},
        )
        result = await vcpu.step()
        assert not result.is_final
        assert len(result.results) == 1
        assert result.results[0].output == "file contents here"
        assert len(gate.calls) == 1
        assert gate.calls[0].name == "Read"

    @pytest.mark.asyncio
    async def test_think_then_act(self):
        """LLM thinks first (text + tool_call), then tool executes."""
        tc = make_tool_call("Bash", {"command": "ls"})
        vcpu, gate = make_vcpu(
            [MockResponse(content="Let me check.", tool_calls=[tc])],
        )
        result = await vcpu.step()
        assert not result.is_final
        assert len(gate.calls) == 1

    @pytest.mark.asyncio
    async def test_return_done(self):
        """LLM says 'Done!' with text_is_final=False → detected as RETURN."""
        vcpu, _ = make_vcpu([MockResponse(content="Done!")], text_is_final=False)
        result = await vcpu.step()
        assert result.is_final


class TestVCPULimits:
    @pytest.mark.asyncio
    async def test_max_iterations(self):
        """Hits iteration limit → final result with retryable error (soft limit)."""
        vcpu, _ = make_vcpu(
            [MockResponse(content="thinking...") for _ in range(5)],
            max_iter=2,
            text_is_final=False,
        )
        # Step 1: thought
        r1 = await vcpu.step()
        # Step 2: thought
        r2 = await vcpu.step()
        # Step 3: should hit limit
        r3 = await vcpu.step()
        assert r3.is_final
        assert "Max iterations" in r3.final_result.output
        assert r3.fault is not None
        assert r3.fault.retryable is True
        assert r3.fault.code == "BUDGET_EXCEEDED"

    @pytest.mark.asyncio
    async def test_max_consecutive_thoughts(self):
        """Too many thoughts without action → forced termination."""
        config = VCPUConfig(max_consecutive_thoughts=2, max_iterations=100)
        responses = [MockResponse(content=f"Let me think about step {i} next and figure out the approach") for i in range(5)]
        alu = MockALU(responses)
        decoder = InstructionDecoder()
        gate = MockGate()
        mmu = MMU()
        mmu.add_user_message("Go")
        vcpu = VCPU(alu, decoder, gate, mmu, [], config=config, text_is_final=False)

        # First thought
        r1 = await vcpu.step()
        assert not r1.is_final
        # Second thought → should terminate
        r2 = await vcpu.step()
        assert r2.is_final

    @pytest.mark.asyncio
    async def test_interruption(self):
        vcpu, _ = make_vcpu([MockResponse(content="hello")])
        vcpu.request_interruption()
        result = await vcpu.step()
        assert result.is_final
        assert result.final_result.status == "CANCELLED"


class TestVCPUMemoryIntegrity:
    @pytest.mark.asyncio
    async def test_tool_results_in_mmu(self):
        """After tool execution, results are written to MMU."""
        tc = make_tool_call("Read", {"file_path": "x.py"}, tc_id="call_1")
        vcpu, _ = make_vcpu(
            [MockResponse(tool_calls=[tc])],
            gate_results={"Read": "def hello(): pass"},
        )
        await vcpu.step()
        ctx = vcpu.mmu.assemble_context()
        # Should have: system, user goal, assistant+tool_calls, tool result
        tool_msgs = [m for m in ctx if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "hello" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_assistant_tool_calls_preserved(self):
        """Assistant message with tool_calls is preserved in MMU."""
        tc = make_tool_call("Bash", {"command": "ls"}, tc_id="call_2")
        vcpu, _ = make_vcpu([MockResponse(content="Checking...", tool_calls=[tc])])
        await vcpu.step()
        ctx = vcpu.mmu.assemble_context()
        assistant_msgs = [m for m in ctx if m.get("role") == "assistant"]
        assert any(m.get("tool_calls") for m in assistant_msgs)

    @pytest.mark.asyncio
    async def test_multi_step_flow(self):
        """Full flow: tool call → observe → reply."""
        tc = make_tool_call("Read", {"file_path": "a.py"})
        vcpu, _ = make_vcpu([
            MockResponse(tool_calls=[tc]),  # Step 1: tool call
            MockResponse(content="Found the bug."),  # Step 2: reply
        ], text_is_final=True)

        r1 = await vcpu.step()
        assert not r1.is_final

        r2 = await vcpu.step()
        assert r2.is_final
        assert "bug" in r2.final_result.output


class TestVCPUErrorHandling:
    @pytest.mark.asyncio
    async def test_hallucination_recovery(self):
        """Decoder detects hallucination → error injected → retry."""
        vcpu, _ = make_vcpu([
            MockResponse(content='[Called Read with file_path="x"]'),  # hallucination
            MockResponse(content="All done."),  # recovery
        ], text_is_final=True)

        r1 = await vcpu.step()
        assert not r1.is_final  # should retry, not crash

        r2 = await vcpu.step()
        assert r2.is_final
