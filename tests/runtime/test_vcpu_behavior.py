"""
Tests for vCPU Specific Behaviors (Refactoring Baseline).

These tests target the specific logic that is being refactored:
1. Mixed Response Splitting (GPT-5.3/Gemini)
2. Hallucination Firewall (Gemini)
3. Tool Name Repair
4. Ephemeral Message Handling (Future)
"""

import pytest
import asyncio
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field

# We need to mock the imports or rely on the environment having them.
# Assuming standard pytest run in the environment.

from nimbus.core.memory.context import PinnedContext
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig
from nimbus.os.gate import KernelGate, SimpleEventStream
from nimbus.core.protocol import ActionIR, ToolResult, Fault
from nimbus.core.models.manifest import ModelManifest, GEMINI_FEATURES, ModelFeatures

# =============================================================================
# Mocks
# =============================================================================

@dataclass
class MockFunction:
    name: str
    arguments: str

@dataclass
class MockToolCall:
    function: MockFunction
    id: str = "call_123"

@dataclass
class MockLLMResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None

class MockLLMClient:
    def __init__(self, responses: List[MockLLMResponse]):
        self.responses = responses
        self.call_count = 0

    async def chat(
        self,
        messages: List[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        mmu: Optional[Any] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
        **kwargs
    ) -> MockLLMResponse:
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            
            # Simulate streaming
            if on_chunk and response.content:
                # Send first part
                on_chunk("Okay, ")
                # Send potentially harmful part
                if "<function_call>" in response.content:
                     on_chunk("<function_call>")
                     on_chunk(" args...>")
                # Send rest
                on_chunk(" let's see.")
                
            return response
        return MockLLMResponse(content="Done")

class MockToolExecutor:
    def __init__(self):
        self.calls = []
        self.results = {}

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        self.calls.append((tool_name, args))
        return self.results.get(tool_name, "Success")

# =============================================================================
# Tests
# =============================================================================

@pytest.fixture
def mmu():
    return MMU(config=MMUConfig(), process_id="test")

@pytest.fixture
def decoder():
    return InstructionDecoder()

@pytest.fixture
def executor():
    return MockToolExecutor()

@pytest.fixture
def gate(executor):
    return KernelGate("test", executor, SimpleEventStream())

@pytest.mark.asyncio
@pytest.mark.skip(reason="Response splitting moved to Adapter layer")
async def test_mixed_response_splitting(mmu, decoder, gate):
    """
    Test that a response with BOTH content and tool_calls is split into:
    1. THOUGHT action (executed immediately)
    2. TOOL_CALL action
    """
    # Simulate GPT-5.3 "Talking while working"
    llm = MockLLMClient([
        MockLLMResponse(
            content="I will read the file now.",
            tool_calls=[MockToolCall(MockFunction("Read", '{"path": "file.txt"}'))]
        )
    ])
    
    vcpu = VCPU(llm, decoder, gate, mmu)
    
    # Execute one step
    step_result = await vcpu.step()
    
    # We expect 2 results: THOUGHT and TOOL_CALL
    assert len(step_result.results) == 2
    
    # First result should be the thought
    res1 = step_result.results[0]
    # Note: ToolResult doesn't have action_kind, we need to check the 'output' or infer from order
    # The VCPU implementation executes the thought first and appends result
    
    # We can inspect step_result.actions but VCPU logic for splitting is special:
    # It creates a 'thought_action', executes it, appends result.
    # Then it decodes 'tool_calls' into actions, executes them, appends results.
    
    # Check results
    assert res1.output == "I will read the file now."
    # We can check if it was a THOUGHT action by checking the memory or behavior
    
    res2 = step_result.results[1]
    # This should be the tool execution result
    assert res2.status == "OK"  # Mock executor returns Success

@pytest.mark.asyncio
@pytest.mark.skip(reason="Hallucination firewall moved to Adapter layer")
async def test_hallucination_firewall_stream(mmu, decoder, gate):
    """Test that hallucinated tags in stream are suppressed."""
    
    # Mock emitted events
    events = []
    def capture_event(event_type, data):
        events.append((event_type, data))
        
    # Setup response with hallucination
    llm = MockLLMClient([
        MockLLMResponse(content="Okay, <function_call> let's see.")
    ])
    
    # Enable firewall by using Gemini manifest
    manifest = ModelManifest("gemini-test", GEMINI_FEATURES)
    vcpu = VCPU(llm, decoder, gate, mmu, manifest=manifest)
    vcpu._emit_event = capture_event # Monkey patch
    
    await vcpu.step()
    
    # Extract emitted text from THINKING events
    emitted_text = "".join([e[1]['content'] for e in events if e[0] == 'THINKING'])
    
    # "<function_call>" should trigger suppression
    assert "<function_call>" not in emitted_text
    assert "Okay, " in emitted_text
    # " let's see." comes after, so it should be suppressed too (firewall locks down)
    assert "let's see" not in emitted_text

@pytest.mark.asyncio
@pytest.mark.skip(reason="Tool name repair logic refactored")
async def test_tool_name_repair(mmu, decoder, gate, executor):
    """Test 'read' -> 'Read' correction."""
    llm = MockLLMClient([
        MockLLMResponse(
            tool_calls=[MockToolCall(MockFunction("read", '{"path": "file.txt"}'))]
        )
    ])
    
    vcpu = VCPU(llm, decoder, gate, mmu)
    await vcpu.step()
    
    # Executor should receive 'Read', not 'read'
    assert len(executor.calls) == 1
    assert executor.calls[0][0] == "Read"
