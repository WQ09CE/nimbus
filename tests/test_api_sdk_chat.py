"""
API SDK Chat 端点完整测试

模拟前端 Vercel AI SDK 的聊天行为，诊断对话历史问题。
"""

import asyncio
import json
import httpx
from typing import List, Dict, Any


BASE_URL = "http://localhost:4096"
SESSION_ID = "test_session_diag_001"


async def send_chat_message(
    client: httpx.AsyncClient,
    messages: List[Dict[str, str]],
    session_id: str,
) -> Dict[str, Any]:
    """
    模拟前端发送聊天消息

    返回解析后的 SSE 事件
    """
    payload = {
        "messages": messages,
        "sessionId": session_id,
    }

    print(f"\n{'='*60}")
    print(f"发送请求:")
    print(f"  Session: {session_id}")
    print(f"  Messages ({len(messages)}):")
    for msg in messages:
        content = msg['content'][:50] + '...' if len(msg['content']) > 50 else msg['content']
        print(f"    [{msg['role']}] {content}")

    events = []
    final_text = ""

    async with client.stream(
        "POST",
        f"{BASE_URL}/api/chat",
        json=payload,
        headers={"Content-Type": "application/json"},
    ) as response:
        print(f"\n响应状态: {response.status_code}")

        async for line in response.aiter_lines():
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                    events.append(event)

                    # 提取文本
                    if event.get("type") == "text-delta":
                        final_text += event.get("delta", "")

                    # 打印关键事件
                    if event.get("type") in ["start", "finish", "tool-input-available"]:
                        print(f"  事件: {event.get('type')}")
                        if event.get("type") == "tool-input-available":
                            input_data = event.get("input", {})
                            if "context" in input_data:
                                print(f"    Context 片段: {input_data['context'][:200]}...")
                            if "message" in input_data:
                                print(f"    Message: {input_data['message'][:100]}...")

                except json.JSONDecodeError:
                    pass

    print(f"\n最终回复: {final_text}")

    return {
        "events": events,
        "text": final_text,
    }


async def run_conversation_test():
    """
    模拟完整对话流程，测试历史记录是否正确传递
    """
    print("\n" + "="*60)
    print("API SDK Chat 对话历史诊断测试")
    print("="*60)

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 对话历史累积
        messages = []

        # 第1轮：打招呼
        print("\n\n>>> 第1轮对话")
        messages.append({"role": "user", "content": "你好"})
        result1 = await send_chat_message(client, messages.copy(), SESSION_ID)
        messages.append({"role": "assistant", "content": result1["text"]})

        await asyncio.sleep(1)

        # 第2轮：自我介绍
        print("\n\n>>> 第2轮对话")
        messages.append({"role": "user", "content": "我叫小王"})
        result2 = await send_chat_message(client, messages.copy(), SESSION_ID)
        messages.append({"role": "assistant", "content": result2["text"]})

        await asyncio.sleep(1)

        # 第3轮：测试记忆
        print("\n\n>>> 第3轮对话 - 测试记忆")
        messages.append({"role": "user", "content": "你还记得我叫什么吗？"})
        result3 = await send_chat_message(client, messages.copy(), SESSION_ID)
        messages.append({"role": "assistant", "content": result3["text"]})

        await asyncio.sleep(1)

        # 第4轮：测试问题回溯
        print("\n\n>>> 第4轮对话 - 测试问题回溯")
        messages.append({"role": "user", "content": "我问你的第一个问题是什么？"})
        result4 = await send_chat_message(client, messages.copy(), SESSION_ID)

        # 验证结果
        print("\n\n" + "="*60)
        print("测试结果验证")
        print("="*60)

        # 检查第4轮回复是否正确
        expected_first_question = "你好"
        if expected_first_question in result4["text"]:
            print(f"✓ 正确识别第一个问题包含 '{expected_first_question}'")
        else:
            print(f"✗ 错误！回复: {result4['text']}")
            print(f"  期望包含: '{expected_first_question}'")

        # 打印完整对话历史
        print("\n\n完整对话历史:")
        for i, msg in enumerate(messages):
            print(f"  {i+1}. [{msg['role']}] {msg['content'][:80]}")


async def test_message_format():
    """
    测试消息格式是否正确解析
    """
    print("\n\n" + "="*60)
    print("消息格式解析测试")
    print("="*60)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 直接发送带历史的消息
        messages = [
            {"role": "user", "content": "问题A"},
            {"role": "assistant", "content": "回答A"},
            {"role": "user", "content": "问题B"},
            {"role": "assistant", "content": "回答B"},
            {"role": "user", "content": "我问的第一个问题是什么？"},
        ]

        result = await send_chat_message(client, messages, "test_format_001")

        if "问题A" in result["text"]:
            print("✓ 正确识别第一个问题")
        else:
            print(f"✗ 错误！回复: {result['text']}")


if __name__ == "__main__":
    print("开始测试...")
    asyncio.run(run_conversation_test())
    asyncio.run(test_message_format())
    print("\n测试完成")
