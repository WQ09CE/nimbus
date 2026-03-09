"""Test SSE event timing to verify real-time tool event delivery."""
import asyncio
import json
import time
import httpx

API = "http://localhost:4096/api/v1"

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        # 1. Create session
        r = await client.post(f"{API}/sessions", json={
            "name": "SSE Timing Test",
            "agent_mode": "standard",
        })
        r.raise_for_status()
        session = r.json()
        session_id = session["id"]
        print(f"Session: {session_id}")

        # 2. POST chat (returns SSE stream) — ask for multiple sequential tool calls
        t0 = time.monotonic()
        async with client.stream(
            "POST",
            f"{API}/sessions/{session_id}/chat",
            json={"content": "请依次执行以下 3 个命令并告诉我结果：1) echo hello  2) echo world  3) date"},
            timeout=120,
        ) as resp:
            print(f"\n{'='*60}")
            print(f"{'Time':>8s}  {'Event':>15s}  Data")
            print(f"{'='*60}")

            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                # Parse SSE events from buffer
                while "\n\n" in buffer:
                    event_str, buffer = buffer.split("\n\n", 1)
                    lines = event_str.strip().split("\n")
                    event_type = ""
                    data = ""
                    for line in lines:
                        if line.startswith("event: "):
                            event_type = line[7:]
                        elif line.startswith("data: "):
                            data = line[6:]

                    elapsed = time.monotonic() - t0

                    # Parse and summarize
                    try:
                        d = json.loads(data) if data else {}
                    except json.JSONDecodeError:
                        d = {"raw": data}

                    summary = ""
                    if event_type == "tool_call":
                        summary = f"TOOL_CALL: {d.get('tool', '?')} args={d.get('args', {})}"
                    elif event_type == "tool_result":
                        out = str(d.get("output", ""))[:80]
                        summary = f"TOOL_RESULT: {d.get('tool', '?')} status={d.get('status')} output={out}"
                    elif event_type == "message":
                        content = str(d.get("content", ""))[:80].replace("\n", "\\n")
                        summary = f"MESSAGE: {content}"
                    elif event_type == "done":
                        summary = f"DONE: {d.get('status')}"
                    elif event_type == "connected":
                        summary = "CONNECTED"
                    elif event_type == "message_start":
                        summary = "MESSAGE_START"
                    elif event_type == "heartbeat":
                        summary = "HEARTBEAT"
                    else:
                        summary = str(d)[:80]

                    print(f"{elapsed:8.3f}s  {event_type:>15s}  {summary}")

                    if event_type == "done":
                        print(f"\n{'='*60}")
                        print(f"Total time: {elapsed:.3f}s")
                        return

if __name__ == "__main__":
    asyncio.run(main())
