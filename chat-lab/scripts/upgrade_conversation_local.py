"""Explicit backed-up UX upgrade. Preserve schedules, cursor and user data; no model/digest run."""

import argparse
import ast
import json
import os
import pwd
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from deploy_agent_local import CONFIG, DATA, REPO, SOCKET, UNITS, admin, env_values, private, run


def schedule_snapshot(c):
    return c.execute(
        "SELECT id,enabled,generation,next_run,md5(instructions) FROM schedules ORDER BY id"
    ).fetchall()


def backfill_receipts(c):
    # Only recover authentic retained tool results for existing published jobs.
    # Do not generate searches, infer missing evidence, or import user-message bodies.
    jobs = c.execute("""SELECT t.id,t.attempt_id FROM schedule_runs r JOIN turns t ON t.id=r.turn_id
        WHERE r.state='published' AND t.state='succeeded'
        AND NOT EXISTS (SELECT 1 FROM turn_events e WHERE e.turn_id=t.id AND e.kind='research')
        ORDER BY r.created_at DESC LIMIT 4""").fetchall()
    count = 0
    for turn_id, attempt in jobs:
        seen = set()
        for worker in ("worker-a", "worker-b", "worker-c"):
            root = DATA / worker / "attempts" / str(attempt)
            if not root.exists():
                continue
            for path in root.rglob("*.jsonl"):
                if path.is_symlink() or path.stat().st_size > 10_000_000:
                    continue
                calls = {}
                for line in path.read_text().splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    message = event.get("data", {}).get("message", {})
                    for call in message.get("tool_calls", []):
                        if call.get("function", {}).get("name") == "search":
                            try:
                                calls[call["id"]] = json.loads(call["function"]["arguments"])
                            except (ValueError, KeyError):
                                pass
                    if (
                        event.get("type") != "tool/result"
                        or message.get("name") != "search"
                        or len(seen) >= 3
                        or message.get("tool_call_id") in seen
                    ):
                        continue
                    args = calls.get(message.get("tool_call_id"))
                    content = message.get("content", "")
                    if not args or not isinstance(content, str) or len(content) > 150000:
                        continue
                    try:
                        result = ast.literal_eval(content)
                    except (ValueError, SyntaxError):
                        continue
                    if not isinstance(result, dict) or not result.get("tool_usage"):
                        continue
                    receipt = {
                        "provenance": "retained_original_tool_log",
                        "query": str(args.get("query", ""))[:1500],
                        "source": args.get("source", ""),
                        "sources": result.get("sources", [])[:4],
                        "tool_usage": result["tool_usage"],
                        "text": result.get("text", "")[:3000],
                    }
                    payload = json.dumps(receipt, ensure_ascii=False)
                    if len(payload) > 16000:
                        continue
                    c.execute(
                        "INSERT INTO turn_events(turn_id,attempt_id,kind,text) VALUES (%s,%s,'research',%s)",
                        (turn_id, attempt, payload),
                    )
                    seen.add(message.get("tool_call_id"))
                    count += 1
    return count


