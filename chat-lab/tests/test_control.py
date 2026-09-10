import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from conftest import rows, update

from nimbus_chat_lab.engine import EchoEngine, NimbusEngine
from nimbus_chat_lab.worker import Worker


async def test_durable_inbox_atomic_batch_and_duplicate_claim(store):
    assert await store.ingest(100, "nimbusbot", [update(2, user=666), update(1)]) == [
        "chat",
        "rejected",
    ]
    assert await store.offset(100) == 3
    assert await store.ingest(100, "nimbusbot", [update(1)]) == ["duplicate"]
    claims = await asyncio.gather(*(store.claim(uuid4()) for _ in range(8)))
    assert sum(c is not None for c in claims) == 1
    assert len(await rows(store, "SELECT * FROM turns")) == 1
    with pytest.raises(ValueError):
        await store.ingest(100, "nimbusbot", [update(3), {"update_id": "bad"}])
    assert await store.offset(100) == 3


async def test_allowlist_exact_chat_and_group_identity(store):
    await store.authorize(100, 1, -20)
    assert await store.ingest(100, "nimbusbot", [update(1, chat=10)]) == ["rejected"]
    assert await store.ingest(100, "nimbusbot", [update(2, chat=-20)]) == ["ignored"]
    text = "😀 @nimbusbot hello"
    assert await store.ingest(
        100,
        "nimbusbot",
        [update(3, text=text, chat=-20, entities=[{"type": "mention", "offset": 3, "length": 10}])],
    ) == ["chat"]
    assert await store.ingest(
        100,
        "nimbusbot",
        [
            update(
                4,
                text="/cancel@nimbusbot",
                chat=-20,
                user=666,
                entities=[{"type": "bot_command", "offset": 0, "length": 17}],
            )
        ],
    ) == ["rejected"]
    assert (await rows(store, "SELECT state FROM turns"))[0]["state"] == "queued"
    assert await store.ingest(
        100, "nimbusbot", [{"update_id": 5, "callback_query": {"data": "approve:anything"}}]
    ) == ["ignored"]


async def test_admission_bound_and_cancel_queued(store):
    store.queue_limit = 1
    await store.authorize(100, 2, 2)
    await store.ingest(100, "nimbusbot", [update(1)])
    assert await store.ingest(100, "nimbusbot", [update(2), update(3, user=2, chat=2)]) == [
        "busy",
        "busy",
    ]
    await store.ingest(100, "nimbusbot", [update(4, "/cancel")])
    assert await store.claim(uuid4()) is None
    assert (await rows(store, "SELECT state FROM turns"))[0]["state"] == "cancelled"
    assert not await rows(store, "SELECT * FROM attempts")


async def test_expiry_fences_renewal_progress_and_late_completion(store):
    await store.ingest(100, "nimbusbot", [update()])
    claim = await store.claim(uuid4())
    assert await store.control(replace(claim, incarnation=uuid4()), renew=True) is None
    await rows(
        store, "UPDATE attempts SET lease_until=clock_timestamp()-interval '1 second' RETURNING id"
    )
    assert await store.control(claim, renew=True) is None
    assert not await store.progress(claim, "zombie")
    assert not await store.finish(claim, "succeeded", "zombie")
    assert await store.recover() == 1
    assert await store.recover() == 0
    assert (await rows(store, "SELECT state,generation FROM turns"))[0] == {
        "state": "interrupted",
        "generation": 2,
    }
    await store.ingest(100, "nimbusbot", [update(2, "followup")])
    newer = await store.claim(uuid4())
    assert newer.turn_id != claim.turn_id
    assert not await store.finish(claim, "failed", "old callback")
    assert await store.finish(newer, "succeeded", "fresh result")
    assert len(await rows(store, "SELECT * FROM outbox WHERE dedupe_key LIKE 'final:%'")) == 2


async def test_completion_cancellation_and_recovery_are_serialized(store):
    await store.ingest(100, "nimbusbot", [update()])
    claim = await store.claim(uuid4())
    await store.ingest(100, "nimbusbot", [update(2, "/cancel")])
    assert await store.control(claim) == "cancel_requested"
    assert not await store.finish(claim, "succeeded", "too late")
    assert await store.finish(claim, "cancelled", "confirmed stopped")
    assert not await store.finish(claim, "cancelled", "again")
    assert await store.recover() == 0
    assert len(await rows(store, "SELECT * FROM outbox WHERE dedupe_key LIKE 'final:%'")) == 1


async def test_worker_echo_and_new_context_boundary(store):
    worker = Worker(store, EchoEngine())
    await store.ingest(100, "nimbusbot", [update()])
    first = await worker.run_once()
    await store.ingest(100, "nimbusbot", [update(2, "second")])
    second = await store.claim(uuid4())
    history = await store.history(second)
    assert history[0]["content"] == "hello"
    assert await store.finish(second, "succeeded", "answer")
    await store.ingest(100, "nimbusbot", [update(3, "/new"), update(4, "new")])
    third = await store.claim(uuid4())
    assert third.session_id == first.session_id and third.epoch == 1
    assert await store.history(third) == []


async def test_worker_cancel_confirms_engine_stopped(store):
    started, stopped = asyncio.Event(), asyncio.Event()

    class SlowEngine:
        async def run(self, claim, history, emit, before_request):
            await before_request()
            started.set()
            try:
                await asyncio.sleep(100)
            finally:
                stopped.set()

    store.lease_seconds = 1
    await store.ingest(100, "nimbusbot", [update()])
    job = asyncio.create_task(Worker(store, SlowEngine()).run_once())
    await asyncio.wait_for(started.wait(), 3)
    await store.ingest(100, "nimbusbot", [update(2, "/cancel")])
    await asyncio.wait_for(job, 5)
    assert stopped.is_set()
    assert (await rows(store, "SELECT state FROM turns"))[0]["state"] == "cancelled"


async def test_real_nimbus_core_mock_model_and_private_logs(store, tmp_path):
    from loguru import logger
    from nimbus.testing.mock_llm import MockLLMAdapter

    logger.disable("nimbus")
    await store.ingest(100, "nimbusbot", [update()])
    claim = await Worker(store, NimbusEngine(tmp_path, adapter_factory=MockLLMAdapter)).run_once()
    result = (await rows(store, "SELECT * FROM turns"))[0]
    assert result["state"] == "succeeded"
    assert "Hello" in result["result"]
    assert list((tmp_path / str(claim.attempt_id) / "session").glob("*.jsonl"))
    assert len(await rows(store, "SELECT * FROM attempts WHERE state='succeeded'")) == 1
