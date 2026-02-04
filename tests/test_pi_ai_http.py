"""
测试 pi-ai HTTP 客户端

运行：
    # 首先启动 pi-ai server
    ./scripts/start-pi-ai.sh &
    
    # 然后运行测试
    pytest tests/test_pi_ai_http.py -v
"""

import asyncio

import pytest

from nimbus.bridge.pi_ai_http import (
    Message,
    PiAiHttpClient,
)


@pytest.fixture
async def client():
    """创建 HTTP 客户端"""
    client = PiAiHttpClient()
    await client.start()
    yield client
    await client.stop()


@pytest.mark.asyncio
async def test_health_check(client):
    """测试健康检查"""
    # 如果 server 没运行，跳过测试
    is_healthy = await client.health_check()
    if not is_healthy:
        pytest.skip("pi-ai server not running")
    assert is_healthy


@pytest.mark.asyncio
async def test_list_models(client):
    """测试列出模型"""
    is_healthy = await client.health_check()
    if not is_healthy:
        pytest.skip("pi-ai server not running")

    models = await client.list_models()
    assert isinstance(models, list)
    # 应该有一些模型
    if models:
        assert "id" in models[0]
        assert "provider" in models[0]


@pytest.mark.asyncio
async def test_complete_simple(client):
    """测试简单的非流式完成"""
    is_healthy = await client.health_check()
    if not is_healthy:
        pytest.skip("pi-ai server not running")

    messages = [
        Message(role="user", content="Say 'hello' and nothing else"),
    ]

    result = await client.complete(messages)

    assert result.content is not None
    assert "hello" in result.content.lower()
    assert result.usage is not None


@pytest.mark.asyncio
async def test_complete_with_tools(client):
    """测试带工具的完成"""
    is_healthy = await client.health_check()
    if not is_healthy:
        pytest.skip("pi-ai server not running")

    messages = [
        Message(role="user", content="What is 2+2? Use the calculator tool."),
    ]

    tools = [{
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Calculate math expressions",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Math expression to evaluate"
                    }
                },
                "required": ["expression"]
            }
        }
    }]

    result = await client.complete(messages, tools=tools)

    # 应该要么返回文本，要么调用工具
    assert result.content or result.tool_calls


@pytest.mark.asyncio
async def test_stream_simple(client):
    """测试简单的流式完成"""
    is_healthy = await client.health_check()
    if not is_healthy:
        pytest.skip("pi-ai server not running")

    messages = [
        Message(role="user", content="Count from 1 to 3"),
    ]

    events = []
    async for event in client.stream(messages):
        events.append(event)

    # 应该有一些事件
    assert len(events) > 0

    # 应该有 delta 或 done 事件
    event_types = [e.type for e in events]
    assert "delta" in event_types or "done" in event_types


# 运行测试
if __name__ == "__main__":
    asyncio.run(test_health_check(PiAiHttpClient()))
