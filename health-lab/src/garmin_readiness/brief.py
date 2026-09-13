"""Minimal disclosure projection and deterministic, evidence-bound health prose."""

import hashlib
import json
from datetime import date, timedelta

from .features import TZ, VERSION, baseline_report, extract, gmt
from .insights import analyze, collect_rows
from .storage import LocalError, day_string, now, private_file
from .trends import build_trends

METHOD_VERSION = "readiness-evidence-v1.2"
METHOD = (
    "个人恢复提示，不是疾病诊断或WHOOP复刻。自定义分数尚未校准，不给0–100总分。"
    "使用过去42天中至少28个同设备、同睡眠算法有效夜晚的HRV与睡眠窗口心率基线；"
    "不足时仅比较同设备相邻夜。HRV高、心率低不自动代表恢复好。"
    "睡眠评分、压力、Body Battery和佳明准备度共享底层信号，不重复计票。"
    "未监测心率的运动可能未反映在训练负荷；习惯不是已确认参加的场次。"
    "不按星期扣分，也不把周期性的恢复需求归一化掉。"
    "联合变化提示采用HRV下降至少15%且夜间心率升高至少2次/分的初始展示规则，以减少小幅抖动提示；不是临床阈值或已校准的恢复分类器。"
    "夜间生理指标回升不代表肌肉/关节完全恢复；需结合实际体感。"
    "日静息心率是佳明日汇总，不混同夜间心率；当天日汇总不作已完成晨间结果。"
    "另有同设备/算法个人模式与下一主睡眠指标的探索分析；以星期为代理，不推断运动出席。"
    "历史回放采用前42天至少28晚、固定星期收缩模型，对照滚动中位数和最近观测；"
    "模型思路来自已探索的同一序列，历史数据又有回填，不能当作独立未见或前瞻验证。"
    "旧设备有效不等于新设备有效；新段不足时只给简单中位数参考，历史分布不是预测区间。"
)
CORE = ("hrv_night_ms", "night_heart_rate_bpm", "sleep_hours")


def request_args(action, args):
    if not isinstance(args, dict):
        raise LocalError("invalid_arguments")
    permitted = {
        "status": set(),
        "method": set(),
        "daily_brief": {"day"},
        "trends": {"days", "end"},
    }
    if action not in permitted or set(args) - permitted[action]:
        raise LocalError("invalid_arguments")
    today = gmt(now()).astimezone(TZ).date()
    day = args.get("day", args.get("end", today.isoformat()))
    if not isinstance(day, str):
        raise LocalError("invalid_arguments")
    try:
        day_string(day)
        target = date.fromisoformat(day)
    except (ValueError, TypeError):
        raise LocalError("invalid_date") from None
    if not today - timedelta(days=180) <= target <= today:
        raise LocalError("date_range_bound")
    days = args.get("days", 7)
    if type(days) is not int or days not in (7, 28, 90):
        raise LocalError("invalid_arguments")
    return target, days


def context(root, day):
    """Do not export free text, device IDs, unknown fields or private paths."""
    p = root / "user_context.json"
    if not p.exists():
        return {}
    private_file(p)
    if p.stat().st_size > 8192:
        raise LocalError("context_bound")
    raw = json.loads(p.read_text())
    activity = raw.get("habitual_activity", {})
    if not isinstance(activity, dict):
        return {}
    result = {}
    if type(activity.get("heart_rate_monitor_worn")) is bool:
        result["habit_has_heart_rate_recording"] = activity["heart_rate_monitor_worn"]
    weekday = activity.get("weekday_iso")
    if type(weekday) is int and weekday in range(1, 8):
        result["habitual_evening_weekday_iso"] = weekday
        result["previous_evening_matches_reported_habit"] = (
            day - timedelta(days=1)
        ).isoweekday() == weekday
    result["actual_attendance_confirmed"] = False
    return result


