#!/usr/bin/env python3
"""Nimbus Stress Test - Concurrent sessions with claude-sonnet-4-5"""

import asyncio
import aiohttp
import time
import json
import sys

BASE_URL = "http://localhost:4096/api/v1"
MODEL = "claude-sonnet-4-5"

# Test tasks of varying complexity
TASKS = [
    {
        "name": "simple-code",
        "prompt": "写一个 Python 函数，实现快速排序算法，然后用 Bash 运行测试验证它能正确排序 [3,1,4,1,5,9,2,6]",
    },
    {
        "name": "file-read-summarize",
        "prompt": "读取 src/nimbus/core/runtime/vcpu.py 的前 100 行，总结这个文件的核心设计思想，用 3 句话概括",
    },
    {
        "name": "multi-step",
        "prompt": "列出 src/nimbus/core/memory/ 目录下所有 .py 文件，然后读取其中最大的那个文件的前 50 行，告诉我它是做什么的",
    },
]


async def create_session(http: aiohttp.ClientSession, name: str) -> str:
    """Create a new session with claude-sonnet-4-5."""
    payload = {
        "name": f"stress-{name}",
        "llm_config": {"provider": "anthropic", "model_id": MODEL},
    }
    async with http.post(f"{BASE_URL}/sessions", json=payload) as resp:
        if resp.status != 201:
            text = await resp.text()
            raise RuntimeError(f"Create session failed ({resp.status}): {text}")
        data = await resp.json()
        return data["id"]


async def send_chat(http: aiohttp.ClientSession, session_id: str, content: str) -> dict:
    """Send a chat message and consume SSE stream, return stats."""
    payload = {"content": content}
    stats = {
        "session_id": session_id,
        "tool_calls": 0,
        "tool_names": [],
        "empty_responses": 0,
        "thoughts": 0,
        "errors": [],
        "final_content": "",
        "all_events": [],
        "start_time": time.time(),
        "first_event_time": None,
        "end_time": None,
    }

    try:
        async with http.post(
            f"{BASE_URL}/sessions/{session_id}/chat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=600),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                stats["errors"].append(f"HTTP {resp.status}: {text[:200]}")
                stats["end_time"] = time.time()
                return stats

            # Parse SSE stream
            buffer = ""
            async for chunk in resp.content:
                text = chunk.decode("utf-8", errors="replace")
                buffer += text

                while "\n\n" in buffer:
                    event_str, buffer = buffer.split("\n\n", 1)
                    lines = event_str.strip().split("\n")

                    event_type = None
                    event_data = None

                    for line in lines:
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            event_data = line[5:].strip()

                    if not event_type or not event_data:
                        continue

                    if stats["first_event_time"] is None:
                        stats["first_event_time"] = time.time()

                    stats["all_events"].append(event_type)

                    try:
                        data = json.loads(event_data)
                    except json.JSONDecodeError:
                        continue

                    if event_type == "tool_call":
                        stats["tool_calls"] += 1
                        tool_name = data.get("tool", data.get("name", "?"))
                        stats["tool_names"].append(tool_name)
                    elif event_type == "message":
                        content = data.get("content", "")
                        if isinstance(content, str):
                            if content:
                                stats["final_content"] += content
                    elif event_type == "error":
                        stats["errors"].append(data.get("message", str(data))[:200])
                    elif event_type == "dag_complete":
                        pass  # Stream will end after this

    except asyncio.TimeoutError:
        stats["errors"].append("TIMEOUT (600s)")
    except Exception as e:
        stats["errors"].append(f"Exception: {str(e)[:200]}")

    stats["end_time"] = time.time()
    return stats


async def run_task(http: aiohttp.ClientSession, task: dict, task_idx: int) -> dict:
    """Run a single test task: create session + send chat."""
    name = task["name"]
    print(f"  [{task_idx}] Creating session for '{name}'...")

    try:
        session_id = await create_session(http, f"{name}-{task_idx}")
        print(f"  [{task_idx}] Session created: {session_id}")
        print(f"  [{task_idx}] Sending task: {task['prompt'][:60]}...")

        stats = await send_chat(http, session_id, task["prompt"])
        stats["task_name"] = name

        duration = (stats["end_time"] or time.time()) - stats["start_time"]
        ttfe = (
            (stats["first_event_time"] - stats["start_time"])
            if stats["first_event_time"]
            else None
        )

        # Count distinct event types
        from collections import Counter
        event_counts = Counter(stats.get("all_events", []))

        has_content = bool(stats.get("final_content", "").strip())
        status = "PASS" if (not stats["errors"] and has_content) else "FAIL"
        ttfe_str = f"TTFE={ttfe:.1f}s" if ttfe else "TTFE=?"
        print(
            f"  [{task_idx}] {status} '{name}' | "
            f"{duration:.1f}s total | {ttfe_str} | "
            f"tools={stats['tool_calls']} | "
            f"events={len(stats.get('all_events', []))} | "
            f"errors={len(stats['errors'])}"
        )
        if event_counts:
            print(f"       Events: {dict(event_counts)}")
        if stats.get("tool_names"):
            print(f"       Tools: {stats['tool_names']}")
        if stats.get("final_content"):
            preview = stats["final_content"][:200].replace("\n", " ")
            print(f"       Content: {preview}...")
        if stats["errors"]:
            for e in stats["errors"]:
                print(f"       ERROR: {e}")

        return stats

    except Exception as e:
        print(f"  [{task_idx}] CRASH '{name}': {e}")
        return {"task_name": name, "errors": [str(e)], "tool_calls": 0, "end_time": time.time(), "start_time": time.time()}


async def main():
    print(f"=== Nimbus Stress Test ===")
    print(f"Server: {BASE_URL}")
    print(f"Model: {MODEL}")
    print(f"Tasks: {len(TASKS)} concurrent sessions")
    print()

    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as http:
        # Health check (use sessions list as ping)
        try:
            async with http.get(f"{BASE_URL}/sessions", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    print(f"Server not healthy: {resp.status}")
                    return
                print("Server healthy ✓")
        except Exception as e:
            print(f"Cannot reach server: {e}")
            return

        print()
        start = time.time()

        # Run all tasks concurrently
        results = await asyncio.gather(
            *[run_task(http, task, i) for i, task in enumerate(TASKS)],
            return_exceptions=True,
        )

        elapsed = time.time() - start

        # Summary
        print()
        print(f"=== Results ({elapsed:.1f}s total) ===")
        print(f"{'Task':<25} {'Status':<8} {'Duration':>10} {'Tools':>6} {'Errors':>7}")
        print("-" * 65)

        passed = 0
        for r in results:
            if isinstance(r, Exception):
                print(f"{'?':<25} {'CRASH':<8} {'?':>10} {'?':>6} {str(r)[:20]:>7}")
                continue
            name = r.get("task_name", "?")
            dur = (r.get("end_time", 0) or 0) - (r.get("start_time", 0) or 0)
            tools = r.get("tool_calls", 0)
            errs = len(r.get("errors", []))
            status = "PASS" if errs == 0 else "FAIL"
            if errs == 0:
                passed += 1
            print(f"{name:<25} {status:<8} {dur:>8.1f}s {tools:>6} {errs:>7}")

        print("-" * 65)
        print(f"Total: {passed}/{len(results)} passed | {elapsed:.1f}s wall time")


if __name__ == "__main__":
    asyncio.run(main())
