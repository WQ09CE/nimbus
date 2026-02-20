import asyncio
import os
import sys
import logging
from typing import Optional
from pathlib import Path

# Add src to sys.path
sys.path.append(os.path.abspath("src"))

from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.orchestration.dispatch_tool import DispatchTool, DispatchToolConfig
from nimbus.core.nimfs.manager import NimFSManager

# Configure logging to see the child agent's model selection
# DispatchTool uses loguru, but we can configure standard logging too
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
from loguru import logger

async def test_dispatch_model_override():
    # 1. Initialize NimFS
    workspace_root = os.path.abspath("./test_workspace")
    os.makedirs(workspace_root, exist_ok=True)
    nimfs = NimFSManager(root_path=workspace_root)
    
    # 2. Initialize AgentOS
    os_config = AgentOSConfig(
        max_tokens=200000,
        default_model="openai/gpt-4o", # Default model
    )
    agent_os = AgentOS(config=os_config, nimfs=nimfs)
    
    # 3. Initialize DispatchTool
    dispatch_tool = DispatchTool(agent_os=agent_os, workspace=Path(workspace_root))
    
    # 4. Prepare the dispatch call
    # We want to test if 'model' parameter is correctly passed and used.
    # The sub-agent role is 'Architect'.
    
    instruction = "分析并给出 'NimFS 存储 Memo 实体' 的方案优缺点。只需要写一个设计文档 memo_design.md 即可。"
    
    print("\n--- Starting Dispatch Test ---")
    print(f"Target Role: Architect")
    print(f"Target Model: gemini-pro (alias for google/gemini-1.5-pro)")
    
    try:
        # Simulate the tool call
        result = await dispatch_tool.dispatch(
            role="Architect",
            task=instruction,
            model="gemini-pro"
        )
        
        print("\n--- Dispatch Result ---")
        print(result)
        
    except Exception as e:
        print(f"\n--- Dispatch Failed ---")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_dispatch_model_override())
