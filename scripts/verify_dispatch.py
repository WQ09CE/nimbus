import asyncio
from nimbus.orchestration.dispatch_tool import DispatchTool, DispatchToolConfig
from nimbus.os.agentos import AgentOS

async def main():
    # 模拟 Orchestrator 的执行环境
    os = AgentOS()
    config = DispatchToolConfig(model_aliases={"gemini-3.1-pro": "google/gemini-1.5-pro-002"})
    dispatch = DispatchTool(os, config)
    
    print("🚀 发起子 Agent 分派测试...")
    result = await dispatch.dispatch(
        task="审计 src/nimbus/core/vcpu.py 的异常回滚逻辑",
        role="architect",
        model="gemini-3.1-pro",
        instructions="请以严苛的视角分析状态一致性，必须产出 Artifact 句柄。"
    )
    print(f"\n✅ 任务返回结果:\n{result}")

if __name__ == "__main__":
    asyncio.run(main())
