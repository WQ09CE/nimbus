import pytest
import asyncio
from unittest.mock import patch, MagicMock

from nimbus.core.heart import Heart, HeartConfig, MessagePriority
from nimbus.core.heart_modules.session_monitor import SessionMonitorModule
from nimbus.core.heart_modules.evolution import EvolutionProposal
from nimbus.core.nimfs.models import MemoryCategory

@pytest.mark.asyncio
async def test_session_monitor():
    config = HeartConfig(
        workspace=".",
        project_id="test_proj",
        tick_interval=0.1
    )
    
    with patch("nimbus.core.nimfs.manager.NimFSManager.write_memory") as mock_write_memory:
        heart = Heart(config)
        monitor = SessionMonitorModule(error_threshold=2)
        heart.add_module(monitor)
        
        original_put = heart.inbox.put
        put_calls = []
        
        async def mock_put(topic, payload=None, priority=MessagePriority.NORMAL):
            put_calls.append((topic, payload))
            await original_put(topic, payload, priority)
            
        heart.inbox.put = mock_put
        
        async def simulate_errors():
            await asyncio.sleep(0.1)
            # 1st error
            await heart.inbox.put("session.error", {"session_id": "s1", "error": "test err 1"})
            await asyncio.sleep(0.2)
            assert len(monitor.session_errors["s1"]) == 1
            
            # 2nd error -> should trigger alert and clear it
            await heart.inbox.put("session.timeout", {"session_id": "s1", "error": "timeout 1"})
            await asyncio.sleep(0.2)
            assert len(monitor.session_errors["s1"]) == 0
            
            heart.stop()

        await asyncio.gather(heart.start(), simulate_errors())
        
        # Verify evolution.propose was sent
        propose_calls = [c for c in put_calls if c[0] == "evolution.propose"]
        assert len(propose_calls) == 1
        proposal = propose_calls[0][1]
        assert isinstance(proposal, EvolutionProposal)
        assert proposal.data["session_id"] == "s1"
        assert proposal.data["error_count"] == 2
        assert "test err 1" in proposal.data["logs"]
        
        # Verify NimFS memory was written
        assert mock_write_memory.call_count == 1
        kwargs = mock_write_memory.call_args.kwargs
        assert kwargs["category"] == MemoryCategory.CASES
        assert kwargs["title"] == "Session Failure: s1"
        assert "failure" in kwargs["tags"]
        assert "auto-generated" in kwargs["tags"]
        assert "test err 1" in kwargs["content"]
        assert "timeout 1" in kwargs["content"]

