"""Temporal arm — activities. Execution goes through the SAME vcompute daemon
(leases, bubblewrap) as the nimbus arm, so the two arms differ only in the control
plane: Temporal history + heartbeats/timeouts/retries/task tokens vs nimbus session
log + ledger epochs."""

import asyncio
import json
import os

import httpx
from temporalio import activity

VC = os.environ.get("NIMBUS_VCOMPUTE_URL", "http://127.0.0.1:8793")
WORKER = os.environ.get("LAB_WORKER", "worker-?")


@activity.defn
async def open_lease() -> str:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{VC}/v1/leases", json={})
        r.raise_for_status()
        return r.json()["lease_id"]


@activity.defn
async def lab_step(lease_id: str, k: int, sleep_s: float, bloat: str = "") -> dict:
    """One Bash step in the lease (same command as the nimbus MockLLM rule);
    heartbeats every second while the command runs so a dead/frozen worker is
    detected by heartbeat timeout, not by start-to-close. ``bloat`` (R4) adds K
    bytes of noise to the step's output — the result payload lands in history."""
    info = activity.info()
    noise = f"base64 -w0 /dev/urandom | head -c {bloat}; echo; " if bloat else ""
    cmd = f"sleep {sleep_s}; {noise}echo step-{k} >> lab_steps.txt; cat lab_steps.txt"
    req = {"call_id": f"{info.workflow_id}-{k}-a{info.attempt}", "command": cmd, "timeout_s": 60}

    async def beat():
        while True:
            activity.heartbeat(f"step {k} attempt {info.attempt} on {WORKER}")
            await asyncio.sleep(1)

    hb = asyncio.create_task(beat())
    result = {}
    try:
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", f"{VC}/v1/leases/{lease_id}/exec", json=req) as resp:
                if resp.status_code == 404:
                    raise RuntimeError(f"LEASE_LOST {lease_id}")
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line and json.loads(line).get("type") == "result":
                        result = json.loads(line)
    finally:
        hb.cancel()
    out = (result.get("output") or "").strip().replace("\n", ",")
    return {"k": k, "worker": WORKER, "attempt": info.attempt, "status": result.get("status"),
            "output": out if not bloat else f"<{len(out)} chars>", "raw": out if bloat else ""}
