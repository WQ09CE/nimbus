import asyncio
from uuid import uuid4

import pytest
from conftest import rows, update

from nimbus_chat_lab.agent_state import AgentState
from nimbus_chat_lab.scheduler import Scheduler


async def foreground(store, n=1, text="hello"):
    store.agent_mode = True
    await store.ingest(100, "bot", [update(n, text)])
    claim = await store.claim(uuid4(), lane="chat")
    state = AgentState(store, claim)
    await state.initialize()
    return state, claim


async def job(store, state, name):
    sid = (await state.schedule("create", {"name": name, "instructions": "test"}))["schedule"]["id"]
    await state.schedule("run_now", {"id": sid})
    await Scheduler(store).tick()
    return sid, await store.claim(uuid4(), lane="jobs")


async def test_agent_chat_has_no_debug_ack_or_final_prefix(store):
    _, claim = await foreground(store)
    assert not await rows(store, "SELECT * FROM outbox")
    await store.finish(claim, "succeeded", "你好，今天想做什么？")
    notices = await rows(store, "SELECT text FROM outbox")
    assert notices == [{"text": "你好，今天想做什么？"}]


async def test_foreground_messages_are_buffered_and_claimed_serially(store):
    store.agent_mode = True
    assert (
        await store.ingest(
            100, "bot", [update(1, "first"), update(2, "second"), update(3, "third")]
        )
        == ["chat"] * 3
    )
    claims = await asyncio.gather(*(store.claim(uuid4(), lane="chat") for _ in range(6)))
    first = next(c for c in claims if c)
    assert len([c for c in claims if c]) == 1 and first.input == "first"
    assert await store.claim(uuid4(), lane="chat") is None
    await store.finish(first, "succeeded", "first answer")
    second = await store.claim(uuid4(), lane="chat")
    assert second.input == "second"
    assert await store.history(second) == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first answer"},
    ]


async def test_two_background_jobs_and_conversation_run_independently(store):
    state, chat = await foreground(store)
    _, a = await job(store, state, "one")
    _, b = await job(store, state, "two")
    assert a and b and len({a.session_id, b.session_id, chat.session_id}) == 3
    assert await store.control(chat) == "running"
    assert await store.control(a) == "running" and await store.control(b) == "running"
    assert await store.typing_chats(100) == [{"chat_id": 1, "thread_id": 0}]
    await store.finish(chat, "succeeded", "configured")
    _, followup = await foreground(store, 2, "进度如何")
    assert followup and await store.control(a) == "running"


async def test_workspaces_are_separate_but_memory_is_shared_across_lanes(store):
    state, _ = await foreground(store)
    await state.memory("set", "preference", "Chinese")
    await state.archive(b"chat-workspace")
    _, claim = await job(store, state, "isolated")
    bg = AgentState(store, claim)
    await bg.initialize()
    assert await bg.archive() == b""
    assert (await bg.memory("get", "preference"))["value"] == "Chinese"
    await bg.archive(b"job-workspace")
    assert await state.archive() == b"chat-workspace"


async def test_activity_has_real_research_and_cancel_keeps_schedule(store):
    state, _ = await foreground(store)
    sid, claim = await job(store, state, "research")
    bg = AgentState(store, claim)
    await bg.initialize()
    await bg.record_research(
        "real query",
        "x",
        {
            "text": "found two verifiable events",
            "sources": ["https://x.com/a/status/123"],
            "tool_usage": {"x_search_calls": 2},
        },
    )
    # Text snapshots must not push research receipts out of the inspection window.
    for n in range(10):
        await store.progress(claim, f"progress {n}")
    info = await state.activity()
    assert info[0]["name"] == "research" and info[0]["research"][0]["query"] == "real query"
    assert info[0]["research"][0]["tool_usage"]["x_search_calls"] == 2
    assert info[0]["progress"] == "progress 9"
    with pytest.raises(PermissionError):
        await bg.activity("cancel", info[0]["run_id"])
    cancelled = await state.activity("cancel", info[0]["run_id"])
    assert cancelled["requested"] and await store.control(claim) == "cancel_requested"
    assert (await rows(store, "SELECT enabled FROM schedules WHERE id=%s", (sid,)))[0]["enabled"]


async def test_unattached_queued_run_can_be_cancelled_without_disabling_schedule(store):
    state, _ = await foreground(store)
    sid = (await state.schedule("create", {"name": "waiting", "instructions": "test"}))["schedule"][
        "id"
    ]
    run = await state.schedule("run_now", {"id": sid})
    assert run["turn_id"] is None
    assert (await state.activity("cancel", run["id"]))["requested"]
    await Scheduler(store).tick()
    assert await store.claim(uuid4(), lane="jobs") is None
    actual = (
        await rows(store, "SELECT state,turn_id FROM schedule_runs WHERE id=%s", (run["id"],))
    )[0]
    assert actual == {"state": "cancelled", "turn_id": None}
    assert (await rows(store, "SELECT enabled FROM schedules WHERE id=%s", (sid,)))[0]["enabled"]


async def test_published_report_is_in_context_only_after_send_and_new_resets_it(store):
    state, chat = await foreground(store)
    _, claim = await job(store, state, "daily")
    await store.finish(chat, "succeeded", "configured")
    await store.finish(claim, "succeeded", "REPORT_TWO_EVENTS")
    await Scheduler(store).tick()
    _, question = await foreground(store, 2, "为什么只有两条")
    assert "REPORT_TWO_EVENTS" not in str(await store.history(question))
    async with await store.connect() as c:
        await c.execute(
            "UPDATE outbox SET state='sent',claimed_at=clock_timestamp() WHERE schedule_run_id IS NOT NULL"
        )
    assert "REPORT_TWO_EVENTS" in str(await store.history(question))
    await store.finish(question, "succeeded", "answered")
    await store.ingest(100, "bot", [update(3, "/new")])
    _, fresh = await foreground(store, 4, "hello")
    assert await store.history(fresh) == []


async def test_status_and_single_background_cancel_are_natural(store):
    state, chat = await foreground(store)
    sid, claim = await job(store, state, "AI 日报")
    await store.finish(chat, "succeeded", "done")
    await store.ingest(100, "bot", [update(2, "/status"), update(3, "/cancel")])
    text = "\n".join(r["text"] for r in await rows(store, "SELECT text FROM outbox"))
    assert "AI 日报：正在执行" in text and "尚未确认" in text
    assert not any(s in text for s in ("queued", "succeeded", "running", "cancel_requested"))
    assert await store.control(claim) == "cancel_requested"
    assert (await rows(store, "SELECT enabled FROM schedules WHERE id=%s", (sid,)))[0]["enabled"]
