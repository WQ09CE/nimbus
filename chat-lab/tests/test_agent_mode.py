import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from conftest import rows, update

from nimbus_chat_lab.agent_state import AgentState, next_slot
from nimbus_chat_lab.scheduler import Scheduler
from nimbus_chat_lab.worker import AuthorityLost


async def actor(store, n=1):
    await store.ingest(100, "bot", [update(n)])
    claim = await store.claim(uuid4())
    state = AgentState(store, claim)
    await state.initialize()
    return state, claim


def test_daily_timezone_and_dst():
    now = datetime(2026, 9, 10, 15, tzinfo=timezone.utc)
    assert next_slot(now, "Asia/Shanghai", 8, 0) == datetime(2026, 9, 11, 0, tzinfo=timezone.utc)
    # New York's missing 02:30 is skipped, not fired at an unintended local time.
    spring = datetime(2026, 3, 8, 6, tzinfo=timezone.utc)
    assert next_slot(spring, "America/New_York", 2, 30).day == 9


async def test_memory_and_identity_bounds(store):
    state, _ = await actor(store)
    await state.memory("set", "pref", "Chinese")
    assert (await state.memory("get", "pref"))["value"] == "Chinese"
    with pytest.raises(ValueError):
        await state.memory("set", "k", "x" * 8001)
    async with await store.connect() as c:
        await c.execute("DELETE FROM allowlist")
    with pytest.raises((AuthorityLost, PermissionError)):
        await state.memory("get", "pref")


async def test_commit_fence_rolls_back_mutation(store):
    state, claim = await actor(store)
    with pytest.raises(AuthorityLost):
        async with state.transaction() as c:
            await c.execute(
                "INSERT INTO agent_memory(bot_id,user_id,chat_id,key,value) VALUES (100,1,1,'should_not_commit','x')"
            )
            await c.execute(
                "UPDATE attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
                (claim.attempt_id,),
            )
    assert not await rows(store, "SELECT * FROM agent_memory")


async def test_schedule_create_dedupe_and_manual_run_idempotency(store):
    state, claim = await actor(store)
    created = await state.schedule(
        "create", {"name": "test", "instructions": "Return a nonce", "hour": 8}
    )
    duplicate = await state.schedule("create", {"name": "test", "instructions": "different"})
    assert created["created"] and not duplicate["created"]
    identifier = created["schedule"]["id"]
    a = await state.schedule("run_now", {"id": identifier})
    b = await state.schedule("run_now", {"id": identifier})
    assert a["id"] == b["id"]
    scheduler = Scheduler(store)
    await scheduler.tick()
    assert (await rows(store, "SELECT state FROM schedule_runs"))[0][
        "state"
    ] == "attached"  # background admission no longer waits for foreground
    await store.finish(claim, "succeeded", "configuration done")
    await asyncio.gather(scheduler.tick(), scheduler.tick())
    assert len(await rows(store, "SELECT * FROM schedule_runs")) == 1
    assert (await rows(store, "SELECT state FROM schedule_runs"))[0]["state"] == "attached"
    job = await store.claim(uuid4())
    assert job and job.turn_id != claim.turn_id
    bg = AgentState(store, job)
    await bg.initialize()
    assert bg.background
    with pytest.raises(PermissionError):
        await bg.schedule("create", {"name": "recursive", "instructions": "No"})
    with pytest.raises(PermissionError):
        await bg.memory("set", "danger", "No")
    await store.finish(job, "succeeded", "REAL_JOB_RESULT")
    await scheduler.tick()
    await scheduler.tick()
    delivered = await rows(store, "SELECT * FROM outbox WHERE dedupe_key LIKE 'scheduled:%'")
    assert len(delivered) == 1 and "REAL_JOB_RESULT" in delivered[0]["text"]
    assert not await rows(
        store, "SELECT * FROM outbox WHERE dedupe_key=%s", (f"final:{job.turn_id}:0",)
    )


