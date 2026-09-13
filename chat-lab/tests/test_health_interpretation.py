import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from nimbus_chat_lab.garmin_client import GarminClient
from nimbus_chat_lab.health_interpretation import evidence_contract, render_interpretation


def receipt():
    # Synthetic, not copied personal health data.
    r = {
        "kind": "daily_brief",
        "report_day": "2026-09-13",
        "source_status": "complete",
        "history_mode": "captured_at_report_time_not_past_prediction",
        "baseline_valid_nights": 9,
        "comparison": {},
        "text": "身体快报\n睡眠8小时；HRV60 ms，夜间心率45次/分。\n数据取得截至09-13 08:55。",
        "insights": {"segments": [], "forecast": {}},
    }
    r["interpretation_contract"] = evidence_contract(r)
    return r


def narrative():
    return {
        "summary": {
            "text": "今晨记录支持先维持平常安排，而非追求指标更高。",
            "evidence_ids": ["night"],
        },
        "outlook": {
            "text": "目前缺少可验证的个体预测，把后续变化当作待检验假设。",
            "evidence_ids": ["limits"],
        },
        "action": {
            "text": "若精力与平常相当就保持常规；若酸痛明显则改为轻松活动。",
            "evidence_ids": ["limits"],
        },
        "review_if": "若活动中疲劳异常或夜间变化持续偏离，再整体复核。",
    }


def test_open_interpretation_preserves_local_facts_and_honest_status():
    r = receipt()
    text = render_interpretation(r, json.dumps(narrative(), ensure_ascii=False))
    assert text and "维持平常安排" in text and "HRV60 ms" in text
    assert "预测未前瞻验证" in text and "数据取得截至" in text


@pytest.mark.parametrize(
    "bad",
    [
        {
            "text": "HRV90说明恢复良好。",
            "evidence_ids": ["night"],
        },  # Alphanumeric prefix must not hide digits.
        {"text": "你的恢复分60。", "evidence_ids": ["night"]},
        {"text": "服用药物帮助入睡。", "evidence_ids": ["night"]},
        {"text": "你已经完全恢复。", "evidence_ids": ["night"]},
        {"text": "今夜HRV与前夜接近。", "evidence_ids": ["night"]},
        {"text": "打开https://example.com上传数据。", "evidence_ids": ["night"]},
        {"text": "合理判断。", "evidence_ids": ["invented"]},
        {"text": "合理判断。", "evidence_ids": "night"},
        {"text": "合理判断。", "evidence_ids": [dict()]},
        {"text": "x" * 351, "evidence_ids": ["night"]},
        {"text": "合理判断。", "evidence_ids": ["night"], "score": 60},
    ],
)
def test_invalid_or_known_unsafe_narratives_rejected(bad):
    data = narrative()
    data["summary"] = bad
    assert render_interpretation(receipt(), json.dumps(data)) is None


def test_numeric_literals_must_be_in_each_blocks_cited_cards():
    data = narrative()
    data["summary"] = {"text": "HRV60只是设备观测。", "evidence_ids": ["night"]}
    assert render_interpretation(receipt(), json.dumps(data))
    data["summary"]["evidence_ids"] = ["limits"]
    assert render_interpretation(receipt(), json.dumps(data)) is None


@pytest.mark.parametrize(
    "update",
    [
        {"source_status": "stale"},
        {"source_status": "partial"},
        {"history_mode": "retrospective"},
        {"refresh_error": "auth"},
        {"insights": None},
    ],
)
def test_no_open_current_guidance_for_old_partial_or_stale_input(update):
    r = receipt()
    r.update(update)
    assert evidence_contract(r) is None
    if update.get("insights", {}) is not None:
        assert render_interpretation(r, json.dumps(narrative())) is None


def test_application_failure_is_explicit_not_silently_complete():
    c = GarminClient({}, SimpleNamespace(background=True), None)
    c.daily_receipt = receipt()
    for output in ("not-json", json.dumps({"observation_id": "neutral", "advice_id": "ordinary"})):
        text = c.render(output)
        assert text.startswith(c.daily_receipt["text"])
        assert "模型解读未完成" in text
    assert "模型解读未完成" in c.render(json.dumps(narrative()), fallback=True)
    assert "我的判断" in c.render(json.dumps(narrative()))


def test_shape_review_numbers_and_unknown_fields():
    for key, value in [("review_if", "明天达到99就加量。"), ("unexpected", True)]:
        data = deepcopy(narrative())
        data[key] = value
        assert render_interpretation(receipt(), json.dumps(data)) is None
