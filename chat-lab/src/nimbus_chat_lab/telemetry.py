"""Opt-in, bounded observational receipts. No bodies, tool arguments or credentials.

These events are never authority, checkpoints, or proof of external effects. A missing
finish means unknown, not zero duration or a confirmed stop. Nested spans overlap.
"""

import asyncio
from contextlib import asynccontextmanager
from time import monotonic
from uuid import UUID, uuid4

from .diagnostics import FAILURE_STAGES, BridgeFailure

PHASES = {
    "history",
    "runtime",
    "model",
    "search",
    "workspace",
    "memory",
    "schedule",
    "activity",
    "clock",
    "garmin",
}
USAGE = {"input_tokens", "output_tokens", "cache_read_tokens", "x_search_calls", "web_search_calls"}


def sanitize_receipt(data):
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("Unsupported receipt")
    if data.get("phase") not in PHASES or data.get("event") not in {"start", "finish"}:
        raise ValueError("Invalid phase/event")
    span_id = str(UUID(data["span_id"]))
    result = {"version": 1, "phase": data["phase"], "event": data["event"], "span_id": span_id}
    if data["event"] == "finish":
        duration, outcome = data.get("duration_ms"), data.get("outcome")
        if type(duration) is not int or not 0 <= duration <= 86400000:
            raise ValueError("Invalid duration")
        if outcome not in {"ok", "error", "cancelled"}:
            raise ValueError("Invalid outcome")
        result.update(duration_ms=duration, outcome=outcome)
        usage = data.get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError("Invalid usage")
        result["usage"] = {}
        for key in USAGE & usage.keys():
            value = usage[key]
            if type(value) is not int or not 0 <= value <= 10**12:
                raise ValueError("Invalid usage count")
            result["usage"][key] = value
        failure = data.get("failure")
        if failure is not None:
            if not isinstance(failure, dict) or failure.get("stage") not in FAILURE_STAGES:
                raise ValueError("Invalid failure metadata")
            if type(failure.get("diagnostic_saved")) is not bool:
                raise ValueError("Invalid diagnostic status")
            result["failure"] = {
                "stage": failure["stage"],
                "request_id": str(UUID(failure["request_id"])),
                "diagnostic_saved": failure["diagnostic_saved"],
            }
    return result


class Observer:
    def __init__(self, store, claim, *, enabled=False):
        self.store, self.claim, self.enabled = store, claim, enabled

    async def record(self, receipt):
        if not self.enabled:
            return
        try:
            async with asyncio.timeout(0.25):
                await self.store.record_telemetry(self.claim, receipt)
        except Exception:
            # Observation failure cannot convert a successful external call to a retry.
            # Cancellation is BaseException and must still propagate.
            pass

    @asynccontextmanager
    async def span(self, phase):
        result = {}
        if not self.enabled or phase not in PHASES:
            # A newly added tool without an instrumentation label must still execute.
            yield result
            return
        receipt = {"version": 1, "span_id": str(uuid4()), "phase": phase, "event": "start"}
        await self.record(receipt)
        start, outcome = monotonic(), "ok"
        try:
            yield result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except BaseException as exc:
            outcome = "error"
            if isinstance(exc, BridgeFailure):
                result["failure"] = exc.summary()
            raise
        finally:
            await self.record(
                {
                    **receipt,
                    "event": "finish",
                    "outcome": outcome,
                    "duration_ms": int((monotonic() - start) * 1000),
                    "usage": result.get("usage", {}),
                    **({"failure": result["failure"]} if "failure" in result else {}),
                }
            )


class ObservedBridge:
    """Decorate Nimbus's existing bridge; no Pi auth/model or tool-loop changes."""

    def __init__(self, bridge, observer):
        self.bridge, self.observer = bridge, observer

    async def chat(self, messages, tools=None, on_chunk=None):
        async with self.observer.span("model") as receipt:
            result = await self.bridge.chat(messages, tools=tools, on_chunk=on_chunk)
            try:
                usage = result.usage
                if usage is not None:
                    # Adapter-reported normalized counts, NOT a billing record.
                    receipt["usage"] = {
                        "input_tokens": usage.input,
                        "output_tokens": usage.output,
                        "cache_read_tokens": usage.cache_read,
                    }
            except Exception:
                pass  # Metadata extraction must not turn provider success into retry.
            return result

    async def search(self, query, source="both", **options):
        async with self.observer.span("search") as receipt:
            result = await self.bridge.search(query, source, **options)
            receipt["usage"] = result.get("tool_usage", {})
            return result
