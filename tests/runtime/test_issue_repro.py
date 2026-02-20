"""
Regression test: max_consecutive_thoughts=1 triggers immediate return.

When max_consecutive_thoughts is 1, a pure-text LLM response should be
treated as a final answer (RETURN), not a thought that needs continuation.
"""

import pytest
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig


@dataclass
class MockLLMResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None


class MockEventStream:
    def __init__(self):
        self.events: List[Any] = []
        self.listeners: List[Any] = []

    def emit(self, event: Any):
        self.events.append(event)

    def add_listener(self, listener: Any):
        self.listeners.append(listener)


class MockGate:
    def __init__(self):
        self.events = MockEventStream()
        self.pid = "test-process"

    async def syscall_tool(self, action, timeout_sec=60.0):
        from nimbus.core.protocol import ToolResult
        return ToolResult(status="OK", output=f"Executed {action.name}")


class MockLLMClient:
    def __init__(self, responses: List[Any]):
        self.responses = responses
        self.call_count = 0

    async def chat(self, messages, tools=None, on_chunk=None):
        if self.call_count < len(self.responses):
            res = self.responses[self.call_count]
            self.call_count += 1
            return res
        return MockLLMResponse(content="Done")


@pytest.mark.asyncio
async def test_max_consecutive_thoughts_is_1():
    """Verify that with max_consecutive_thoughts=1, it returns immediately after one thought."""
    mmu = MMU(config=MMUConfig(), process_id="test_consecutive")
    decoder = InstructionDecoder()

    # LLM returns a plain text thought
    llm = MockLLMClient([
        MockLLMResponse(content="Hello, I am an assistant.")
    ])

    config = VCPUConfig(max_consecutive_thoughts=1)
    gate = MockGate()

    vcpu = VCPU(llm, decoder, gate, mmu, config=config)

    result = await vcpu.step()

    # Short conversational text is detected as REPLY by decoder (not THOUGHT),
    # so _handle_return is called directly — consecutive_thoughts stays 0.
    assert result.is_final is True
    assert vcpu._state.consecutive_thoughts == 0  # REPLY path, not THOUGHT path
    # Check that no continuation poke was added to MMU
    last_msg = mmu.current_frame.messages[-1]
    assert "[System]" not in last_msg.content
