"""
Tests for Nimbus v2 core components.

Run with: pytest tests/test_v2_core.py -v
"""

import asyncio
from typing import Any, Dict

import pytest

from nimbus.core.protocol import (
    ActionIR,
    Fault,
    ToolResult,
)
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.os.gate import (
    KernelGate,
    SimpleEventStream,
)

# =============================================================================
# Protocol Tests
# =============================================================================

class TestActionIR:
    """Tests for ActionIR."""

    def test_create_tool_call(self):
        action = ActionIR(
            kind="TOOL_CALL",
            name="Read",
            args={"file_path": "/path/to/file"}
        )
        assert action.kind == "TOOL_CALL"
        assert action.name == "Read"
        assert action.args["file_path"] == "/path/to/file"
        assert len(action.id) == 8  # UUID hex[:8]

    def test_create_sub_call(self):
        action = ActionIR(
            kind="SUB_CALL",
            name="explore",
            args={"goal": "find auth module"}
        )
        assert action.kind == "SUB_CALL"

    def test_create_return(self):
        action = ActionIR(
            kind="RETURN",
            name="return",
            args={"result": "done"}
        )
        assert action.kind == "RETURN"


class TestToolResult:
    """Tests for ToolResult."""

    def test_ok_result(self):
        result = ToolResult(status="OK", output={"data": "test"})
        assert result.status == "OK"
        assert result.output == {"data": "test"}
        assert result.fault is None

    def test_error_result_with_fault(self):
        fault = Fault(
            domain="TOOL",
            code="TOOL_FAILURE",
            message="Something went wrong"
        )
        result = ToolResult(status="ERROR", fault=fault)
        assert result.status == "ERROR"
        assert result.fault is not None
        assert "TOOL_FAILURE" in str(result.fault)


class TestFault:
    """Tests for Fault."""

    def test_fault_str(self):
        fault = Fault(
            domain="LLM",
            code="ILL_INSTRUCTION",
            message="Bad instruction"
        )
        assert str(fault) == "[LLM:ILL_INSTRUCTION] Bad instruction"

    def test_fault_is_exception(self):
        fault = Fault(
            domain="PERMISSION",
            code="PERMISSION_DENIED",
            message="Not allowed"
        )
        assert isinstance(fault, Exception)

        # Can be raised
        with pytest.raises(Fault) as exc_info:
            raise fault
        assert exc_info.value.domain == "PERMISSION"


# =============================================================================
# Decoder Tests
# =============================================================================

class TestInstructionDecoder:
    """Tests for InstructionDecoder."""

    def test_decode_thought(self):
        decoder = InstructionDecoder()
        actions = decoder.decode(content="Let me think about this...", tool_calls=None)

        assert len(actions) == 1
        assert actions[0].kind == "THOUGHT"
        assert "think" in actions[0].args["text"]

    def test_decode_tool_call_dict(self):
        decoder = InstructionDecoder()
        tool_calls = [{
            "function": {
                "name": "Read",
                "arguments": '{"file_path": "/test.txt"}'
            }
        }]
        actions = decoder.decode(content=None, tool_calls=tool_calls)

        assert len(actions) == 1
        assert actions[0].kind == "TOOL_CALL"
        assert actions[0].name == "Read"
        assert actions[0].args["file_path"] == "/test.txt"

    def test_decode_control_flow_sub_call(self):
        decoder = InstructionDecoder()
        tool_calls = [{
            "function": {
                "name": "call_subroutine",
                "arguments": '{"goal": "explore codebase"}'
            }
        }]
        actions = decoder.decode(content=None, tool_calls=tool_calls)

        assert len(actions) == 1
        assert actions[0].kind == "SUB_CALL"

    def test_decode_control_flow_return(self):
        decoder = InstructionDecoder()
        tool_calls = [{
            "function": {
                "name": "return_result",
                "arguments": '{"result": "task done"}'
            }
        }]
        actions = decoder.decode(content=None, tool_calls=tool_calls)

        assert len(actions) == 1
        assert actions[0].kind == "RETURN"

    def test_detect_hallucination(self):
        decoder = InstructionDecoder()

        with pytest.raises(Fault) as exc_info:
            decoder.decode(content="[Called Read with file=/test.txt]", tool_calls=None)

        assert exc_info.value.code == "ILL_INSTRUCTION"
        assert "text-based tool simulation" in exc_info.value.message

    def test_detect_hallucination_patterns(self):
        decoder = InstructionDecoder()

        patterns = [
            "[Calling some_tool]",
            "[Tool: Read]",
            "```tool\nRead\n```",
        ]

        for pattern in patterns:
            with pytest.raises(Fault):
                decoder.decode(content=pattern, tool_calls=None)


