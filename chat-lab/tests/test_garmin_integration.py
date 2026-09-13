import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from conftest import rows
from nimbus.adapters.types import VcpuLLMResponse
from test_agent_mode import actor

from nimbus_chat_lab.agent_engine import AgentEngine
from nimbus_chat_lab.agent_state import AgentState
from nimbus_chat_lab.garmin_client import GarminClient
from nimbus_chat_lab.scheduler import Scheduler
from nimbus_chat_lab.worker import AuthorityLost


async def noop(*args):
    pass


@pytest.mark.parametrize("agent_mode", [False, True])
async def test_health_scope_inherited_and_no_history_activity_schedule_leak(store, agent_mode):
    store.agent_mode = agent_mode
    state, claim = await actor(store)
    s = (
        await state.schedule(
            "create",
            {
                "name": "medical PRIVATE_NAME",
                "instructions": "PRIVATE_INSTRUCTIONS",
                "data_scope": "health",
                "hour": 9,
            },
        )
    )["schedule"]["id"]
    await state.schedule("run_now", {"id": s})
    await store.finish(claim, "succeeded", "PRIVATE_CREATE_ACK")
    scheduler = Scheduler(store)
    await scheduler.tick()
    job = await store.claim(uuid4())
    bg = AgentState(store, job)
    await bg.initialize()
    assert bg.data_scope == "health" and bg.report_slot
    await bg.record_search_event(
        {"request_id": "private", "query": "PRIVATE_SEARCH_QUERY", "outcome": "completed"}
    )
    await store.progress(job, "PRIVATE_PROGRESS")
    await store.finish(job, "succeeded", "PRIVATE_HEALTH_RESULT")
    await scheduler.tick()
    async with await store.connect() as c:
        await c.execute(
            "UPDATE outbox SET state='sent',claimed_at=clock_timestamp() WHERE state='pending'"
        )
    front, claim2 = await actor(store, 2)
    for value in (
        await store.history(claim2),
        await front.activity("list"),
        await front.schedule("list", {}),
    ):
        text = json.dumps(value, default=str)
        assert "PRIVATE_" not in text
    existing = await front.schedule(
        "create", {"name": "medical PRIVATE_NAME", "instructions": "noop"}
    )
    assert "PRIVATE_" not in json.dumps(existing)
    with pytest.raises(ValueError):
        await front.schedule("update", {"id": s, "data_scope": "public"})


async def test_public_background_cannot_enter_health_and_revocation_fences_label(store):
    state, claim = await actor(store)
    s = (await state.schedule("create", {"name": "public", "instructions": "No health"}))[
        "schedule"
    ]["id"]
    await state.schedule("run_now", {"id": s})
    await store.finish(claim, "succeeded", "done")
    await Scheduler(store).tick()
    job = await store.claim(uuid4())
    bg = AgentState(store, job)
    await bg.initialize()
    with pytest.raises(PermissionError):
        await bg.enter_health()
    async with await store.connect() as c:
        await c.execute("DELETE FROM allowlist")
    with pytest.raises((AuthorityLost, PermissionError)):
        await state.enter_health()


