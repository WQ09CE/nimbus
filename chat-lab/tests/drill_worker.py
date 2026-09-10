"""Synthetic worker process used only by bounded signal drills. No model/network/tools."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from nimbus_chat_lab.store import Store
from nimbus_chat_lab.worker import Worker


async def main():
    root = Path(sys.argv[1])
    delay = float(sys.argv[2])
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)

    class FixtureEngine:
        async def run(self, claim, history, emit, before_request):
            await before_request()
            # Counts test-operation admission, NOT a remote sandbox command.
            with (root / "operations.jsonl").open("a") as f:
                f.write(
                    json.dumps(
                        {"pid": os.getpid(), "attempt": str(claim.attempt_id), "event": "start"}
                    )
                    + "\n"
                )
            (root / "started").write_text(str(claim.attempt_id))
            try:
                await asyncio.sleep(delay)
                await emit("fixture complete")
                return "fixture complete"
            finally:
                (root / "stopped").write_text("confirmed fixture coroutine ended")

    worker = Worker(
        Store(os.environ["NIMBUS_LAB_DSN"], lease_seconds=0.8), FixtureEngine(), run_timeout=20
    )
    # Single attempt. SIGTERM drains that attempt; no additional claim happens.
    await worker.run_once()


try:
    asyncio.run(main())
except Exception as error:
    print(type(error).__name__, file=sys.stderr)
    sys.exit(1)