# =============================================================================
# Gate Tests
# =============================================================================

class SimpleToolExecutor:
    """Simple tool executor for testing."""

    def __init__(self):
        self.tools: Dict[str, Any] = {
            "echo": lambda args: args.get("message", ""),
            "slow": lambda args: asyncio.sleep(10),  # For timeout testing
            "fail": lambda args: self._raise_error(),
        }

    def _raise_error(self):
        raise ValueError("Tool failed intentionally")

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        if tool_name not in self.tools:
            raise Fault(
                domain="TOOL",
                code="TOOL_NOT_FOUND",
                message=f"Tool '{tool_name}' not found"
            )

        func = self.tools[tool_name]
        result = func(args)
        if asyncio.iscoroutine(result):
            return await result
        return result


class TestKernelGate:
    """Tests for KernelGate."""

    @pytest.fixture
    def gate(self):
        return KernelGate(
            pid="test-001",
            tool_executor=SimpleToolExecutor(),
            event_stream=SimpleEventStream(),
            default_timeout=1.0,
        )

    @pytest.mark.asyncio
    async def test_successful_tool_call(self, gate):
        action = ActionIR(kind="TOOL_CALL", name="echo", args={"message": "hello"})
        result = await gate.syscall_tool(action)

        assert result.status == "OK"
        assert result.output == "hello"
        assert result.fault is None
        assert "total" in result.timing_ms

    # Note: test_permission_denied was removed as permission checking was removed from Gate

    @pytest.mark.asyncio
    async def test_timeout(self, gate):
        action = ActionIR(kind="TOOL_CALL", name="slow", args={})
        result = await gate.syscall_tool(action, timeout_sec=0.1)

        assert result.status == "TIMEOUT"
        assert result.fault is not None
        assert result.fault.code == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_tool_failure(self, gate):
        # Call a tool that raises an exception
        action = ActionIR(kind="TOOL_CALL", name="fail", args={})
        result = await gate.syscall_tool(action)

        assert result.status == "ERROR"
        assert result.fault is not None
        assert result.fault.code == "TOOL_FAILURE"

    @pytest.mark.asyncio
    async def test_event_emission(self, gate):
        action = ActionIR(kind="TOOL_CALL", name="echo", args={"message": "test"})
        await gate.syscall_tool(action)

        events = gate.events.events
        assert len(events) >= 2  # At least TOOL_STARTED and TOOL_FINISHED

        event_types = [e.type for e in events]
        assert "TOOL_STARTED" in event_types
        assert "TOOL_FINISHED" in event_types

    # Note: test_post_ipc and test_request_replan were removed as IPC/Replan
    # functionality was removed from Gate (YAGNI)


# =============================================================================
# Integration Test
# =============================================================================

class TestIntegration:
    """Integration tests for the full decode -> gate flow."""

    @pytest.mark.asyncio
    async def test_decode_and_execute(self):
        # Setup
        decoder = InstructionDecoder()
        gate = KernelGate(
            pid="integration-001",
            tool_executor=SimpleToolExecutor(),
            event_stream=SimpleEventStream(),
        )

        # Decode tool call
        tool_calls = [{
            "function": {
                "name": "echo",
                "arguments": '{"message": "integration test"}'
            }
        }]
        actions = decoder.decode(content=None, tool_calls=tool_calls)

        # Execute through gate
        assert len(actions) == 1
        result = await gate.syscall_tool(actions[0])

        assert result.status == "OK"
        assert result.output == "integration test"
