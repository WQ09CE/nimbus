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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
from loguru import logger

async def test_dispatch_with_instructions():
    # 1. Initialize NimFS
    workspace_root = os.path.abspath("./test_workspace_instructions")
    os.makedirs(workspace_root, exist_ok=True)
    nimfs = NimFSManager(root_path=workspace_root)
    
    # 2. Initialize AgentOS
    os_config = AgentOSConfig(
        max_tokens=200000,
        default_model="openai/gpt-4o",
    )
    agent_os = AgentOS(config=os_config, nimfs=nimfs)
    
    # 3. Initialize DispatchTool
    dispatch_tool = DispatchTool(agent_os=agent_os, workspace=Path(workspace_root))
    
    # 4. Prepare the dispatch call with custom instructions
    task = "创建一个名为 test_instruction.txt 的文件，内容随机。"
    custom_instructions = "在创建文件之前，必须先打印一段关于 'Nimbus 架构' 的简短评论作为思考过程。"
    
    print("\n--- Starting Dispatch Test with Instructions ---")
    print(f"Task: {task}")
    print(f"Instructions: {custom_instructions}")
    
    try:
        # Simulate the tool call
        result = await dispatch_tool.dispatch(
            role="Implementer",
            task=task,
            instructions=custom_instructions,
            model="gpt-4o"
        )
        
        print("\n--- Dispatch Result ---")
        print(result)
        
    except Exception as e:
        print(f"\n--- Dispatch Failed ---")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_dispatch_with_instructions())
