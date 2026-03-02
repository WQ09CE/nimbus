import pytest
import asyncio
from typing import Any, Dict, List, Optional
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig
from nimbus.os.gate import KernelGate, SimpleEventStream
from nimbus.core.protocol import ActionIR, ToolResult
from nimbus.core.models.manifest import ModelManifest

class MockToolExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        self.calls.append((tool_name, args))
        return "Real Success"

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

class MockLLMResponse:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

class MockFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments

class MockToolCall:
    def __init__(self, function, id="call_123"):
        self.function = function
        self.id = id

class MockLLMClient:
    async def chat(self, messages, tools, on_chunk=None):
        return MockLLMResponse(
            tool_calls=[MockToolCall(MockFunction("Read", '{"path": "test.txt"}'))]
        )

@pytest.mark.asyncio
async def test_vcpu_dry_run_mode(mmu, decoder, gate, executor):
    """验证 Dry-Run 模式下不调用真实工具。"""
    config = VCPUConfig(dry_run=True)
    llm = MockLLMClient()
    # 使用 GPT_FEATURES 避免 manifest 初始化错误
    from nimbus.core.models.manifest import GPT_FEATURES
    manifest = ModelManifest(model_id="test_model", features=GPT_FEATURES)
    vcpu = VCPU(llm, decoder, gate, mmu, config=config, manifest=manifest)
    
    step_result = await vcpu.step()
    
    # 验证没有调用真实 executor
    assert len(executor.calls) == 0
    
    # 验证返回了模拟结果
    assert len(step_result.results) == 1
    result = step_result.results[0]
    assert "[Dry-Run]" in result.output
    assert "Successfully simulated execution of Read" in result.output

@pytest.mark.asyncio
async def test_vcpu_real_run_mode(mmu, decoder, gate, executor):
    """验证非 Dry-Run 模式下正常调用真实工具。"""
    config = VCPUConfig(dry_run=False)
    llm = MockLLMClient()
    from nimbus.core.models.manifest import GPT_FEATURES
    manifest = ModelManifest(model_id="test_model", features=GPT_FEATURES)
    vcpu = VCPU(llm, decoder, gate, mmu, config=config, manifest=manifest)
    
    step_result = await vcpu.step()
    
    # 验证调用了真实 executor
    assert len(executor.calls) == 1
    assert executor.calls[0][0] == "Read"
    
    # 验证返回了真实结果
    assert len(step_result.results) == 1
    result = step_result.results[0]
    assert result.output == "Real Success"
