#!/usr/bin/env python3
"""
Nimbus + Pi 集成示例

展示如何使用 pi-ai 和 pi-tui 驱动 Nimbus Agent OS

架构:
    ┌─────────────────────────────────────────────┐
    │  Nimbus (Python)                             │
    │  - vCPU: Agent 循环控制                       │
    │  - MMU: Context Stack 管理                   │
    │  - Session: 持久化                           │
    └─────────────────┬───────────────────────────┘
                      │ JSON-RPC
                      ▼
    ┌─────────────────────────────────────────────┐
    │  Pi Bridge (Node.js)                         │
    │  - pi-ai: LLM 调用                           │
    │  - pi-tui: 终端渲染                          │
    └─────────────────────────────────────────────┘

Usage:
    # 需要先编译 pi-bridge
    cd nimbus/bridge && npm install && npx tsc
    
    # 运行示例
    python examples/nimbus_with_pi.py
"""

import asyncio
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nimbus.v2.adapters import PiLLMAdapter, PiLLMConfig, PiIOAdapter
from nimbus.v2.core.memory.mmu import MMU, MMUConfig
from nimbus.v2.core.session import SessionManager
from nimbus.v2.core.memory.context import Message


async def main():
    """主函数"""
    print("=" * 60)
    print("Nimbus + Pi Integration Demo")
    print("=" * 60)
    print()
    
    # 配置
    llm_config = PiLLMConfig(
        provider="anthropic",
        model_id="claude-sonnet-4-20250514",
        max_tokens=4096,
    )
    
    # 初始化 MMU（Context Stack）
    mmu = MMU(config=MMUConfig(
        auto_detect_failures=True,
        auto_extract_on_pop=True,
    ))
    
    # 初始化 Session
    session_mgr = SessionManager(session_dir=Path("~/.nimbus/sessions").expanduser())
    session_mgr.new_session()
    
    print(f"Session: {session_mgr.get_session_file()}")
    print()
    
    # 启动 Pi 适配器
    async with PiLLMAdapter(llm_config) as llm:
        io = PiIOAdapter(llm._client)
        
        # 显示可用模型
        models = await llm.get_models()
        print("Available models:")
        for m in models:
            print(f"  - {m['provider']}/{m['id']}")
        print()
        
        # Agent 循环
        while True:
            # 获取用户输入
            user_input = await io.input("\n> ")
            
            if user_input.lower() in ("exit", "quit", "q"):
                break
            
            if user_input.lower() == "/gc":
                # 显示 Context Stack 状态
                stats = mmu.get_stats()
                await io.notify(f"Context Stats: {stats}", "info")
                continue
            
            if user_input.lower() == "/clear":
                mmu.clear()
                await io.notify("Context cleared", "info")
                continue
            
            # 添加到 MMU
            mmu.add_user_message(user_input)
            
            # 持久化
            session_mgr.append_message(Message(role="user", content=user_input))
            
            # 组装上下文（过滤失败的 tool calls）
            context = mmu.assemble_context(filter_discardable=True)
            
            # 调用 LLM
            await io.print_streaming("\nAssistant: ")
            
            full_response = ""
            async for event in llm.stream(context):
                if event.type == "text":
                    await io.print_streaming(event.text)
                    full_response += event.text
                elif event.type == "stop":
                    break
            
            await io.print_streaming("\n")
            
            # 添加响应到 MMU
            mmu.add_assistant_message(full_response)
            
            # 持久化
            session_mgr.append_message(Message(role="assistant", content=full_response))
    
    print("\nGoodbye!")


async def demo_context_stack():
    """演示 Context Stack 功能"""
    print("=" * 60)
    print("Context Stack Demo")
    print("=" * 60)
    
    mmu = MMU(config=MMUConfig(
        auto_detect_failures=True,
        auto_extract_on_pop=True,
    ))
    
    # 模拟一系列 tool calls
    print("\n1. 模拟失败的文件搜索...")
    mmu.add_user_message("Find the auth module")
    
    # 失败的尝试
    mmu.add_tool_result("tc-1", "Read", "[Error] File not found: /src/auth.py")
    mmu.add_tool_result("tc-2", "Read", "[Error] File not found: /app/auth.py")
    
    # 成功的尝试
    mmu.add_tool_result("tc-3", "Read", "Found: /lib/auth/main.py\n# Auth module\ndef authenticate()...")
    mmu.add_assistant_message("Found the auth module at /lib/auth/main.py")
    
    # 查看统计
    print(f"\n2. 统计: {mmu.get_discardable_count()} messages will be filtered")
    
    # 组装上下文（过滤失败的）
    context_filtered = mmu.assemble_context(filter_discardable=True)
    context_full = mmu.assemble_context(filter_discardable=False)
    
    print(f"\n3. Full context: {len(context_full)} messages")
    print(f"   Filtered context: {len(context_filtered)} messages")
    
    # 显示过滤效果
    print("\n4. Filtered context contains:")
    for msg in context_filtered:
        # msg 可能是 Message 对象或 dict
        if hasattr(msg, 'role'):
            role = msg.role
            content = msg.content
        else:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
        
        content_str = content[:50] if isinstance(content, str) else str(content)[:50]
        print(f"   [{role}] {content_str}...")


if __name__ == "__main__":
    # 检查参数
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        asyncio.run(demo_context_stack())
    else:
        asyncio.run(main())
