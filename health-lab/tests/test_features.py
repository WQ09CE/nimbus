import copy
from datetime import date, datetime, timedelta

import pytest
from conftest import data_for, insert_day, seed_history

from garmin_readiness.features import baseline_report, extract, number

DAY = "2026-09-10"
CUTOFF = "2026-09-11T12:00:00Z"


def records(day):
    return {
        k: {"status": "ok", "payload": data_for(day, k)}
        for k in ("sleep", "hrv", "heart_rate", "rhr", "readiness")
    }


def extract_fixture(current=None, previous=None):
    return extract(
        DAY,
        current if current is not None else records(DAY),
        previous if previous is not None else records("2026-09-09"),
        cutoff=CUTOFF,
    )


def test_utc8_cross_midnight_and_last_night_not_weekly_average():
    result = extract_fixture()
    assert result["sleep_start"] == "2026-09-09T23:00:00+08:00"
    assert result["sleep_end"] == "2026-09-10T08:00:00+08:00"
    assert result["hrv_night_ms"] == 60
    assert result["night_heart_rate_bpm"] == 52
    assert result["daily_rhr_reference_only"] == 45
    assert result["night_hr_10min_bin_coverage"] == 1
    assert result["baseline_eligible"]
    assert result["garmin_morning_readiness_reference"] == 80


@pytest.mark.parametrize("value", [None, -1, 0, float("nan"), float("inf"), True, "unknown"])
def test_invalid_hrv_never_becomes_zero_or_good_score(value):
    current = records(DAY)
    current["hrv"]["payload"]["hrvSummary"]["lastNightAvg"] = value
    result = extract_fixture(current)
    assert result["hrv_night_ms"] is None and not result["baseline_eligible"]


def test_numeric_string_is_accepted():
    assert number("60", 1, 500) == 60


def test_wrong_date_and_unconfirmed_sleep_refused():
    current = records(DAY)
    current["hrv"]["payload"]["hrvSummary"]["calendarDate"] = "2026-09-09"
    current["sleep"]["payload"]["dailySleepDTO"]["sleepWindowConfirmed"] = False
    assert not extract_fixture(current)["baseline_eligible"]


def test_oversampling_does_not_inflate_night_coverage():
    current = records(DAY)
    heart = current["heart_rate"]["payload"]
    heart["heartRateValues"] = [heart["heartRateValues"][0]] * 10000
    result = extract_fixture(current, {})
    assert result["night_hr_samples"] == 1
    assert result["night_heart_rate_bpm"] is None
    assert not result["baseline_eligible"]


def test_unknown_readiness_context_not_assumed_morning():
    current = records(DAY)
    current["readiness"]["payload"][0]["inputContext"] = None
    result = extract_fixture(current)
    assert result["garmin_morning_readiness_reference"] is None
    assert result["baseline_eligible"]  # Comparison is not a required model input.


@pytest.mark.parametrize(
    "stamp,local,accepted",
    [
        ("2026-09-10T00:00:00.0", "2026-09-10T08:00:00.0", True),
        ("2026-09-10T00:00:00.0", "2026-09-10T07:00:00.0", False),
        ("2026-09-10T00:00:00.0", None, False),
        ("2026-09-10T00:00:00Z", None, True),
    ],
)
def test_cn_readiness_timestamp_pair(stamp, local, accepted):
    current = records(DAY)
    row = current["readiness"]["payload"][0]
    row.pop("timestampGMT")
    row["timestamp"] = stamp
    if local is not None:
        row["timestampLocal"] = local
    # A newer intraday reading must not replace the explicit morning reference.
    current["readiness"]["payload"].append(
        {
            "calendarDate": DAY,
            "inputContext": "AFTER_ACTIVITY",
            "score": 99,
            "timestamp": "2026-09-10T02:00:00Z",
        }
    )
    result = extract_fixture(current)
    assert result["garmin_morning_readiness_reference"] == (80 if accepted else None)


def test_morning_load_reference_and_invalid_sleep():
    current = records(DAY)
    row = current["readiness"]["payload"][0]
    row["acuteLoad"] = 123
    assert extract_fixture(current)["garmin_acute_load_reference"] == 123
    row["validSleep"] = False
    result = extract_fixture(current)
    assert result["garmin_morning_readiness_reference"] is None
    assert result["garmin_acute_load_reference"] is None


def test_daytime_primary_episode_is_not_ordinary_night_baseline():
    current = records(DAY)
    dto = current["sleep"]["payload"]["dailySleepDTO"]
    start = datetime.fromisoformat("2026-09-10T14:00:00+08:00")
    end = datetime.fromisoformat("2026-09-10T18:00:00+08:00")
    dto.update(
        sleepStartTimestampGMT=int(start.timestamp() * 1000),
        sleepEndTimestampGMT=int(end.timestamp() * 1000),
        sleepTimeSeconds=3 * 3600,
    )
    current["hrv"]["payload"]["sleepEndTimestampGMT"] = end.isoformat()
    result = extract_fixture(current)
    assert result["sleep_hours"] == 3  # Retain raw projection for review, do not erase it.
    assert "primary_sleep_clock_atypical_review" in result["flags"]
    assert not result["baseline_eligible"]


def test_incomplete_sleep_not_scored():
    result = extract(DAY, records(DAY), records("2026-09-09"), cutoff="2026-09-09T22:00:00Z")
    assert not result["baseline_eligible"]


def test_baseline_past_only_and_no_invented_total(archive):
    target = date.fromisoformat(DAY)
    seed_history(archive, target)
    result = baseline_report(archive, DAY, cutoff=CUTOFF)
    assert result["baseline_valid_nights"] == 42
    assert result["signals"] and result["score"] is None
    before = copy.deepcopy(result["signals"])
    insert_day(archive, target + timedelta(days=1), hrv=400, hr=190)
    assert baseline_report(archive, DAY, cutoff=CUTOFF)["signals"] == before
    assert (
        baseline_report(archive, DAY, cutoff="2026-09-10T00:00:00Z")["baseline_valid_nights"] == 0
    )


def test_small_sample_reports_insufficient_baseline(archive):
    target = date.fromisoformat(DAY)
    for n in range(5):
        insert_day(archive, target - timedelta(days=n))
    result = baseline_report(archive, DAY, cutoff=CUTOFF)
    assert result["baseline_valid_nights"] < 28
    assert result["signals"] == {} and result["score"] is None


@pytest.mark.parametrize(
    "matches,preferred,accepted", [(True, True, True), (False, True, False), (True, False, False)]
)
def test_sleep_need_device_is_only_a_dated_preferred_tracker_proxy(matches, preferred, accepted):
    current = records(DAY)
    dto = current["sleep"]["payload"]["dailySleepDTO"]
    dto.pop("deviceId")
    dto["sleepNeed"] = {
        "calendarDate": DAY if matches else "2026-09-09",
        "preferredActivityTracker": preferred,
        "deviceId": 456,
    }
    result = extract_fixture(current)
    assert (result["device_key"] is not None) == accepted
    if accepted:
        assert result["device_key_source"] == "sleep_need_preferred_tracker_proxy"
        assert any("proxy_not_verified" in flag for flag in result["flags"])


def test_known_device_change_excludes_old_baseline(archive):
    target = date.fromisoformat(DAY)
    seed_history(archive, target)
    payload = data_for(DAY, "sleep")
    payload["dailySleepDTO"]["deviceId"] = 456
    archive.record(DAY, "sleep", payload, fetched_at="2026-09-11T05:00:00Z")
    assert baseline_report(archive, DAY, cutoff=CUTOFF)["baseline_valid_nights"] == 0
