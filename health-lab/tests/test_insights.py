from copy import deepcopy
from datetime import date, timedelta

import pytest

from garmin_readiness.insights import analyze, collect_rows, predict


def series(n=100):
    first = date(2026, 5, 1)
    rows = []
    for i in range(n):
        day = first + timedelta(days=i)
        # Known synthetic calendar effect, NOT a fitted real-user label.
        burden = (day - timedelta(days=1)).isoweekday() == 2
        rows.append(
            {
                "day": day.isoformat(),
                "segment": "s1",
                "eligible": True,
                "sleep_hours": 6.4 if burden else 8.1,
                "hrv_night_ms": 42.0 if burden else 65.0,
                "night_heart_rate_bpm": 53.0 if burden else 46.0,
            }
        )
    return rows


def test_fixed_calendar_candidate_can_beat_both_simple_baselines():
    rows = series()
    result, _ = analyze(rows, date.fromisoformat(rows[-1]["day"]), habitual_evening_weekday=2)
    group = result["segments"][0]
    for evaluation in group["forecast_evaluation"].values():
        assert evaluation["n"] == 72
        assert evaluation["retrospective_gain_supported"]
        assert not evaluation["prospectively_validated"]
    p = group["habit_pattern"]
    assert p["not_actual_attendance"] and p["n"] >= 12
    assert p["change_from_previous"]["hrv_night_ms"]["decreased"] == p["previous_night_pairs"]
    assert result["forecast"]["score"] is None
    assert not result["historical_as_known_validation"]


def test_prefix_predictions_do_not_use_target_or_future_labels():
    rows = series(80)
    _, before = analyze(rows, date.fromisoformat(rows[-1]["day"]))
    changed = deepcopy(rows)
    for r in changed[55:]:
        r["hrv_night_ms"] = 200.0
    _, after = analyze(changed, date.fromisoformat(rows[-1]["day"]))
    a = before["s1"]["hrv_night_ms"]
    b = after["s1"]["hrv_night_ms"]
    for x, y in zip(a, b):
        if x["day"] <= rows[55]["day"]:
            assert x["predictions"] == y["predictions"]
        assert x["train_last_day"] < x["day"]


def test_new_device_cannot_inherit_old_validation_or_absolute_calibration():
    rows = series(90)
    for r in rows[-10:]:
        r["segment"] = "new"
        r["hrv_night_ms"] = 150.0
    result, _ = analyze(rows, date.fromisoformat(rows[-1]["day"]))
    assert result["forecast"]["training_n"] == 10
    f = result["forecast"]["values"]["hrv_night_ms"]
    assert f["point"] == 150
    assert f["status"] == "simple_reference_not_validated_personal_forecast"
    assert result["segments"][1]["forecast_evaluation"]["hrv_night_ms"]["n"] == 0


def test_quality_missing_current_and_future_rows_do_not_create_forecast():
    rows = series(50)
    rows[-1]["eligible"] = False
    result, _ = analyze(rows, date.fromisoformat(rows[-1]["day"]))
    assert result["forecast"]["values"] == {}
    assert result["excluded_nights"] == 1
    cutoff = date.fromisoformat(rows[35]["day"])
    a, _ = analyze(rows, cutoff)
    b, _ = analyze(rows[:36], cutoff)
    assert a == b


def test_no_gain_not_promoted_and_bad_inputs_rejected():
    rows = series(70)
    for r in rows:
        r.update(sleep_hours=8.0, hrv_night_ms=60.0, night_heart_rate_bpm=48.0)
    result, _ = analyze(rows, date.fromisoformat(rows[-1]["day"]))
    assert not result["segments"][0]["forecast_evaluation"]["sleep_hours"][
        "retrospective_gain_supported"
    ]
    with pytest.raises(ValueError):
        predict(rows, rows[-1]["day"], "sleep_hours")
    with pytest.raises(ValueError):
        predict([{**rows[0], "segment": None}], rows[1]["day"], "sleep_hours")
    with pytest.raises(ValueError):
        analyze(rows + [rows[-1]], date.fromisoformat(rows[-1]["day"]))


def test_collection_respects_actual_fetch_cutoff_and_has_no_raw_identifiers(archive):
    from conftest import insert_day

    target = date(2026, 9, 10)
    insert_day(archive, target - timedelta(days=1), fetched_at="2026-09-10T04:00:00Z")
    insert_day(archive, target, fetched_at="2026-09-11T04:00:00Z")
    old = collect_rows(archive, target, cutoff="2026-09-10T12:00:00Z", days=1)
    current = collect_rows(archive, target, cutoff="2026-09-11T12:00:00Z", days=1)
    assert not old[0]["eligible"] and current[0]["eligible"]
    assert "device_key" not in current[0] and "sources" not in current[0]
    with pytest.raises(Exception):
        collect_rows(archive, target + timedelta(days=3), cutoff="2026-09-11T12:00:00Z")
