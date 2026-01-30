#!/usr/bin/env python3
"""
简单的 Nimbus + Pi 聊天示例
不使用复杂的异步输入
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nimbus.v2.adapters import PiLLMAdapter, PiLLMConfig
from nimbus.v2.core.memory.mmu import MMU, MMUConfig


async def chat_once(llm, mmu, user_input: str):
    """单次对话"""
    mmu.add_user_message(user_input)
    context = mmu.assemble_context(filter_discardable=True)
    
    full_response = ""
    async for event in llm.stream(context):
        if event.type == "text":
            print(event.text, end="", flush=True)
            full_response += event.text
        elif event.type == "usage":
            print(f"\n[tokens: {event.usage}]")
        elif event.type == "stop":
            break
        elif event.type == "error":
            print(f"\n[Error: {event.error}]")
            break
    
    if full_response:
        mmu.add_assistant_message(full_response)
    print()


async def main():
    print("=" * 50)
    print("Nimbus + Pi Chat")
    print("=" * 50)
    print("Commands: /gc, /clear, /quit")
    print()
    
    config = PiLLMConfig(
        provider="anthropic",
        model_id="claude-sonnet-4-20250514",
        max_tokens=1000,
    )
    
    mmu = MMU(config=MMUConfig(auto_detect_failures=True))
    
    async with PiLLMAdapter(config) as llm:
        while True:
            try:
                # 同步读取输入（简单可靠）
                user_input = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                break
            
            if not user_input:
                continue
            
            # 命令处理
            if user_input == "/quit" or user_input == "/exit":
                print("Bye!")
                break
            
            if user_input == "/gc":
                count = mmu.get_discardable_count()
                total = len(mmu.assemble_context(filter_discardable=False))
                print(f"Context: {total} messages, {count} discardable")
                continue
            
            if user_input == "/clear":
                mmu.clear()
                print("Context cleared")
                continue
            
            # 正常对话
            print("Assistant: ", end="", flush=True)
            await chat_once(llm, mmu, user_input)


if __name__ == "__main__":
    asyncio.run(main())
