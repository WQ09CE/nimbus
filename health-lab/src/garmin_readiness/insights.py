"""Local personal patterns and a pre-specified, retrospective forecast experiment.

No recovery score, attendance inference, cross-device calibration or provider calls.
A rolling backtest reconstructs history from the current archive: NOT as-known then.
"""

import math
import random
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median

from .features import TZ, extract, gmt
from .storage import LocalError, now
from .trends import quantile, stats

VERSION = "personal-insights-v0.1"
METRICS = ("sleep_hours", "hrv_night_ms", "night_heart_rate_bpm")
MIN_TRAIN = 28
WINDOW_DAYS = 42
SHRINK_PRIOR = 3  # Fixed before looking at study results; no hyperparameter search.


def evening_weekday(day):
    return (date.fromisoformat(day) - timedelta(days=1)).isoweekday()


def collect_rows(archive, end, *, cutoff=None, days=120):
    cutoff = cutoff or now()
    if type(days) is not int or not 1 <= days <= 180:
        raise LocalError("date_range_bound")
    stamp = gmt(cutoff)
    if not stamp or end > stamp.astimezone(TZ).date():
        raise LocalError("invalid_cutoff")
    first = end - timedelta(days=days - 1)
    previous = archive.latest((first - timedelta(days=1)).isoformat(), cutoff)
    tags, rows = {}, []
    for offset in range(days):
        day = (first + timedelta(days=offset)).isoformat()
        current = archive.latest(day, cutoff)
        f = extract(day, current, previous, cutoff=cutoff)
        key = (f["device_key"], f["sleep_version"])
        known = key[0] is not None and key[1] is not None
        if known and key not in tags:
            tags[key] = f"segment_{len(tags) + 1}"
        stamps = [f["sources"].get(k, {}).get("fetched_at") for k in ("sleep", "hrv", "heart_rate")]
        stamps.append(f["previous_day_hr_source"].get("fetched_at"))
        rows.append(
            {
                "day": day,
                "segment": tags.get(key),
                "eligible": bool(f["baseline_eligible"] and known),
                **{k: f[k] for k in METRICS},
                "sleep_start": f["sleep_start"],
                "sleep_end": f["sleep_end"],
                "flags": f["flags"],
                "available_at": max(stamps) if all(stamps) else None,
            }
        )
        previous = current
    return rows


def paired_pattern(rows, weekday):
    """Habit is a calendar proxy. Each comparison stays in one device/algorithm segment."""
    by_day = {r["day"]: r for r in rows}
    chosen = [r for r in rows if evening_weekday(r["day"]) == weekday]
    before, after = [], []
    for r in chosen:
        d = date.fromisoformat(r["day"])
        p = by_day.get((d - timedelta(days=1)).isoformat())
        n = by_day.get((d + timedelta(days=1)).isoformat())
        if p and p["segment"] == r["segment"]:
            before.append((p, r))
        if n and n["segment"] == r["segment"]:
            after.append((r, n))

    def changes(pairs):
        return {
            k: {
                "delta": stats([b[k] - a[k] for a, b in pairs]),
                "increased": sum(b[k] > a[k] for a, b in pairs),
                "decreased": sum(b[k] < a[k] for a, b in pairs),
                "unchanged": sum(b[k] == a[k] for a, b in pairs),
            }
            for k in METRICS
        }

    others = [r for r in rows if evening_weekday(r["day"]) != weekday]
    return {
        "evening_weekday_iso": weekday,
        "n": len(chosen),
        "not_actual_attendance": True,
        "habit_nights": {k: stats([r[k] for r in chosen]) for k in METRICS},
        "other_nights": {k: stats([r[k] for r in others]) for k in METRICS},
        "previous_night_pairs": len(before),
        "change_from_previous": changes(before),
        "next_night_pairs": len(after),
        "change_to_next": changes(after),
        "short_sleep_reference_7h": {
            "habit": sum(r["sleep_hours"] < 7 for r in chosen),
            "others": sum(r["sleep_hours"] < 7 for r in others),
            "not_personal_sleep_need": True,
        },
    }


