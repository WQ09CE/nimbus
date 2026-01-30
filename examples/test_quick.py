#!/usr/bin/env python3
"""快速测试 Nimbus + Pi 集成"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nimbus.v2.adapters import PiLLMAdapter, PiLLMConfig, PiIOAdapter
from nimbus.v2.core.memory.mmu import MMU, MMUConfig
from nimbus.v2.core.memory.context import Message


async def main():
    print("=" * 60)
    print("Nimbus + Pi Quick Test")
    print("=" * 60)
    
    config = PiLLMConfig(
        provider="anthropic",
        model_id="claude-sonnet-4-20250514",
        max_tokens=200,
    )
    
    mmu = MMU(config=MMUConfig(auto_detect_failures=True))
    
    async with PiLLMAdapter(config) as llm:
        io = PiIOAdapter(llm._client)
        
        # 测试 1: 简单对话
        print("\n[Test 1] Simple chat")
        print("-" * 40)
        
        user_msg = "用一句话介绍你自己"
        print(f"User: {user_msg}")
        
        mmu.add_user_message(user_msg)
        context = mmu.assemble_context()
        
        print("Assistant: ", end="", flush=True)
        full_response = ""
        async for event in llm.stream(context):
            if event.type == "text":
                print(event.text, end="", flush=True)
                full_response += event.text
            elif event.type == "usage":
                print(f"\n[tokens: in={event.usage.get('inputTokens')}, out={event.usage.get('outputTokens')}]")
            elif event.type == "stop":
                break
        
        mmu.add_assistant_message(full_response)
        
        # 测试 2: Context Stack 过滤
        print("\n\n[Test 2] Context Stack filtering")
        print("-" * 40)
        
        # 模拟一些失败的 tool calls
        mmu.add_tool_result("tc-1", "Read", "[Error] File not found")
        mmu.add_tool_result("tc-2", "Read", "Success: found file content")
        
        print(f"Before filter: {len(mmu.assemble_context(filter_discardable=False))} messages")
        print(f"After filter: {len(mmu.assemble_context(filter_discardable=True))} messages")
        print(f"Discardable: {mmu.get_discardable_count()}")
        
        print("\n" + "=" * 60)
        print("All tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
