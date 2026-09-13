import asyncio
import json
from uuid import uuid4

import pytest
from conftest import rows, update

from nimbus_chat_lab.operations import Operations
from nimbus_chat_lab.telemetry import Observer, sanitize_receipt


async def claimed(store):
    await store.ingest(100, "bot", [update()])
    return await store.claim(uuid4())


async def test_disabled_observation_is_zero_io(store, monkeypatch):
    claim = await claimed(store)

    async def forbidden(*args, **kwargs):
        raise AssertionError("disabled instrumentation connected")

    monkeypatch.setattr(store, "connect", forbidden)
    async with Observer(store, claim).span("runtime"):
        pass
    async with Observer(store, claim, enabled=True).span("future_uninstrumented_tool"):
        pass


async def test_enabled_observation_records_bounded_sanitized_timing(store):
    claim = await claimed(store)
    async with Observer(store, claim, enabled=True).span("model") as observation:
        observation["usage"] = {"input_tokens": 12, "output_tokens": 3, "prompt": "PRIVATE"}
    events = await rows(store, "SELECT text FROM turn_events WHERE kind='telemetry' ORDER BY seq")
    assert len(events) == 2
    assert "PRIVATE" not in json.dumps(events)
    receipts = (await Operations(store).report())["tasks"][0]["telemetry"]
    assert receipts[0]["event"] == "start"
    assert receipts[1]["event"] == "finish"
    assert receipts[1]["outcome"] == "ok" and receipts[1]["duration_ms"] >= 0
    assert receipts[1]["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert receipts[0]["span_id"] == receipts[1]["span_id"]


async def test_observation_errors_do_not_change_execution_outcome(store, monkeypatch):
    claim = await claimed(store)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("PRIVATE-DSN")

    monkeypatch.setattr(store, "record_telemetry", unavailable)
    async with Observer(store, claim, enabled=True).span("workspace"):
        pass
    with pytest.raises(ValueError, match="original"):
        async with Observer(store, claim, enabled=True).span("workspace"):
            raise ValueError("original")


async def test_cancellation_not_swallowed_and_exception_text_not_logged(store):
    claim = await claimed(store)
    with pytest.raises(asyncio.CancelledError):
        async with Observer(store, claim, enabled=True).span("model"):
            raise asyncio.CancelledError("PRIVATE")
    text = json.dumps(await rows(store, "SELECT text FROM turn_events WHERE kind='telemetry'"))
    assert "cancelled" in text and "PRIVATE" not in text


async def test_telemetry_fenced_and_bounded_separately_from_progress(store):
    claim = await claimed(store)
    receipt = {"version": 1, "phase": "model", "event": "start", "span_id": str(uuid4())}
    for _ in range(256):
        assert await store.record_telemetry(claim, receipt)
    assert not await store.record_telemetry(claim, receipt)
    assert await store.progress(claim, "still visible")
    assert len(await rows(store, "SELECT seq FROM turn_events WHERE kind='text'")) == 1
    async with await store.connect() as c:
        await c.execute("UPDATE attempts SET lease_until=clock_timestamp()-interval '1 second'")
    assert not await store.record_telemetry(claim, receipt)
    assert len(await rows(store, "SELECT seq FROM turn_events WHERE kind='telemetry'")) == 256


async def test_real_agentos_and_worker_emit_phases_with_scripted_provider(
    store, tmp_path, monkeypatch
):
    # Real AgentOS/native tools/PG/Worker; only provider is scripted. No Pi/model call.
    from nimbus.adapters.types import TokenUsage, VcpuLLMResponse

    from nimbus_chat_lab import agent_engine
    from nimbus_chat_lab.worker import Worker

    class ScriptedBridge:
        def __init__(self, *args):
            self.calls = 0

        async def chat(self, messages, tools=None, on_chunk=None):
            self.calls += 1
            if self.calls == 1:
                calls = [
                    {
                        "id": "memory-1",
                        "type": "function",
                        "function": {
                            "name": "memory",
                            "arguments": json.dumps(
                                {"action": "set", "args": {"key": "test", "value": "PRIVATE"}}
                            ),
                        },
                    }
                ]
            elif self.calls == 2:
                calls = [
                    {
                        "id": "clock-2",
                        "type": "function",
                        "function": {"name": "clock", "arguments": "{}"},
                    },
                    {
                        "id": "search-2",
                        "type": "function",
                        "function": {
                            "name": "search",
                            "arguments": json.dumps({"query": "synthetic query", "source": "x"}),
                        },
                    },
                ]
            else:
                calls = []
            return VcpuLLMResponse(
                content="finished" if not calls else "",
                tool_calls=calls,
                usage=TokenUsage(input=12, output=3, cache_read=0),
            )

        async def search(self, query, source, **options):
            return {
                "text": "SYNTHETIC RESEARCH",
                "sources": [],
                "tool_usage": {"x_search_calls": 1},
            }

    monkeypatch.setattr(agent_engine, "PiBridge", ScriptedBridge)
    store.agent_mode = True
    await store.ingest(100, "bot", [update()])
    engine = agent_engine.AgentEngine(store, tmp_path, "unused", {}, observe=True)
    claim = await Worker(store, engine, observe=True).run_once()
    assert (await rows(store, "SELECT state FROM turns"))[0]["state"] == "succeeded"
    task = (await Operations(store).report())["tasks"][0]
    assert task["attempt_id"] == str(claim.attempt_id)
    finishes = [r for r in task["telemetry"] if r["event"] == "finish"]
    assert {r["phase"] for r in finishes} == {
        "history",
        "runtime",
        "model",
        "memory",
        "clock",
        "search",
    }
    assert sum(r["usage"].get("input_tokens", 0) for r in finishes) == 36
    assert sum(r["usage"].get("x_search_calls", 0) for r in finishes) == 1
    assert "PRIVATE" not in json.dumps(task) and "SYNTHETIC RESEARCH" not in json.dumps(task)


def test_receipt_validation_does_not_export_arbitrary_keys():
    receipt = {
        "version": 1,
        "phase": "model",
        "event": "finish",
        "span_id": str(uuid4()),
        "outcome": "ok",
        "duration_ms": 4,
        "prompt": "PRIVATE",
        "usage": {"input_tokens": True},
    }
    with pytest.raises(ValueError):
        sanitize_receipt(receipt)
    receipt["usage"] = {"input_tokens": 7, "secret": "PRIVATE"}
    assert "PRIVATE" not in json.dumps(sanitize_receipt(receipt))
    receipt["phase"] = "PRIVATE"
    with pytest.raises(ValueError):
        sanitize_receipt(receipt)