def predict(history, target_day, metric):
    """Pure prefix predictor; caller must supply only preceding comparable observations."""
    if not history or any(r["day"] >= target_day for r in history):
        raise ValueError("Prediction history must precede target")
    if len({r["segment"] for r in history}) != 1 or history[0]["segment"] is None:
        raise ValueError("Mixed or unknown segment")
    transform = math.log if metric == "hrv_night_ms" else float
    inverse = math.exp if metric == "hrv_night_ms" else float
    values = [transform(r[metric]) for r in history]
    center = median(values)
    same_weekday = [
        transform(r[metric])
        for r in history
        if evening_weekday(r["day"]) == evening_weekday(target_day)
    ]
    n = len(same_weekday)
    offset = (median(same_weekday) - center) * n / (n + SHRINK_PRIOR) if n >= 3 else 0
    return {
        "rolling_median": inverse(center),
        "last_observation": history[-1][metric],
        "calendar_shrinkage": inverse(center + offset),
    }


def weekly_uncertainty(trials, reference):
    """Paired resampling of complete week blocks; exploratory uncertainty, not clinical CI."""
    groups = defaultdict(list)
    for t in trials:
        iso = date.fromisoformat(t["day"]).isocalendar()
        groups[(iso.year, iso.week)].append(t)
    blocks = list(groups.values())
    if len(blocks) < 4:
        return {"weeks": len(blocks), "delta_mae_p05_p95": None}
    rng = random.Random(0)
    differences = []
    for _ in range(1000):
        sample = [t for _ in blocks for t in rng.choice(blocks)]
        differences.append(
            mean(t["errors"][reference] - t["errors"]["calendar_shrinkage"] for t in sample)
        )
    return {
        "weeks": len(blocks),
        "delta_mae_p05_p95": [
            round(quantile(differences, 0.05), 4),
            round(quantile(differences, 0.95), 4),
        ],
    }


def backtest(rows):
    trials = {k: [] for k in METRICS}
    for row in rows:
        day = date.fromisoformat(row["day"])
        history = [
            r
            for r in rows
            if (day - timedelta(days=WINDOW_DAYS)).isoformat() <= r["day"] < row["day"]
            and r["segment"] == row["segment"]
        ]
        if len(history) < MIN_TRAIN:
            continue
        for metric in METRICS:
            predictions = predict(history, row["day"], metric)
            trials[metric].append(
                {
                    "day": row["day"],
                    "train_n": len(history),
                    "train_last_day": history[-1]["day"],
                    "observed": row[metric],
                    "predictions": predictions,
                    "errors": {m: abs(v - row[metric]) for m, v in predictions.items()},
                }
            )
    results = {}
    for metric, values in trials.items():
        if not values:
            results[metric] = {"n": 0, "retrospective_gain_supported": False}
            continue
        losses = {
            model: mean(t["errors"][model] for t in values)
            for model in ("rolling_median", "last_observation", "calendar_shrinkage")
        }
        reference = min(("rolling_median", "last_observation"), key=losses.get)
        gain = 1 - losses["calendar_shrinkage"] / losses[reference] if losses[reference] else None
        uncertainty = weekly_uncertainty(values, reference)
        interval = uncertainty["delta_mae_p05_p95"]
        results[metric] = {
            "n": len(values),
            "from": values[0]["day"],
            "to": values[-1]["day"],
            "mae": {k: round(v, 4) for k, v in losses.items()},
            "best_simple_reference": reference,
            "relative_mae_reduction": round(gain, 4) if gain is not None else None,
            "weekly_uncertainty": uncertainty,
            "retrospective_gain_supported": bool(
                len(values) >= 28
                and uncertainty["weeks"] >= 4
                and gain is not None
                and gain >= 0.1
                and interval
                and interval[0] > 0
            ),
            "prospectively_validated": False,
        }
    return results, trials


