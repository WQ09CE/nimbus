
import asyncio
from nimbus.storage.sqlite import SQLiteStorage

async def inspect_db():
    storage = SQLiteStorage(".nimbus/nimbus.db")
    await storage.initialize()
    
    sessions = await storage.get_recent_sessions(limit=1)
    if not sessions:
        print("No sessions found")
        return
        
    sid = sessions[0]["id"]
    print(f"Session: {sid}")
    
    messages = await storage.get_messages(sid)
    print(f"Messages count: {len(messages)}")
    for m in messages:
        content_preview = m['content'][:50].replace('\n', ' ') if m['content'] else "(no content)"
        print(f"[{m['role']}] {content_preview}")
        if m.get('artifacts'):
            print(f"  Artifacts: {len(m['artifacts'])}")

    await storage.close()

if __name__ == "__main__":
    asyncio.run(inspect_db())
