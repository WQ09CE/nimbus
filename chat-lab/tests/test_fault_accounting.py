"""Real subprocess faults + real isolated PG, synthetic execution, no Telegram/model.

Expected acceptance is recorded at ingress by the driver, NOT reconstructed from
an active-worker index. This tests registration/cleanup coverage, not continuation.
"""

import asyncio
import json
import signal
from uuid import uuid4

import pytest
from conftest import rows, update
from test_faults import launch

from nimbus_chat_lab.operations import Operations


@pytest.mark.parametrize("size", [12, 100])
async def test_mixed_batch_fixed_accounting_after_worker_kill_and_freeze(store, tmp_path, size):
    store.agent_mode = True
    store.queue_limit = 128  # isolated test budget; never changes production's 64
    accepted_updates = []
    for n in range(1, size + 1):
        user = (n - 1) // 4 + 1
        await store.authorize(100, user, user)
        message = update(n, f"synthetic task {n}", user=user, chat=user)
        assert await store.ingest(100, "bot", [message]) == ["chat"]
        accepted_updates.append(n)
    # Lost semantic ACK after commit: replay is duplicate, not another logical task.
    assert await store.ingest(100, "bot", [update(1)]) == ["duplicate"]
    receipts = await rows(
        store, "SELECT update_id,turn_id FROM inbox WHERE bot_id=100 ORDER BY update_id"
    )
    assert [r["update_id"] for r in receipts] == accepted_updates
    assert all(r["turn_id"] for r in receipts)
    expected = {f"turn:{r['turn_id']}" for r in receipts}
    assert len(expected) == size
    incident = {"version": 1, "task_ids": sorted(expected)}
    ops = Operations(store)
    processes = []
    try:
        a = await launch(store, tmp_path / "killed", 10)
        processes.append(a)
        b = await launch(store, tmp_path / "frozen", 10)
        processes.append(b)
        # Independent healthy owner can complete while the two fixture workers run.
        healthy = await store.claim(uuid4())
        assert healthy is not None
        assert await store.finish(healthy, "succeeded", "healthy result")
        async with await store.connect() as c:
            await c.execute(
                "UPDATE outbox SET state='uncertain' WHERE dedupe_key=%s",
                (f"final:{healthy.turn_id}:0",),
            )
        # One queued task gets a real control cancellation; not all accepted tasks are runnable.
        queued = await rows(
            store,
            "SELECT s.user_id FROM turns t JOIN sessions s ON s.id=t.session_id WHERE t.state='queued' AND NOT EXISTS (SELECT 1 FROM turns a WHERE a.session_id=t.session_id AND a.state IN ('running','cancel_requested')) ORDER BY t.created_at LIMIT 1",
        )
        user = queued[0]["user_id"]
        await store.ingest(100, "bot", [update(1001, "/cancel", user=user, chat=user)])
        a.kill()
        await a.wait()
        b.send_signal(signal.SIGSTOP)
        await asyncio.sleep(1.2)
        before = await ops.report(cohort=incident, limit=200)
        assert before["coverage_complete"] and before["total_tasks"] == size
        assert before["alerts"]["expired_leases"] == 2
        assert before["alerts"]["uncertain_deliveries"] == 1
        # Competing scanners must neither double-close nor replay.
        assert sum(await asyncio.gather(store.recover(), store.recover())) == 2
        after = await ops.report(cohort=incident, limit=200)
        assert after["expected_tasks"] == size and not after["missing_tasks"]
        assert {t["task_id"] for t in after["tasks"]} == expected
        states = [t["execution_state"] for t in after["tasks"]]
        assert states.count("interrupted") == 2
        assert states.count("succeeded") == 1
        assert states.count("cancelled") == 1
        assert states.count("queued") == size - 4
        assert len(await rows(store, "SELECT id FROM attempts")) == 3
        b.send_signal(signal.SIGCONT)
        assert await asyncio.wait_for(b.wait(), 6) == 0
        assert await store.recover() == 0
        final = await ops.report(cohort=incident, limit=200)
        assert [(t["task_id"], t["execution_state"], t["generation"]) for t in final["tasks"]] == [
            (t["task_id"], t["execution_state"], t["generation"]) for t in after["tasks"]
        ]
        for name in ("killed", "frozen"):
            operations = (tmp_path / name / "operations.jsonl").read_text().splitlines()
            assert len(operations) == 1
            assert json.loads(operations[0])["event"] == "start"
    finally:
        for proc in processes:
            if proc.returncode is None:
                proc.send_signal(signal.SIGCONT)
                proc.kill()
                await proc.wait()
