import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from nimbus.core.heart_modules.memory_consolidator import MemoryConsolidatorModule
from nimbus.core.heart import Heart, HeartMessage
from nimbus.core.nimfs.models import MemoryCategory, MemoryScope

@pytest.fixture
def mock_heart():
    heart = MagicMock(spec=Heart)
    heart.nimfs = MagicMock()
    return heart

@pytest.fixture
def mock_llm():
    return AsyncMock()

@pytest.mark.asyncio
async def test_memory_consolidator_deduplication(mock_heart, mock_llm):
    # Setup mock NimFS to return existing memory
    existing_memory = MagicMock()
    existing_memory.category = MemoryCategory.PATTERNS
    existing_memory.memory_id = "patterns-1234"
    existing_memory.title = "React 18 Architecture"
    existing_memory.abstract = "We use React 18."
    mock_heart.nimfs.search_memory.return_value = [existing_memory]

    # Setup mock LLM to return an "UPDATE" action
    mock_responses = [
        {
            "action": "UPDATE",
            "target_id": "patterns-1234",
            "title": "React 18 Architecture Refined",
            "problem_statement": "Need React 18 Server Components.",
            "solution_decision": "Use RSC.",
            "context_rationale": "It is faster.",
            "tags": ["react"],
            "category": "PATTERNS"
        }
    ]
    
    class MockResponse:
        @property
        def content(self):
            return json.dumps(mock_responses)
            
    mock_llm.chat.return_value = MockResponse()

    # Initialize Consolidator
    consolidator = MemoryConsolidatorModule(llm_client=mock_llm)
    
    # Fire the event
    msg = HeartMessage(
        id="msg-1",
        topic="session.completed", 
        payload={"session_id": "test-session", "summary": "We implemented React 18 RSC today."}
    )
    await consolidator.handle_message(mock_heart, msg)

    # Verify existing memory search was called
    mock_heart.nimfs.search_memory.assert_called_once_with(query="*", top_k=20)
    
    # Verify the LLM prompt included the existing memory
    calls = mock_llm.chat.call_args_list
    mmu_arg = calls[0].kwargs["mmu"]
    prompt_str = "\n".join([str(msg.content) for msg in mmu_arg._stack[0].messages])
    assert "patterns-1234" in prompt_str
    assert "React 18 Architecture" in prompt_str

    # Verify we overwrote the memory (memory_id was passed to write_memory)
    mock_heart.nimfs.write_memory.assert_called_once()
    kwargs = mock_heart.nimfs.write_memory.call_args.kwargs
    assert kwargs["memory_id"] == "patterns-1234"
    assert kwargs["category"] == MemoryCategory.PATTERNS
    assert kwargs["title"] == "React 18 Architecture Refined"
