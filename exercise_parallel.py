import asyncio
import time
import os
from nimbus.agentos import AgentOS

async def run_exercise():
    # 1. 初始化 AgentOS
    # 确保 workspace 指向当前目录
    os.environ["NIMBUS_WORKSPACE_ROOT"] = os.getcwd()
    agent_os = AgentOS()
    await agent_os.start()

    print("🚀 Starting Parallel Exercise...")

    # 2. 定义并行任务
    tasks = [
        {
            "goal": "总结 src/nimbus/core/heart.py 的核心职责",
            "llm_client": "flash"
        },
        {
            "goal": "总结 src/nimbus/core/models/registry.py 的核心职责",
            "llm_client": "flash"
        },
        {
            "goal": "总结 src/nimbus/agentos.py 的并行调度逻辑",
            "llm_client": "flash"
        }
    ]

    # 3. 执行 spawn_batch
    start_time = time.perf_counter()
    
    # 我们故意设置一个短的超时来触发 scavenge 逻辑（可选），或者保持长一点看正常完成
    # 任务通常 5-10s 完成
    results = await agent_os.spawn_batch(
        tasks=tasks,
        timeout=30.0,
        strategy="wait_all"
    )
    
    end_time = time.perf_counter()
    duration = end_time - start_time

    # 4. 打印并汇总结果
    print(f"\n✅ All tasks finished in {duration:.2f} seconds.")
    
    for i, res in enumerate(results):
        print(f"\n--- Task {i+1} Result ---")
        # ToolResult 对象通常有 output 或 content 属性，根据实际结构调整
        # 在 spawn_batch 返回的是 ToolResult 列表
        print(f"Status: {res.status}")
        print(f"Output summary: {str(res.output)[:200]}...")
        if res.error:
            print(f"Error: {res.error}")

    # 5. 验证 Heart 组件 (这里通过日志或内部状态检查)
    # Heart 实例在 agent_os.heart
    if agent_os.heart:
        print(f"\n💓 Heart Status: {agent_os.heart.state}")
        # 我们可以检查是否收到过相关的 message
        # 虽然直接访问内部私有属性不太好，但这是演习
        print("Heart checked in.")
    
    await agent_os.stop()

if __name__ == "__main__":
    asyncio.run(run_exercise())
