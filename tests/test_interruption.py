"""
Test for Graceful Interruption (Phase 3)

Verifies:
1. vCPU respects interruption request between steps
2. Session state is preserved after interruption
3. Can resume from interruption
"""

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional

import pytest

from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.protocol import ToolResult
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, LLMResponse, VCPUConfig
from nimbus.os.gate import KernelGate

# --- Mock Components ---

class SlowLLMClient:
    def __init__(self):
        self.call_count = 0

    async def chat(self, messages, tools=None, on_chunk=None) -> LLMResponse:
        self.call_count += 1
        # Simulate thinking time
        await asyncio.sleep(0.1)

        # Infinite loop behavior
        return MockResponse(
            content=f"Step {self.call_count}",
            tool_calls=[{
                "id": f"call_{self.call_count}",
                "type": "function",
                # Use varying path to avoid DoomLoop detection
                "function": {"name": "Read", "arguments": "{\"path\": \"test_" + str(self.call_count) + ".txt\"}"}
            }]
        )

@dataclass
class MockResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None

class MockGate(KernelGate):
    def __init__(self, executor):
        super().__init__(pid="test", tool_executor=executor)

    async def syscall_tool(self, action, timeout_sec=None):
        return ToolResult(status="OK", output=f"Read {action.id}")

# --- Tests ---

@pytest.mark.asyncio
async def test_vcpu_graceful_interruption():
    # 1. Setup infinite running vCPU
    llm = SlowLLMClient()
    mmu = MMU(MMUConfig())
    gate = MockGate(None) # type: ignore
    decoder = InstructionDecoder()

    vcpu = VCPU(
        alu=llm,
        decoder=decoder,
        gate=gate,
        mmu=mmu,
        config=VCPUConfig(max_iterations=100)
    )

    # 2. Start Execution in Background
    task = asyncio.create_task(vcpu.execute("Infinite task"))

    # Let it run for a bit (reach step 2 or 3)
    await asyncio.sleep(0.3)
    assert vcpu._state.is_running
    assert vcpu._state.iteration >= 1

    # 3. Request Interruption
    print("\n--- Requesting Interruption ---")
    vcpu.request_pause()
    assert vcpu._state.interruption_requested

    # 4. Wait for it to stop
    result = await task

    # 5. Verify Interruption
    assert result.status == "CANCELLED"
    assert result.fault.code == "INTERRUPTED"
    assert not vcpu._state.is_running
    assert vcpu._state.interruption_requested

    print(f"\n--- Interrupted at step {vcpu._state.iteration} ---")

    # 6. Verify State is checkpoints-ready
    # We should be able to create a checkpoint here
    ckpt = vcpu.create_checkpoint("sess_int", reason="interrupted")
    assert ckpt.step_index == vcpu._state.iteration
    assert ckpt.can_resume is True

if __name__ == "__main__":
    asyncio.run(test_vcpu_graceful_interruption())
