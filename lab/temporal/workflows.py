"""Temporal arm — the N-step lab turn as a workflow (deterministic code only)."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import lab_step, open_lease


@workflow.defn
class LabTurn:
    def __init__(self) -> None:
        self._progress: list = []

    @workflow.query
    def progress(self) -> list:
        return self._progress

    @workflow.run
    async def run(self, steps: int, sleep_s: float, heartbeat_timeout_s: float = 15.0,
                  max_attempts: int = 3, bloat: str = "") -> dict:
        lease = await workflow.execute_activity(
            open_lease, start_to_close_timeout=timedelta(seconds=10),
        )
        self._progress.append({"lease": lease})
        for k in range(1, steps + 1):
            r = await workflow.execute_activity(
                lab_step, args=[lease, k, sleep_s, bloat],
                start_to_close_timeout=timedelta(seconds=60),
                heartbeat_timeout=timedelta(seconds=heartbeat_timeout_s),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1),
                                         maximum_attempts=max_attempts),
            )
            self._progress.append({k2: v for k2, v in r.items() if k2 != "raw"})
        return {"done": f"LAB_DONE {steps}", "lease": lease, "progress": self._progress}