def daily(archive, day, *, cutoff=None):
    cutoff = cutoff or now()
    full = baseline_report(archive, day.isoformat(), cutoff=cutoff)
    f = full["current"]
    current = archive.latest(day.isoformat(), cutoff)
    records = archive.latest((day - timedelta(days=1)).isoformat(), cutoff)
    previous = extract(
        (day - timedelta(days=1)).isoformat(),
        records,
        archive.latest((day - timedelta(days=2)).isoformat(), cutoff),
        cutoff=cutoff,
    )
    provenance = {k: f["sources"].get(k) for k in ("hrv", "sleep", "heart_rate")}
    provenance["previous_heart_rate"] = f["previous_day_hr_source"]
    available_stamps = [r["fetched_at"] for r in provenance.values() if r and r.get("fetched_at")]
    oldest_core = min(available_stamps) if len(available_stamps) == 4 else None
    all_stamps = [r["fetched_at"] for r in f["sources"].values() if r.get("fetched_at")]
    collected = max(all_stamps) if all_stamps else None
    complete = f["baseline_eligible"] and f["device_key"] is not None
    state = "complete" if complete else "partial" if current else "pending_sync"
    if (
        complete
        and day == gmt(cutoff).astimezone(TZ).date()
        and (not oldest_core or (gmt(cutoff) - gmt(oldest_core)).total_seconds() > 1200)
    ):
        state = "stale"
    comparable = (
        f["baseline_eligible"]
        and previous["baseline_eligible"]
        and f["device_key"] is not None
        and f["device_key"] == previous["device_key"]
        and f["sleep_version"] == previous["sleep_version"]
    )
    baseline_ok = bool(full["signals"]) and f["device_key"] is not None
    comparison = (
        {k: full["signals"][k]["historical_median"] for k in CORE}
        if baseline_ok
        else ({k: previous[k] for k in CORE} if comparable else {})
    )
    observed = {}
    advice = {"ordinary": "结合体感安排今天的活动，不必追求单个指标更高。"}
    if state == "complete":
        if (
            comparison
            and f["hrv_night_ms"] <= 0.85 * comparison["hrv_night_ms"]
            and f["night_heart_rate_bpm"] >= comparison["night_heart_rate_bpm"] + 2
        ):
            observed["joint_shift"] = (
                "HRV下降、夜间心率升高，今天值得多留意疲劳感；这不是疾病诊断。"
            )
            advice["recovery"] = "如果仍明显疲劳或酸痛，今天先降低活动强度，把恢复放在前面。"
        if f["sleep_hours"] < 7:
            observed["short_sleep"] = "记录的夜睡不足一般参考的七小时，但这不是你的已校准睡眠需求。"
            advice["sleep"] = "今晚尽量保持稳定的睡眠时间，减少临时晚睡。"
        observed["neutral"] = "先把这些指标与实际精力、疲劳和酸痛放在一起看。"
    else:
        observed["limited"] = "今日数据未齐或不够新，暂不据此判断今天是否恢复充分。"
        advice = {"sync": "醒来后让手表与Garmin Connect同步，稍后可再查看。"}
    ctx = context(archive.root, day)
    if (
        state == "complete"
        and ctx.get("previous_evening_matches_reported_habit")
        and not ctx.get("habit_has_heart_rate_recording", True)
    ):
        observed["habit"] = (
            "若昨晚如常参加了未监测心率的运动，佳明负荷可能未完整记录；本次参加情况尚未确认。"
        )
    values = {
        k: f[k] for k in (*CORE, "sleep_start", "sleep_end", "garmin_morning_readiness_reference")
    }
    report = {
        "schema_version": 1,
        "kind": "daily_brief",
        "report_day": day.isoformat(),
        "timezone": "Asia/Shanghai",
        "computed_at": cutoff,
        "collected_at": collected,
        "oldest_required_fetch": oldest_core,
        "source_status": state,
        "normalizer_version": VERSION,
        "method_version": METHOD_VERSION,
        "history_mode": "captured_at_report_time_not_past_prediction",
        "night": values,
        "baseline_valid_nights": full["baseline_valid_nights"],
        "baseline_status": "available" if baseline_ok else "insufficient",
        "comparison_kind": "past_same_device_baseline"
        if baseline_ok
        else "previous_same_device_night"
        if comparable
        else "unavailable",
        "comparison": comparison,
        "score": None,
        "calibration_status": "not_fitted",
        "quality_flags": f["flags"],
        "context": ctx,
        "observations": observed,
        "advice": advice,
    }
    historical_view(report, cutoff)
    # Calculations stay local. No raw records, IDs, labels or paths go to the model.
    insight_rows = collect_rows(archive, day, cutoff=cutoff, days=120)
    insight_summary, _ = analyze(
        insight_rows,
        day,
        habitual_evening_weekday=ctx.get("habitual_evening_weekday_iso"),
    )
    if state != "complete" or report["history_mode"] == "retrospective":
        insight_summary["forecast"]["values"] = {}
    insight_summary["input_sha256"] = hashlib.sha256(
        json.dumps(insight_rows, sort_keys=True).encode()
    ).hexdigest()
    report["insights"] = insight_summary
    with archive.connect() as conn:
        history_sources = [
            dict(r)
            for r in conn.execute(
                "SELECT id,day,kind,fetched_at,status,digest FROM (SELECT *,row_number() OVER (PARTITION BY day,kind ORDER BY fetched_at DESC,id DESC) AS n FROM observations WHERE day>=? AND day<=? AND fetched_at<=?) WHERE n=1",
                ((day - timedelta(days=120)).isoformat(), day.isoformat(), gmt(cutoff).isoformat()),
            )
        ]
    provenance["history_manifest"] = history_sources
    snapshot = hashlib.sha256(
        json.dumps({"report": report, "sources": provenance}, sort_keys=True).encode()
    ).hexdigest()
    report["snapshot_id"] = snapshot
    # Complete provenance stays local. It is not part of the model projection.
    archive.save_analysis(
        day.isoformat(),
        METHOD_VERSION,
        {
            "snapshot_id": snapshot,
            "sources": f["sources"],
            "history_manifest": history_sources,
            "previous_hr_source": f["previous_day_hr_source"],
            "baseline": full,
            "public_summary": report,
        },
    )
    report["text"] = render_daily(report)
    return report


