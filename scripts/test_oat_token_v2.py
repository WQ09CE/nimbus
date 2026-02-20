import httpx
import json
import asyncio

async def test_oat_token_v2():
    token = "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"
    # 尝试使用 authorization header 而不是 x-api-key
    url = "https://api.anthropic.com/v1/messages"
    
    headers = {
        "authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "claude-code-2025-01-22",
        "user-agent": "ClaudeCode/2.1.42",
        "content-type": "application/json",
    }
    
    data = {
        "model": "claude-3-7-sonnet-20250219",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "Hello"}
        ]
    }
    
    print(f"尝试使用 Bearer Authorization 访问...")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data, timeout=30.0)
        print(f"状态码: {response.status_code}")
        print(f"内容: {response.text}")

if __name__ == "__main__":
    asyncio.run(test_oat_token_v2())
