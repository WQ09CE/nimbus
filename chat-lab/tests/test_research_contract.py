import asyncio
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from nimbus.adapters.types import VcpuLLMResponse
from test_agent_mode import actor

from nimbus_chat_lab import agent_engine
from nimbus_chat_lab.agent_engine import AgentEngine
from nimbus_chat_lab.diagnostics import BridgeFailure
from nimbus_chat_lab.research import Research, x_filters


def test_actual_typescript_projection_and_failure_contract():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("search_protocol.mjs"))],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def noop(*args):
    pass


class State:
    def __init__(self):
        self.events = []

    async def record_search_event(self, value):
        self.events.append(value)

    async def record_research(self, *args):
        pass


async def test_native_parallel_searches_have_full_deadlines_and_real_error_status(
    store, tmp_path, monkeypatch
):
    _, claim = await actor(store)
    active = peak = 0
    started = []
    captured = []

    class Bridge:
        def __init__(self, *args):
            self.n = 0
            self.last_failure = None

        async def chat(self, messages, tools=None, on_chunk=None):
            self.n += 1
            if self.n <= 3:
                queries = [f"{self.n}-{i}" for i in range(4 if self.n < 3 else 1)]
                return VcpuLLMResponse(
                    tool_calls=[
                        {
                            "id": q,
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": json.dumps(
                                    {"query": q, "source": "x", "mode": "discover"}
                                ),
                            },
                        }
                        for q in queries
                    ]
                )
            captured.extend(messages)
            return VcpuLLMResponse(content="done")

        async def search(self, query, source, **options):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            started.append(query)
            try:
                await asyncio.sleep(0.15)
                return {"text": "synthetic", "sources": [], "tool_usage": {}}
            finally:
                active -= 1

    config = agent_engine.AgentConfig
    monkeypatch.setattr(
        agent_engine, "AgentConfig", lambda **kw: config(**{**kw, "tool_timeout": 0.4})
    )
    monkeypatch.setattr(agent_engine, "PiBridge", Bridge)
    # Also exercise a foreground registry containing Garmin; do not disable privacy to get concurrency.
    assert (
        await AgentEngine(
            store,
            tmp_path,
            "unused",
            {"health": {"socket": str(tmp_path / "unused.sock"), "key": "k" * 64}},
        ).run(claim, [], noop, noop)
        == "done"
    )
    assert peak == 4 and len(started) == 8
    events = [
        json.loads(line)
        for f in tmp_path.glob("*/session/*.jsonl")
        for line in f.read_text().splitlines()
    ]
    results = [e["data"] for e in events if e["type"] == "action/result"]
    assert [e["status"] for e in results] == ["OK"] * 8 + ["ERROR"]
    assert "search_budget_exhausted" in results[-1]["output_preview"]
    assert "ToolResult(status=" not in str(captured)
    from nimbus_chat_lab.agent_state import AgentState

    state = AgentState(store, claim)
    await state.initialize()
    # All failed/admitted requests produce safe ledger events, not only successes.
    async with await store.connect() as c:
        n = (
            await (
                await c.execute(
                    "SELECT count(*) AS n FROM turn_events WHERE turn_id=%s AND kind='search_attempt'",
                    (claim.turn_id,),
                )
            ).fetchone()
        )["n"]
    assert n == 17


async def test_private_scope_has_no_search_or_query_receipt():
    state = State()
    state.data_scope = "health"
    r = Research(None, state)
    assert not r.status()["available"]
    result = await r.run("PRIVATE_HEALTH", "x")
    assert result["status"] == "ERROR" and not result["output"]["dispatched"]
    assert not state.events and r.used == 0


async def test_saturated_batch_is_not_sent_or_charged():
    entered = 0
    ready = asyncio.Event()

    class Bridge:
        async def search(self, *args, **kwargs):
            nonlocal entered
            entered += 1
            await ready.wait()
            return {"text": "x"}

    state = State()
    r = Research(Bridge(), state)
    calls = [asyncio.create_task(r.run(str(i), "x")) for i in range(4)]
    while entered < 4:
        await asyncio.sleep(0)
    result = await r.run("overflow", "x")
    assert result["status"] == "ERROR" and result["output"]["error"] == "search_busy"
    assert not result["output"]["dispatched"] and r.used == 4
    ready.set()
    await asyncio.gather(*calls)
    assert r.active == 0


