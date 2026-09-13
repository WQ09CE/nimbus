"""Conservative schema projection and past-only baseline; no invented recovery score."""

import hashlib
import math
from datetime import date, datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from .storage import day_string, now

TZ = ZoneInfo("Asia/Shanghai")
VERSION = "baseline-v0.4"


def number(value, low, high):
    if isinstance(value, bool) or value is None:
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) and low <= value <= high else None


def obj(value):
    return value if isinstance(value, dict) else {}


def gmt(value):
    try:
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            result = datetime.fromtimestamp(value / 1000, timezone.utc)
        elif isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            result = result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
            result = result.astimezone(timezone.utc)
        else:
            return None
        return result if 2000 <= result.year <= 2100 else None
    except (ValueError, OSError, OverflowError):
        return None


def readiness_time(row):
    if "timestampGMT" in row:
        return gmt(row["timestampGMT"])
    raw = row.get("timestamp")
    stamp = gmt(raw)
    if stamp is None:
        return None
    # Mainland responses use timestamp + timestampLocal. For an unzoned string,
    # require the local counterpart to confirm UTC rather than silently guessing.
    try:
        local = row.get("timestampLocal")
        if local is not None:
            local = datetime.fromisoformat(local.replace("Z", "+00:00"))
            if local.tzinfo is None:
                local = local.replace(tzinfo=TZ)
            return stamp if abs((local - stamp).total_seconds()) < 1 else None
        if isinstance(raw, (int, float)):
            return stamp
        return stamp if datetime.fromisoformat(raw.replace("Z", "+00:00")).tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None


def heart_points(payload, expected_day):
    payload = obj(payload)
    if payload.get("calendarDate", expected_day) != expected_day:
        return []
    descriptors = payload.get("heartRateValueDescriptors")
    ti, hi = 0, 1
    if descriptors is not None:
        if not isinstance(descriptors, list):
            return []
        mapping = {v.get("key"): v.get("index") for v in descriptors if isinstance(v, dict)}
        ti, hi = mapping.get("timestamp"), mapping.get("heartrate")
        if any(type(i) is not int or not 0 <= i <= 8 for i in (ti, hi)):
            return []
    values = payload.get("heartRateValues")
    if not isinstance(values, list):
        return []
    result = []
    for row in values:
        if not isinstance(row, list) or len(row) <= max(ti, hi):
            continue
        stamp, hr = gmt(row[ti]), number(row[hi], 20, 220)
        if stamp is not None and hr is not None:
            result.append((stamp, hr))
    return result