@pytest.mark.parametrize("parallel", [True, False])
@pytest.mark.parametrize("reverse", [False, True])
async def test_real_native_parallel_calls_cannot_mix_health_with_search(
    store, tmp_path, monkeypatch, parallel, reverse
):
    from nimbus_chat_lab import agent_engine

    accesses = []
    searches = []
    path = tmp_path / "health.sock"

    async def handler(reader, writer):
        request = json.loads(await reader.readline())
        accesses.append(request["action"])
        writer.write(
            json.dumps(
                {
                    "ok": True,
                    "result": {
                        "schema_version": 1,
                        "kind": "status",
                        "text": "PRIVATE_HEALTH_SENTINEL",
                    },
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handler, path=str(path))

    class Bridge:
        def __init__(self, *args):
            self.n = 0

        async def chat(self, messages, tools=None, on_chunk=None):
            self.n += 1
            if self.n == 1:
                return VcpuLLMResponse(
                    tool_calls=[
                        {
                            "id": "a",
                            "type": "function",
                            "function": {
                                "name": "garmin",
                                "arguments": json.dumps({"action": "status", "args": {}}),
                            },
                        },
                        {
                            "id": "b",
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": json.dumps({"query": "public query", "source": "web"}),
                            },
                        },
                    ][:: -1 if reverse and parallel else 1][: 2 if parallel else 1]
                )
            if self.n == 2 and not parallel:
                return VcpuLLMResponse(
                    tool_calls=[
                        {
                            "id": "c",
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": json.dumps(
                                    {"query": "export PRIVATE_HEALTH_SENTINEL", "source": "web"}
                                ),
                            },
                        }
                    ]
                )
            return VcpuLLMResponse(content="Invented recovery score 99")

        async def search(self, *args, **kwargs):
            searches.append(args)
            return {"text": "public", "sources": []}

    monkeypatch.setattr(agent_engine, "PiBridge", Bridge)
    state, claim = await actor(store)

    async def authority():
        if await store.control(claim, renew=True) != "running":
            raise AuthorityLost()

    try:
        result = await AgentEngine(
            store,
            tmp_path / "attempts",
            "unused",
            {"health": {"socket": str(path), "key": "fake-key"}},
        ).run(claim, [], noop, authority)
        assert not (accesses and searches), "Both channels must never be admitted in the same turn"
        if not parallel:
            assert accesses == ["status"] and not searches
        assert "Invented" not in result and "99" not in result
        if accesses:
            assert result == "PRIVATE_HEALTH_SENTINEL"
        assert (await rows(store, "SELECT data_scope FROM turns WHERE id=%s", (claim.turn_id,)))[0][
            "data_scope"
        ] == "health"
    finally:
        server.close()
        await server.wait_closed()


def test_renderer_does_not_accept_model_scores_or_free_prose():
    c = GarminClient({}, SimpleNamespace(background=True), noop)
    c.daily_receipt = {
        "kind": "daily_brief",
        "text": "事实数值由本地计算\n已验证解释\n建议",
        "observations": {"a": "已验证解释", "b": "另一条已验证解释"},
        "advice": {"x": "建议"},
    }
    for bad in (
        "恢复分99",
        '{"observation_id":"fake","advice_id":"x"}',
        '{"observation_id":"a","advice_id":"x","score":99}',
    ):
        assert c.render(bad) == c.daily_receipt["text"]
    assert "另一条已验证解释" in c.render('{"observation_id":"b","advice_id":"x"}')


async def test_runtime_day_and_call_bounds_before_socket(tmp_path):
    state = SimpleNamespace(
        background=True,
        data_scope="health",
        report_slot=datetime.now(timezone.utc),
        identity=(100, 1, 1),
    )

    async def label():
        pass

    state.enter_health = label
    c = GarminClient({"socket": str(tmp_path / "absent"), "key": "not-exposed"}, state, noop)
    with pytest.raises(ValueError):
        await c.execute("daily_brief", {"day": "2000-01-01"})
    with pytest.raises(ValueError):
        await c.execute("status", {"url": "file:///token"})
    with pytest.raises(ValueError):
        await c.execute("trends", {"days": True})
    for _ in range(4):
        r = await c.execute("status", {})
        assert r["unavailable"]
        assert "not-exposed" not in json.dumps(r) and str(tmp_path) not in json.dumps(r)
    with pytest.raises(ValueError):
        await c.execute("status", {})


async def test_response_is_not_returned_after_authority_loss(tmp_path):
    path = tmp_path / "h.sock"
    calls = 0

    async def handler(reader, writer):
        await reader.readline()
        writer.write(
            b'{"ok":true,"result":{"schema_version":1,"kind":"status","text":"PRIVATE"}}\n'
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handler, path=str(path))

    async def authority():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AuthorityLost()

    async def label():
        pass

    state = SimpleNamespace(
        background=False, data_scope="public", identity=(100, 1, 1), enter_health=label
    )
    c = GarminClient({"socket": str(path), "key": "fake"}, state, authority)
    try:
        with pytest.raises(AuthorityLost):
            await c.execute("status", {})
        assert c.last_receipt is None
    finally:
        server.close()
        await server.wait_closed()
