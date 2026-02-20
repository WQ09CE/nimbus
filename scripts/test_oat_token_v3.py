import httpx
import json
import asyncio

async def test_oat_token_v3():
    token = "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"
    url = "https://api.anthropic.com/v1/messages"
    
    # 根据一些逆向项目的最新发现，尝试不同的 beta header
    betas = [
        "claude-code-2025-01-22",
        "prompt-caching-2024-07-31",
        "message-batches-2024-09-24"
    ]
    
    headers = {
        "x-api-key": token,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": ",".join(betas),
        "user-agent": "ClaudeCode/2.1.42",
        "content-type": "application/json",
    }
    
    data = {
        "model": "claude-3-7-sonnet-20250219",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "hi"}
        ]
    }
    
    print(f"尝试组合 Beta Headers...")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data, timeout=30.0)
        print(f"状态码: {response.status_code}")
        print(f"内容: {response.text}")

if __name__ == "__main__":
    asyncio.run(test_oat_token_v3())
