import asyncio
from datetime import timedelta, timezone
from uuid import uuid4

import pytest
from conftest import rows
from test_agent_mode import actor

from nimbus_chat_lab.agent_state import AgentState
from nimbus_chat_lab.scheduler import Scheduler
from nimbus_chat_lab.worker import AuthorityLost


async def prepared(store):
    state, claim = await actor(store)
    s = (await state.schedule("create", {"name": "review", "instructions": "Reply TEST"}))[
        "schedule"
    ]["id"]
    await state.schedule("run_now", {"id": s})
    await store.finish(claim, "succeeded", "done")
    scheduler = Scheduler(store)
    await scheduler.tick()
    job = await store.claim(uuid4())
    return s, scheduler, job


async def test_revocation_blocks_model_authority_progress_final_and_delivery(store):
    _, claim = await actor(store)
    assert await store.progress(claim, "snapshot")
    async with await store.connect() as c:
        await c.execute("DELETE FROM allowlist")
    assert await store.control(claim, renew=True) is None
    assert not await store.progress(claim, "must not publish")
    assert not await store.finish(claim, "succeeded", "must not publish")
    assert not await store.drafts(100)
    assert not await store.claim_delivery(100)
    assert not await rows(store, "SELECT * FROM outbox WHERE state='pending'")


async def test_revocation_transaction_serializes_request_and_delivery_admission(store):
    _, claim = await actor(store)
    c = await store.connect()
    try:
        await c.execute("DELETE FROM allowlist")
        control = asyncio.create_task(store.control(claim, renew=True))
        delivery = asyncio.create_task(store.claim_delivery(100))
        await asyncio.sleep(0.1)
        assert not control.done() and not delivery.done()
        await c.commit()
        assert await control is None and await delivery is None
    finally:
        await c.close()


async def test_unlinked_legacy_schedule_delivery_fails_closed(store):
    await actor(store)
    async with await store.connect() as c:
        await c.execute("UPDATE outbox SET dedupe_key='scheduled:unknown:0',schedule_run_id=NULL")
    assert await store.claim_delivery(100) is None


async def test_scheduled_lease_is_capped_by_deadline(store):
    _, _, job = await prepared(store)
    async with await store.connect() as c:
        await c.execute(
            "UPDATE schedule_runs SET expires_at=clock_timestamp()+interval '2 seconds' WHERE turn_id=%s",
            (job.turn_id,),
        )
    assert await store.control(job, renew=True) == "running"
    actual = (
        await rows(
            store,
            "SELECT a.lease_until<=r.expires_at AS capped FROM attempts a JOIN schedule_runs r ON r.turn_id=a.turn_id WHERE a.id=%s",
            (job.attempt_id,),
        )
    )[0]
    assert actual["capped"]


async def test_disabled_scheduled_delivery_cannot_be_resurrected_by_429(store):
    sid, scheduler, job = await prepared(store)
    await store.finish(job, "succeeded", "deliverable")
    await scheduler.tick()
    async with await store.connect() as c:
        await c.execute("UPDATE outbox SET state='sent' WHERE schedule_run_id IS NULL")
    sending = await store.claim_delivery(100)
    assert sending and sending["schedule_run_id"]
    state, claim = await actor(store, 2)
    await state.schedule("disable", {"id": sid})
    await store.settle_delivery(sending, "pending", delay=1, error_class="rate_limit")
    actual = (
        await rows(store, "SELECT state,error_class FROM outbox WHERE id=%s", (sending["id"],))
    )[0]
    assert actual == {"state": "failed", "error_class": "authorization_changed"}
    await store.finish(claim, "succeeded", "disabled")


async def test_job_deadline_fences_tools_without_scheduler_tick(store):
    _, _, job = await prepared(store)
    state = AgentState(store, job)
    await state.initialize()
    async with await store.connect() as c:
        await c.execute(
            "UPDATE schedule_runs SET expires_at=clock_timestamp()-interval '1 second' WHERE turn_id=%s",
            (job.turn_id,),
        )
    assert await store.control(job, renew=True) is None
    with pytest.raises(AuthorityLost):
        await state.archive(b"MUST_NOT_COMMIT")
    assert not await store.finish(job, "succeeded", "late result")
    assert not await rows(store, "SELECT * FROM agent_workspaces")


async def test_scheduled_expiry_at_commit_rolls_back_workspace(store):
    _, _, job = await prepared(store)
    state = AgentState(store, job)
    await state.initialize()
    with pytest.raises(AuthorityLost):
        async with state.transaction() as c:
            await c.execute("INSERT INTO agent_workspaces VALUES (100,1,1,'x')")
            await c.execute(
                "UPDATE schedule_runs SET expires_at=clock_timestamp()-interval '1 second' WHERE turn_id=%s",
                (job.turn_id,),
            )
    assert not await rows(store, "SELECT * FROM agent_workspaces")


async def test_multiday_downtime_uses_most_recent_eligible_slot(store):
    state, claim = await actor(store)
    sid = (await state.schedule("create", {"name": "catchup", "instructions": "x"}))["schedule"][
        "id"
    ]
    async with await store.connect() as c:
        now = (await (await c.execute("SELECT clock_timestamp() AS t")).fetchone())["t"]
        recent = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        await c.execute(
            "UPDATE schedules SET timezone='UTC',hour=%s,minute=0,next_run=%s WHERE id=%s",
            (recent.hour, recent - timedelta(days=2), sid),
        )
    await store.finish(claim, "succeeded", "configured")
    await Scheduler(store).tick()
    active = await rows(store, "SELECT slot,state FROM schedule_runs WHERE state='attached'")
    assert active == [{"slot": recent, "state": "attached"}]
    assert (await rows(store, "SELECT next_run FROM schedules"))[0]["next_run"] > now


async def test_replacement_generation_can_reuse_prepared_daily_slot(store):
    state, _ = await actor(store)
    async with await store.connect() as c:
        now = (await (await c.execute("SELECT clock_timestamp() AS t")).fetchone())["t"]
    due = now.astimezone(timezone.utc) + timedelta(minutes=20)
    sid = (
        await state.schedule(
            "create",
            {
                "name": "replace",
                "instructions": "old",
                "timezone": "UTC",
                "hour": due.hour,
                "minute": due.minute,
                "lead_minutes": 30,
            },
        )
    )["schedule"]["id"]
    scheduler = Scheduler(store)
    await scheduler.tick()
    old = (await rows(store, "SELECT slot,generation FROM schedule_runs"))[0]
    await state.schedule("update", {"id": sid, "instructions": "new"})
    await scheduler.tick()
    all_runs = await rows(
        store, "SELECT slot,generation,state FROM schedule_runs ORDER BY generation"
    )
    assert len(all_runs) == 2 and all_runs[0]["state"] == "cancelled"
    assert (
        all_runs[1]["slot"] == old["slot"]
        and all_runs[1]["generation"] == old["generation"] + 1
        and all_runs[1]["state"] == "queued"
    )