@pytest.mark.parametrize(
    "code,http,status",
    [("upstream_http_error", 500, "ERROR"), ("upstream_timeout", None, "TIMEOUT")],
)
async def test_safe_failures_and_budget_are_visible(code, http, status):
    class Bridge:
        async def search(self, *args, **kw):
            e = BridgeFailure("provider_error", uuid4())
            e.search_error = {
                "code": code,
                "retryable": True,
                "http_status": http,
                "raw": "PRIVATE_PROVIDER_BODY",
            }
            raise e

    state = State()
    r = Research(Bridge(), state)
    for _ in range(8):
        result = await r.run("synthetic", "web")
        assert result["status"] == status and result["output"]["error"] == code
    result = await r.run("ninth", "web")
    assert result["output"]["error"] == "search_budget_exhausted"
    assert r.used == 8 and r.active == 0 and "PRIVATE_PROVIDER_BODY" not in str(state.events)


@pytest.mark.parametrize(
    "value",
    [
        {"url": "https://bad.invalid"},
        {"from_date": "2026-02-30"},
        {"from_date": "2026-09-12", "to_date": "2026-09-11"},
        {"allowed_x_handles": ["@OpenAI"]},
        {"allowed_x_handles": ["a"], "excluded_x_handles": ["b"]},
        {"excluded_x_handles": ["a"] * 21},
    ],
)
def test_x_filter_schema_rejects_unknown_invalid_or_conflicting_values(value):
    with pytest.raises(ValueError):
        x_filters(value)


async def test_x_filters_are_real_options_not_only_query_words():
    calls = []

    class Bridge:
        async def search(self, *args, **kw):
            calls.append(kw)
            return {"text": "public"}

    r = Research(Bridge(), State())
    filters = {
        "from_date": "2026-09-11",
        "to_date": "2026-09-12",
        "allowed_x_handles": ["OpenAIDevs"],
    }
    await r.run("explicit timestamp window", "x", "discover", filters)
    assert calls[0]["x_filters"] == filters
    bad = await r.run("verify", "web", "verify", filters)
    assert bad["output"]["error"] == "invalid_x_filters" and r.used == 1


async def test_rate_limit_cooldown_cannot_be_bypassed_by_switching_to_web():
    calls = []

    class Bridge:
        async def search(self, *args, **kw):
            calls.append(args)
            e = BridgeFailure("provider_error", uuid4())
            e.search_error = {
                "code": "upstream_http_error",
                "http_status": 429,
                "retry_after_seconds": 90,
                "retryable": True,
            }
            raise e

    r = Research(Bridge(), State())
    first = await r.run("x", "x")
    assert first["output"]["http_status"] == 429
    second = await r.run("web", "web")
    assert second["output"]["error"] == "search_provider_cooldown"
    assert not second["output"]["dispatched"] and r.used == 1 and len(calls) == 1


async def test_private_bridge_envelope_projects_only_safe_failure_fields(tmp_path):
    import sys

    from nimbus_chat_lab.agent_bridge import PiBridge

    script = tmp_path / "child.py"
    envelope = {
        "nimbus": True,
        "error": "provider_request_failed",
        "failure": {
            "code": "upstream_http_error",
            "http_status": 500,
            "retryable": True,
            "raw": "PRIVATE_DETAIL",
        },
        "diagnostic": {"error": {"message": "PRIVATE_DETAIL"}},
    }
    script.write_text(
        "#!"
        + sys.executable
        + "\nimport sys\nsys.stdin.read()\nprint("
        + repr(json.dumps(envelope))
        + ")\n"
    )
    script.chmod(0o700)
    bridge = PiBridge(tmp_path, noop, str(script))
    with pytest.raises(BridgeFailure) as caught:
        await bridge.search("public", "web")
    assert caught.value.search_error == {
        "code": "upstream_http_error",
        "http_status": 500,
        "retryable": True,
    }
    assert "PRIVATE_DETAIL" not in str(caught.value.summary())
    assert caught.value.diagnostic_saved


async def test_turn_budget_reserves_final_model_and_bounds_http():
    options = []

    class Bridge:
        async def search(self, *args, **kw):
            options.append(kw)
            return {"text": "x"}

    r = Research(Bridge(), State(), run_timeout=250)
    await r.run("one", "x")
    assert 30 < options[0]["timeout_seconds"] < 36
    r.deadline -= 50
    result = await r.run("two", "x")
    assert result["output"]["error"] == "search_deadline_budget_exhausted" and len(options) == 1


async def test_cancellation_never_becomes_search_failure_or_retries():
    entered = asyncio.Event()

    class Bridge:
        async def search(self, *a, **kw):
            entered.set()
            await asyncio.Future()

    state = State()
    r = Research(Bridge(), state)
    task = asyncio.create_task(r.run("query", "x"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert r.active == 0 and r.used == 1 and state.events[-1]["outcome"] == "cancelled"
