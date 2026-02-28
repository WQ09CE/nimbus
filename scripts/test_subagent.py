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
    
    vcpu_config = VCPUConfig(max_iterations=10)
    agent_config = AgentOSConfig(vcpu_config=vcpu_config)
    agent_os = AgentOS(llm_client=llm, config=agent_config)
    
    workspace = Path.cwd()
    from nimbus.tools import register_default_tools
    register_default_tools(agent_os, workspace=workspace)
    
    agent_os._ensure_heart_running()
    
    print("\n--- Sending request to AgentOS ---")
    
    # Wait for completion
    stream = agent_os.run_stream(goal="Use the Explorer to list the files in the scripts folder")
    async for event in stream:
        event_type = event.get("type", "unknown")
        if event_type in ("message", "thinking", "text"):
            pass
        else:
            print(f"[{event_type}] {event}")
            
    await agent_os.shutdown()
    await llm.stop()

if __name__ == "__main__":
    asyncio.run(main())
