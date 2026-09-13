"""Isolated PG observation overhead baseline. No production DSN/model/Telegram/tools.

Uses a synthetic 10-ms await, not provider latency. Run manually before opting in.
"""

import asyncio
import json
import math
import os
import tempfile
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

import pgserver

from nimbus_chat_lab.operations import Operations
from nimbus_chat_lab.store import Store
from nimbus_chat_lab.telemetry import Observer


def percentile(values, p):
    return sorted(values)[math.ceil(len(values) * p) - 1]


async def run():
    with tempfile.TemporaryDirectory(prefix="nimbus-observation-") as directory:
        pg = pgserver.get_server(Path(directory) / "pg", cleanup_mode="stop")
        try:
            store = Store(pg.get_uri(), lease_seconds=60, agent_mode=True)
            await store.initialize()
            await store.authorize(100, 1, 1)
            await store.ingest(
                100,
                "fixture",
                [
                    {
                        "update_id": 1,
                        "message": {
                            "message_id": 1,
                            "from": {"id": 1},
                            "chat": {"id": 1, "type": "private"},
                            "text": "synthetic observation baseline",
                        },
                    }
                ],
            )
            claim = await store.claim(uuid4())
            samples = {False: [], True: []}
            for _ in range(30):
                for enabled in (False, True):
                    observer = Observer(store, claim, enabled=enabled)
                    start = perf_counter()
                    async with observer.span("model"):
                        await asyncio.sleep(0.01)
                    samples[enabled].append((perf_counter() - start) * 1000)
            report_started = perf_counter()
            report = await Operations(store).report()
            report_ms = (perf_counter() - report_started) * 1000
            async with await store.connect() as c:
                events = await (
                    await c.execute(
                        "SELECT count(*) AS n,sum(octet_length(text)) AS bytes FROM turn_events WHERE kind='telemetry'"
                    )
                ).fetchone()
            overhead_ms = percentile(samples[True], 0.95) - percentile(samples[False], 0.95)
            passed = events["n"] == 60 and overhead_ms < 100 and report_ms < 500
            return {
                "scope": "isolated_pg_synthetic_10ms_await_no_provider_no_telegram",
                "samples_per_mode": 30,
                "disabled_median_ms": round(median(samples[False]), 3),
                "enabled_median_ms": round(median(samples[True]), 3),
                "disabled_p95_ms": round(percentile(samples[False], 0.95), 3),
                "enabled_p95_ms": round(percentile(samples[True], 0.95), 3),
                "p95_difference_ms": round(overhead_ms, 3),
                "report_ms": round(report_ms, 3),
                "receipt_count": events["n"],
                "receipt_payload_bytes": events["bytes"],
                "report_bytes": len(json.dumps(report).encode()),
                "budget": "synthetic p95 difference <100ms; one-task report <500ms",
                "passed": passed,
                "limitations": [
                    "not_production_load",
                    "no_wal_or_index_storage_accounting",
                    "no_provider_latency_or_billing_claim",
                ],
            }
        finally:
            await asyncio.to_thread(pg.cleanup)


if __name__ == "__main__":
    os.umask(0o077)
    result = asyncio.run(run())
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
