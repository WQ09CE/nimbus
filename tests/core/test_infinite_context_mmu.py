import asyncio
import os
import shutil
import pytest
from nimbus.core.mmu import MMU, MMUConfig, PinnedContext
from nimbus.core.loop import RuntimeLoop, LoopConfig
from nimbus.core.protocol import ToolResult, StepResult, ActionIR, Fault

class DummyVCPU:
    def __init__(self):
        self.iteration = 0
        self._interrupted = False

    def request_interruption(self):
        self._interrupted = True

    async def step(self):
        self.iteration += 1
        return StepResult(
            actions=[ActionIR(id="a", kind="REPLY", name="", args={"text": f"Iteration {self.iteration}"})],
            results=[ToolResult(status="OK", output=f"Reply {self.iteration}")],
            is_final=False,
            fault=None
        )

@pytest.mark.asyncio
async def test_infinite_context_compaction():
    # Very small context token limit to force frequent compactions
    mmu_config = MMUConfig(max_context_tokens=100, compress_threshold=0.5, keep_recent_tokens=50)
    mmu = MMU(mmu_config)
    mmu.set_pinned(PinnedContext(system_rules="I am a testing bot."))
    
    vcpu = DummyVCPU()
    loop_config = LoopConfig(max_compactions=5)
    loop = RuntimeLoop(vcpu, mmu, loop_config)

    compactions_emitted = 0
    compacted_summaries = []

    # Stream loop and manually inject some long messages to bust the budget
    async for event in loop.stream():
        if event["type"] == "text_delta":
            # Just push massive history to MMU
            mmu.add_user_message("USER BUST LIMIT " * 50)
            mmu.add_assistant_message("ASSISTANT REPLY " * 50)
            
            if vcpu.iteration >= 10:
                loop.request_interruption()
                
        elif event["type"] == "context_compacted":
            compactions_emitted += 1
            compacted_summaries.append(event.get("summary"))

    # Assert that multiple compactions happened during this "infinite" conversation!
    assert compactions_emitted > 0, "No compactions emitted under tight budget!"
    assert len(compacted_summaries) == compactions_emitted
    
    print(f"Total compactions triggered: {compactions_emitted}")
    for i, s in enumerate(compacted_summaries):
        print(f"Summary {i}:\n{s}")

if __name__ == "__main__":
    asyncio.run(test_infinite_context_compaction())


@pytest.mark.asyncio
async def test_many_productive_compactions_do_not_error():
    """A long run compacts well past the old fixed ceiling of 3 without a
    spurious CTX_OVERFLOW, as long as each compaction makes progress."""
    mmu = MMU(MMUConfig(max_context_tokens=400, compress_threshold=0.5, keep_recent_tokens=100))
    mmu.set_pinned(PinnedContext(system_rules="bot"))
    vcpu = DummyVCPU()
    loop = RuntimeLoop(vcpu, mmu, LoopConfig(max_compactions=100))

    compactions = 0
    errored = False
    async for event in loop.stream():
        if event["type"] == "text_delta":
            mmu.add_user_message("U " * 80)
            mmu.add_assistant_message("A " * 80)
            if vcpu.iteration >= 12:
                loop.request_interruption()
        elif event["type"] == "context_compacted":
            compactions += 1
        elif event["type"] == "final" and getattr(event["result"], "status", None) == "ERROR":
            errored = True

    assert compactions > 3, f"expected many compactions, got {compactions}"
    assert not errored, "productive compaction must not raise CTX_OVERFLOW"


@pytest.mark.asyncio
async def test_genuine_exhaustion_bails_after_unproductive():
    """A single message larger than the window can't be reduced; compaction must
    bail (CTX_OVERFLOW) only after consecutive no-progress attempts."""
    mmu = MMU(MMUConfig(max_context_tokens=300, compress_threshold=0.5, keep_recent_tokens=100))
    mmu.set_pinned(PinnedContext(system_rules="bot"))
    # Irreducible: one giant message far bigger than the whole window.
    mmu.add_user_message("X" * 5000)
    vcpu = DummyVCPU()
    loop = RuntimeLoop(vcpu, mmu, LoopConfig(max_compactions=100, max_unproductive_compactions=2))

    errored = False
    async for event in loop.stream():
        if event["type"] == "final" and getattr(event["result"], "status", None) == "ERROR":
            errored = True
        if vcpu.iteration >= 6:
            loop.request_interruption()

    assert errored, "irreducible giant message should trigger genuine-exhaustion error"
