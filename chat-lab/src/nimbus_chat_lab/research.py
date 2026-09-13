"""Bounded public research admission and safe, model-visible execution receipts.

No queue sits inside the native tool deadline. Saturated batches get a not-sent
receipt; after the batch completes the planner may try again within its budget.
"""

import asyncio
import re
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from uuid import uuid4

from .diagnostics import BridgeFailure, BridgeTimeout
from .worker import AuthorityLost, CancelRequested

LIMIT = 8
CONCURRENCY = 4
HTTP_SECONDS = 175
MODEL_RESERVE = 195
ERRORS = {
    "upstream_http_error",
    "upstream_timeout",
    "upstream_network_error",
    "provider_response_invalid",
    "provider_incomplete",
    "provider_no_search",
    "provider_error",
}


def x_filters(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {
        "from_date",
        "to_date",
        "allowed_x_handles",
        "excluded_x_handles",
    }:
        raise ValueError("Invalid X filters")
    result = {}
    for key, item in value.items():
        if key.endswith("_date"):
            if not isinstance(item, str) or date.fromisoformat(item).isoformat() != item:
                raise ValueError("Invalid date")
            result[key] = item
        else:
            if (
                not isinstance(item, list)
                or not 1 <= len(item) <= 20
                or any(
                    not isinstance(h, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", h)
                    for h in item
                )
            ):
                raise ValueError("Invalid handles")
            result[key] = list(dict.fromkeys(item))
    if result.get("from_date", "0000") > result.get("to_date", "9999"):
        raise ValueError("Reversed dates")
    if "allowed_x_handles" in result and "excluded_x_handles" in result:
        raise ValueError("Conflicting handles")
    return result


def window_filters(window, filters=None):
    """UTC calendar envelope for [start,end), not a claim of second-level filtering.

    Legacy dates remain pass-through. Exact-window callers cannot also supply dates:
    a previous contract incorrectly called provider end dates inclusive whole days.
    """
    filters = x_filters(filters)
    if window is None:
        return filters, None
    if not isinstance(window, dict) or set(window) != {"start", "end"}:
        raise ValueError("Window requires start/end")
    if any(key in filters for key in ("from_date", "to_date")):
        raise ValueError("Conflicting calendar and exact windows")
    stamps = []
    for value in (window["start"], window["end"]):
        if not isinstance(value, str) or len(value) > 40 or "T" not in value:
            raise ValueError("Timestamp required")
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Timezone required")
        stamps.append(stamp.astimezone(timezone.utc))
    start, end = stamps
    if not timedelta(0) < end - start <= timedelta(days=180):
        raise ValueError("Window must span at most 180 days")
    ceiling = end.date() + timedelta(days=end.timetz().replace(tzinfo=None) != time())
    filters.update(from_date=start.date().isoformat(), to_date=ceiling.isoformat())
    return filters, {"start": start.isoformat(), "end": end.isoformat()}


class Research:
    def __init__(self, bridge, state, *, run_timeout=900):
        self.bridge, self.state = bridge, state
        self.deadline = monotonic() + run_timeout
        self.used = self.active = self.events = 0
        self.cooldown_until = 0

    def status(self):
        return {
            "available": getattr(self.state, "data_scope", "public") == "public",
            "request_limit": LIMIT,
            "requests_used": self.used,
            "requests_remaining": max(0, LIMIT - self.used),
            "max_concurrent": CONCURRENCY,
            "active": self.active,
            "provider_cooldown_seconds": max(0, int(self.cooldown_until - monotonic() + 0.999)),
            "cooldown_scope": "this_turn_not_global",
            "queue_policy": "reject_not_wait",
            "http_timeout_seconds": HTTP_SECONDS,
            "native_tool_timeout_seconds": 200,
            "turn_seconds_remaining": max(0, int(self.deadline - monotonic())),
            "final_model_reserve_seconds": MODEL_RESERVE,
            "failed_admitted_requests_consume_budget": True,
            "automatic_retries": 0,
        }

    async def record(self, receipt):
        if self.events >= 32:
            return
        self.events += 1
        try:
            async with asyncio.timeout(0.25):
                await self.state.record_search_event(receipt)
        except (AuthorityLost, CancelRequested, asyncio.CancelledError):
            raise
        except Exception:
            pass  # evidence failure cannot replay a completed external read

    async def run(self, query, source, mode="research", filters=None, window=None):
        if getattr(self.state, "data_scope", "public") != "public":
            return {
                "status": "ERROR",
                "output": {"error": "private_scope_no_search", "dispatched": False},
            }
        if (
            not isinstance(query, str)
            or not 1 <= len(query) <= 12000
            or source not in ("x", "web", "both")
            or mode not in ("discover", "verify", "research")
        ):
            return {
                "status": "ERROR",
                "output": {
                    "error": "invalid_search_arguments",
                    "dispatched": False,
                    "research_budget": self.status(),
                },
            }
        try:
            filters, exact_window = window_filters(window, filters)
            if source == "web" and filters:
                raise ValueError("X filters with web")
            if exact_window:
                query += (
                    "\nRuntime exact post window [start,end): "
                    + exact_window["start"]
                    + " to "
                    + exact_window["end"]
                    + ". Covering UTC keyword dates: since:"
                    + filters["from_date"]
                    + " until:"
                    + filters["to_date"]
                    + ". These bounds supersede prose date calculations. Check exact post "
                    "timestamps; calendar filtering is only a superset, not exact eligibility."
                )
                if len(query) > 12000:
                    raise ValueError("Query plus window exceeds bound")
        except (ValueError, TypeError, OverflowError):
            return {
                "status": "ERROR",
                "output": {
                    "error": "invalid_search_window" if window is not None else "invalid_x_filters",
                    "dispatched": False,
                    "research_budget": self.status(),
                },
            }
        identity = str(uuid4())
        started = monotonic()
        timeout = min(HTTP_SECONDS, self.deadline - started - MODEL_RESERVE - 20)
        base = {
            "request_id": identity,
            "query": query[:500],
            "source": source,
            "mode": mode,
            "x_filters": filters,
            **({"x_window": exact_window} if exact_window else {}),
        }
        code = (
            "search_budget_exhausted"
            if self.used >= LIMIT
            else "search_deadline_budget_exhausted"
            if timeout < 5
            else "search_provider_cooldown"
            if started < self.cooldown_until
            else "search_busy"
            if self.active >= CONCURRENCY
            else None
        )
        if code:
            result = {
                **base,
                "outcome": "rejected",
                "error": code,
                "dispatched": False,
                "duration_ms": 0,
                "retryable": code == "search_busy",
                "research_budget": self.status(),
            }
            await self.record(result)
            return {"status": "ERROR", "output": result}
        # Synchronous reservation before any await: concurrent calls cannot overspend.
        self.used += 1
        self.active += 1
        result = None
        try:
            await self.record(
                {
                    **base,
                    "outcome": "admitted",
                    "dispatched": True,
                    "research_budget": self.status(),
                }
            )
            try:
                result = await self.bridge.search(
                    query, source, mode=mode, timeout_seconds=timeout, x_filters=filters
                )
            except BridgeFailure as exc:
                safe = getattr(exc, "search_error", {})
                code = safe.get("code") if safe.get("code") in ERRORS else "provider_error"
                if isinstance(exc, BridgeTimeout):
                    code = "bridge_timeout"
                result = {
                    **base,
                    "outcome": "error",
                    "error": code,
                    "dispatched": True,
                    "bridge_failure": exc.summary(),
                    "retryable": bool(safe.get("retryable", False)),
                }
                if type(safe.get("http_status")) is int and 100 <= safe["http_status"] <= 599:
                    result["http_status"] = safe["http_status"]
                    if safe["http_status"] == 429:
                        delay = safe.get("retry_after_seconds", 30)
                        delay = max(1, min(3600, delay)) if type(delay) is int else 30
                        self.cooldown_until = max(self.cooldown_until, monotonic() + delay)
                        result["retry_after_seconds"] = delay
                result["duration_ms"] = int((monotonic() - started) * 1000)
                await self.record({**result, "research_budget": self.status()})
                return {
                    "status": "TIMEOUT"
                    if code in ("upstream_timeout", "bridge_timeout")
                    else "ERROR",
                    "output": {**result, "research_budget": self.status()},
                }
            await self.state.record_research(query, source, result)
            await self.record(
                {
                    **base,
                    "outcome": "completed",
                    "dispatched": True,
                    "duration_ms": int((monotonic() - started) * 1000),
                    "quality": result.get("quality", "unknown"),
                    "source_count": result.get("source_count", 0),
                    "research_budget": self.status(),
                }
            )
            return {
                **result,
                "research_request_id": identity,
                **(
                    {
                        "search_window": {
                            **exact_window,
                            "provider_dates": filters,
                            "exact_timestamp_filter_verified": False,
                        }
                    }
                    if exact_window
                    else {}
                ),
                "research_budget": self.status(),
                "duration_ms": int((monotonic() - started) * 1000),
            }
        except asyncio.CancelledError:
            # No fabricated upstream status: cancellation can precede HTTP dispatch.
            try:
                await self.record(
                    {
                        **base,
                        "outcome": "cancelled",
                        "dispatched": True,
                        "duration_ms": int((monotonic() - started) * 1000),
                        "remote_effect": "unknown",
                    }
                )
            except (Exception, asyncio.CancelledError):
                pass
            raise
        finally:
            self.active -= 1
