import pytest
import asyncio
import os
import time
from nimbus.core.heart import Heart, HeartConfig, MessagePriority, HeartState
from nimbus.core.heart_modules.health import HealthMonitorModule
from nimbus.core.heart_modules.memory import MemoryManagerModule

@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "heart_workspace"
    ws.mkdir()
    return str(ws)

@pytest.mark.asyncio
async def test_heart_lifecycle(workspace):
    config = HeartConfig(
        workspace=workspace,
        project_id="test-project",
        tick_interval=0.1
    )
    heart = Heart(config)
    
    assert heart.state == HeartState.STARTING
    
    async def stop_later():
        await asyncio.sleep(0.3)
        heart.stop()
        
    await asyncio.gather(
        heart.start(),
        stop_later()
    )
    
    assert heart.state == HeartState.STOPPED

@pytest.mark.asyncio
async def test_heart_inbox_and_modules(workspace):
    config = HeartConfig(
        workspace=workspace,
        project_id="test-project",
        tick_interval=0.1
    )
    heart = Heart(config)
    
    health_mod = HealthMonitorModule(check_interval_ticks=1)
    memory_mod = MemoryManagerModule(gc_interval_ticks=10)
    
    heart.add_module(health_mod)
    heart.add_module(memory_mod)
    
    async def brain_sim():
        # Wait for heart to start processing
        await asyncio.sleep(0.1)
        
        # Send a probe
        await heart.inbox.put("health.probe", {})
        
        # Mark memory dirty
        await heart.inbox.put("memory.mark_dirty", {"id": "123"})
        
        # Wait a bit for processing
        await asyncio.sleep(0.2)
        
        heart.stop()

    await asyncio.gather(
        heart.start(),
        brain_sim()
    )
    
    # Check if memory was marked dirty and then cleaned (or at least dirty bit system works)
    # The memory module clears the dirty bit when it processes it
    # But wait, memory_mod looks for keys starting with "mem:"
    assert heart.is_dirty("mem:123") == False # Should have been processed and cleared

@pytest.mark.asyncio
async def test_heart_inbox_priority(workspace):
    config = HeartConfig(
        workspace=workspace,
        project_id="test-project",
        tick_interval=0.1
    )
    heart = Heart(config)
    
    await heart.inbox.put("low_priority", {}, MessagePriority.LOW)
    await heart.inbox.put("urgent_priority", {}, MessagePriority.URGENT)
    await heart.inbox.put("normal_priority", {}, MessagePriority.NORMAL)
    
    # Manually get from inbox without starting the heart loop
    msg1 = await heart.inbox.get()
    msg2 = await heart.inbox.get()
    msg3 = await heart.inbox.get()
    
    assert msg1.topic == "urgent_priority"
    assert msg2.topic == "normal_priority"
    assert msg3.topic == "low_priority"

@pytest.mark.asyncio
async def test_memory_concurrency_control(workspace):
    config = HeartConfig(
        workspace=workspace,
        project_id="test-project",
        tick_interval=0.1
    )
    heart = Heart(config)
    memory_mod = MemoryManagerModule(gc_interval_ticks=10)
    heart.add_module(memory_mod)
    
    async def brain_sim():
        await asyncio.sleep(0.1)
        # Brain state running - GC and compaction should be deferred
        await heart.inbox.put("brain.state_change", {"state": "running"})
        await asyncio.sleep(0.1)
        
        await heart.inbox.put("memory.mark_dirty", {"id": "123"})
        await asyncio.sleep(0.2)
        
        # Still running, memory should remain dirty
        assert heart.is_dirty("mem:123") == True
        
        # Brain state idle - compaction should process
        await heart.inbox.put("brain.state_change", {"state": "idle"})
        await asyncio.sleep(0.2)
        
        # Memory should be cleaned
        assert heart.is_dirty("mem:123") == False
        
        heart.stop()

    await asyncio.gather(
        heart.start(),
        brain_sim()
    )
