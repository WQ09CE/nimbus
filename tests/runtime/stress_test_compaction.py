import asyncio
import logging
import json
import pytest
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.runtime.vcpu import VCPU
from nimbus.core.runtime.config import VCPUConfig
from nimbus.core.runtime.decoder import DefaultDecoder
from nimbus.core.protocol import ToolResult, ActionIR
from nimbus.core.memory.context import Message, PinnedContext

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stress_test_compaction")

class MockALU:
    def __init__(self):
        self.call_count = 0

    async def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], on_chunk: Any = None) -> Any:
        self.call_count += 1
        
        # We need to simulate a task that goes on for 20+ iterations.
        # Each iteration the LLM will call a tool.
        # After 25 iterations, it will return a final answer.
        
        if self.call_count > 25:
            return MockResponse(content="Task completed successfully after stress test.", tool_calls=[])
        
        # Return a tool call to keep the loop going
        tool_call = {
            "id": f"call_{self.call_count}",
            "type": "function",
            "function": {
                "name": "step_tool",
                "arguments": json.dumps({"count": self.call_count})
            }
        }
        return MockResponse(content=f"Reasoning for step {self.call_count}", tool_calls=[tool_call])

@dataclass
class MockResponse:
    content: Optional[str]
    tool_calls: List[Dict[str, Any]]

class MockGate:
    def __init__(self):
        self.pid = "test-process"
        self.events = None
        
    async def syscall_tool(self, action: ActionIR) -> ToolResult:
        return ToolResult(status="OK", output=f"Result of {action.name}")

@pytest.mark.asyncio
async def test_high_pressure_compaction():
    # 1. 实例化 MMU 并将 threshold 设为极小值
    # 注意：MMU.needs_compression 检查 estimate_tokens 和消息数量（>=10）
    # MMUConfig.compress_threshold 默认 0.9
    # 我们希望频繁触发 archive_and_reset
    mmu_config = MMUConfig(
        max_context_tokens=1000, # 极小的 token 预算
        compress_threshold=0.1,  # 只要用到 10% 就触发
        keep_recent_messages=5,
        frame_budget=800
    )
    mmu = MMU(config=mmu_config)
    
    # 设置 Anchor (Pinned Context)
    pinned = PinnedContext(
        system_rules="You are a helpful stress-test bot.",
        workspace_info="Nimbus Stress Test Workspace",
        capabilities="Can call step_tool"
    )
    mmu.set_pinned(pinned)
    
    # 2. 实例化 VCPU 并开启 Dry-Run
    vcpu_config = VCPUConfig(
        max_iterations=50,
        dry_run=True,
        compact_on_limit=True
    )
    
    alu = MockALU()
    gate = MockGate()
    decoder = DefaultDecoder()
    
    vcpu = VCPU(
        alu=alu,
        decoder=decoder,
        gate=gate,
        mmu=mmu,
        config=vcpu_config
    )
    
    # 3. 构造任务流并执行
    goal = "Run for at least 25 steps to trigger multiple compactions."
    
    logger.info("Starting VCPU stress test...")
    
    # 记录初始状态
    initial_history_len = len(mmu.current_frame.messages)
    
    # 手动添加 goal，因为我们没用 vcpu.run
    mmu.add_user_message(goal)
    
    # 执行 run
    # 因为 vcpu.run 可能因为某些 FSM 内部判断提前退出，我们手动跑 step
    final_result = None
    while not vcpu.is_done and vcpu.iteration < 50:
        step_res = await vcpu.step()
        if step_res.is_final:
            final_result = step_res.final_result
            break
    
    # 4. 验证结果
    if not vcpu.is_done:
        logger.error(f"VCPU failed to complete. Final result: {final_result}")
        if hasattr(final_result, 'output'):
            logger.error(f"Output: {final_result.output}")
    
    assert vcpu.is_done, "Task should complete successfully"
    assert alu.call_count >= 25, f"Should have run at least 25 iterations, but ran {alu.call_count}"
    assert vcpu._state.compaction_count > 0, "Should have triggered at least one compaction"
    
    # 验证 Anchor 消息在压缩前后一致
    current_pinned = mmu.get_pinned()
    assert current_pinned.system_rules == pinned.system_rules
    assert current_pinned.workspace_info == pinned.workspace_info
    
    # 验证 history 长度在压缩后确实减少了（或由于 limit 保持在较小值）
    # 在 25 步中，如果不压缩，会有 50+ 条消息（assistant + tool result）。
    # 压缩后应该远少于这个数。
    logger.info(f"Final history length: {len(mmu.current_frame.messages)}")
    assert len(mmu.current_frame.messages) < 50
    
    logger.info(f"Compactions triggered: {vcpu._state.compaction_count}")
    logger.info("Stress test compaction completed successfully.")

if __name__ == "__main__":
    asyncio.run(test_high_pressure_compaction())
