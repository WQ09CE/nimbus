"""
E2E test for append message functionality.

Tests that user can append a message while agent is running,
and the message is properly sequenced in the conversation.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.protocol import ActionIR, ToolResult
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig


@dataclass
class MockLLMResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None


def _make_tool_call(call_id: str, name: str, arguments: str) -> Dict[str, Any]:
    """Create a tool_call dict matching the format expected by MMU (same as real LLM clients)."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class MockLLMClient:
    """Mock LLM that simulates multi-turn tool usage."""

    def __init__(self):
        self.call_count = 0
        self.messages_received: List[List[Dict[str, Any]]] = []

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_chunk: Optional[Any] = None,
    ) -> MockLLMResponse:
        self.messages_received.append(messages)
        self.call_count += 1

        # First call: return a tool call (Bash)
        if self.call_count == 1:
            return MockLLMResponse(
                tool_calls=[
                    _make_tool_call("call_1", "Bash", '{"command": "echo hello"}'),
                ]
            )

        # Second call: return another tool call
        if self.call_count == 2:
            return MockLLMResponse(
                tool_calls=[
                    _make_tool_call("call_2", "Bash", '{"command": "echo world"}'),
                ]
            )

        # Final call: return result
        return MockLLMResponse(
            tool_calls=[
                _make_tool_call("call_final", "return_result", '{"result": "done"}'),
            ]
        )


class MockEventStream:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class MockGate:
    """Mock Gate for testing."""

    def __init__(self):
        self.events = MockEventStream()
        self.pid = "test-pid"
        self.call_history = []

    async def syscall_tool(
        self, action: ActionIR, timeout_sec: float = 60.0
    ) -> ToolResult:
        self.call_history.append(action.name)
        return ToolResult(status="OK", output=f"Output of {action.name}")


class TestAppendMessage:
    """Test append message functionality."""

    @pytest.mark.asyncio
    async def test_message_order_without_append(self):
        """Test normal message order without appending."""
        llm = MockLLMClient()
        mmu = MMU(config=MMUConfig(max_context_tokens=10000))
        decoder = InstructionDecoder()
        gate = MockGate()

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=VCPUConfig(max_iterations=5),
            tools=[
                {"type": "function", "function": {"name": "Bash", "parameters": {}}},
            ],
        )

        await vcpu.execute("Run some commands")

        # Check message order in final context
        context = mmu.assemble_context()
        roles = [msg["role"] for msg in context]

        # Should be: system, user, assistant (with tool_calls), tool, assistant, tool, ...
        print("Message roles:", roles)

        # Verify basic structure
        assert roles[0] == "system"  # Pinned goal
        assert roles[1] == "user"  # Original request

    @pytest.mark.asyncio
    async def test_append_message_during_tool_execution(self):
        """Test that appending a message during tool execution maintains correct order."""
        llm = MockLLMClient()
        mmu = MMU(config=MMUConfig(max_context_tokens=10000))
        decoder = InstructionDecoder()
        gate = MockGate()

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=VCPUConfig(max_iterations=5),
            tools=[
                {"type": "function", "function": {"name": "Bash", "parameters": {}}},
            ],
        )

        # Start execution
        mmu.add_user_message("Run some commands")

        # Simulate first step (LLM returns tool call)
        step1 = await vcpu.step()

        # Now simulate user appending a message
        # This is what happens when user clicks "Append Message"
        mmu.add_user_message("Also do something else")

        # Continue execution
        step2 = await vcpu.step()

        # Check message order
        context = mmu.assemble_context()

        print("\n=== Message Order ===")
        for i, msg in enumerate(context):
            role = msg["role"]
            content = msg.get("content", "")[:50] if msg.get("content") else "(no content)"
            tool_calls = "has_tool_calls" if msg.get("tool_calls") else ""
            print(f"  {i}: [{role}] {content} {tool_calls}")

        # Verify that user message is NOT between assistant+tool_calls and tool result
        # This is the bug we're looking for
        for i, msg in enumerate(context):
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                # Next message should be tool result, not user message
                if i + 1 < len(context):
                    next_msg = context[i + 1]
                    if next_msg["role"] == "user":
                        pytest.fail(
                            f"User message at position {i+1} is between assistant (with tool_calls) "
                            f"and tool result. This breaks the OpenAI API message order requirement."
                        )

    @pytest.mark.asyncio
    async def test_message_sequence_validity(self):
        """Test that message sequence is valid for OpenAI API."""
        llm = MockLLMClient()
        mmu = MMU(config=MMUConfig(max_context_tokens=10000))

        # Simulate a typical conversation with tool calls
        mmu.add_user_message("Do something")
        mmu.add_assistant_with_tool_calls(
            content=None,
            tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "Bash", "arguments": "{}"}}]
        )
        mmu.add_tool_result("call_1", "Bash", "output")

        # Now add user message - this should be OK
        mmu.add_user_message("Thanks, now do something else")

        context = mmu.assemble_context()

        # Validate sequence
        is_valid, error = self._validate_message_sequence(context)
        assert is_valid, f"Invalid message sequence: {error}"

    @pytest.mark.asyncio
    async def test_inject_during_pending_tool_calls(self):
        """Test behavior when injecting message while tool calls are pending."""
        mmu = MMU(config=MMUConfig(max_context_tokens=10000))

        # Simulate: user sends request, assistant returns tool call
        mmu.add_user_message("Do something")
        mmu.add_assistant_with_tool_calls(
            content=None,
            tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "Bash", "arguments": "{}"}}]
        )

        # User tries to append message BEFORE tool result is added
        # This is the problematic case
        mmu.add_user_message("Wait, also do this")

        # Then tool result comes
        mmu.add_tool_result("call_1", "Bash", "output")

        context = mmu.assemble_context()

        print("\n=== Problematic Sequence ===")
        for i, msg in enumerate(context):
            role = msg["role"]
            content = str(msg.get("content", ""))[:50]
            print(f"  {i}: [{role}] {content}")

        # Validate - should now pass with the fix
        is_valid, error = self._validate_message_sequence(context)

        if not is_valid:
            pytest.fail(f"Message sequence invalid: {error}")
        else:
            print("\n✅ Sequence is valid - fix working correctly")

    def _validate_message_sequence(self, messages: List[Dict[str, Any]]) -> tuple[bool, str]:
        """
        Validate message sequence follows OpenAI API requirements.
        
        Rules:
        1. After assistant message with tool_calls, next message(s) must be tool results
        2. User message cannot appear between assistant+tool_calls and corresponding tool results
        """
        pending_tool_calls = set()

        for i, msg in enumerate(messages):
            role = msg["role"]

            if role == "assistant" and msg.get("tool_calls"):
                # Track pending tool calls
                for tc in msg["tool_calls"]:
                    tc_id = tc.get("id") or tc.get("tool_call_id")
                    if tc_id:
                        pending_tool_calls.add(tc_id)

            elif role == "tool":
                # Tool result - should have corresponding pending call
                tc_id = msg.get("tool_call_id")
                if tc_id and tc_id in pending_tool_calls:
                    pending_tool_calls.remove(tc_id)

            elif role == "user":
                # User message - should NOT appear while tool calls are pending
                if pending_tool_calls:
                    return False, f"User message at position {i} while tool calls {pending_tool_calls} are pending"

        return True, ""


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
