import pytest
import asyncio
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig, LLMResponse
from nimbus.os.gate import KernelGate

@dataclass
class MockFunction:
    name: str
    arguments: str

@dataclass
class MockToolCall:
    function: MockFunction
    id: str = "call_123"

class MockLLMClient:
    def __init__(self, responses: List[Any]):
        self.responses = responses
        self.call_count = 0

    async def chat(self, messages, tools=None, on_chunk=None):
        if self.call_count < len(self.responses):
            res = self.responses[self.call_count]
            self.call_count += 1
            return res
        return LLMResponse(content="Done")

@pytest.mark.asyncio
async def test_max_consecutive_thoughts_is_1():
    """Verify that with max_consecutive_thoughts=1, it returns immediately after one thought."""
    mmu = MMU(config=MMUConfig(), process_id="test_consecutive")
    decoder = InstructionDecoder()
    
    # LLM returns a plain text thought
    llm = MockLLMClient([
        LLMResponse(content="Hello, I am an assistant.")
    ])
    
    # Use default config where we just set max_consecutive_thoughts = 1
    config = VCPUConfig(max_consecutive_thoughts=1)
    
    # Mock Gate
    async def mock_syscall(name, args): return "Success"
    gate = KernelGate(mock_syscall)
    
    vcpu = VCPU(llm, decoder, gate, mmu, config=config)
    
    # First step should process the thought
    result = await vcpu.step()
    
    # Since max_consecutive_thoughts = 1, the first thought should trigger _handle_return
    # resulting in is_final=True for the step result.
    assert result.is_final is True
    assert vcpu._state.consecutive_thoughts == 1
    # Check that no continuation poke was added to MMU
    # The poke message starts with "[System]"
    last_msg = mmu.current_frame.messages[-1]
    assert "[System]" not in last_msg.content
