"""
Nimbus-OpenWork Integration E2E Test

Test the OpenCode compatible API layer to ensure OpenWork can connect.

Usage:
    # Start Nimbus server first:
    cd nimbus && python -m nimbus.cli serve

    # Then run this test:
    PYTHONPATH=. python nimbus/examples/openwork_e2e_test.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import aiohttp

# Configuration
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 4096  # OpenCode default port
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
OLLAMA_MODEL = "qwen3:8b"


async def check_ollama():
    """Check if Ollama is running."""
    print("=" * 60)
    print("Step 0: Checking Ollama")
    print("=" * 60)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:11434/api/tags") as resp:
                if resp.status != 200:
                    print(f"[WARN] Ollama not responding: {resp.status}")
                    return False
                print("[OK] Ollama running")
                return True
    except Exception as e:
        print(f"[WARN] Cannot connect to Ollama: {e}")
        return False


async def test_opencode_health():
    """Test if server responds (using native API health check)."""
    print("\n" + "=" * 60)
    print("Step 1: Testing Server Health")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/api/v1/health") as resp:
            if resp.status != 200:
                print(f"[FAIL] Health check failed: {resp.status}")
                return False
            data = await resp.json()
            print(f"[OK] Server healthy: {data}")
            return True


async def test_opencode_session_list():
    """Test listing sessions via OpenCode API."""
    print("\n" + "=" * 60)
    print("Step 2: Testing OpenCode Session List (GET /session)")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/session") as resp:
            if resp.status != 200:
                print(f"[FAIL] Session list failed: {resp.status}")
                return False
            data = await resp.json()
            print(f"[OK] Found {len(data)} session(s)")
            for s in data[:3]:
                print(f"     - {s['id']}: {s['title']}")
            return True


async def test_opencode_session_create():
    """Test creating a session via OpenCode API."""
    print("\n" + "=" * 60)
    print("Step 3: Testing OpenCode Session Create (POST /session)")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        payload = {
            "title": "openwork-e2e-test",
            "directory": "/tmp/test",
        }
        async with session.post(f"{BASE_URL}/session", json=payload) as resp:
            if resp.status != 201:
                print(f"[FAIL] Session create failed: {resp.status}")
                text = await resp.text()
                print(f"       Response: {text}")
                return None
            data = await resp.json()
            print(f"[OK] Session created:")
            print(f"     ID: {data['id']}")
            print(f"     Title: {data['title']}")
            print(f"     Time: {data['time']}")
            return data["id"]


async def test_opencode_session_get(session_id: str):
    """Test getting a session via OpenCode API."""
    print("\n" + "=" * 60)
    print(f"Step 4: Testing OpenCode Session Get (GET /session/{session_id})")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/session/{session_id}") as resp:
            if resp.status != 200:
                print(f"[FAIL] Session get failed: {resp.status}")
                return False
            data = await resp.json()
            print(f"[OK] Session retrieved:")
            print(f"     ID: {data['id']}")
            print(f"     Title: {data['title']}")
            return True


async def test_opencode_message_send(session_id: str):
    """Test sending a message via OpenCode API (SSE stream)."""
    print("\n" + "=" * 60)
    print(f"Step 5: Testing OpenCode Message Send (POST /session/{session_id}/message)")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        payload = {"content": "What is 2 + 2?"}
        print(f"[INFO] Sending: {payload['content']}")

        events = []
        current_event = None
        current_data = None

        async with session.post(
            f"{BASE_URL}/session/{session_id}/message",
            json=payload,
            headers={"Accept": "text/event-stream"},
            timeout=aiohttp.ClientTimeout(total=120)
        ) as resp:
            if resp.status != 200:
                print(f"[FAIL] Message send failed: {resp.status}")
                return False

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

        # Check for content
        for e in events:
            if e.get("event") == "content.delta":
                text = e.get("data", {}).get("text", "")[:200]
                print(f"[RESPONSE] {text}")
                break

        return len(events) > 0


async def test_opencode_message_list(session_id: str):
    """Test listing messages via OpenCode API."""
    print("\n" + "=" * 60)
    print(f"Step 6: Testing OpenCode Message List (GET /session/{session_id}/message)")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/session/{session_id}/message") as resp:
            if resp.status != 200:
                print(f"[FAIL] Message list failed: {resp.status}")
                return False
            data = await resp.json()
            print(f"[OK] Found {len(data)} message(s)")
            for m in data[:5]:
                role = m.get("info", {}).get("role", "unknown")
                parts = m.get("parts", [])
                text = ""
                for p in parts:
                    if p.get("type") == "text":
                        text = p.get("text", "")[:80]
                        break
                print(f"     [{role}] {text}...")
            return True


async def test_opencode_global_event():
    """Test global event stream."""
    print("\n" + "=" * 60)
    print("Step 7: Testing OpenCode Global Event (GET /event)")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        events = []
        try:
            async with session.get(
                f"{BASE_URL}/event",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    print(f"[FAIL] Event stream failed: {resp.status}")
                    return False

                start_time = time.time()
                current_event = None
                current_data = None

                async for line in resp.content:
                    if time.time() - start_time > 3:
                        break

                    line = line.decode("utf-8").strip()
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        current_data = line[5:].strip()
                    elif line == "" and current_event and current_data:
                        try:
                            event_data = json.loads(current_data)
                            events.append({"event": current_event, "data": event_data})
                            print(f"[SSE] {current_event}: {str(event_data)[:80]}")
                        except:
                            pass
                        current_event = None
                        current_data = None

        except asyncio.TimeoutError:
            pass

        print(f"[OK] Received {len(events)} event(s) in 3s")
        return len(events) > 0


async def test_opencode_session_delete(session_id: str):
    """Test deleting a session via OpenCode API."""
    print("\n" + "=" * 60)
    print(f"Step 8: Testing OpenCode Session Delete (DELETE /session/{session_id})")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        async with session.delete(f"{BASE_URL}/session/{session_id}") as resp:
            if resp.status != 204:
                print(f"[FAIL] Session delete failed: {resp.status}")
                return False
            print(f"[OK] Session {session_id} deleted")
            return True


async def run_tests():
    """Run all OpenCode compatibility tests."""
    print("=" * 60)
    print("Nimbus-OpenWork Integration E2E Test")
    print(f"Target: {BASE_URL}")
    print("Testing OpenCode Compatible API")
    print("=" * 60)

    # Check Ollama (optional)
    has_ollama = await check_ollama()
    if not has_ollama:
        print("[WARN] Ollama not available, chat test will use mock response")

    results = []

    # Test server health
    results.append(("Server Health", await test_opencode_health()))

    # Test OpenCode API
    results.append(("Session List", await test_opencode_session_list()))

    session_id = await test_opencode_session_create()
    results.append(("Session Create", session_id is not None))

    if session_id:
        results.append(("Session Get", await test_opencode_session_get(session_id)))
        results.append(("Message Send (SSE)", await test_opencode_message_send(session_id)))
        results.append(("Message List", await test_opencode_message_list(session_id)))
        results.append(("Global Event", await test_opencode_global_event()))
        results.append(("Session Delete", await test_opencode_session_delete(session_id)))

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
        print("\n[ALL TESTS PASSED] OpenWork can connect to Nimbus!")
    else:
        print(f"\n[{failed} TEST(S) FAILED]")

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
