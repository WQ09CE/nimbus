import asyncio
import sys
import traceback
from pathlib import Path

# Add src to path manually if needed
src_path = Path(__file__).parent.parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

try:
    print(f"Loading modules... sys.path: {sys.path}")
    from nimbus.agentos import AgentOS, AgentOSConfig
    from nimbus.core.runtime.vcpu import LLMClient
    print("Modules loaded.")
except ImportError as e:
    print(f"ImportError: {e}")
    traceback.print_exc()
    sys.exit(1)

# Mock LLM Client
class MockLLM(LLMClient):
    async def chat(self, messages, tools=None):
        return None

async def test_skill_loading():
    try:
        print("Starting test_skill_loading...")
        # Setup paths
        fixtures_dir = Path(__file__).parent.parent / "fixtures" / "skills"
        print(f"Fixtures dir: {fixtures_dir}")
        if not fixtures_dir.exists():
            print("ERROR: Fixtures dir does not exist!")
            return

        # Configure AgentOS with skill path
        config = AgentOSConfig(
            skill_paths=[fixtures_dir],
            kernel_tools=False # Disable kernel tools for faster test
        )
        print("Config created.")
        
        agent_os = AgentOS(llm_client=MockLLM(), config=config)
        print("AgentOS initialized.")
        
        # Verify tools are registered
        tools = agent_os._tools.list_tools()
        print(f"Tools available: {tools}")
        
        if "Greet" not in tools:
            print("ERROR: Greet tool not found!")
            sys.exit(1)
        
        # Execute the Greet tool
        greet_func = agent_os._tools._tools["Greet"]
        print(f"Greet func: {greet_func}")
        
        # Test normal execution
        print("Testing Greet(name='Alice')...")
        result = await greet_func(name="Alice")
        print(f"Result: {result}")
        if "Hello, Alice!" not in result:
             print("ERROR: Expected 'Hello, Alice!' in result")
             sys.exit(1)
        
        # Test boolean flag
        print("Testing Greet(name='Bob', loud=True)...")
        result_loud = await greet_func(name="Bob", loud=True)
        print(f"Result: {result_loud}")
        if "HELLO, BOB!" not in result_loud:
             print("ERROR: Expected 'HELLO, BOB!' in result")
             sys.exit(1)
             
        print("All tests passed!")

    except Exception as e:
        print(f"Exception in test: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_skill_loading())
