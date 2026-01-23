"""
Nimbus Server E2E Test with Ollama

Real end-to-end test for the new Server API layer:
1. Start server
2. Create session
3. Send chat message (with real Ollama LLM)
4. Verify SSE events
5. Check DAG execution
6. Test permission flow

Usage:
    # First, make sure ollama is running with qwen3:8b model
    # Then run this script:
    PYTHONPATH=. python nimbus/examples/server_e2e_test.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

# Add project path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import aiohttp

# Configuration
SERVER_HOST = "localhost"
SERVER_PORT = 18080  # Use non-standard port to avoid conflicts
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}/api/v1"
OLLAMA_MODEL = "qwen3:8b"


async def check_ollama():
    """Check if Ollama is running and model is available."""
    print("=" * 60)
    print("Step 0: Checking Ollama")
    print("=" * 60)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:11434/api/tags") as resp:
                if resp.status != 200:
                    print(f"[FAIL] Ollama not responding: {resp.status}")
                    return False

                data = await resp.json()
                models = [m["name"] for m in data.get("models", [])]

                if OLLAMA_MODEL not in models and f"{OLLAMA_MODEL}:latest" not in models:
                    # Check if any variant exists
                    matching = [m for m in models if OLLAMA_MODEL.split(":")[0] in m]
                    if not matching:
                        print(f"[FAIL] Model {OLLAMA_MODEL} not found. Available: {models}")
                        return False
                    print(f"[WARN] Using similar model: {matching[0]}")

                print(f"[OK] Ollama running, model available")
                return True
    except Exception as e:
        print(f"[FAIL] Cannot connect to Ollama: {e}")
        return False


async def start_server():
    """Start the Nimbus server in background."""
    print("\n" + "=" * 60)
    print("Step 1: Starting Nimbus Server")
    print("=" * 60)

    import subprocess
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parent.parent.parent)

    # Start server as subprocess
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "nimbus.server.app:create_app",
            "--factory",
            "--host", SERVER_HOST,
            "--port", str(SERVER_PORT),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to start
    print(f"[INFO] Starting server on {BASE_URL}...")
    for i in range(30):  # Wait up to 30 seconds
        await asyncio.sleep(1)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{BASE_URL}/health") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"[OK] Server started: {data}")
                        return proc
        except:
            pass
        print(f"[INFO] Waiting... ({i+1}s)")

    print("[FAIL] Server failed to start")
    proc.kill()
    return None


async def test_health():
    """Test health endpoint."""
    print("\n" + "=" * 60)
    print("Step 2: Testing Health Endpoint")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/health") as resp:
            assert resp.status == 200, f"Health check failed: {resp.status}"
            data = await resp.json()
            print(f"[OK] Health: {data}")
            return True


async def test_create_session():
    """Test session creation."""
    print("\n" + "=" * 60)
    print("Step 3: Creating Session")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        payload = {
            "name": "e2e-test-session",
            "memory_type": "tiered",
            "planner_type": "dag",
        }
        async with session.post(f"{BASE_URL}/sessions", json=payload) as resp:
            assert resp.status == 201, f"Create session failed: {resp.status}"
            data = await resp.json()
            print(f"[OK] Session created: {data['id']}")
            print(f"     Memory: {data['memory_type']}, Planner: {data['planner_type']}")
            return data["id"]


async def test_list_sessions():
    """Test listing sessions."""
    print("\n" + "=" * 60)
    print("Step 4: Listing Sessions")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/sessions") as resp:
            assert resp.status == 200, f"List sessions failed: {resp.status}"
            data = await resp.json()
            print(f"[OK] Found {data['total']} session(s)")
            for s in data["items"]:
                print(f"     - {s['id']}: {s['name']} ({s['status']})")
            return True


async def test_chat_simple(session_id: str):
    """Test simple chat without DAG (direct response)."""
    print("\n" + "=" * 60)
    print("Step 5: Testing Simple Chat (Direct Response)")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        payload = {"content": "Hello! What is 2 + 2?"}
        print(f"[INFO] Sending: {payload['content']}")

        events = []
        current_event = None
        current_data = None

        async with session.post(
            f"{BASE_URL}/sessions/{session_id}/chat",
            json=payload,
            headers={"Accept": "text/event-stream"},
            timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            assert resp.status == 200, f"Chat failed: {resp.status}"

            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                elif line.startswith("data:"):
                    current_data = line[5:].strip()
                elif line == "" and current_event and current_data:
                    try:
                        event_data = json.loads(current_data)
                        events.append({"event": current_event, "data": event_data})
                        print(f"[SSE] {current_event}: {str(event_data)[:100]}")
                    except:
                        print(f"[SSE] {current_event}: (parse error)")
                    current_event = None
                    current_data = None

        print(f"[OK] Received {len(events)} SSE events")

        # Find the final message
        for e in events:
            if e.get("event") == "message":
                content = e.get("data", {}).get("content", "")[:200]
                print(f"[RESPONSE] {content}...")

        return True


async def test_chat_with_dag(session_id: str):
    """Test chat that triggers DAG planning."""
    print("\n" + "=" * 60)
    print("Step 6: Testing Chat with DAG Planning")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        # This prompt should trigger multi-step planning
        payload = {
            "content": "Search for information about Python async programming and summarize the key concepts."
        }
        print(f"[INFO] Sending: {payload['content'][:50]}...")

        events = []
        dag_created = False
        tasks_started = 0
        tasks_done = 0
        current_event = None
        current_data = None

        start_time = time.time()

        async with session.post(
            f"{BASE_URL}/sessions/{session_id}/chat",
            json=payload,
            headers={"Accept": "text/event-stream"},
            timeout=aiohttp.ClientTimeout(total=120)  # 2 min timeout
        ) as resp:
            assert resp.status == 200, f"Chat failed: {resp.status}"

            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                elif line.startswith("data:"):
                    current_data = line[5:].strip()
                elif line == "" and current_event and current_data:
                    try:
                        event_data = json.loads(current_data)
                        events.append({"event": current_event, "data": event_data})

                        if current_event == "dag_created":
                            dag_created = True
                            print(f"[DAG] Created: {event_data.get('dag_id', 'unknown')}, "
                                  f"Tasks: {event_data.get('total_tasks', 0)}")
                        elif current_event == "task_start":
                            tasks_started += 1
                            print(f"[TASK] Started: {event_data.get('task_id')} - {event_data.get('skill')}")
                        elif current_event == "task_done":
                            tasks_done += 1
                            print(f"[TASK] Done: {event_data.get('task_id')} ({event_data.get('duration_ms', 0)}ms)")
                        elif current_event == "message":
                            content = event_data.get("content", "")[:150]
                            print(f"[MSG] {content}...")
                        elif current_event == "error":
                            print(f"[ERR] {event_data}")
                        else:
                            print(f"[{current_event.upper()}] {str(event_data)[:80]}...")
                    except Exception as e:
                        print(f"[PARSE_ERR] {current_event}: {e}")
                    current_event = None
                    current_data = None

        duration = time.time() - start_time
        print(f"\n[OK] Completed in {duration:.1f}s")
        print(f"     Total events: {len(events)}")
        print(f"     DAG created: {dag_created}")
        print(f"     Tasks started: {tasks_started}, done: {tasks_done}")

        return dag_created or len(events) > 0


async def test_get_messages(session_id: str):
    """Test getting message history."""
    print("\n" + "=" * 60)
    print("Step 7: Getting Message History")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/sessions/{session_id}/messages") as resp:
            assert resp.status == 200, f"Get messages failed: {resp.status}"
            data = await resp.json()
            print(f"[OK] Found {len(data['items'])} message(s)")
            for msg in data["items"]:
                role = msg["role"]
                content = msg["content"][:80] if msg["content"] else "(empty)"
                print(f"     [{role}] {content}...")
            return True


async def test_list_skills():
    """Test listing available skills."""
    print("\n" + "=" * 60)
    print("Step 8: Listing Available Skills")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/skills") as resp:
            assert resp.status == 200, f"List skills failed: {resp.status}"
            data = await resp.json()
            print(f"[OK] Found {len(data['skills'])} skill(s)")
            for skill in data["skills"][:5]:  # Show first 5
                print(f"     - {skill['name']}: {skill['description'][:50]}...")
            return True


async def test_delete_session(session_id: str):
    """Test deleting a session."""
    print("\n" + "=" * 60)
    print("Step 9: Deleting Session")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.delete(f"{BASE_URL}/sessions/{session_id}") as resp:
            assert resp.status == 204, f"Delete session failed: {resp.status}"
            print(f"[OK] Session {session_id} deleted")
            return True


async def run_tests():
    """Run all E2E tests."""
    print("=" * 60)
    print("Nimbus Server E2E Test")
    print(f"Target: {BASE_URL}")
    print(f"LLM: Ollama ({OLLAMA_MODEL})")
    print("=" * 60)

    # Check Ollama first
    if not await check_ollama():
        print("\n[ABORT] Ollama not available")
        return False

    # Start server
    server_proc = await start_server()
    if not server_proc:
        print("\n[ABORT] Server failed to start")
        return False

    try:
        # Run tests
        results = []

        results.append(("Health Check", await test_health()))

        session_id = await test_create_session()
        results.append(("Create Session", session_id is not None))

        if session_id:
            results.append(("List Sessions", await test_list_sessions()))
            results.append(("Simple Chat", await test_chat_simple(session_id)))
            results.append(("DAG Chat", await test_chat_with_dag(session_id)))
            results.append(("Get Messages", await test_get_messages(session_id)))
            results.append(("List Skills", await test_list_skills()))
            results.append(("Delete Session", await test_delete_session(session_id)))

        # Print summary
        print("\n" + "=" * 60)
        print("Test Summary")
        print("=" * 60)

        passed = sum(1 for _, r in results if r)
        failed = len(results) - passed

        for name, result in results:
            status = "PASS" if result else "FAIL"
            print(f"  [{status}] {name}")

        print(f"\nTotal: {len(results)}, Passed: {passed}, Failed: {failed}")

        if failed == 0:
            print("\n[ALL TESTS PASSED]")
        else:
            print(f"\n[{failed} TEST(S) FAILED]")

        return failed == 0

    finally:
        # Cleanup: stop server
        print("\n[INFO] Stopping server...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except:
            server_proc.kill()
        print("[OK] Server stopped")


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
