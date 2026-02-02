"""
PoC Test for vCPU State Recovery (Phase 1 Mandatory Amendment)

This test verifies that the vCPU and MMU state can be:
1. Serialized to a Pydantic model
2. Converted to JSON (simulating DB storage)
3. Loaded back into a FRESH vCPU instance
4. Resumed seamlessly

Constraint Check:
- No Pickle usage
- Step-level checkpointing
- Data-driven rehydration
"""

import asyncio
import json
import pytest
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from nimbus.core.runtime.vcpu import VCPU, VCPUConfig, LLMResponse
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.os.gate import KernelGate
from nimbus.core.persistence import SessionCheckpointModel

# --- Mock Components ---

class MockLLMClient:
    def __init__(self):
        self.call_count = 0
        self.history = []

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_chunk = None
    ) -> LLMResponse:
        self.call_count += 1
        self.history.append(messages)
        
        # Simple scenario:
        # 1. User says "Hi" -> Assistant thinks -> Tool call "Read"
        # 2. Tool result -> Assistant says "Done"
        
        last_msg = messages[-1]
        
        if self.call_count == 1:
            # Step 1: Respond with a tool call
            return MockResponse(
                content="I will read the file.",
                tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "Read", "arguments": "{\"path\": \"test.txt\"}"}
                }]
            )
        else:
            # Step 2: Final response
            return MockResponse(content="I read the file. Task done.")

@dataclass
class MockResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None

class MockGate(KernelGate):
    def __init__(self, executor):
        super().__init__(pid="test", tool_executor=executor)

    async def syscall_tool(self, action, timeout_sec=None):
        from nimbus.core.protocol import ToolResult
        # Return a fake result for Read
        if action.name == "Read":
            return ToolResult(status="OK", output="File content here")
        return ToolResult(status="ERROR", output="Unknown tool")

# --- Tests ---

@pytest.mark.asyncio
async def test_vcpu_state_persistence_poc():
    # 1. Setup Initial vCPU
    llm = MockLLMClient()
    mmu = MMU(MMUConfig())
    gate = MockGate(None) # type: ignore
    decoder = InstructionDecoder()
    
    vcpu = VCPU(
        alu=llm,
        decoder=decoder,
        gate=gate,
        mmu=mmu,
        config=VCPUConfig(max_iterations=10)
    )
    
    # 2. Run Step 1
    # Add initial goal
    mmu.add_user_message("Read test.txt")
    
    # Execute one step (Think -> Tool Call)
    print("\n--- Executing Step 1 ---")
    step1 = await vcpu.step()
    
    assert step1.is_final is False
    assert len(step1.actions) == 1
    assert step1.actions[0].name == "Read"
    assert vcpu._state.iteration == 1
    
    # Verify memory has the assistant message
    msgs = mmu.assemble_context()
    assert msgs[-1]["role"] == "tool" # The tool result was added in step()
    assert msgs[-2]["role"] == "assistant"
    
    # 3. Create Checkpoint
    print("\n--- Creating Checkpoint ---")
    checkpoint_model = vcpu.create_checkpoint("session_1")
    
    # Verify Model Content
    assert checkpoint_model.step_index == 1
    assert len(checkpoint_model.memory_snapshot.stack[0].messages) > 0
    
    # 4. JSON Serialization Test (Simulate DB)
    print("\n--- Serializing to JSON ---")
    json_data = checkpoint_model.model_dump_json()
    # Verify it's a string and contains data
    assert "test.txt" in json_data
    
    # 5. Restore to NEW vCPU
    print("\n--- Restoring from JSON ---")
    loaded_data = json.loads(json_data)
    loaded_model = SessionCheckpointModel(**loaded_data)
    
    new_llm = MockLLMClient()
    new_llm.call_count = 1 # Hack: Set state to match what "would" happen (or we rely on history)
                           # In real life, LLM is stateless, so call_count=0 is fine if we provide full history.
                           # But our mock logic depends on call_count. 
                           # If we resume, we expect the next call to be call_count=2 logic.
    new_llm.call_count = 1 
    
    new_mmu = MMU(MMUConfig())
    new_vcpu = VCPU(
        alu=new_llm,
        decoder=decoder,
        gate=gate,
        mmu=new_mmu,
        config=VCPUConfig(max_iterations=10)
    )
    
    # Restore
    new_vcpu.restore_from_checkpoint(loaded_model)
    
    # Verify State
    assert new_vcpu._state.iteration == 1
    assert new_mmu.current_frame.messages[-1].role == "tool"
    assert new_mmu.current_frame.messages[-1].content == "File content here"
    
    # 6. Continue Execution (Step 2)
    print("\n--- Resuming Execution (Step 2) ---")
    step2 = await new_vcpu.step()
    
    # Should produce final result now
    assert step2.is_final is True or step2.results[0].is_final
    # Actually step() returns StepResult, checking if it got the final response
    # The mock LLM returns "Task done" which is text only.
    # InstructionDecoder parses text-only as THOUGHT (implicit return if configured) or just content
    # VCPU treats empty tool calls + content as assistant message -> wait for next loop unless is_final logic handles it.
    
    # Wait, in vcpu.step():
    # if tool_calls_count == 0 and response.content:
    #   ... add_assistant_message ...
    #   actions = decoder.decode(...) -> THOUGHT/RETURN
    
    # If LLM says "Task done", Decoder usually makes it a THOUGHT or RETURN.
    # If it's THOUGHT, it's not final unless VCPU config says so?
    # Actually vcpu.step() checks action.kind == "RETURN" or is_final flag.
    
    # Let's check step2 actions
    print(f"Step 2 Actions: {step2.actions}")
    
    # If standard decoder sees text, it might just be a thought.
    # But for this test, we just want to verify we CAN continue.
    assert new_vcpu._state.iteration == 2
    
    print("\n--- PoC Verification Successful ---")

if __name__ == "__main__":
    # Allow running directly with python
    asyncio.run(test_vcpu_state_persistence_poc())
