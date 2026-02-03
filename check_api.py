
import asyncio
import httpx
import sys

BASE_URL = "http://localhost:4096/api/v1"

async def check_messages():
    async with httpx.AsyncClient() as client:
        # 1. List sessions to get the latest ID
        resp = await client.get(f"{BASE_URL}/sessions?limit=1")
        if resp.status_code != 200:
            print(f"Error listing sessions: {resp.text}")
            return
            
        data = resp.json()
        if not data["items"]:
            print("No sessions found.")
            return
            
        session_id = data["items"][0]["id"]
        print(f"Checking session: {session_id}")
        
        # 2. Get messages
        resp = await client.get(f"{BASE_URL}/sessions/{session_id}/messages")
        if resp.status_code != 200:
            print(f"Error getting messages: {resp.text}")
            return
            
        messages = resp.json()["items"]
        print(f"Total messages: {len(messages)}")
        
        for i, m in enumerate(messages):
            print(f"[{i}] {m['role']}: {str(m.get('content'))[:50]}... (Artifacts: {len(m.get('artifacts', []))})")

if __name__ == "__main__":
    asyncio.run(check_messages())
