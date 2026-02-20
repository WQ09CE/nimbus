import pytest
import asyncio
from typing import List, Any
from dataclasses import dataclass
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig
from nimbus.os.gate import KernelGate

@dataclass
class LLMResponse:
    content: str
    tool_calls: List[Any] = None

class MockLLMClient:
    def __init__(self, responses: List[LLMResponse]):
        self.responses = responses
        self.call_count = 0

    async def chat(self, messages, tools=None, on_chunk=None):
        if self.call_count < len(self.responses):
            res = self.responses[self.call_count]
            self.call_count += 1
            return res
        return LLMResponse(content="Default response")

@pytest.fixture
def setup_vcpu():
    def _setup(responses, max_consecutive_thoughts=3):
        mmu = MMU(config=MMUConfig(), process_id="test_semantic")
        decoder = InstructionDecoder()
        llm = MockLLMClient(responses)
        config = VCPUConfig(max_consecutive_thoughts=max_consecutive_thoughts)
        
        async def mock_syscall(name, args): return "Success"
        gate = KernelGate(mock_syscall)
        
        return VCPU(llm, decoder, gate, mmu, config=config), mmu
    return _setup

@pytest.mark.asyncio
async def test_scenario_a_explicit_reply(setup_vcpu):
    """场景 A：LLM 返回带 <reply>Hello</reply> 标签的文本，验证是否被解析为 REPLY 动作，且 VCPU 正常返回。"""
    vcpu, mmu = setup_vcpu([
        LLMResponse(content="<reply>Hello, how can I help you today?</reply>")
    ])
    
    result = await vcpu.step()
    
    # 验证 VCPU 立即结束
    assert result.is_final is True
    # 验证最后一个消息是回复内容 (由 InstructionDecoder 处理)
    last_msg = mmu.current_frame.messages[-1]
    assert "Hello, how can I help you today?" in last_msg.content

@pytest.mark.asyncio
async def test_scenario_b_heuristic_reply(setup_vcpu):
    """场景 B：LLM 返回纯对话文本（如 '你好吗？'），验证启发式逻辑是否将其识别为 REPLY。"""
    vcpu, mmu = setup_vcpu([
        LLMResponse(content="你好，请问有什么我可以帮您的吗？")
    ])
    
    result = await vcpu.step()
    
    # 验证即使没有标签，纯对话也被识别为最终返回（REPLY 语义）
    assert result.is_final is True
    assert vcpu._state.consecutive_thoughts == 0 # REPLY 不计入 thought 计数或在返回前重置

@pytest.mark.asyncio
async def test_scenario_c_thought_with_plan(setup_vcpu):
    """场景 C：LLM 返回包含后续计划的文本（如 'Next I will read file'），验证是否仍被识别为 THOUGHT 并触发后续逻辑。"""
    # 这里设置 max_consecutive_thoughts 为 2，确保不会因为计数 1 就退出
    vcpu, mmu = setup_vcpu([
        LLMResponse(content="I need to check the project structure. Next I will list the files."),
        LLMResponse(content="<reply>Done</reply>")
    ], max_consecutive_thoughts=2)
    
    # 执行第一步
    result = await vcpu.step()
    
    # 验证它被视为 THOUGHT 而不是 REPLY，因此没有结束 (is_final=False)
    # 注意：如果启用了自动 Poke，step 可能不会直接返回 final=True 除非遇到终止条件
    # 在 VCPU.step() 逻辑中，如果解析为 THOUGHT 且没达到阈值，它会继续（如果 LLMClient 有后续）
    # 或者返回当前状态。
    
    # 验证不是 final (因为它有计划，被识别为 THOUGHT)
    assert result.is_final is False
    assert vcpu._state.consecutive_thoughts == 1
    
    # 执行第二步
    result2 = await vcpu.step()
    assert result2.is_final is True

