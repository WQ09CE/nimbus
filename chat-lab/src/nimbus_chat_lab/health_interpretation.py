"""Bounded evidence-referenced health narrative, not a clinical semantic validator.

Local calculations own numbers and forecast status. Structural/numeric checks catch
known failure modes; model policy and case review are still required for meaning.
"""

import json
import re

VERSION = "health-interpretation-v1"
NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
UNSAFE = re.compile(
    r"确诊|诊断为|患有|服用|停药|加药|剂量|治愈|完全恢复|保证恢复|必然恢复|恢复评分|恢复分|预测概率|置信度[为是:]|\bmg\b",
    re.I,
)
POLICY = """
When daily_brief contains interpretation_contract, replace the old ID-selection response with
JSON ONLY, exact shape:
{"summary":{"text":"...","evidence_ids":["..."]},
 "outlook":{"text":"...","evidence_ids":["..."]},
 "action":{"text":"...","evidence_ids":["..."]},
 "review_if":"..."}.
Each text is a short Chinese paragraph (roughly 40-80 characters), each evidence list 1-3
exact IDs from interpretation_contract.evidence. Across the whole output, use at most THREE
non-basic cards (cards other than night/comparison/limits). review_if: concrete observation changing the
interpretation, not a request for daily annotation. No other fields, markdown or unreferenced
numbers. If numbers are useful, copy literals from the cited cards exactly; never calculate
scores, probabilities, new effect sizes, training doses or precision. Prefer interpretation
rather than repeating all card values: application appends compact evidence and scope/validation
limits. Do NOT turn each paragraph into methodological disclaimers; retain only a limitation
needed to avoid a misleading claim. Keep insight, expectation and practical focus in the foreground.
Do not call small adjacent-night shifts recovery improvement, even in the review_if section.
Summary is about the LAST COMPLETED main sleep and existing patterns. Refer to it as 昨夜 or
最近记录, never 今夜/今晚/明晨/明天: those belong to outlook, not observed facts. Synthesize the
most meaningful current or repeated personal pattern, not a list of metrics. Outlook: a conditional expectation and why; use calendar proxies as hypotheses, not
confirmed attendance or exercise causality. Distinguish old-device history from new-device
calibration. Old repeated decline/rebound is not proof muscles recovered or sleep costs disappear.
Prediction targets are next-main-sleep measurements, not daytime energy/performance. Historical
walk-forward gains came from a previously explored series, not independent unseen or prospective
validation. If only simple_reference exists, call it a reference/hypothesis, never a validated
personal forecast. Observed p10/p90 are historical distribution, NOT a prediction interval.
Action: one low-risk practical focus motivated by the pattern, rather than boilerplate "listen
to your body". For a recurring pattern suggest what to watch/change conditionally, not stop the
habit or assume participation. Do not claim an intervention is proven beneficial from correlation.
Normal/stale/insufficient days must remain meaningful but honest. Existing patterns are not "newly
discovered" every morning. Do not fabricate fresh discoveries, symptoms, fitted scores, medication
advice or diagnoses. No access to raw health, public search, memory, files or external tools.
If interpretation_contract is absent, retain the legacy observation_id/advice_id response.
"""


