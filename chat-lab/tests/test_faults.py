import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest
from conftest import rows, update


async def launch(store, root, delay):
    root.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(root), "NIMBUS_LAB_DSN": store.dsn}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("drill_worker.py")),
        str(root),
        str(delay),
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    for _ in range(120):
        if (root / "started").exists():
            return proc
        if proc.returncode is not None:
            raise AssertionError((await proc.stderr.read()).decode())
        await asyncio.sleep(0.05)
    proc.kill()
    await proc.wait()
    raise AssertionError("fixture worker did not start")


@pytest.mark.parametrize("round_number", [1, 2])
@pytest.mark.parametrize("fault", ["kill", "freeze", "short_freeze", "term"])
async def test_real_process_lifecycle(store, tmp_path, fault, round_number):
    store.lease_seconds = 0.8
    await store.ingest(100, "nimbusbot", [update()])
    root = tmp_path / "worker-a"
    proc = await launch(store, root, 0.5 if fault in {"short_freeze", "term"} else 10)
    try:
        if fault == "kill":
            proc.send_signal(signal.SIGKILL)
            await proc.wait()
            await asyncio.sleep(1)
        elif fault == "freeze":
            proc.send_signal(signal.SIGSTOP)
            await asyncio.sleep(1.2)
        elif fault == "short_freeze":
            proc.send_signal(signal.SIGSTOP)
            await asyncio.sleep(0.1)
            assert await store.recover() == 0
            proc.send_signal(signal.SIGCONT)
        else:
            proc.send_signal(signal.SIGTERM)
        if fault in {"kill", "freeze"}:
            assert await store.recover() == 1
            before = (await rows(store, "SELECT state,result,generation FROM turns"))[0]
            assert before["state"] == "interrupted"
            # A distinct worker process completes a real follow-up, while A remains frozen.
            await store.ingest(100, "nimbusbot", [update(2, "followup")])
            other = await launch(store, tmp_path / "worker-b", 0)
            assert await asyncio.wait_for(other.wait(), 5) == 0
            if fault == "freeze":
                proc.send_signal(signal.SIGCONT)
                assert await asyncio.wait_for(proc.wait(), 5) == 0
            after = (
                await rows(store, "SELECT state,result,generation FROM turns ORDER BY created_at")
            )[0]
            assert after == before  # resumed zombie cannot change terminal state/result/generation
            assert len(await rows(store, "SELECT * FROM turns WHERE state='succeeded'")) == 1
        else:
            assert await asyncio.wait_for(proc.wait(), 5) == 0
            assert (await rows(store, "SELECT state FROM turns"))[0]["state"] == "succeeded"
        operations = [json.loads(x) for x in (root / "operations.jsonl").read_text().splitlines()]
        assert len(operations) == 1  # no replay of the synthetic operation
        assert operations[0]["pid"] == proc.pid
    finally:
        if proc.returncode is None:
            proc.send_signal(signal.SIGCONT)
            proc.kill()
            await proc.wait()


async def test_postgres_restart_means_unknown_not_new_admission(store, postgres, tmp_path):
    from pgserver._commands import pg_ctl

    await store.ingest(100, "nimbusbot", [update()])
    proc = await launch(store, tmp_path / "worker", 10)
    stopped = False
    try:
        await asyncio.to_thread(pg_ctl, ["-w", "-m", "fast", "stop"], pgdata=postgres.pgdata)
        stopped = True
        assert await asyncio.wait_for(proc.wait(), 12) != 0
        await asyncio.to_thread(postgres.ensure_postgres_running)
        stopped = False
        # A fast DB restart need not outlast the persisted lease. Unknown is not
        # expired: wait for the authoritative deadline instead of inventing loss.
        recovered = 0
        for _ in range(30):
            recovered += await store.recover()
            if recovered:
                break
            await asyncio.sleep(0.1)
        assert recovered == 1
        assert (await rows(store, "SELECT state FROM turns"))[0]["state"] == "interrupted"
        assert len(await rows(store, "SELECT * FROM attempts")) == 1
    finally:
        if stopped:
            await asyncio.to_thread(postgres.ensure_postgres_running)
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
