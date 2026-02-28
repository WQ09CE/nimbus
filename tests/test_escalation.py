import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from nimbus.core.heart import Heart, HeartMessage
from nimbus.core.heart_modules.session_monitor import SessionMonitorModule
from nimbus.core.models.registry import ModelRegistry
from nimbus.agentos import AgentOS, Process
from nimbus.core.runtime.vcpu import VCPU
from nimbus.core.runtime.failure_reporter import LLMClient

@pytest.mark.asyncio
async def test_model_escalation_flow():
    # 1. Setup SessionMonitor
    monitor = SessionMonitorModule(error_threshold=3)
    heart = MagicMock(spec=Heart)
    heart.outbox = asyncio.Queue()
    
    # Simulate 2 errors for a session
    sid = "test_session_1"
    error_msg = HeartMessage(id="err1", topic="session.error", payload={"session_id": sid, "error": "Internal Error"})
    
    await monitor.handle_message(heart, error_msg)
    assert heart.outbox.empty() # First error - no escalate yet
    
    await monitor.handle_message(heart, error_msg)
    # Second error - should trigger escalate
    esc_msg = await heart.outbox.get()
    assert esc_msg.topic == "system.escalate"
    assert esc_msg.payload["session_id"] == sid

    # 2. Setup AgentOS and mock process
    mock_llm = MagicMock(spec=LLMClient)
    os = AgentOS(llm_client=mock_llm)
    # Mock a process with a low-tier model (e.g., flash)
    mock_vcpu = MagicMock(spec=VCPU)
    # Get a real manifest for flash (2026: use new model ID)
    from nimbus.core.models.manifest import get_model_manifest
    mock_vcpu.manifest = get_model_manifest("google/gemini-3-flash-preview")
    
    process = MagicMock(spec=Process)
    process.vcpu = mock_vcpu
    os._processes[sid] = process
    
    # 3. Handle the escalation message in AgentOS
    # We'll call the logic directly or simulate the queue
    await os.heart.outbox.put(esc_msg)
    
    # Start the intervention task briefly or just call one iteration
    # Since _handle_interventions is a 'while True' loop, we'll use a timeout
    try:
        await asyncio.wait_for(os._handle_interventions(), timeout=0.1)
    except asyncio.TimeoutError:
        pass
        
    # 4. Verify escalation
    new_model = process.vcpu.manifest.model_id
    # From gemini-3-flash-preview (flash) -> gemini-3.1-pro-preview (pro)
    assert new_model != "gemini-flash"
    # Should have escalated to a higher-tier model (pro or above)
    from nimbus.core.models.registry import ModelRegistry
    info = ModelRegistry.get(new_model)
    assert info is not None, f"Escalated to unknown model: {new_model}"
    assert info.rank >= 2, f"Expected rank >= 2 (pro), got {info.rank} for {new_model}"
    
    print(f"Escalation successful: {new_model} (tier={info.tier})")

if __name__ == "__main__":
    asyncio.run(test_model_escalation_flow())