def evidence_contract(receipt):
    insights = receipt.get("insights")
    if (
        not isinstance(insights, dict)
        or receipt.get("source_status") != "complete"
        or receipt.get("history_mode") == "retrospective"
        or receipt.get("refresh_error")
    ):
        return None
    cards = [{"id": "night", "text": receipt["text"].splitlines()[1]}]
    comparison = receipt.get("comparison", {})
    if comparison:
        kind = (
            "同设备前夜，描述性比较"
            if receipt["comparison_kind"] == "previous_same_device_night"
            else "同设备过去基线"
        )
        cards.append(
            {
                "id": "comparison",
                "text": f"{kind}：HRV {comparison['hrv_night_ms']:g} ms，夜间心率{comparison['night_heart_rate_bpm']:g}次/分。",
            }
        )
    cards.append(
        {
            "id": "limits",
            "text": f"该夜之前同设备有效历史{receipt['baseline_valid_nights']}晚；精力、酸痛及逐场活动情况未在此摘要记录，不给恢复总分。",
        }
    )
    # Keep at most the current segment and the most recent older segment.
    all_segments = insights.get("segments", [])
    current = next((g for g in all_segments if g.get("is_current")), None)
    older = [g for g in all_segments if not g.get("is_current")]
    previous = max(older, key=lambda g: g["to"]) if older else None
    segments = [g for g in (previous, current) if g is not None]
    for group in segments:
        p = group.get("habit_pattern")
        if not p or not p.get("n"):
            continue
        prefix = "current" if group["is_current"] else "prior"
        label = "当前设备段" if group["is_current"] else "旧设备段，不能直接校准新设备"
        weekday = "一二三四五六日"[p["evening_weekday_iso"] - 1]
        n = p["previous_night_pairs"]
        after = p["next_night_pairs"]
        hrv = p["change_from_previous"]["hrv_night_ms"]
        hr = p["change_from_previous"]["night_heart_rate_bpm"]
        sleep = p["change_from_previous"]["sleep_hours"]["delta"]
        text = (
            f"{label}（{group['from']}至{group['to']}）：周{weekday}晚对应记录{p['n']}次；"
            f"与前夜可配对{n}次，HRV下降{hrv['decreased']}次、夜间心率升高{hr['increased']}次。"
        )
        if n:
            text += f"睡眠时长变化中位数{sleep['median']:g}小时。"
        if after:
            text += (
                f"随后夜可配对{after}次，HRV回升{p['change_to_next']['hrv_night_ms']['increased']}次，"
                f"夜间心率下降{p['change_to_next']['night_heart_rate_bpm']['decreased']}次。"
            )
        text += "这是星期代理分组，非逐场出席或因果证据；回弹不等于肌肉恢复或睡眠负担消失。"
        display = (
            f"{'新表' if group['is_current'] else '旧表'}周{weekday}晚：与前夜配对{n}次，"
            f"HRV降{hrv['decreased']}次、夜间心率升{hr['increased']}次。"
        )
        if n:
            display += f"睡眠变化中位数{sleep['median']:g}小时。"
        if after:
            display += (
                f"后夜配对{after}次，HRV回升{p['change_to_next']['hrv_night_ms']['increased']}次、"
                f"心率回落{p['change_to_next']['night_heart_rate_bpm']['decreased']}次。"
            )
        cards.append({"id": f"{prefix}_pattern", "text": text, "display": display})
        evaluation = group.get("forecast_evaluation", {})
        pieces, short_pieces = [], []
        for metric, label_metric in [
            ("hrv_night_ms", "HRV"),
            ("night_heart_rate_bpm", "夜间心率"),
            ("sleep_hours", "睡眠时长"),
        ]:
            value = evaluation.get(metric, {})
            gain = value.get("relative_mae_reduction")
            if value.get("n", 0) >= 28 and gain is not None:
                result = (
                    "达到探索门槛" if value["retrospective_gain_supported"] else "未达到探索门槛"
                )
                pieces.append(
                    f"{label_metric}回放{value['n']}步，误差相对最佳简单基线减少{gain * 100:.1f}%，{result}"
                )
                short_pieces.append(f"{label_metric}({value['n']}步){gain * 100:.1f}%")
        if pieces:
            cards.append(
                {
                    "id": f"{prefix}_evaluation",
                    "text": label
                    + "："
                    + "；".join(pieces)
                    + "。模型思路来自同一历史序列；非独立未见验证、非当时已知数据、非前瞻效果。",
                    "display": ("新表" if group["is_current"] else "旧表")
                    + "回放误差相对最佳简单基线减少："
                    + "、".join(short_pieces)
                    + "；不是独立未见/前瞻验证，不能当身体恢复幅度。",
                }
            )
    f = insights.get("forecast", {})
    if f.get("values"):
        parts = []
        for key, label, unit in [
            ("sleep_hours", "睡眠", "小时"),
            ("hrv_night_ms", "HRV", "ms"),
            ("night_heart_rate_bpm", "夜间心率", "次/分"),
        ]:
            v = f["values"][key]
            parts.append(f"{label}{v['point']:g}{unit}")
        simple = all(v["method"] == "rolling_median" for v in f["values"].values())
        qualifier = (
            "仅滚动中位数参考，不是已验证的个人预测"
            if simple
            else "含历史回放支持的探索预测，未前瞻验证"
        )
        cards.append(
            {
                "id": "next_reference",
                "text": f"下一主睡眠日{f['target_day']}："
                + "、".join(parts)
                + f"；同设备训练记录{f['training_n']}晚。{qualifier}；不能推断白天精力或运动表现。",
                "display": f"{f['target_day']}下一主睡眠参考中心："
                + "、".join(parts)
                + f"（当前设备{f['training_n']}晚，{'简单参考' if simple else '探索预测'}，非能力评分）。",
            }
        )
    return {
        "version": VERSION,
        "evidence": cards,
        "required_sections": ["summary", "outlook", "action", "review_if"],
        "numeric_claims": "only literals from cited cards; citations do not prove semantic validity",
    }