def analyze(rows, end, *, habitual_evening_weekday=None):
    if habitual_evening_weekday is not None and (
        type(habitual_evening_weekday) is not int or not 1 <= habitual_evening_weekday <= 7
    ):
        raise ValueError("Invalid habitual weekday")
    ordered = sorted((r for r in rows if r["day"] <= end.isoformat()), key=lambda r: r["day"])
    if len({r["day"] for r in ordered}) != len(ordered):
        raise ValueError("Duplicate day")
    valid = [
        r
        for r in ordered
        if r["eligible"]
        and r["segment"] is not None
        and all(type(r[k]) in (float, int) and math.isfinite(r[k]) and r[k] > 0 for k in METRICS)
    ]
    current = next((r["segment"] for r in reversed(ordered) if r["day"] == end.isoformat()), None)
    groups, detail = [], {}
    for segment in dict.fromkeys(r["segment"] for r in valid):
        group = [r for r in valid if r["segment"] == segment]
        evaluation, trials = backtest(group)
        detail[segment] = trials
        groups.append(
            {
                "segment": segment,
                "is_current": segment == current,
                "from": group[0]["day"],
                "to": group[-1]["day"],
                "valid_nights": len(group),
                "summary": {k: stats([r[k] for r in group]) for k in METRICS},
                "habit_pattern": paired_pattern(group, habitual_evening_weekday)
                if habitual_evening_weekday
                else None,
                "forecast_evaluation": evaluation,
            }
        )
    current_rows = [
        r
        for r in valid
        if r["segment"] == current
        and r["day"] >= (end - timedelta(days=WINDOW_DAYS - 1)).isoformat()
    ]
    next_day = (end + timedelta(days=1)).isoformat()
    forecast = {
        "target_day": next_day,
        "target": "next_main_sleep_measurements_not_daytime_capacity",
        "score": None,
        "prospectively_validated": False,
        "training_n": len(current_rows),
        "values": {},
    }
    current_eval = next((g["forecast_evaluation"] for g in groups if g["is_current"]), {})
    # A missing current night blocks current guidance, even when older values exist.
    if current_rows and current_rows[-1]["day"] == end.isoformat():
        for metric in METRICS:
            predictions = predict(current_rows, next_day, metric)
            supported = current_eval.get(metric, {}).get("retrospective_gain_supported", False)
            method = "calendar_shrinkage" if supported else "rolling_median"
            forecast["values"][metric] = {
                "method": method,
                "point": round(predictions[method], 2),
                "status": "retrospectively_supported_exploratory_forecast"
                if supported
                else "simple_reference_not_validated_personal_forecast",
                "observed_p10_p90_NOT_prediction_interval": [
                    round(quantile([r[metric] for r in current_rows], 0.1), 2),
                    round(quantile([r[metric] for r in current_rows], 0.9), 2),
                ],
            }
    return {
        "version": VERSION,
        "end": end.isoformat(),
        "requested_nights": len(ordered),
        "valid_nights": len(valid),
        "excluded_nights": len(ordered) - len(valid),
        "current_segment": current,
        "segments": groups,
        "forecast": forecast,
        "evaluation_mode": "retrospective_walk_forward_reconstructed_from_current_archive",
        "historical_as_known_validation": False,
        "independent_unseen_validation": False,
        "hypothesis_origin": "calendar_pattern_previously_explored_in_this_series",
        "limitations": [
            "Calendar association is not confirmed exercise attendance or a causal exercise effect.",
            "Prediction targets sleep/HRV/night HR, not energy, sports performance or a recovery score.",
            "Old-device effects and validation cannot calibrate the current device.",
            "Archived measurements were backfilled; chronological training is not historical data availability.",
            "One calendar-shrinkage candidate fixed before this run, two simple references, three targets; no hyperparameter search.",
            "The calendar hypothesis came from prior analysis of this same series: these rolling targets are not an independent unseen validation set.",
            "Weekly resampling is exploratory; retrospective improvement is not prospective or decision-value validation.",
        ],
    }, detail
