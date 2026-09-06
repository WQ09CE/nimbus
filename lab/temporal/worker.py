"""Temporal arm — one worker process = one "pod" (identity from LAB_WORKER)."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from activities import lab_step, open_lease  # noqa: E402
from temporalio.client import Client  # noqa: E402
from temporalio.worker import Worker  # noqa: E402
from workflows import LabTurn  # noqa: E402

TASK_QUEUE = "nimbus-lab"


async def main() -> None:
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233"))
    identity = os.environ.get("LAB_WORKER", "worker-x")
    async with Worker(client, task_queue=TASK_QUEUE, workflows=[LabTurn],
                      activities=[open_lease, lab_step], identity=identity,
                      max_concurrent_activities=4):
        print(f"{identity} polling task queue {TASK_QUEUE}", flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
