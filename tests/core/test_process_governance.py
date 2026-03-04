import asyncio
import pytest
from unittest.mock import MagicMock
from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.protocol import ToolResult, Fault
from nimbus.core.runtime.config import VCPUConfig

class MockALU:
    async def chat(self, messages=None, tools=None, mmu=None, on_chunk=None, **kwargs):
        # Simulate a slow process or infinite loop by sleeping
        await asyncio.sleep(10)
        return MagicMock(content="Done")

@pytest.mark.asyncio
async def test_process_timeout_and_post_mortem():
    # Setup AgentOS with a very small timeout for testing
    config = AgentOSConfig(
        default_timeout=2.0,
        vcpu_config=VCPUConfig(max_iterations=10)
    )
    os = AgentOS(llm_client=MockALU(), config=config)
    
    # Spawn a process that will definitely timeout due to MockALU sleep
    pid = os.spawn(goal="Run forever", role="engineer")
    process = os.get_process(pid)
    
    # Manually add some messages to MMU to simulate history for post-mortem
    process.mmu.add_user_message("Step 1")
    process.mmu.add_assistant_message("Thinking...")
    process.mmu.add_user_message("Step 2")
    process.mmu.add_assistant_message("Still thinking...")
    process.mmu.add_user_message("Step 3")
    
    # Wait for the process with a short timeout
    result = await os.wait(pid, timeout=1.0)
    
    # Verify timeout status
    assert result.status == "TIMEOUT"
    assert result.fault.code == "TIMEOUT"
    
    # Verify Post-Mortem data
    assert "post_mortem" in result.output
    post_mortem = result.output["post_mortem"]
    # Should have last 3 messages
    assert len(post_mortem) == 3
    # Note: MMU might have initialized with goal which shifts indices
    # We just check if our messages are present in the list
    contents = [m["content"] for m in post_mortem]
    # In VCPU.step, we added messages AFTER spawn, but RuntimeLoop also adds 
    # the goal message if pin_goal is True.
    assert "Step 3" in contents
    assert "Still thinking..." in contents

@pytest.mark.asyncio
async def test_process_manual_terminate():
    os = AgentOS(llm_client=MockALU())
    pid = os.spawn(goal="Run forever", role="engineer")
    
    # Start it in background
    process = os.get_process(pid)
    task = asyncio.create_task(os.wait(pid))
    
    # Give it a moment to start
    await asyncio.sleep(0.1)
    assert process.state == "RUNNING"
    
    # Terminate it
    success = os.terminate(pid, reason="test_kill")
    assert success is True
    assert process.state == "CANCELLED"
    
    # Wait for task to complete/cancel
    result = await task
    assert result.status == "TIMEOUT" or result.status == "CANCELLED"
