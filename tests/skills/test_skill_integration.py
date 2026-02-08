import asyncio
import pytest
from pathlib import Path
from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.runtime.vcpu import LLMClient

# Mock LLM Client
class MockLLM(LLMClient):
    async def chat(self, messages, tools=None):
        return None

@pytest.mark.asyncio
async def test_skill_loading():
    """Test loading and execution of skills via AgentOS."""
    # Setup paths
    fixtures_dir = Path(__file__).parent.parent / "fixtures" / "skills"
    
    if not fixtures_dir.exists():
        pytest.skip("Fixtures not found")

    # Configure AgentOS with skill path
    config = AgentOSConfig(
        skill_paths=[fixtures_dir],
        kernel_tools=False # Disable kernel tools for faster test
    )
    
    agent_os = AgentOS(llm_client=MockLLM(), config=config)
    
    # Verify tools are registered
    tools = agent_os._tools.list_tools()
    assert "Greet" in tools
    
    # Execute the Greet tool via function registry
    greet_func = agent_os._tools._tools["Greet"]
    
    # Test normal execution
    result = await greet_func(name="Alice")
    assert "Hello, Alice!" in result
    
    # Test boolean flag
    result_loud = await greet_func(name="Bob", loud=True)
    assert "HELLO, BOB!" in result_loud
