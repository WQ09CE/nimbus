import asyncio
import json
import subprocess
import sys
from datetime import date

import pytest
from conftest import data_for, insert_day, seed_history

from garmin_readiness.brief import daily, projection, render_daily, request_args
from garmin_readiness.gateway import Gateway, atomic_json
from garmin_readiness.storage import Archive, LocalError


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("garmin_readiness.brief.now", lambda: "2026-09-11T04:05:00+00:00")
    monkeypatch.setattr("garmin_readiness.gateway.now", lambda: "2026-09-11T04:05:00+00:00")


def test_minimal_projection_and_local_provenance(archive):
    target = date(2026, 9, 11)
    seed_history(archive, target)
    raw = data_for(target.isoformat(), "sleep")
    raw["userProfilePK"] = "PRIVATE_PROFILE_SENTINEL"
    archive.record(target.isoformat(), "sleep", raw, fetched_at="2026-09-11T04:01:00+00:00")
    report = daily(archive, target, cutoff="2026-09-11T04:05:00+00:00")
    assert report["source_status"] == "complete"
    assert report["baseline_status"] == "available"
    assert report["score"] is None
    for forbidden in (
        "PRIVATE_PROFILE_SENTINEL",
        "deviceId",
        "device_key",
        "digest",
        "heartRateValues",
    ):
        assert forbidden not in json.dumps(report)
    with archive.connect() as c:
        saved = json.loads(
            c.execute("SELECT body FROM reports ORDER BY id DESC LIMIT 1").fetchone()[0]
        )
    assert saved["history_manifest"] and saved["snapshot_id"] == report["snapshot_id"]
    assert (
        render_daily(report, {"observation_id": "FAKE_SCORE_99", "advice_id": "FAKE"})
        == report["text"]
    )


@pytest.mark.parametrize("hrv,hr,flag", [(59, 53, False), (45, 57, True)])
def test_joint_hint_ignores_small_mixed_fluctuations(archive, hrv, hr, flag):
    day = date(2026, 9, 11)
    seed_history(archive, day)
    insert_day(archive, day, hrv=hrv, hr=hr, fetched_at="2026-09-11T04:01:00+00:00")
    r = daily(archive, day, cutoff="2026-09-11T04:05:00+00:00")
    assert ("joint_shift" in r["observations"]) == flag
    assert r["score"] is None


def test_history_is_not_advice_about_current_recovery(archive):
    day = date(2026, 9, 10)
    seed_history(archive, day)
    r = daily(archive, day, cutoff="2026-09-11T04:05:00+00:00")
    assert r["history_mode"] == "retrospective"
    assert r["text"].startswith("历史身体记录")
    assert "今天" not in r["text"] and "今日" not in r["text"]
    assert "不据此判断你现在" in r["text"]


def test_device_change_no_false_baseline_or_comparison(archive):
    day = date(2026, 9, 11)
    seed_history(archive, day)
    raw = data_for(day.isoformat(), "sleep")
    raw["dailySleepDTO"]["deviceId"] = 456
    archive.record(day.isoformat(), "sleep", raw, fetched_at="2026-09-11T04:01:00+00:00")
    r = daily(archive, day, cutoff="2026-09-11T04:05:00+00:00")
    assert r["baseline_valid_nights"] == 0
    assert r["comparison_kind"] == "unavailable"
    assert not r["comparison"]


def test_stale_missing_unconfirmed_not_today_assessment(archive):
    day = date(2026, 9, 11)
    r = daily(archive, day, cutoff="2026-09-11T04:05:00+00:00")
    assert r["source_status"] == "pending_sync" and "HRV " not in r["text"]
    seed_history(archive, day)
    r = daily(archive, day, cutoff="2026-09-11T05:00:00+00:00")
    assert r["source_status"] == "stale" and "HRV " not in r["text"]
    raw = data_for(day.isoformat(), "sleep")
    raw["dailySleepDTO"]["sleepWindowConfirmed"] = False
    archive.record(day.isoformat(), "sleep", raw, fetched_at="2026-09-11T04:01:00+00:00")
    r = daily(archive, day, cutoff="2026-09-11T04:05:00+00:00")
    assert r["source_status"] == "partial" and "HRV " not in r["text"]


def test_context_is_whitelisted_habit_not_verified_event(archive):
    day = date(2026, 9, 11)
    seed_history(archive, day)
    atomic_json(
        archive.root / "user_context.json",
        {
            "habitual_activity": {
                "weekday_iso": 4,
                "heart_rate_monitor_worn": False,
                "private_note": "DO NOT EXPORT THIS",
            },
            "token": "secret",
        },
    )
    r = daily(archive, day, cutoff="2026-09-11T04:05:00+00:00")
    assert r["context"]["previous_evening_matches_reported_habit"]
    assert not r["context"]["actual_attendance_confirmed"]
    assert "habit" in r["observations"]
    assert "DO NOT EXPORT" not in json.dumps(r) and "secret" not in json.dumps(r)


