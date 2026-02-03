
import asyncio
import httpx
import json
import uuid

BASE_URL = "http://localhost:4096/api/v1"

async def test_session_persistence():
    print("🚀 Starting Session Persistence E2E Test...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Create new session
        print("\n1️⃣ Creating new session...")
        resp = await client.post(f"{BASE_URL}/sessions", json={
            "name": "Persistence Test",
            "memory_type": "tiered",
            "planner_type": "dag"
        })
        assert resp.status_code == 201
        session_id = resp.json()["id"]
        print(f"✅ Session created: {session_id}")
        
        # 2. Send message: "你好" (Simulate 'load session' + 'send message')
        print("\n2️⃣ Sending message '你好'...")
        # Use streaming endpoint but we'll just consume it
        msg_payload = {"content": "你好"}
        
        assistant_reply = ""
        async with client.stream("POST", f"{BASE_URL}/sessions/{session_id}/chat", json=msg_payload) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        data = json.loads(data_str)
                        if "content" in data:
                            assistant_reply += data["content"]
                    except:
                        pass
        
        print(f"✅ Received reply: {assistant_reply[:50]}...")
        
        # 3. Simulate "Refresh Page" -> Load Session Messages
        print("\n3️⃣ Simulating Page Refresh (Loading History)...")
        # Add a small delay to ensure DB write completes (though it should be awaited in backend)
        await asyncio.sleep(1)
        
        resp = await client.get(f"{BASE_URL}/sessions/{session_id}/messages")
        assert resp.status_code == 200
        messages = resp.json()["items"]
        
        print(f"📊 Loaded {len(messages)} messages from history")
        
        # 4. Verify content
        found_user = False
        found_assistant = False
        
        for m in messages:
            print(f"   - [{m['role']}] {str(m.get('content'))[:30]}...")
            if m['role'] == 'user' and '你好' in str(m.get('content')):
                found_user = True
            if m['role'] == 'assistant' and len(str(m.get('content'))) > 0:
                found_assistant = True
        
        if found_user and found_assistant:
            print("\n🎉 SUCCESS: Both User message and Assistant reply persisted!")
        else:
            print("\n❌ FAILED: Messages missing from history!")
            if not found_user: print("   - Missing User message")
            if not found_assistant: print("   - Missing Assistant message")

if __name__ == "__main__":
    asyncio.run(test_session_persistence())
