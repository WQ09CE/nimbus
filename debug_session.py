import asyncio
import sys
from nimbus.storage.sqlite import SQLiteStorage

async def check_session_data(session_id):
    storage = SQLiteStorage()
    await storage.initialize()
    
    print(f"Checking session: {session_id}")
    
    async with storage._get_connection() as db:
        # 1. Count messages
        cursor = await db.execute(
            "SELECT count(*) as count FROM messages WHERE session_id = ?",
            (session_id,)
        )
        row = await cursor.fetchone()
        msg_count = row['count']
        print(f"Messages in DB: {msg_count}")
        
        # 2. Check latest checkpoint
        cursor = await db.execute(
            "SELECT id, timestamp, memory_snapshot FROM session_checkpoints WHERE session_id = ? ORDER BY timestamp DESC LIMIT 1",
            (session_id,)
        )
        row = await cursor.fetchone()
        
        if row:
            print(f"Latest Checkpoint: {row['id']} ({row['timestamp']})")
            import json
            snapshot = json.loads(row['memory_snapshot'])
            stack = snapshot.get('stack', [])
            
            snap_msg_ids = set()
            for frame in stack:
                for m in frame.get('messages', []):
                    # Message in snapshot might not have ID if it's not persisted properly?
                    # Wait, Message object in memory doesn't have 'id' field by default!
                    # The ID is generated when saving to DB.
                    # But wait, MMU Message struct DOES NOT HAVE ID field.
                    pass
            
            # Since MMU messages don't have IDs, we can't compare IDs.
            # We can only compare content/role/count.
            
            total_snap_msgs = 0
            for frame in stack:
                total_snap_msgs += len(frame.get('messages', []))
            print(f"Messages in Checkpoint Snapshot: {total_snap_msgs}")
            
            if total_snap_msgs < msg_count:
                print("⚠️ WARNING: Checkpoint has fewer messages than DB! Data loss in MMU?")
                
                # List DB messages to see what might be missing
                print("\nDB Messages:")
                cursor = await db.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at ASC", (session_id,))
                db_msgs = await cursor.fetchall()
                for i, m in enumerate(db_msgs):
                    content_preview = m['content'][:20].replace('\n', ' ')
                    print(f"  {i}: [{m['role']}] {content_preview}...")
                    
                print("\nSnapshot Messages:")
                i = 0
                for frame in stack:
                    for m in frame.get('messages', []):
                        content = m.get('content')
                        content_preview = str(content)[:20].replace('\n', ' ') if content else "[No Content]"
                        print(f"  {i}: [{m['role']}] {content_preview}...")
                        i += 1

            
    await storage.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Find latest session if not provided
        async def find_latest():
            storage = SQLiteStorage()
            await storage.initialize()
            async with storage._get_connection() as db:
                cursor = await db.execute("SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1")
                row = await cursor.fetchone()
                return row['id'] if row else None
        
        session_id = asyncio.run(find_latest())
        if not session_id:
            print("No sessions found.")
            sys.exit(1)
    else:
        session_id = sys.argv[1]
        
    asyncio.run(check_session_data(session_id))
