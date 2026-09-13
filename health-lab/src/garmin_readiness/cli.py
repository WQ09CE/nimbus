"""Local interactive onboarding and explicitly requested read-only sync. No service install."""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .features import TZ, baseline_report
from .provider import authenticate, sync
from .storage import Archive, LocalError
from .trends import build_trends

MESSAGES = {
    "login_required": "尚未登录。请在本机终端运行 onboard；不要把密码或验证码发到聊天里。",
    "tty_required": "登录需要本机交互终端，拒绝通过管道接收密码。",
    "auth": "认证未通过或会话失效。核对大陆账号；需要重新认证时运行 login --reauth。",
    "rate_limit": "佳明限流。本次已停止，没有循环重试；稍后再试，勿连续重新登录。",
    "access_denied": "佳明拒绝访问。本次已停止，请在官方 App 核对账号与同步状态。",
    "transport": "网络或佳明服务失败。本次已停止，已取得的数据保留；没有循环重试。",
    "different_account_refused": "账号与本地归档不一致，拒绝混合数据。原有凭据不应被新账号覆盖。",
    "another_command_is_running": "另一个本地命令正在运行，请等待它完成。",
    "archive_corrupt": "归档校验失败。已停止，不用损坏数据生成指标；请检查本地归档。",
}


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def parser():
    p = argparse.ArgumentParser(
        description="佳明大陆账号本地只读采集与恢复研究（非医疗/WHOOP算法）"
    )
    p.add_argument(
        "--data-dir", type=Path, default=Path.home() / ".local/share/garmin-readiness-cn"
    )
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("onboard", help="本机交互登录并读取最近3天；不创建自动任务")
    login = commands.add_parser("login", help="交互登录或验证已有token")
    login.add_argument("--reauth", action="store_true", help="交互重新认证；先核对账号再替换token")
    collect = commands.add_parser("sync", help="使用现有token，只读增量/历史同步（每天7类接口）")
    collect.add_argument("--days", type=int, default=3)
    collect.add_argument("--end", type=date.fromisoformat, default=None)
    commands.add_parser("status", help="离线覆盖清点，不显示账号或健康数值")
    trends = commands.add_parser("trends", help="离线长期趋势与分段比较；会显示健康统计")
    trends.add_argument("--days", type=int, default=90)
    trends.add_argument("--end", type=date.fromisoformat, default=None)
    trends.add_argument("--as-of", help="仅使用该时刻前已采集数据，必须包含时区")
    report = commands.add_parser(
        "report", help="离线计算并本地保存健康摘要/个人基线；会显示健康数值"
    )
    report.add_argument("--day", type=date.fromisoformat, default=None)
    report.add_argument("--as-of", help="只使用该时间前已采集的记录；必须包含时区")
    label = commands.add_parser("label", help="记录主观状态，留待未来拟合；1低/无，5高/重")
    label.add_argument("--day", type=date.fromisoformat, default=None)
    label.add_argument("--energy", type=int, required=True, choices=range(1, 6))
    label.add_argument("--soreness", type=int, required=True, choices=range(1, 6))
    label.add_argument("--before-viewing-score", action="store_true")
    return p


def run(args):
    today = datetime.now(TZ).date()
    day = getattr(args, "day", None) or today
    if day > today:
        raise LocalError("future_date_refused")
    end = getattr(args, "end", None) or today
    days = getattr(args, "days", 3)
    if end > today or not 1 <= days <= 180:
        raise LocalError("date_range_bound")
    archive = Archive(args.data_dir)
    with archive.lock():
        if args.command == "status":
            emit(archive.coverage())
        elif args.command in {"login", "onboard", "sync"}:
            interactive = args.command in {"login", "onboard"}
            force = getattr(args, "reauth", False)
            needs_input = force or not (archive.tokens / "garmin_tokens.json").exists()
            if interactive and needs_input and not (sys.stdin.isatty() and sys.stderr.isatty()):
                raise LocalError("tty_required")
            api = authenticate(archive, interactive=interactive, force=force)
            print("大陆账号认证成功；凭据仅保存在本机私有目录。", flush=True)
            if args.command != "login":
                result = sync(
                    archive,
                    api,
                    end - timedelta(days=days - 1),
                    end,
                    progress=lambda day, kind, state: print(f"{day} {kind}: {state}", flush=True),
                )
                emit(result)
                print(
                    "只读采集完成。status 查看覆盖；report 在本机查看健康摘要。未创建自动任务、未连接 Bot。"
                )
        elif args.command == "report":
            result = baseline_report(archive, day.isoformat(), cutoff=args.as_of)
            archive.save_report(result)
            emit(result)
        elif args.command == "trends":
            result = build_trends(archive, end - timedelta(days=days - 1), end, cutoff=args.as_of)
            archive.save_analysis(end.isoformat(), result["version"], result)
            emit(result)
        elif args.command == "label":
            archive.label(day.isoformat(), args.energy, args.soreness, args.before_viewing_score)
            print("主观记录已本地保存。尚未训练总分模型。")


def main():
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    args = parser().parse_args()
    try:
        run(args)
    except LocalError as exc:
        # LocalError codes are controlled by our code, not raw upstream exception strings.
        code = str(exc)
        print(f"未完成：{MESSAGES.get(code, code)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已停止；已完成的采集保留，未保存密码。", file=sys.stderr)
        return 130
    except Exception:
        # In particular never dump exception locals, HTTP errors or OAuth URLs.
        print("未完成：本地文件、输入或依赖异常。未输出敏感错误正文。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