def extract(day, records, previous, *, cutoff=None):
    day_string(day)
    cutoff = gmt(cutoff or now())
    flags = []

    def payload(kind, source=records):
        record = source.get(kind, {})
        return record.get("payload") if record.get("status") == "ok" else None

    hrv = obj(payload("hrv"))
    summary = obj(hrv.get("hrvSummary"))
    sleep = obj(payload("sleep"))
    dto = obj(sleep.get("dailySleepDTO"))
    hrv_value = number(summary.get("lastNightAvg"), 1, 500)
    if summary.get("calendarDate") != day:
        hrv_value = None
        flags.append("hrv_date_missing_or_mismatched")
    if hrv_value is None:
        flags.append("night_hrv_missing_or_invalid")
    start, end = gmt(dto.get("sleepStartTimestampGMT")), gmt(dto.get("sleepEndTimestampGMT"))
    seconds = number(dto.get("sleepTimeSeconds"), 1, 20 * 3600)
    valid_sleep = bool(
        start
        and end
        and cutoff
        and start < end <= cutoff
        and end.astimezone(TZ).date().isoformat() == day
        and dto.get("calendarDate") == day
        and seconds is not None
        and seconds <= (end - start).total_seconds() + 60
        and (end - start).total_seconds() <= 20 * 3600
    )
    if not valid_sleep:
        flags.append("sleep_window_missing_or_invalid")
    elif 12 <= (start + (end - start) / 2).astimezone(TZ).hour < 22:
        # Do not interpret a daytime/evening primary episode as an ordinary full night.
        # This may be shift work, travel or a classification issue; retain for review.
        flags.append("primary_sleep_clock_atypical_review")
    if dto.get("sleepWindowConfirmed") is not True:
        flags.append("sleep_not_confirmed")
    if dto.get("sleepFromDevice") is False:
        flags.append("sleep_not_from_device")

    # Cross-check HRV sleep window when the endpoint provides one; never use Local epochs.
    hrv_end = gmt(hrv.get("sleepEndTimestampGMT") or hrv.get("endTimestampGMT"))
    if valid_sleep and hrv_end and abs((hrv_end - end).total_seconds()) > 2 * 3600:
        flags.append("hrv_sleep_windows_disagree")

    night_hr, coverage, sample_count = None, 0.0, 0
    if valid_sleep:
        previous_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        points = heart_points(payload("heart_rate", previous), previous_day)
        points += heart_points(payload("heart_rate"), day)
        points = sorted({t: hr for t, hr in points if start <= t < end}.items())
        sample_count = len(points)
        bins = {}
        for stamp, hr in points:
            key = int((stamp - start).total_seconds() // 600)
            bins.setdefault(key, []).append(hr)
        expected = math.ceil((end - start).total_seconds() / 600)
        coverage = len(bins) / expected
        stamps = [start, *[p[0] for p in points], end]
        max_gap = max((b - a).total_seconds() for a, b in zip(stamps, stamps[1:]))
        if sample_count >= 30 and coverage >= 0.7 and max_gap <= 3600:
            # Equal-weight time bins, not high-frequency sample bursts.
            night_hr = median(median(values) for values in bins.values())
    if night_hr is None:
        flags.append("night_heart_rate_insufficient_coverage")

    rhr = None
    metrics = obj(obj(payload("rhr")).get("allMetrics"))
    values = obj(metrics.get("metricsMap")).get("WELLNESS_RESTING_HEART_RATE", [])
    if isinstance(values, list):
        for row in values:
            if obj(row).get("calendarDate") == day:
                rhr = number(row.get("value"), 20, 220)
    readiness = payload("readiness")
    readiness = [readiness] if isinstance(readiness, dict) else readiness
    morning = []
    if isinstance(readiness, list):
        for row in readiness:
            row = obj(row)
            if (
                row.get("inputContext") == "AFTER_WAKEUP_RESET"
                and row.get("calendarDate", day) == day
                and row.get("validSleep") is not False
            ):
                stamp = readiness_time(row)
                if (
                    stamp
                    and cutoff
                    and stamp <= cutoff
                    and stamp.astimezone(TZ).date().isoformat() == day
                ):
                    value = number(row.get("score"), 0, 100)
                    if value is not None:
                        morning.append((stamp, value, number(row.get("acuteLoad"), 0, 100000)))
    morning_entry = max(morning, key=lambda item: item[0]) if morning else None
    if not morning:
        flags.append("garmin_morning_readiness_not_identified")

    if valid_sleep and seconds < 7 * 3600:
        flags.append("sleep_under_7h_not_personalized_threshold")
    if start and end and hrv_end is None:
        flags.append("hrv_sleep_window_unavailable")
    device = dto.get("deviceId")
    device_source = "sleep_dto" if device is not None else None
    need = obj(dto.get("sleepNeed"))
    if (
        device is None
        and need.get("calendarDate") == day
        and need.get("preferredActivityTracker") is True
        and need.get("deviceId") is not None
    ):
        device = need["deviceId"]
        device_source = "sleep_need_preferred_tracker_proxy"
        flags.append("device_identity_is_preferred_tracker_proxy_not_verified_sleep_sensor")
    device_key = hashlib.sha256(str(device).encode()).hexdigest() if device is not None else None
    eligible = bool(
        valid_sleep
        and hrv_value is not None
        and night_hr is not None
        and dto.get("sleepWindowConfirmed") is True
        and dto.get("sleepFromDevice") is not False
        and "hrv_sleep_windows_disagree" not in flags
        and "primary_sleep_clock_atypical_review" not in flags
    )
    return {
        "day": day,
        "timezone": "Asia/Shanghai",
        "normalizer_version": VERSION,
        "hrv_night_ms": hrv_value,
        "night_heart_rate_bpm": night_hr,
        "night_hr_10min_bin_coverage": round(coverage, 3),
        "night_hr_samples": sample_count,
        "sleep_hours": round(seconds / 3600, 3) if valid_sleep else None,
        "sleep_start": start.astimezone(TZ).isoformat() if valid_sleep else None,
        "sleep_end": end.astimezone(TZ).isoformat() if valid_sleep else None,
        "sleep_score_reference": number(
            obj(obj(dto.get("sleepScores")).get("overall")).get("value"), 0, 100
        ),
        "daily_rhr_reference_only": rhr,
        "garmin_morning_readiness_reference": morning_entry[1] if morning_entry else None,
        "garmin_acute_load_reference": morning_entry[2] if morning_entry else None,
        "device_key": device_key,
        "device_key_source": device_source,
        "sleep_version": dto.get("sleepVersion"),
        "baseline_eligible": eligible,
        "flags": flags,
        "sources": {
            k: {field: r.get(field) for field in ("id", "fetched_at", "status", "digest")}
            for k, r in records.items()
        },
        "previous_day_hr_source": {
            k: previous.get("heart_rate", {}).get(k)
            for k in ("id", "fetched_at", "status", "digest")
        },
    }


def baseline_report(archive, day, *, cutoff=None):
    day_string(day)
    cutoff = cutoff or now()
    target = date.fromisoformat(day)
    cache = {}

    def feature(d):
        for key in (d.isoformat(), (d - timedelta(days=1)).isoformat()):
            if key not in cache:
                cache[key] = archive.latest(key, cutoff)
        return extract(
            d.isoformat(),
            cache[d.isoformat()],
            cache[(d - timedelta(days=1)).isoformat()],
            cutoff=cutoff,
        )

    current = feature(target)
    history = [feature(target - timedelta(days=n)) for n in range(1, 43)]
    usable = [
        r
        for r in history
        if r["baseline_eligible"]
        and r["device_key"] == current["device_key"]
        and r["sleep_version"] == current["sleep_version"]
    ]
    signals = {}
    if len(usable) >= 28 and current["baseline_eligible"]:
        for key, floor, transform in (
            ("hrv_night_ms", 0.05, math.log),
            ("night_heart_rate_bpm", 1.0, float),
            ("sleep_hours", 0.5, float),
        ):
            values = [transform(r[key]) for r in usable]
            center = median(values)
            scale = max(floor, 1.4826 * median(abs(v - center) for v in values))
            z = (transform(current[key]) - center) / scale
            signals[key] = {
                "robust_z": round(z, 3),
                "historical_median": round(median(r[key] for r in usable), 3),
            }
            if abs(z) > 3:
                current["flags"].append(f"unusual_{key}_review_not_automatically_good_or_bad")
    if current["device_key"] is None:
        current["flags"].append("device_identity_unavailable_history_may_mix_devices")
    return {
        "scope": "local_descriptive_baseline_not_medical_or_whoop_model",
        "as_of": cutoff,
        "history_mode": "retrospective_unless_explicit_as_of_capture",
        "current": current,
        "baseline_valid_nights": len(usable),
        "baseline_window_days": 42,
        "minimum_valid_nights": 28,
        "signals": signals,
        "score": None,
        "model_status": "not_fitted_no_validated_target_labels",
        "note": "只报告个人偏离；不把HRV高、心率低或相对多睡直接等同于恢复好。日汇总RHR与佳明分数仅作对照。",
    }
