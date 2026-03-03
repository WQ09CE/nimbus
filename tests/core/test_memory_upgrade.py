import pytest
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from nimbus.core.session import SessionManager
from nimbus.core.heart_modules.memory import MemoryManagerModule
from nimbus.core.memory.episodic_store import EpisodicStore
from nimbus.core.memory.profile_store import ProfileStore
from nimbus.core.memory.context import Message
from nimbus.core.heart import Heart, HeartConfig

# Dummy LLM client for mocking the reflection GC
class DummyLLMClient:
    class chat:
        class completions:
            @staticmethod
            def create(*args, **kwargs):
                class Msg:
                    content = '{"entities": [{"key": "user_preference", "value": "likes Python", "entity_type": "preference"}]}'
                class Ch:
                    message = Msg()
                class Resp:
                    choices = [Ch()]
                return Resp()

@pytest.mark.asyncio
async def test_memory_upgrade_reflection_loop():
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Ensure episodic store looks in the right directory.
        # SessionManager(session_dir=temp_path) creates things directly in temp_path/YYYY-MM-DD
        # EpisodicStore by default looks in workspace/.nimbus/sessions. Let's override it or create the right structure.
        
        # 1. Setup SessionManager to generate Episodic Memory
        nimbus_dir = temp_path / ".nimbus" / "sessions"
        nimbus_dir.mkdir(parents=True, exist_ok=True)
        session_mgr = SessionManager(session_dir=nimbus_dir)
        session_id = "test-session-123"
        session_mgr.new_session()
        
        # 2. Simulate 20 high-frequency un-structured messages
        for i in range(20):
            session_mgr.append_message(Message(role="user", content=f"Hello, this is noisy message {i} about my Python love."))
            session_mgr.append_message(Message(role="assistant", content=f"Understood noise {i}."))
            
        # Verify Episodic data is persisted
        episodic = EpisodicStore(temp_path)
        res = episodic.search("love", limit=50)
        assert len(res) == 20, "Episodic store should reflect all user messages."
        
        # 3. Setup Heart with MemoryManagerModule
        config = HeartConfig(workspace=str(temp_path), project_id="test", tick_interval=0.1)
        heart = Heart(config)
        
        # Override MM to run GC very quickly
        mm_module = MemoryManagerModule(llm_client=DummyLLMClient(), gc_interval_ticks=1, lock_timeout=2.0)
        heart.add_module(mm_module)
        
        # 4. Trigger one GC tick
        await mm_module.run_cron(heart)
        
        # 5. Verify Semantic Profile was produced by the background GC reflection
        profile = ProfileStore(temp_path)
        summary = profile.get_all_summary()
        
        assert "user_preference" in summary
        assert "likes Python" in summary
        
        # Verify episodic data is NOT lost
        res_after = episodic.search("love", limit=50)
        assert len(res_after) == 20, "Episodic logs should remain intact after reflection."