async def test_early_collection_holds_publication_without_blocking_chat(store):
    state, claim = await actor(store)
    s = await state.schedule("create", {"name": "early", "instructions": "Result"})
    async with await store.connect() as c:
        await c.execute(
            "UPDATE schedules SET next_run=clock_timestamp()+interval '1 hour',lead_minutes=30 WHERE id=%s",
            (s["schedule"]["id"],),
        )
    scheduler = Scheduler(store)
    await scheduler.tick()
    assert not await rows(store, "SELECT * FROM schedule_runs")
    async with await store.connect() as c:
        await c.execute(
            "UPDATE schedules SET next_run=clock_timestamp()+interval '20 minutes' WHERE id=%s",
            (s["schedule"]["id"],),
        )
    await store.finish(claim, "succeeded", "done")
    await scheduler.tick()
    job = await store.claim(uuid4())
    assert job
    await store.finish(job, "succeeded", "EARLY_RESULT")
    await scheduler.tick()
    assert not await rows(store, "SELECT * FROM outbox WHERE dedupe_key LIKE 'scheduled:%'")
    await store.ingest(100, "bot", [update(2, "normal conversation while report ready")])
    assert len(await rows(store, "SELECT * FROM turns WHERE state='queued'")) == 1
    async with await store.connect() as c:
        await c.execute("UPDATE schedule_runs SET slot=clock_timestamp()-interval '1 second'")
    await scheduler.tick()
    await scheduler.tick()
    assert len(await rows(store, "SELECT * FROM outbox WHERE dedupe_key LIKE 'scheduled:%'")) == 1


async def test_disable_fences_queued_runs_and_pending_delivery(store):
    state, claim = await actor(store)
    s = (await state.schedule("create", {"name": "off", "instructions": "Nothing"}))["schedule"][
        "id"
    ]
    await state.schedule("run_now", {"id": s})
    result = await state.schedule("disable", {"id": s})
    assert not result["enabled"]
    await store.finish(claim, "succeeded", "disabled")
    await Scheduler(store).tick()
    assert not await store.claim(uuid4())
    assert (await rows(store, "SELECT state FROM schedule_runs"))[0]["state"] == "cancelled"


async def test_revocation_and_late_catchup_do_not_execute(store):
    state, claim = await actor(store)
    s = (await state.schedule("create", {"name": "late", "instructions": "Nothing"}))["schedule"][
        "id"
    ]
    async with await store.connect() as c:
        await c.execute(
            "UPDATE schedules SET timezone='UTC',hour=extract(hour FROM timezone('UTC',clock_timestamp()-interval '6 hours')),minute=extract(minute FROM timezone('UTC',clock_timestamp()-interval '6 hours')),next_run=clock_timestamp()-interval '6 hours' WHERE id=%s",
            (s,),
        )
    await store.finish(claim, "succeeded", "done")
    await Scheduler(store).tick()
    assert (await rows(store, "SELECT state FROM schedule_runs"))[0]["state"] == "expired"
    assert not await store.claim(uuid4())
    async with await store.connect() as c:
        await c.execute("DELETE FROM allowlist")
        await c.execute("UPDATE schedules SET next_run=clock_timestamp()")
    await Scheduler(store).tick()
    assert not (await rows(store, "SELECT enabled FROM schedules"))[0]["enabled"]


async def test_interrupted_scheduled_turn_is_not_replayed(store):
    state, claim = await actor(store)
    s = (await state.schedule("create", {"name": "interrupt", "instructions": "Nothing"}))[
        "schedule"
    ]["id"]
    await state.schedule("run_now", {"id": s})
    await store.finish(claim, "succeeded", "queued")
    scheduler = Scheduler(store)
    await scheduler.tick()
    job = await store.claim(uuid4())
    async with await store.connect() as c:
        await c.execute(
            "UPDATE attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
            (job.attempt_id,),
        )
    await store.recover()
    await scheduler.tick()
    await scheduler.tick()
    assert not await store.claim(uuid4())
    notices = await rows(store, "SELECT * FROM outbox WHERE dedupe_key LIKE 'scheduled:%'")
    assert (
        len(notices) == 1
        and "没有自动重跑" in notices[0]["text"]
        and "interrupted" not in notices[0]["text"]
    )


async def test_cross_identity_schedule_access_denied(store):
    state, _ = await actor(store)
    with pytest.raises(ValueError):
        await state.schedule("disable", {"id": str(uuid4())})
    with pytest.raises(ValueError):
        await state.schedule("create", {"name": "bad", "instructions": "x", "hour": True})
    with pytest.raises(ValueError):
        await state.schedule(
            "create", {"name": "bad", "instructions": "x", "command": "host shell"}
        )
