"""Temporal arm — start a lab turn and trace its progress (worker + attempt per step).
usage: run_turn.py [STEPS=5] [SLEEP=3]"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from temporalio.client import Client, WorkflowExecutionStatus  # noqa: E402
from workflows import LabTurn  # noqa: E402


async def main(steps: int, sleep_s: float) -> None:
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233"))
    wid = f"lab-turn-{int(time.time())}"
    handle = await client.start_workflow(LabTurn.run, args=[steps, sleep_s], id=wid, task_queue="nimbus-lab")
    print(f"workflow {wid}", flush=True)
    t0, seen, blind_since = time.monotonic(), 0, None
    while True:
        try:
            prog = await handle.query(LabTurn.progress)
            if blind_since is not None:  # queries are workflow tasks: a dead sticky worker blinds them
                print(f"  +{time.monotonic() - t0:6.2f}s query available again "
                      f"(blind for {time.monotonic() - blind_since:.1f}s)", flush=True)
                blind_since = None
        except Exception as e:
            prog = None
            if blind_since is None:
                blind_since = time.monotonic()
                print(f"  +{time.monotonic() - t0:6.2f}s query unavailable: {type(e).__name__}", flush=True)
        if prog and len(prog) > seen:
            for item in prog[seen:]:
                print(f"  +{time.monotonic() - t0:6.2f}s {item}", flush=True)
            seen = len(prog)
        desc = await handle.describe()
        if desc.status != WorkflowExecutionStatus.RUNNING:
            break
        await asyncio.sleep(1)
    try:
        res = await handle.result()
        print(f"  result: {res['done']} in {time.monotonic() - t0:.2f}s (lease {res['lease']})", flush=True)
    except Exception as e:
        print(f"  workflow ended without result: {type(e).__name__}: {str(e)[:200]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5, float(sys.argv[2]) if len(sys.argv) > 2 else 3.0))
