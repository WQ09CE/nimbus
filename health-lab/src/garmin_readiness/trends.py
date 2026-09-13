"""Retrospective descriptive trends; associations are not causal/clinical findings."""

import math
from collections import Counter
from datetime import date, datetime, timedelta
from statistics import mean, median, pstdev

from .features import TZ, VERSION, extract, gmt, number, obj
from .storage import LocalError, now

METRICS = (
    "hrv_night_ms",
    "night_heart_rate_bpm",
    "daily_rhr_reference_only",
    "sleep_hours",
    "garmin_morning_readiness_reference",
    "garmin_acute_load_reference",
    "sleep_score_reference",
    "nap_hours",
    "reported_total_sleep_hours",
    "sleep_respiration_per_min",
    "sleep_spo2_percent",
    "sleep_stress_reference",
    "sleep_body_battery_change_reference",
)


def quantile(values, q):
    values = sorted(values)
    place = (len(values) - 1) * q
    low, high = math.floor(place), math.ceil(place)
    return values[low] + (values[high] - values[low]) * (place - low)


def stats(values):
    values = [v for v in values if v is not None and math.isfinite(v)]
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(mean(values), 2),
        "median": round(median(values), 2),
        "p10": round(quantile(values, 0.1), 2),
        "p90": round(quantile(values, 0.9), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def clock_stats(timestamps):
    minutes = [d.hour * 60 + d.minute for t in timestamps if t and (d := datetime.fromisoformat(t))]
    if not minutes:
        return {"n": 0}
    sine = mean(math.sin(2 * math.pi * m / 1440) for m in minutes)
    cosine = mean(math.cos(2 * math.pi * m / 1440) for m in minutes)
    if math.hypot(sine, cosine) < 0.2:
        return {"n": len(minutes), "diffuse_clock_times": True}
    center = math.atan2(sine, cosine) * 1440 / (2 * math.pi)
    unwrapped = [center + (m - center + 720) % 1440 - 720 for m in minutes]

    def clock(value):
        minute = round(value) % 1440
        return f"{minute // 60:02d}:{minute % 60:02d}"

    return {
        "n": len(minutes),
        "median": clock(median(unwrapped)),
        "p10": clock(quantile(unwrapped, 0.1)),
        "p90": clock(quantile(unwrapped, 0.9)),
        "spread_sd_minutes": round(pstdev(unwrapped), 1),
    }


def aggregate(rows):
    nights = [r["sleep_hours"] for r in rows if r.get("sleep_hours") is not None]
    total = [
        r["reported_total_sleep_hours"]
        for r in rows
        if r.get("reported_total_sleep_hours") is not None
    ]
    return {
        "days": len(rows),
        "metrics": {key: stats([r.get(key) for r in rows]) for key in METRICS},
        "night_sleep_counts": {
            "valid": len(nights),
            "under_6h": sum(x < 6 for x in nights),
            "under_7h": sum(x < 7 for x in nights),
            "at_least_7h": sum(x >= 7 for x in nights),
        },
        "reported_total_sleep_counts": {"valid": len(total), "under_7h": sum(x < 7 for x in total)},
        "bedtime": clock_stats([r.get("sleep_start") for r in rows]),
        "wake_time": clock_stats([r.get("sleep_end") for r in rows]),
    }


def association(rows, x, y):
    pairs = [(r.get(x), r.get(y)) for r in rows if r.get(x) is not None and r.get(y) is not None]
    if len(pairs) < 20:
        return {"n": len(pairs), "pearson_r": None}
    xs, ys = zip(*pairs)
    xm, ym = mean(xs), mean(ys)
    denom = math.sqrt(sum((v - xm) ** 2 for v in xs) * sum((v - ym) ** 2 for v in ys))
    return {
        "n": len(pairs),
        "pearson_r": round(sum((x - xm) * (y - ym) for x, y in pairs) / denom, 3)
        if denom
        else None,
    }


def windows(rows, start, end):
    spans = {
        "all": (start, end),
        "first28": (start, min(end, start + timedelta(days=27))),
        "prior28": (end - timedelta(days=55), end - timedelta(days=28)),
        "recent28": (end - timedelta(days=27), end),
        "prior7": (end - timedelta(days=13), end - timedelta(days=7)),
        "recent7": (end - timedelta(days=6), end),
    }
    return {
        key: {
            "from": a.isoformat(),
            "to": b.isoformat(),
            **aggregate([r for r in rows if a.isoformat() <= r["day"] <= b.isoformat()]),
        }
        for key, (a, b) in spans.items()
    }


def build_trends(archive, start, end, *, cutoff=None):
    if not 1 <= (end - start).days + 1 <= 180:
        raise LocalError("date_range_bound")
    cutoff = cutoff or now()
    stamp = gmt(cutoff)
    if stamp is None:
        raise LocalError("invalid_cutoff")
    captured_day = stamp.astimezone(TZ).date()
    cache = {}
    rows = []
    unreviewed_primary_episodes = []
    tags = {}
    for offset in range((end - start).days + 1):
        d = start + timedelta(days=offset)
        for day in (d - timedelta(days=1), d):
            key = day.isoformat()
            if key not in cache:
                cache[key] = archive.latest(key, cutoff)
        records = cache[d.isoformat()]
        row = extract(
            d.isoformat(), records, cache[(d - timedelta(days=1)).isoformat()], cutoff=cutoff
        )
        if "primary_sleep_clock_atypical_review" in row["flags"]:
            unreviewed_primary_episodes.append(
                {
                    key: row[key]
                    for key in (
                        "day",
                        "sleep_hours",
                        "sleep_start",
                        "sleep_end",
                        "garmin_morning_readiness_reference",
                    )
                }
            )
            for field in (
                "hrv_night_ms",
                "garmin_morning_readiness_reference",
                "garmin_acute_load_reference",
            ):
                row[field] = None
        if any(
            flag in row["flags"]
            for flag in (
                "sleep_not_confirmed",
                "sleep_not_from_device",
                "primary_sleep_clock_atypical_review",
            )
        ):
            for field in (
                "sleep_hours",
                "sleep_start",
                "sleep_end",
                "night_heart_rate_bpm",
                "sleep_score_reference",
            ):
                row[field] = None
        if "hrv_sleep_windows_disagree" in row["flags"]:
            row["hrv_night_ms"] = None
        sleep = (
            obj(records.get("sleep", {}).get("payload"))
            if records.get("sleep", {}).get("status") == "ok"
            else {}
        )
        dto = obj(sleep.get("dailySleepDTO"))
        # Additional quantities are descriptive only, never extra independent recovery votes.
        if dto.get("calendarDate") != d.isoformat() or row["sleep_hours"] is None:
            dto, sleep = {}, {}
        nap = number(dto.get("napTimeSeconds"), 0, 12 * 3600)
        row["nap_hours"] = nap / 3600 if nap is not None and d < captured_day else None
        row["reported_total_sleep_hours"] = (
            row["sleep_hours"] + row["nap_hours"]
            if row["sleep_hours"] is not None and row["nap_hours"] is not None
            else None
        )
        row["sleep_respiration_per_min"] = number(dto.get("averageRespirationValue"), 5, 40)
        row["sleep_spo2_percent"] = number(dto.get("averageSpO2Value"), 50, 100)
        row["sleep_stress_reference"] = number(dto.get("avgSleepStress"), 0, 100)
        row["sleep_body_battery_change_reference"] = number(
            sleep.get("bodyBatteryChange"), -100, 100
        )
        if d >= captured_day:
            row["daily_rhr_reference_only"] = None  # Day summary may not be final yet.
        key = row["device_key"]
        if key is not None and key not in tags:
            tags[key] = f"tracker_{len(tags) + 1}"
        row["tracker"] = tags.get(key, "unknown")
        rows.append(row)
    periods = windows(rows, start, end)
    segments = []
    for row in rows:
        if not segments or segments[-1]["tracker"] != row["tracker"]:
            segments.append(
                {"tracker": row["tracker"], "from": row["day"], "to": row["day"], "days": 1}
            )
        else:
            segments[-1]["to"] = row["day"]
            segments[-1]["days"] += 1
    current_tracker = next(
        (r["tracker"] for r in reversed(rows) if r["tracker"] != "unknown"), None
    )
    current_sleep_version = next(
        (r["sleep_version"] for r in reversed(rows) if r["tracker"] == current_tracker), None
    )
    consistent = [
        r
        for r in rows
        if current_tracker
        and r["tracker"] == current_tracker
        and r["sleep_version"] == current_sleep_version
    ]
    # Exploratory multi-signal deviations using only preceding dates and the same known tracker.
    deviations = []
    evaluable_days = 0
    for i, row in enumerate(rows):
        if not row["baseline_eligible"] or row["tracker"] == "unknown":
            continue
        previous = [
            p
            for p in rows[max(0, i - 42) : i]
            if p["baseline_eligible"]
            and p["tracker"] == row["tracker"]
            and p["sleep_version"] == row["sleep_version"]
        ]
        if len(previous) < 28:
            continue
        evaluable_days += 1
        hbase = median(p["hrv_night_ms"] for p in previous)
        rbase = median(p["night_heart_rate_bpm"] for p in previous)
        change = 100 * (row["hrv_night_ms"] / hbase - 1)
        hr_delta = row["night_heart_rate_bpm"] - rbase
        if change <= -20 and hr_delta >= 5:
            deviations.append(
                {
                    "day": row["day"],
                    "hrv_change_pct": round(change, 1),
                    "night_hr_change_bpm": round(hr_delta, 1),
                    "sleep_hours": row["sleep_hours"],
                }
            )
    latest = [
        {
            k: r.get(k)
            for k in (
                "day",
                "tracker",
                *METRICS,
                "sleep_start",
                "sleep_end",
                "baseline_eligible",
                "flags",
            )
        }
        for r in rows[-14:]
    ]
    return {
        "scope": "retrospective_descriptive_health_trends_not_diagnosis",
        "version": "trends-v0.2",
        "normalizer_version": VERSION,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "as_of": cutoff,
        "requested_days": len(rows),
        "valid_joint_nights": sum(r["baseline_eligible"] for r in rows),
        "quality_flags": dict(Counter(f for r in rows for f in r["flags"])),
        "sleep_version_counts": dict(Counter(str(r["sleep_version"]) for r in rows)),
        "current_sleep_version": current_sleep_version,
        "periods": periods,
        "tracker_segments": segments,
        "unreviewed_primary_episodes": unreviewed_primary_episodes,
        "current_tracker": current_tracker,
        "current_tracker_periods": windows(consistent, start, end),
        "weekday": aggregate([r for r in rows if date.fromisoformat(r["day"]).weekday() < 5]),
        "weekend": aggregate([r for r in rows if date.fromisoformat(r["day"]).weekday() >= 5]),
        "exploratory_correlations_same_tracker": {
            "night_hrv_vs_night_hr": association(
                consistent, "hrv_night_ms", "night_heart_rate_bpm"
            ),
            "night_sleep_vs_garmin_readiness": association(
                consistent, "sleep_hours", "garmin_morning_readiness_reference"
            ),
        },
        "multi_signal_deviations": deviations,
        "deviation_evaluable_days": evaluable_days,
        "deviation_rule": "HRV <= -20% and night HR >= +5bpm vs previous 42-day same-tracker median; at least28 valid nights; exploratory, not clinical thresholds",
        "latest14": latest,
        "limitations": [
            "No causal inference, diagnosis, performance test or calibrated custom readiness score.",
            "Preferred-tracker identity can be a proxy, not verified physical sleep sensor.",
            "Current-day daily RHR/naps excluded until calendar day ends.",
            "Sleep stage/SpO2/stress/Body Battery are consumer-device estimates; shared source signals are not independent evidence.",
            "Only Garmin acute-load reference is available, not detailed workout logs; no illness, alcohol, medication or subjective context collected yet.",
        ],
    }
