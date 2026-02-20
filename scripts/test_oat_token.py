import httpx
import json
import asyncio

async def test_oat_token():
    token = "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"
    url = "https://api.anthropic.com/v1/messages"
    
    headers = {
        "x-api-key": token,
        "anthropic-version": "2023-06-01",
        # 核心：使用 Claude Code 专用的 beta header
        "anthropic-beta": "claude-code-2025-01-22",
        "user-agent": "ClaudeCode/2.1.42",
        "content-type": "application/json",
    }
    
    data = {
        "model": "claude-3-7-sonnet-20250219",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "Hello, if you can hear me, please reply with 'OAT_SUCCESS' and nothing else."}
        ]
    }
    
    print(f"正在尝试使用 OAT Token 访问 Anthropic API...")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=data, timeout=30.0)
            
            if response.status_code == 200:
                result = response.json()
                print("\n✅ 访问成功!")
                print(f"响应内容: {result['content'][0]['text']}")
            else:
                print(f"\n❌ 访问失败 (状态码: {response.status_code})")
                print(f"错误详情: {response.text}")
                
                if "authentication_error" in response.text:
                    print("\n提示: 可能是 anthropic-beta 的版本号需要更新，或者该 Token 绑定了特定的 IP/Session。")
        
        except Exception as e:
            print(f"\n网络请求发生错误: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_oat_token())