@pytest.mark.parametrize(
    "action,args",
    [
        ("get_all", {}),
        ("daily_brief", {"url": "https://example.com"}),
        ("daily_brief", {"day": "../../tokens"}),
        ("daily_brief", {"day": "2026-09-12"}),
        ("trends", {"days": True}),
        ("trends", {"days": 180}),
        ("status", {"account": "other"}),
        ("method", {"path": "/etc/passwd"}),
    ],
)
def test_arguments_are_not_a_generic_connector(action, args):
    with pytest.raises((LocalError, ValueError)):
        request_args(action, args)


def test_trends_projection_has_no_day_by_day_or_identity(archive):
    seed_history(archive, date(2026, 9, 11))
    r = projection(archive, "trends", {"days": 28})
    assert r["kind"] == "trends" and "latest14" not in r
    assert "device_key" not in json.dumps(r)


def test_worker_real_process_safe_errors_no_network(tmp_path):
    root = tmp_path / "account"
    a = Archive(root)
    with a.lock():
        a.bind("a" * 64)
    request = {
        "root": str(root),
        "account": "b" * 64,
        "refresh": False,
        "action": "method",
        "args": {},
    }
    p = subprocess.run(
        [sys.executable, "-m", "garmin_readiness.gateway", "--worker"],
        input=json.dumps(request).encode(),
        capture_output=True,
        timeout=10,
    )
    assert json.loads(p.stdout) == {"ok": False, "error": "different_account_refused"}
    assert not p.stderr
    request["account"] = "a" * 64
    p = subprocess.run(
        [sys.executable, "-m", "garmin_readiness.gateway", "--worker"],
        input=json.dumps(request).encode(),
        capture_output=True,
        timeout=10,
    )
    assert json.loads(p.stdout)["result"]["kind"] == "method"
    assert not list(a.tokens.iterdir())


def test_required_refresh_does_not_join_an_offline_projection(tmp_path):
    async def go():
        g = Gateway(
            {
                "root": str(tmp_path / "private"),
                "identity": [100, 1, 1],
                "key": "k" * 64,
                "account": "a" * 64,
            }
        )
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def run(action, args, refresh, key):
            calls.append(refresh)
            if not refresh:
                entered.set()
                await release.wait()
            return {
                "ok": True,
                "result": {"kind": "daily_brief", "text": "safe", "refreshed": refresh},
            }

        g.run_child = run
        req = {
            "identity": [100, 1, 1],
            "key": "k" * 64,
            "action": "daily_brief",
            "args": {},
            "refresh": False,
        }
        foreground = asyncio.create_task(g.dispatch(req))
        await entered.wait()
        background = asyncio.create_task(g.dispatch({**req, "refresh": True}))
        await asyncio.sleep(0)
        release.set()
        await foreground
        assert (await background)["result"]["refreshed"]
        assert calls == [False, True]

    asyncio.run(go())


def test_cached_prior_day_is_explicitly_retrospective(tmp_path):
    from garmin_readiness.brief import daily

    a = Archive(tmp_path / "private")
    with a.lock():
        seed_history(a, date(2026, 9, 10))
        prior = daily(a, date(2026, 9, 10), cutoff="2026-09-10T04:05:00+00:00")
    g = Gateway(
        {"root": str(a.root), "identity": [100, 1, 1], "key": "k" * 64, "account": "a" * 64}
    )
    atomic_json(g.cache / "prior.json", {"ok": True, "result": prior})
    r = g.cached("prior")["result"]
    assert r["cache_only"] and r["source_status"] == "stale"
    assert r["text"].startswith("历史身体记录")
    assert "今天" not in r["text"] and "今日" not in r["text"]


def test_rpc_auth_singleflight_and_durable_cooldown(tmp_path):
    async def go():
        cfg = {
            "root": str(tmp_path / "private"),
            "identity": [100, 1, 1],
            "key": "k" * 64,
            "account": "a" * 64,
        }
        g = Gateway(cfg)
        calls = []
        release = asyncio.Event()

        async def run(*args):
            calls.append(args)
            await release.wait()
            return {"ok": True, "result": {"kind": "status", "text": "safe"}}

        g.run_child = run
        request = {
            "identity": [100, 1, 1],
            "key": "wrong",
            "action": "daily_brief",
            "args": {},
            "refresh": True,
        }
        assert (await g.dispatch(request))["error"] == "unauthorized"
        request["key"] = cfg["key"]
        first = asyncio.create_task(g.dispatch(request))
        await asyncio.sleep(0)
        second = asyncio.create_task(g.dispatch(request))
        await asyncio.sleep(0)
        assert len(calls) == 1
        # Cancelling one waiter does not cancel the shared account read.
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        assert (await second)["ok"]
        atomic_json(g.cache / "refresh-state.json", {"next_allowed": "2026-09-11T05:00:00+00:00"})
        assert not Gateway(cfg).can_refresh()

    asyncio.run(go())
