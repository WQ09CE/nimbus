from datetime import date, datetime

from conftest import data_for, seed_history

from garmin_readiness.trends import association, build_trends, clock_stats, stats


def test_clock_across_midnight_not_midday():
    result = clock_stats(["2026-09-01T23:50:00+08:00", "2026-09-03T00:10:00+08:00"])
    assert result["median"] == "00:00"
    assert result["spread_sd_minutes"] == 10


def test_missing_data_and_small_correlation_not_fabricated():
    assert stats([None, float("nan")]) == {"n": 0}
    assert association([{"x": 1, "y": 2}], "x", "y")["pearson_r"] is None


def test_window_counts_and_current_day_exclusion(archive):
    target = date(2026, 9, 10)
    seed_history(archive, target)
    result = build_trends(archive, date(2026, 7, 29), target, cutoff="2026-09-11T12:00:00Z")
    assert result["requested_days"] == 44
    assert result["valid_joint_nights"] == 44
    assert result["periods"]["recent28"]["days"] == 28
    assert result["periods"]["recent7"]["days"] == 7
    assert result["periods"]["all"]["metrics"]["reported_total_sleep_hours"]["n"] == 0
    assert result["current_tracker"] == "tracker_1"
    assert len(result["tracker_segments"]) == 1
    assert result["multi_signal_deviations"] == []
    assert "score" not in result


def test_unconfirmed_sleep_excluded_from_descriptive_sleep_metrics(archive):
    target = date(2026, 9, 10)
    seed_history(archive, target)
    payload = data_for(target.isoformat(), "sleep")
    payload["dailySleepDTO"]["sleepWindowConfirmed"] = False
    archive.record(target.isoformat(), "sleep", payload, fetched_at="2026-09-11T05:00:00Z")
    result = build_trends(archive, target, target, cutoff="2026-09-11T12:00:00Z")
    assert result["periods"]["all"]["metrics"]["sleep_hours"]["n"] == 0
    assert result["periods"]["all"]["metrics"]["night_heart_rate_bpm"]["n"] == 0
    assert result["valid_joint_nights"] == 0


def test_atypical_primary_episode_kept_for_review_not_in_night_statistics(archive):
    target = date(2026, 9, 10)
    seed_history(archive, target)
    start = datetime.fromisoformat("2026-09-10T14:00:00+08:00")
    end = datetime.fromisoformat("2026-09-10T18:00:00+08:00")
    sleep = data_for(target.isoformat(), "sleep")
    sleep["dailySleepDTO"].update(
        sleepStartTimestampGMT=int(start.timestamp() * 1000),
        sleepEndTimestampGMT=int(end.timestamp() * 1000),
        sleepTimeSeconds=3 * 3600,
    )
    hrv = data_for(target.isoformat(), "hrv")
    hrv["sleepEndTimestampGMT"] = end.isoformat()
    for kind, payload in (("sleep", sleep), ("hrv", hrv)):
        archive.record(target.isoformat(), kind, payload, fetched_at="2026-09-11T05:00:00Z")
    result = build_trends(archive, target, target, cutoff="2026-09-11T12:00:00Z")
    assert result["periods"]["all"]["metrics"]["sleep_hours"]["n"] == 0
    assert result["periods"]["all"]["metrics"]["hrv_night_ms"]["n"] == 0
    assert result["unreviewed_primary_episodes"][0]["sleep_hours"] == 3
    assert result["deviation_evaluable_days"] == 0


def test_naps_not_assumed_zero_and_today_not_final(archive):
    target = date(2026, 9, 11)
    seed_history(archive, target)
    for day in ("2026-09-10", "2026-09-11"):
        payload = data_for(day, "sleep")
        payload["dailySleepDTO"]["napTimeSeconds"] = 3600
        archive.record(day, "sleep", payload, fetched_at="2026-09-11T05:00:00Z")
    result = build_trends(archive, date(2026, 9, 10), target, cutoff="2026-09-11T12:00:00Z")
    metrics = result["periods"]["all"]["metrics"]
    assert metrics["reported_total_sleep_hours"] == {
        "n": 1,
        "mean": 9.0,
        "median": 9.0,
        "p10": 9.0,
        "p90": 9.0,
        "min": 9.0,
        "max": 9.0,
    }
    assert metrics["daily_rhr_reference_only"]["n"] == 1
