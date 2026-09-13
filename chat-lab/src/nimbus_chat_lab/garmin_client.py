"""Fixed local health RPC; no Garmin SDK, raw paths or credentials in tool results."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .health_interpretation import POLICY, evidence_contract, render_interpretation

MAX_RESPONSE = 24576
HEALTH_SYSTEM = (
    """
Health privacy policy: only garmin and clock are available after a health read. Do not use search,
workspace, memory, schedule or activity to export private health information. Public scheduled jobs
cannot read health data. Actual health data comes only from tool receipts, not from old chat guesses.
For a health daily task, call garmin daily_brief with args:{} (runtime binds the planned local day).
You may call method for the versioned readiness rationale. Readiness is not a medical diagnosis or
WHOOP reproduction. Never invent a custom score when score=null, reconstruct unrecorded activity HR,
or treat a habitual activity as confirmed attendance. Same-device baselines only; adjacent-night
comparisons are descriptive, not a fitted baseline. Garmin scores share signals. Night HR is not
Garmin's daily RHR; physiological recovery is not proof muscles/joints recovered.
For legacy daily_brief receipts WITHOUT interpretation_contract, finish with JSON only: {"observation_id":"<one available observation key>",
"advice_id":"<one available advice key>"}. Choose only from the receipt. The application renders the
actual numbers and evidence-bound sentences; do not compose new numbers or diagnoses. Other garmin
queries have a fixed text response rendered by the application. Never request passwords or MFA here.
"""
    + POLICY
)


class GarminClient:
    def __init__(self, config, state, before_request):
        self.config, self.state, self.before_request = config, state, before_request
        self.calls = 0
        self.last_receipt = None
        self.daily_receipt = None

    async def execute(self, action, args):
        allowed = {
            "status": set(),
            "method": set(),
            "daily_brief": {"day"},
            "trends": {"days", "end"},
        }
        if (
            not isinstance(action, str)
            or action not in allowed
            or not isinstance(args, dict)
            or set(args) - allowed[action]
        ):
            raise ValueError("Invalid health arguments")
        if self.state.background and self.state.data_scope != "health":
            raise PermissionError("Public jobs cannot read health data")
        if self.calls >= 4:
            raise ValueError("Health request limit")
        args = dict(args)
        if self.state.background and action == "daily_brief":
            slot = self.state.report_slot
            if slot is None:
                raise PermissionError("Missing scheduled slot")
            day = slot.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
            if args.get("day", day) != day:
                raise ValueError("Scheduled day is runtime-bound")
            args["day"] = day
        now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date()
        for key in ("day", "end"):
            if key in args:
                try:
                    day = datetime.strptime(args[key], "%Y-%m-%d").date()
                    if day.isoformat() != args[key] or not now - timedelta(days=180) <= day <= now:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ValueError("Invalid health day") from None
        if "days" in args and (type(args["days"]) is not int or args["days"] not in (7, 28, 90)):
            raise ValueError("Invalid health range")
        self.calls += 1
        await self.before_request()
        # Commit the data label before any PHI can be returned, including failed turns.
        await self.state.enter_health()
        writer = None
        try:
            async with asyncio.timeout(160 if self.state.background else 18):
                reader, writer = await asyncio.open_unix_connection(
                    self.config["socket"], limit=MAX_RESPONSE + 1
                )
                request = {
                    "key": self.config["key"],
                    "identity": list(self.state.identity),
                    "action": action,
                    "args": args,
                    "refresh": self.state.background and action == "daily_brief",
                }
                writer.write(json.dumps(request).encode() + b"\n")
                await writer.drain()
                data = await reader.readline()
                if len(data) > MAX_RESPONSE + 1:
                    raise ValueError("response_bound")
                payload = json.loads(data)
                if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
                    raise ValueError("protocol")
                if payload["ok"]:
                    result = payload["result"]
                    allowed_fields = {
                        "status": {"latest_sleep_day", "fetched_at"},
                        "method": {"method_version", "score"},
                        "trends": {
                            "start",
                            "end",
                            "computed_at",
                            "periods_current_device_only",
                            "segments",
                        },
                        "daily_brief": {
                            "report_day",
                            "timezone",
                            "computed_at",
                            "collected_at",
                            "oldest_required_fetch",
                            "source_status",
                            "normalizer_version",
                            "method_version",
                            "history_mode",
                            "night",
                            "baseline_valid_nights",
                            "baseline_status",
                            "comparison_kind",
                            "comparison",
                            "score",
                            "calibration_status",
                            "quality_flags",
                            "context",
                            "observations",
                            "advice",
                            "snapshot_id",
                            "insights",
                        },
                    }[action] | {
                        "schema_version",
                        "kind",
                        "text",
                        "refresh_error",
                        "cache_only",
                        "last_refresh_error",
                    }
                    if not isinstance(result, dict) or set(result) - allowed_fields:
                        raise ValueError("unexpected_health_fields")
                    if (
                        not isinstance(result, dict)
                        or result.get("schema_version") != 1
                        or result.get("kind") != action
                    ):
                        raise ValueError("schema")
                    if not isinstance(result.get("text"), str) or len(result["text"]) > 6000:
                        raise ValueError("text_bound")
                    if action == "daily_brief":
                        if result.get("score") is not None or result.get("report_day") != args.get(
                            "day", now.isoformat()
                        ):
                            raise ValueError("score_or_date")
                        if (
                            not isinstance(result.get("observations"), dict)
                            or not result["observations"]
                            or not isinstance(result.get("advice"), dict)
                            or not result["advice"]
                        ):
                            raise ValueError("choices")
                        for choices in (result["observations"], result["advice"]):
                            if len(choices) > 8 or any(
                                not isinstance(v, str) or len(v) > 300 for v in choices.values()
                            ):
                                raise ValueError("choice_bound")
                else:
                    # Do not propagate arbitrary service exceptions or messages.
                    result = {
                        "schema_version": 1,
                        "kind": action,
                        "unavailable": True,
                        "text": "健康摘要暂不可用，尚未据此判断今天的恢复情况。可稍后再查看；若需认证，请只在本机登录。",
                    }
        except (OSError, ValueError, KeyError, TypeError, TimeoutError):
            result = {
                "schema_version": 1,
                "kind": action,
                "unavailable": True,
                "text": "健康数据服务暂不可用，未取得本次摘要，也没有生成恢复分。",
            }
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
        await self.before_request()
        if self.state.background and action == "daily_brief" and not result.get("unavailable"):
            try:
                contract = evidence_contract(result)
                if contract:
                    result["interpretation_contract"] = contract
            except (ValueError, KeyError, TypeError, IndexError):
                # Keep the valid local receipt; a malformed optional analysis cannot inject prose.
                result["interpretation_unavailable"] = True
        self.last_receipt = result
        if action == "daily_brief":
            self.daily_receipt = result
        return result

    def render(self, model_output="", *, fallback=False):
        receipt = self.daily_receipt if self.state.background else self.last_receipt
        if not receipt:
            return "本次未取得健康工具摘要，暂不判断今天的身体恢复情况。"
        text = receipt["text"]
        if receipt.get("interpretation_contract"):
            interpreted = None if fallback else render_interpretation(receipt, model_output)
            if interpreted is not None:
                return interpreted
            return text + "\n本次仅为本地事实摘要，模型解读未完成或未通过证据格式检查。"
        if receipt.get("interpretation_unavailable"):
            return text + "\n个人模式解释暂不可用，本次仅提供本地事实摘要。"
        if receipt.get("kind") == "daily_brief" and not receipt.get("unavailable"):
            try:
                choice = json.loads(model_output)
                if not isinstance(choice, dict) or set(choice) != {"observation_id", "advice_id"}:
                    raise ValueError()
                obs = receipt["observations"][choice["observation_id"]]
                advice = receipt["advice"][choice["advice_id"]]
                # Replace only whole known canonical lines, never accept model prose/numbers.
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    if line in receipt["observations"].values():
                        lines[i] = obs
                    elif line in receipt["advice"].values():
                        lines[i] = advice
                text = "\n".join(lines)
            except (ValueError, KeyError, TypeError):
                pass  # Deterministic complete local brief, not ungrounded text.
        if fallback:
            text += "\n本次为本地事实摘要，模型解读未完成。"
        return text[:8000]
