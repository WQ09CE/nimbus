import asyncio
from nimbus.orchestration.dispatch_tool import DispatchTool, DispatchToolConfig
from nimbus.os.agent_os import AgentOS

async def test():
    # 注意：这里模拟 Orchestrator 环境中的调用逻辑
    # 验证 instructions 参数和 role 动态指定
    print("🚀 启动 Gemini-3.1-Pro Architect 审计任务...")
    # 这里通过模拟调用 DispatchTool 的内部逻辑来验证参数解析
    config = DispatchToolConfig()
    # 检查别名是否已更新
    print(f"✅ Gemini 别名检测: {config.model_aliases.get('gemini-3.1-pro')}")
    
    # 模拟任务下达
    task = "分析 vcpu.py 的并发鲁棒性"
    instructions = "请特别关注状态转换中的原子性。输出必须超过 1500 字以触发 NimFS 自动转储。"
    
    # 由于我们在 Server 环境，这里直接打印出拼接后的系统提示词预览（验证逻辑）
    from nimbus.prompts.manager import PromptManager
    pm = PromptManager()
    full_prompt = pm.get_system_prompt(role="architect", instructions=instructions, model_name="gemini-3.1-pro")
    print("\n--- 拼接后的 System Prompt 预览 ---")
    print(full_prompt[:500] + "...")
    print("\n--- 验证结束 ---")

if __name__ == "__main__":
    asyncio.run(test())
