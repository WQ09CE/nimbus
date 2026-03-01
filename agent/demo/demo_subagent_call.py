import asyncio
import os
from pathlib import Path
from nimbus.agentos import AgentOS, OSConfig
from nimbus.orchestration.specialist_tools import ExploreTool
from nimbus.adapters.llm_factory import create_llm_client

async def main():
    # 1. 初始化 AgentOS
    # 假设使用默认模型，或者可以从环境变量获取
    model_name = os.getenv("NIMBUS_MODEL", "gpt-4o")
    llm_client = await create_llm_client(model_name)
    
    # 获取项目根目录 (假设脚本在 agent/demo/ 下运行)
    workspace_root = Path(__file__).parent.parent.parent.absolute()
    os_config = OSConfig(workspace_root=str(workspace_root))
    
    agent_os = AgentOS(llm_client=llm_client, config=os_config)
    
    # 2. 定义一个简单的 Explorer 任务
    task = "请搜索项目根目录下的 README.md 文件，并简要概括其内容。"
    
    print(f"--- 启动 Explorer 子代理 ---")
    print(f"任务: {task}\n")
    
    # 3. 使用 ExploreTool (它是 SpecialistTool 的子类)
    # ExploreTool 内部会调用 agent_os.spawn 并等待结果
    explorer = ExploreTool(agent_os=agent_os, workspace=workspace_root)
    
    # 4. 执行并打印结果
    # execute 方法封装了 spawn, wait 以及结果格式化（包括 NimFS 结果读取）
    try:
        result = await explorer.execute(task=task)
        print("\n--- 子代理执行结果 ---")
        print(result)
    except Exception as e:
        print(f"执行过程中出错: {e}")

if __name__ == "__main__":
    asyncio.run(main())