def apply():
    os.umask(0o077)
    assert env_values(CONFIG / "worker.env").get("NIMBUS_AGENT_MODE") == "1"
    with admin() as c:
        assert not c.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='sessions' AND column_name='lane'"
        ).fetchone(), "Already migrated; inspect rather than blindly retry"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = DATA / "backups" / f"conversation-{stamp}"
    backup.mkdir(mode=0o700)
    for name in ("gateway.env", "worker.env"):
        private(CONFIG / name)
        shutil.copy2(CONFIG / name, backup / name)
    shutil.copy2(UNITS / "nimbus-chat-worker@.service", backup / "nimbus-chat-worker@.service")
    # Do not interrupt a user's active run to improve its UX.
    for _ in range(180):
        with admin() as c:
            active = c.execute(
                "SELECT count(*) FROM turns WHERE state IN ('queued','running','cancel_requested')"
            ).fetchone()[0]
        if not active:
            break
        time.sleep(1)
    assert active == 0, "Active work remains; retry maintenance later"
    run(
        "systemctl",
        "--user",
        "stop",
        "nimbus-chat-gateway.service",
        "nimbus-chat-scheduler.service",
    )
    # Close the intake/check race: allow any just-admitted work to finish normally.
    for _ in range(920):
        with admin() as c:
            active = c.execute(
                "SELECT count(*) FROM turns WHERE state IN ('queued','running','cancel_requested')"
            ).fetchone()[0]
        if not active:
            break
        time.sleep(1)
    assert active == 0, "Drain did not complete; inspect before restarting intake"
    run(
        "systemctl",
        "--user",
        "stop",
        "nimbus-chat-worker@a.service",
        "nimbus-chat-worker@b.service",
        "nimbus-chat-worker@c.service",
    )
    with admin() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM turns WHERE state IN ('queued','running','cancel_requested')"
            ).fetchone()[0]
            == 0
        )
        before = schedule_snapshot(c)
        identities = c.execute("SELECT * FROM allowlist ORDER BY bot_id,user_id,chat_id").fetchall()
        offsets = c.execute("SELECT id,poll_offset FROM bots ORDER BY id").fetchall()
    dump = backup / "pre-conversation.dump"
    run(
        "pg_dump",
        "-h",
        SOCKET,
        "-p",
        "55432",
        "-U",
        pwd.getpwuid(os.getuid()).pw_name,
        "-d",
        "nimbus_chat",
        "-Fc",
        "-f",
        str(dump),
    )
    dump.chmod(0o600)
    run("pg_restore", "--list", str(dump))
    with admin() as c:
        c.execute("SET LOCAL ROLE nimbus_chat_owner")
        c.execute((REPO / "chat-lab/src/nimbus_chat_lab/conversation_schema.sql").read_text())
        recovered = backfill_receipts(c)
        assert schedule_snapshot(c) == before
        assert (
            c.execute("SELECT * FROM allowlist ORDER BY bot_id,user_id,chat_id").fetchall()
            == identities
        )
        assert c.execute("SELECT id,poll_offset FROM bots ORDER BY id").fetchall() == offsets
    shutil.copy2(
        REPO / "chat-lab/deploy/nimbus-chat-worker@.service", UNITS / "nimbus-chat-worker@.service"
    )
    for instance in ("b", "c"):
        dropin = UNITS / f"nimbus-chat-worker@{instance}.service.d"
        dropin.mkdir(exist_ok=True)
        path = dropin / "lane.conf"
        if path.exists():
            shutil.copy2(path, backup / f"worker-{instance}-lane.conf")
        shutil.copy2(REPO / "chat-lab/deploy/background-lane.conf", path)
    run("systemctl", "--user", "daemon-reload")
    units = [
        "nimbus-chat-worker@a.service",
        "nimbus-chat-worker@b.service",
        "nimbus-chat-worker@c.service",
        "nimbus-chat-scheduler.service",
        "nimbus-chat-gateway.service",
    ]
    run(
        "systemd-analyze",
        "--user",
        "verify",
        *(
            str(UNITS / "nimbus-chat-worker@.service"),
            str(UNITS / "nimbus-chat-scheduler.service"),
            str(UNITS / "nimbus-chat-gateway.service"),
        ),
    )
    run("systemctl", "--user", "enable", "--now", *units)
    time.sleep(3)
    lanes = {}
    for instance, expected in (("a", "chat"), ("b", "jobs"), ("c", "jobs")):
        unit = f"nimbus-chat-worker@{instance}.service"
        run("systemctl", "--user", "is-active", "--quiet", unit)
        pid = int(run("systemctl", "--user", "show", unit, "-p", "MainPID", "--value").stdout)
        # Only this non-secret key may leave the local process inspection.
        entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        actual = next(
            e.split(b"=", 1)[1].decode() for e in entries if e.startswith(b"NIMBUS_WORKER_LANE=")
        )
        assert actual == expected
        lanes[instance] = actual
    for unit in units:
        run("systemctl", "--user", "is-active", "--quiet", unit)
    report = {
        "at_utc": stamp,
        "backup": str(backup),
        "schedules_preserved": True,
        "schedule_count": len(before),
        "identity_and_cursor_preserved_at_migration": True,
        "historical_search_receipts_recovered": recovered,
        "worker_lanes": lanes,
        "services_active": True,
        "digest_reexecutions": 0,
        "model_calls": 0,
    }
    (backup / "upgrade.json").write_text(json.dumps(report, indent=2) + "\n")
    (REPO / ".artifacts/conversation-deployment.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    try:
        apply()
    except Exception as error:
        print(
            f"UPGRADE STOPPED: {type(error).__name__}; inspect private backup and service state, no automatic retry.",
            file=sys.stderr,
        )
        sys.exit(1)