def historical_view(report, viewed_at):
    """Adapt a projection/view, never claim past measurements describe the current body."""
    if date.fromisoformat(report["report_day"]) < gmt(viewed_at).astimezone(TZ).date():
        report["history_mode"] = "retrospective"
        report["observations"] = {
            k: v.replace("今天", "当时").replace("今日", "当时")
            for k, v in report["observations"].items()
        }
        report["advice"] = {"history": "这是历史数据回顾，不据此判断你现在的恢复情况。"}


def render_daily(r, selection=None):
    selection = selection if isinstance(selection, dict) else {}
    title = "历史身体记录" if r["history_mode"] == "retrospective" else "身体快报"
    lines = [f"{title} · {r['report_day']}"]
    if r["source_status"] == "complete":
        n = r["night"]
        total = round(n["sleep_hours"] * 60)
        lines.append(
            f"该夜睡眠{total // 60}小时{total % 60:02d}分；HRV {n['hrv_night_ms']:g} ms，夜间心率{n['night_heart_rate_bpm']:g}次/分。"
        )
        if r["comparison"]:
            c = r["comparison"]
            label = (
                "同设备过去基线"
                if r["comparison_kind"] == "past_same_device_baseline"
                else "同设备前一晚（描述性比较）"
            )
            lines.append(
                f"对照{label}：HRV {c['hrv_night_ms']:g} ms，夜间心率{c['night_heart_rate_bpm']:g}次/分。"
            )
        value = n["garmin_morning_readiness_reference"]
        if value is not None:
            lines.append(f"佳明醒后准备度{value:g}，仅作参考，不等于全部恢复。")
    key = selection.get("observation_id")
    if key not in r["observations"]:
        key = next(iter(r["observations"]))
    lines.append(r["observations"][key])
    key = selection.get("advice_id")
    if key not in r["advice"]:
        key = next(reversed(r["advice"]))
    lines.append(r["advice"][key])
    if r["baseline_status"] == "insufficient":
        lines.append(
            f"该日之前同设备有效历史{r['baseline_valid_nights']}晚，比较依据不足。"
            if r["history_mode"] == "retrospective"
            else f"同设备历史有效夜晚{r['baseline_valid_nights']}，基线仍在积累。"
        )
    lines.append("自定义恢复分尚未校准，暂不评分。")
    if r["collected_at"]:
        lines.append(
            "数据取得截至" + gmt(r["collected_at"]).astimezone(TZ).strftime("%m-%d %H:%M") + "。"
        )
    return "\n".join(lines)


def projection(archive, action, args):
    target, days = request_args(action, args)
    if action == "method":
        return {
            "schema_version": 1,
            "kind": "method",
            "method_version": METHOD_VERSION,
            "score": None,
            "text": METHOD,
        }
    if action == "daily_brief":
        return daily(archive, target)
    if action == "status":
        with archive.connect() as c:
            row = c.execute(
                "SELECT max(day) AS latest_day,max(fetched_at) AS fetched_at FROM observations WHERE kind='sleep' AND status='ok'"
            ).fetchone()
        return {
            "schema_version": 1,
            "kind": "status",
            "latest_sleep_day": row["latest_day"],
            "fetched_at": row["fetched_at"],
            "text": f"最近取得睡眠记录的日期：{row['latest_day'] or '暂无'}。自定义恢复分尚未校准。",
        }
    full = build_trends(archive, target - timedelta(days=days - 1), target)
    periods = {
        k: {"days": v["days"], "metrics": {m: v["metrics"][m] for m in CORE}}
        for k, v in full["current_tracker_periods"].items()
    }
    all_period = full["current_tracker_periods"]["all"]
    lines = [
        f"最近{days}天：{full['valid_joint_nights']}条可用常规夜间记录，{len(full['tracker_segments'])}个设备分段。",
        "当前设备、同睡眠算法分段的描述性均值：",
    ]
    for key, label, unit in [
        ("hrv_night_ms", "HRV", "ms"),
        ("night_heart_rate_bpm", "夜间心率", "次/分"),
        ("sleep_hours", "夜睡", "小时"),
    ]:
        metric = all_period["metrics"][key]
        lines.append(f"{label}：{metric.get('mean', '暂无')}{unit}（样本{metric['n']}）。")
    lines.append("不直接比较新旧设备的绝对值；自定义总分未校准。")
    return {
        "schema_version": 1,
        "kind": "trends",
        "start": full["start"],
        "end": full["end"],
        "computed_at": full["as_of"],
        "periods_current_device_only": periods,
        "segments": full["tracker_segments"],
        "text": "\n".join(lines),
    }
