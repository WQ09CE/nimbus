import asyncio
import pytest
import time
from unittest.mock import MagicMock, AsyncMock
from nimbus.core.heart_modules.memory import AsyncRWLock
from nimbus.core.heart_modules.session_monitor import SessionMonitorModule
from nimbus.core.heart import Heart, HeartConfig, HeartMessage

@pytest.mark.asyncio
async def test_rwlock_timeout():
    lock = AsyncRWLock()
    await lock.acquire_write()
    
    with pytest.raises(asyncio.TimeoutError):
        await lock.acquire_read(timeout=0.1)
    
    await lock.release_write()
    await lock.acquire_read(timeout=0.1)
    await lock.release_read()

@pytest.mark.asyncio
async def test_rwlock_force_release():
    lock = AsyncRWLock()
    await lock.acquire_write()
    assert lock._writer_active is True
    
    lock.force_release()
    assert lock._writer_active is False
    assert lock._readers == 0
    
    # Should be able to acquire now
    await lock.acquire_write(timeout=0.1)
    await lock.release_write()

@pytest.mark.asyncio
async def test_session_monitor_circuit_breaker():
    monitor = SessionMonitorModule(rate_limit_count=3, rate_limit_window=10.0)
    heart = MagicMock(spec=Heart)
    heart.inbox = MagicMock()
    heart.inbox.put = AsyncMock()
    heart.outbox = asyncio.Queue()
    
    # Simulate 3 iterations with no output
    for i in range(3):
        msg = HeartMessage(id=f"t{i}", topic="session.iteration", payload={"session_id": "test_sid", "has_output": False})
        await monitor.handle_message(heart, msg)
    
    # Check if intervention signal was sent to heart.outbox
    intervention_msg = await heart.outbox.get()
    assert intervention_msg.topic == "system.intervention"
    assert intervention_msg.payload["type"] == "RATE_LIMIT_EXCEEDED"
    
    # Check if evolution proposal was sent to heart.inbox (mocked)
    heart.inbox.put.assert_called()
    # Check any call had evolution.propose
    found = False
    for call in heart.inbox.put.call_args_list:
        if call[1].get('topic') == "evolution.propose":
            found = True
            break
    assert found
