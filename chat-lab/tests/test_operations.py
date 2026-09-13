import asyncio
import json
import os
import sys
from uuid import uuid4

import pytest
from conftest import rows, update
from test_conversation import foreground, job

from nimbus_chat_lab.operations import Operations, local_dsn, read_cohort, write_cohort


def test_local_dsn_is_private_data_not_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".config/nimbus-chat-lab/worker.env"
    path.parent.mkdir(parents=True, mode=0o700)
    value = "postgresql://fixture@/fixture?host=/private/socket&port=55432"
    path.write_text("NIMBUS_LAB_DSN=" + value + "\nOTHER=ignored\n")
    path.chmod(0o600)
    assert local_dsn() == value
    path.chmod(0o644)
    with pytest.raises(ValueError):
        local_dsn()
    path.chmod(0o600)
    path.write_text("NIMBUS_LAB_DSN=one\nNIMBUS_LAB_DSN=two\n")
    with pytest.raises(ValueError):
        local_dsn()


async def test_read_only_report_uses_metadata_not_conversation(store):
    store.agent_mode = True
    secret = "NEVER-EXPORT-user-text"
    await store.ingest(100, "bot", [update(1, secret)])
    claim = await store.claim(uuid4())
    await store.progress(claim, secret)
    before = await rows(store, "SELECT * FROM turns")
    report = await Operations(store).report()
    assert secret not in json.dumps(report, default=str)
    assert report["total_tasks"] == 1
    task = report["tasks"][0]
    assert task["task_id"] == f"turn:{claim.turn_id}"
    assert task["execution_state"] == "running"
    assert task["incarnation"] == str(claim.incarnation)
    assert task["lease_observation"] == "unexpired_not_liveness_proof"
    assert task["telemetry"] == []
    assert "phase_timing_unavailable" in task["evidence_gaps"]
    assert before == await rows(store, "SELECT * FROM turns")


async def test_report_counts_unattached_and_attached_jobs_once(store):
    state, chat = await foreground(store)
    _, bg = await job(store, state, "name-not-for-diagnostics")
    sid = (await state.schedule("create", {"name": "queued", "instructions": "private"}))[
        "schedule"
    ]["id"]
    unattached = await state.schedule("run_now", {"id": sid})
    report = await Operations(store).report()
    assert report["total_tasks"] == 3
    assert len(report["tasks"]) == 3
    assert {t["turn_id"] for t in report["tasks"]} == {str(chat.turn_id), str(bg.turn_id), None}
    pending = next(t for t in report["tasks"] if t["task_id"] == f"run:{unattached['id']}")
    assert pending["waiting_for"] == "scheduler_admission"
    assert "name-not-for-diagnostics" not in json.dumps(report)


async def test_expired_owner_report_does_not_recover_or_assume_stop(store):
    await store.ingest(100, "bot", [update()])
    claim = await store.claim(uuid4())
    async with await store.connect() as c:
        await c.execute("UPDATE attempts SET lease_until=clock_timestamp()-interval '1 second'")
    report = await Operations(store).report()
    task = report["tasks"][0]
    assert task["execution_state"] == "running"
    assert task["lease_observation"] == "expired"
    assert "lease_expired" in task["attention"]
    assert report["alerts"]["expired_leases"] == 1
    assert await rows(store, "SELECT state FROM turns") == [{"state": "running"}]
    assert await store.recover() == 1
    task = (await Operations(store).report())["tasks"][0]
    assert task["execution_state"] == "interrupted"
    assert task["external_effects"] == "not_established"
    assert "interrupted" in task["attention"]
    assert task["delivery"] == {"pending": 1}
    assert await store.finish(claim, "succeeded", "stale") is False