def render_interpretation(receipt, model_output):
    contract = receipt.get("interpretation_contract")
    if (
        not contract
        or receipt.get("source_status") != "complete"
        or receipt.get("history_mode") == "retrospective"
        or receipt.get("refresh_error")
    ):
        return None
    try:
        data = json.loads(model_output)
        if not isinstance(data, dict) or set(data) != {"summary", "outlook", "action", "review_if"}:
            return None
        cards = {c["id"]: c["text"] for c in contract["evidence"]}
        displays = {c["id"]: c.get("display", c["text"]) for c in contract["evidence"]}
        parts, used = [], []
        for name, label in [
            ("summary", "我的判断"),
            ("outlook", "接下来怎么看"),
            ("action", "首要行动"),
        ]:
            block = data[name]
            if not isinstance(block, dict) or set(block) != {"text", "evidence_ids"}:
                return None
            text, ids = block["text"], block["evidence_ids"]
            if (
                not isinstance(text, str)
                or not 1 <= len(text) <= 350
                or not isinstance(ids, list)
                or not 1 <= len(ids) <= 3
                or any(not isinstance(i, str) or i not in cards for i in ids)
            ):
                return None
            allowed_numbers = set(
                NUMBER.findall(" ".join(cards[i] + " " + displays[i] for i in ids))
            )
            if set(NUMBER.findall(text)) - allowed_numbers:
                return None
            if name == "summary" and re.search(r"今夜|今晚|明晨|明天", text):
                return None  # Completed observations must not be phrased as future nights.
            used.extend(ids)
            parts.append(f"{label}：{text.strip()}")
        review = data["review_if"]
        if not isinstance(review, str) or not 1 <= len(review) <= 240 or NUMBER.search(review):
            return None
        prose = "\n".join(parts + [review])
        if UNSAFE.search(prose) or re.search(r"https?://|file://|[\x00-\x08\x0b-\x1f]", prose):
            return None
        # Source/status disclaimers are application-owned, not removable by the model.
        evidence = [
            displays[i] for i in dict.fromkeys(used) if i not in ("night", "comparison", "limits")
        ]
        if len(evidence) > 3:
            return None
        facts = [cards["night"]]
        if "comparison" in used and "comparison" in cards:
            facts.append(cards["comparison"])
        text = "\n\n".join(
            [
                f"身体快报 · {receipt['report_day']}",
                *parts,
                "复核条件：" + review,
                "依据：" + "\n".join(facts + evidence),
            ]
        )
        if any(i.startswith("prior_") for i in used):
            text += "\n星期分组不是逐场出席或因果证据；旧表模式不直接校准新表。"
        text += "\n模型解释供参考；体感未记录，预测未前瞻验证，不是诊断。"
        stamps = [line for line in receipt["text"].splitlines() if line.startswith("数据取得截至")]
        if stamps:
            text += "\n" + stamps[-1]
        return text if len(text) <= 2400 else None
    except (ValueError, KeyError, TypeError, IndexError):
        return None
