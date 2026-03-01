import asyncio
import logging
from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.runtime.config import VCPUConfig
from nimbus.adapters.llm_factory import create_llm_client
from nimbus.config import get_config
import os
from pathlib import Path

# Setup basic logging
logging.basicConfig(level=logging.INFO)

async def main():
    cfg = get_config()
    model = cfg.default_model
    llm = await create_llm_client(model=model)
    
    vcpu_config = VCPUConfig(max_iterations=15)
    agent_config = AgentOSConfig(vcpu_config=vcpu_config)
    agent_os = AgentOS(llm_client=llm, config=agent_config)
    
    workspace = Path.cwd()
    from nimbus.tools import register_default_tools
    register_default_tools(agent_os, workspace=workspace)
    
    agent_os._ensure_heart_running()
    
    print("\n--- Sending request to AgentOS ---")
    
    goal = '''
You need to test the Verify Gate logic. 
Spawn a SubAgent with the `SpawnSubAgent` tool. Give it the role 'engineer' and set `expected_schema` to:
{"status": "success", "data": "the secret code"}

Instruct the SubAgent to deliberately send an invalid SendMessage payload that does NOT contain these keys on its first try. It should observe the ToolExecutionError from the Verify Gate. Then, it should fix the payload and send it again properly.

CRITICAL: Do NOT finish your turn. After spawning the subagent, you MUST use the `ReadInbox` tool repeatedly until you receive the secret code from the subagent. Only finalize your response once the correct payload arrives in your inbox.
'''

    stream = agent_os.run_stream(goal=goal)
    async for event in stream:
        event_type = event.get("type", "unknown")
        if event_type == "text":
            print(event.get("content", ""), end="")
        elif event_type in ("message", "thinking"):
            pass
        else:
            print(f"[{event_type}] {event}")
            
    await agent_os.shutdown()
    await llm.stop()

if __name__ == "__main__":
    asyncio.run(main())
