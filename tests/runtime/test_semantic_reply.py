import pytest
from typing import List, Any, Optional
from dataclasses import dataclass
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig


@dataclass
class LLMResponse:
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
        gate = MockGate()
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
    assert vcpu._state.consecutive_thoughts == 0  # REPLY 不计入 thought 计数


@pytest.mark.asyncio
async def test_scenario_c_thought_with_plan(setup_vcpu):
    """场景 C：LLM 返回包含后续计划的文本（如 'Next I will read file'），验证是否仍被识别为 THOUGHT 并触发后续逻辑。"""
    vcpu, mmu = setup_vcpu([
        LLMResponse(content="I need to check the project structure. Next I will list the files."),
        LLMResponse(content="<reply>Done</reply>")
    ], max_consecutive_thoughts=2)

    # 执行第一步
    result = await vcpu.step()

    # 验证不是 final (因为它有计划，被识别为 THOUGHT)
    assert result.is_final is False
    assert vcpu._state.consecutive_thoughts == 1

    # 执行第二步
    result2 = await vcpu.step()
    assert result2.is_final is True
