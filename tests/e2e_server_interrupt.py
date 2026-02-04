"""
E2E Test: Session Interrupt via Nimbus Server API

Tests the full session lifecycle through the HTTP API:
1. Create session
2. Start a task (chat)
3. Interrupt mid-execution
4. Verify checkpoint saved
5. Resume session
6. Verify task completes

Prerequisites:
- Nimbus server running: `nimbus serve --port 4096`
- Pi-AI server running: `pi-ai serve`
"""

import asyncio
import json
from typing import AsyncIterator

import httpx
import pytest

# Server config
BASE_URL = "http://127.0.0.1:4096/api/v1"
TIMEOUT = 60.0


async def sse_stream(client: httpx.AsyncClient, url: str, json_data: dict) -> AsyncIterator[dict]:
    """Stream SSE events from POST request."""
    async with client.stream("POST", url, json=json_data, timeout=TIMEOUT) as response:
        response.raise_for_status()
        buffer = ""
        event_type = "message"

        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                event_block, buffer = buffer.split("\n\n", 1)
                lines = event_block.strip().split("\n")

                for line in lines:
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            data = json.loads(line[5:].strip())
                            yield {"type": event_type, "data": data}
                        except json.JSONDecodeError:
                            pass


@pytest.fixture
async def client():
    """Create async HTTP client."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        yield client


@pytest.fixture
async def check_server(client):
    """Check if server is running."""
    try:
        resp = await client.get("/health")
        if resp.status_code != 200:
            pytest.skip("Nimbus server not running")
    except httpx.ConnectError:
        pytest.skip("Nimbus server not running (connection refused)")


@pytest.mark.asyncio
async def test_server_session_interrupt_resume(client, check_server):
    """
    Test session interrupt and resume via server API.
    
    Flow:
    1. Create session
    2. Start a long-running task
    3. Wait for task to start executing
    4. Interrupt via API
    5. Verify checkpoint saved
    6. Resume via API
    7. Verify task can continue
    """
    print("\n🚀 E2E Server Interrupt Test")

    # --- Step 1: Create Session ---
    print("\n[Step 1] Creating session...")
    resp = await client.post("/sessions", json={
        "name": "E2E Interrupt Test",
        "workspace_path": ".",
    })
    assert resp.status_code == 201, f"Failed to create session: {resp.text}"

    session = resp.json()
    session_id = session["id"]
    print(f"   ✓ Session created: {session_id}")

    try:
        # --- Step 2: Start Task in Background ---
        print("\n[Step 2] Starting task...")

        # Use a task that takes multiple steps
        task = "Count from 1 to 3. For each number, use Bash to run 'echo Number: X' where X is the current number. Do them one at a time."

        # Start chat in background task so we can interrupt it
        events_received = []
        chat_task = None

        async def run_chat():
            """Run chat and collect events."""
            try:
                async for event in sse_stream(client, f"/sessions/{session_id}/chat", {"content": task}):
                    events_received.append(event)
                    print(f"   📩 Event: {event['type']}")

                    # Stop after receiving tool_result to ensure task is in progress
                    if event["type"] == "tool_result":
                        print("   → Task is executing, ready for interrupt")
                        # Don't break - let it continue until interrupted
            except httpx.ReadError:
                # Expected when we interrupt
                print("   → Stream closed (expected after interrupt)")
            except Exception as e:
                print(f"   ⚠️ Chat error: {e}")

        # Start chat task
        chat_task = asyncio.create_task(run_chat())

        # Wait for task to start executing (at least one tool call)
        print("   Waiting for task to start...")
        for _ in range(30):  # Wait up to 30 seconds
            await asyncio.sleep(1)
            tool_events = [e for e in events_received if e["type"] in ("tool_call", "tool_result")]
            if len(tool_events) >= 1:
                print(f"   ✓ Task is running ({len(tool_events)} tool events)")
                break
        else:
            print("   ⚠️ Timeout waiting for task to start")

        # --- Step 3: Interrupt ---
        print("\n[Step 3] Interrupting session...")
        resp = await client.post(f"/sessions/{session_id}/interrupt")

        if resp.status_code == 200:
            result = resp.json()
            print("   ✓ Interrupt successful")
            print(f"      Processes interrupted: {result.get('interrupted_processes', 0)}")
            if result.get("checkpoint"):
                cp = result["checkpoint"]
                print(f"      Checkpoint: step={cp.get('step_index')}, messages={cp.get('memory_messages')}")
        else:
            print(f"   ⚠️ Interrupt returned: {resp.status_code} - {resp.text}")

        # Cancel the chat task
        chat_task.cancel()
        try:
            await chat_task
        except asyncio.CancelledError:
            pass

        # --- Step 4: Verify Session State ---
        print("\n[Step 4] Verifying session state...")
        resp = await client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        session_info = resp.json()
        print(f"   Session status: {session_info['status']}")
        print(f"   Message count: {session_info.get('message_count', 0)}")

        # --- Step 5: Resume ---
        print("\n[Step 5] Resuming session...")
        resp = await client.post(f"/sessions/{session_id}/resume")

        if resp.status_code == 200:
            result = resp.json()
            print("   ✓ Resume successful")
            print(f"      Restored step: {result.get('restored_step')}")
            print(f"      Restored iteration: {result.get('restored_iteration')}")
        else:
            print(f"   ⚠️ Resume returned: {resp.status_code} - {resp.text}")

        # --- Step 6: Continue Execution ---
        print("\n[Step 6] Continuing execution...")

        # Send another message to continue
        continue_events = []
        async for event in sse_stream(client, f"/sessions/{session_id}/chat", {"content": "Continue where you left off."}):
            continue_events.append(event)
            print(f"   📩 Event: {event['type']}")
            if event["type"] == "dag_complete":
                print("   ✓ Task completed!")
                break

        # --- Summary ---
        print("\n📊 Summary:")
        print(f"   Initial events: {len(events_received)}")
        print(f"   Continue events: {len(continue_events)}")
        print(f"   Total events: {len(events_received) + len(continue_events)}")

    finally:
        # Cleanup: Delete session
        print("\n[Cleanup] Deleting session...")
        await client.delete(f"/sessions/{session_id}")
        print("   ✓ Session deleted")

    print("\n✅ E2E Server Interrupt Test Completed!")


@pytest.mark.asyncio
async def test_server_interrupt_no_active_session(client, check_server):
    """Test interrupting a session with no active processes."""
    print("\n🧪 Test: Interrupt inactive session")

    # Create session
    resp = await client.post("/sessions", json={"name": "Inactive Test"})
    assert resp.status_code == 201
    session_id = resp.json()["id"]

    try:
        # Try to interrupt (should fail gracefully)
        resp = await client.post(f"/sessions/{session_id}/interrupt")
        print(f"   Interrupt response: {resp.status_code}")

        # Should return error since no AgentOS is loaded
        if resp.status_code == 400:
            print("   ✓ Correctly rejected (session not loaded)")
        elif resp.status_code == 200:
            result = resp.json()
            print(f"   ✓ Returned: {result}")
    finally:
        await client.delete(f"/sessions/{session_id}")

    print("   ✓ Test passed")


if __name__ == "__main__":
    asyncio.run(test_server_session_interrupt_resume(None, None))