async def test_fixed_cohort_pagination_missing_and_unrelated_followup(store, tmp_path):
    store.agent_mode = True
    owner = uuid4()
    accepted = []
    for n in range(1, 5):
        assert await store.ingest(100, "bot", [update(n)]) == ["chat"]
        claim = await store.claim(owner)
        accepted.append(f"turn:{claim.turn_id}")
        await store.finish(claim, "succeeded", "done")
    ops = Operations(store)
    cohort = await ops.capture(owner)
    assert set(cohort["task_ids"]) == set(accepted)
    target = tmp_path / "cohort.json"
    write_cohort(target, cohort)
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_cohort(target, cohort)
    assert read_cohort(target) == cohort
    missing = f"turn:{uuid4()}"
    cohort["task_ids"].append(missing)
    # Unrelated work after capture never changes the incident denominator.
    await store.ingest(100, "bot", [update(5)])
    first = await ops.report(cohort=cohort, limit=2)
    second = await ops.report(cohort=cohort, limit=2, after=first["next_after"])
    assert first["expected_tasks"] == second["expected_tasks"] == 5
    assert first["total_tasks"] == second["total_tasks"] == 4
    assert first["missing_tasks"] == [missing]
    assert not first["coverage_complete"]
    assert first["next_after"] and second["next_after"] is None
    assert {t["task_id"] for t in first["tasks"] + second["tasks"]} == set(accepted)


async def test_cohort_rejects_malformed_inputs_and_unsafe_files(store, tmp_path):
    ops = Operations(store)
    with pytest.raises(ValueError):
        await ops.report(limit=0)
    with pytest.raises(ValueError):
        await ops.report(cohort={"version": 1, "task_ids": ["private text"]})
    with pytest.raises(ValueError):
        await ops.report(cohort={"version": 1, "task_ids": [f"turn:{uuid4()}"] * 2})
    target = tmp_path / "world-readable"
    target.write_text("{}")
    target.chmod(0o644)
    with pytest.raises(ValueError):
        read_cohort(target)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        read_cohort(link)


async def test_delivery_unknown_is_alert_not_success(store):
    store.agent_mode = True
    await store.ingest(100, "bot", [update()])
    claim = await store.claim(uuid4())
    await store.finish(claim, "succeeded", "private-result")
    async with await store.connect() as c:
        await c.execute("UPDATE outbox SET state='uncertain',error_class='PRIVATE-ERROR'")
    report = await Operations(store).report()
    task = report["tasks"][0]
    assert task["execution_state"] == "succeeded"
    assert task["waiting_for"] == "delivery_reconciliation"
    assert task["delivery"] == {"uncertain": 1}
    assert report["alerts"]["uncertain_deliveries"] == 1
    assert "PRIVATE" not in json.dumps(report)


async def test_operator_cli_reads_only_and_captures_private_cohort(store, tmp_path):
    await store.ingest(100, "bot", [update()])
    claim = await store.claim(uuid4())
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "NIMBUS_LAB_DSN": store.dsn,
        "NIMBUS_TG_TOKEN_FILE": "/does-not-exist",
    }

    config = tmp_path / ".config/nimbus-chat-lab/worker.env"
    config.parent.mkdir(parents=True, mode=0o700)
    config.write_text("NIMBUS_LAB_DSN=" + store.dsn + "\n")
    config.chmod(0o600)

    async def command(*args):
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "nimbus_chat_lab.cli",
            "ops",
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), 20)
            assert proc.returncode == 0, stderr.decode()
            return json.loads(stdout)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    path = tmp_path / "incident.json"
    capture = await command(
        "capture", "--incarnation", str(claim.incarnation), "--output", str(path)
    )
    assert capture == {"captured_tasks": 1, "database_read_only": True}
    report = await command("report", "--cohort", str(path))
    assert report["coverage_complete"] and report["total_tasks"] == 1
    assert report["tasks"][0]["execution_state"] == "running"
    env.pop("NIMBUS_LAB_DSN")
    local_report = await command("--local", "report")
    assert local_report["read_only"] and local_report["total_tasks"] == 1
    assert len(await rows(store, "SELECT id FROM attempts")) == 1


async def test_future_result_is_publication_wait_not_running(store):
    state, _ = await foreground(store)
    _, bg = await job(store, state, "future")
    await store.finish(bg, "succeeded", "NOT SENT")
    async with await store.connect() as c:
        await c.execute(
            "UPDATE schedule_runs SET slot=clock_timestamp()+interval '1 hour' WHERE turn_id=%s",
            (bg.turn_id,),
        )
    report = await Operations(store).report()
    task = next(t for t in report["tasks"] if t["turn_id"] == str(bg.turn_id))
    assert task["waiting_for"] == "publication_time"
    assert task["delivery"] == {}
    assert "NOT SENT" not in json.dumps(report)
