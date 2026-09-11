"""One-time local Agent cutover. Requires --apply; no Telegram subscriptions are created.

Drains/stops ingress, backs up/restores PG, applies additive schema, starts real worker
and scheduler, runs an isolated-history operator canary, removes ONLY canary data,
and finally starts ingress. On failure it leaves ingress stopped for inspection.
Secrets are read locally and never placed in argv or printed.
"""

import argparse
import asyncio
import io
import json
import os
import pwd
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from nimbus_chat_lab.agent_sandbox import Sandbox
from nimbus_chat_lab.store import Store

HOME = Path.home()
REPO = Path(__file__).resolve().parents[2]
CONFIG = HOME / ".config/nimbus-chat-lab"
DATA = HOME / ".local/share/nimbus-chat-lab"
UNITS = HOME / ".config/systemd/user"
SOCKET = f"/run/user/{os.getuid()}/nimbus-chat-postgres"
ENV = {
    k: os.environ[k]
    for k in ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
    if k in os.environ
}


def run(*args, timeout=160):
    return subprocess.run(
        args, check=True, capture_output=True, text=True, env=ENV, timeout=timeout
    )


def admin(database="nimbus_chat", autocommit=False):
    return psycopg.connect(
        host=SOCKET,
        port=55432,
        user=pwd.getpwuid(os.getuid()).pw_name,
        dbname=database,
        autocommit=autocommit,
    )


def private(path):
    info = path.lstat()
    if (
        path.is_symlink()
        or not path.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise PermissionError("Private owned regular configuration required")


def env_values(path):
    private(path)
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )


def update_env(path, values):
    existing = env_values(path)
    existing.update(values)
    temp = path.with_suffix(".agent-new")
    temp.write_text("".join(f"{k}={v}\n" for k, v in existing.items()))
    temp.chmod(0o600)
    temp.replace(path)


async def canary(report, identity):
    store = Store(env_values(CONFIG / "worker.env")["NIMBUS_LAB_DSN"], agent_mode=True)
    turn_id = uuid4()
    nonce = "AGENT-LIVE-" + uuid4().hex[:12]
    async with await store.connect() as c:
        assert not (await (await c.execute("SELECT 1 FROM schedules LIMIT 1")).fetchone())
        assert not (await (await c.execute("SELECT 1 FROM agent_memory LIMIT 1")).fetchone())
        assert not (await (await c.execute("SELECT 1 FROM agent_workspaces LIMIT 1")).fetchone())
        session = await (
            await c.execute(
                "SELECT * FROM sessions WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND thread_id=0",
                identity,
            )
        ).fetchone()
        assert session and session["epoch"] >= 0
        assert not await (
            await c.execute(
                "SELECT 1 FROM turns WHERE state IN ('queued','running','cancel_requested') LIMIT 1"
            )
        ).fetchone()
        prompt = f"OPERATOR DEPLOYMENT CANARY, not a Telegram user message. Actually write {nonce} to deployment-proof.txt in workspace, read it back, set persistent memory key deployment_canary to exactly that value, read it back, and reply with exactly {nonce}. Do not create or modify any schedule. No search needed. This synthetic history epoch will be deleted before Telegram ingress resumes."
        await c.execute(
            "INSERT INTO turns(id,session_id,epoch,state,input) VALUES (%s,%s,-1,'queued',%s)",
            (turn_id, session["id"], prompt),
        )
    final = None
    for _ in range(900):
        await asyncio.sleep(1)
        async with await store.connect() as c:
            final = await (
                await c.execute("SELECT state,result,attempt_id FROM turns WHERE id=%s", (turn_id,))
            ).fetchone()
        if final["state"] in ("succeeded", "failed", "interrupted", "cancelled"):
            break
    assert final and final["state"] == "succeeded" and nonce in final["result"]
    async with await store.connect() as c:
        memory = await (
            await c.execute(
                "SELECT value FROM agent_memory WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND key='deployment_canary'",
                identity,
            )
        ).fetchone()
        workspace = await (
            await c.execute(
                "SELECT archive FROM agent_workspaces WHERE bot_id=%s AND user_id=%s AND chat_id=%s",
                identity,
            )
        ).fetchone()
        assert memory and memory["value"] == nonce and workspace
        with zipfile.ZipFile(io.BytesIO(workspace["archive"])) as z:
            assert z.read("deployment-proof.txt").decode() == nonce
        assert not await (await c.execute("SELECT 1 FROM schedules LIMIT 1")).fetchone()
        attempts = (
            await (
                await c.execute("SELECT count(*) AS n FROM attempts WHERE turn_id=%s", (turn_id,))
            ).fetchone()
        )["n"]
        pending = await (
            await c.execute(
                "SELECT state,tries FROM outbox WHERE dedupe_key LIKE %s", (f"final:{turn_id}:%",)
            )
        ).fetchall()
        assert (
            attempts == 1 and len(pending) == 1 and pending[0] == {"state": "pending", "tries": 0}
        )
        # Gateway is stopped, no real inbound work can race this cleanup. Remove
        # only this nonce, exact archive, epoch -1 turn and its own audit/outbox rows.
        await c.execute(
            "DELETE FROM agent_memory WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND key='deployment_canary' AND value=%s",
            (*identity, nonce),
        )
        await c.execute(
            "DELETE FROM agent_workspaces WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND archive=%s",
            (*identity, workspace["archive"]),
        )
        await c.execute("DELETE FROM outbox WHERE dedupe_key LIKE %s", (f"final:{turn_id}:%",))
        await c.execute("DELETE FROM turn_events WHERE turn_id=%s", (turn_id,))
        await c.execute("DELETE FROM attempts WHERE turn_id=%s", (turn_id,))
        await c.execute("DELETE FROM turns WHERE id=%s AND epoch=-1", (turn_id,))
    report["production_canary"] = {
        "transport": "OPERATOR SYNTHETIC TURN / REAL PRODUCTION PG AND USER WORKER UNIT / TELEGRAM DELIVERY SUPPRESSED",
        "native_agent_succeeded": True,
        "workspace_roundtrip": True,
        "memory_roundtrip": True,
        "one_attempt": True,
        "canary_data_removed": True,
        "daily_tasks_created": 0,
        "telegram_delivery_attempts": 0,
    }


def apply():
    os.umask(0o077)
    private(CONFIG / "agent-runtime.json")
    if env_values(CONFIG / "worker.env").get("NIMBUS_AGENT_MODE") == "1":
        raise RuntimeError("Already in Agent mode; this is not a blind retry or upgrade command")
    config = json.loads((CONFIG / "agent-runtime.json").read_text())
    Sandbox(None, None, config).verify_runtime()
    run("podman", "image", "exists", config["image"])
    with admin() as c:
        identities = c.execute('SELECT bot_id,user_id,chat_id FROM allowlist').fetchall()
        assert len(identities) == 1
        identity = identities[0]  # Existing operator-confirmed identity; never add/infer a sender.
        assert identity[0] == int(env_values(CONFIG / 'gateway.env')['NIMBUS_BOT_ID'])
        assert identity[1] == identity[2] and identity[1] > 0
        assert (
            c.execute(
                "SELECT count(*) FROM turns WHERE state IN ('queued','running','cancel_requested')"
            ).fetchone()[0]
            == 0
        )
        if c.execute("SELECT to_regclass('public.schedules')").fetchone()[0]:
            assert c.execute("SELECT count(*) FROM schedules").fetchone()[0] == 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = DATA / "backups" / f"agent-cutover-{stamp}"
    backup.mkdir(mode=0o700)
    report = {"at_utc": stamp, "backup": str(backup), "daily_tasks_created": 0}
    for name in ("gateway.env", "worker.env", "agent-runtime.json"):
        private(CONFIG / name)
        shutil.copy2(CONFIG / name, backup / name)
    for name in ("nimbus-chat-gateway.service", "nimbus-chat-worker@.service"):
        shutil.copy2(UNITS / name, backup / name)
    run(
        "systemctl", "--user", "stop", "nimbus-chat-gateway.service", "nimbus-chat-worker@a.service"
    )
    dump = backup / "pre-agent.dump"
    pgargs = ["-h", SOCKET, "-p", "55432", "-U", pwd.getpwuid(os.getuid()).pw_name]
    run("pg_dump", *pgargs, "-d", "nimbus_chat", "-Fc", "-f", str(dump))
    dump.chmod(0o600)
    probe = "nimbus_agent_restore_" + uuid4().hex[:10]
    with admin("postgres", True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(probe)))
    try:
        run("pg_restore", *pgargs, "--exit-on-error", "-d", probe, str(dump))
        with admin() as live, admin(probe) as restored:
            query = "SELECT (SELECT count(*) FROM inbox),(SELECT count(*) FROM turns),(SELECT count(*) FROM outbox)"
            assert live.execute(query).fetchone() == restored.execute(query).fetchone()
    finally:
        with admin("postgres", True) as c:
            c.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(probe)))
    report["pre_cutover_backup_restore"] = True
    with admin() as c:
        c.execute("SET LOCAL ROLE nimbus_chat_owner")
        c.execute((REPO / "chat-lab/src/nimbus_chat_lab/agent_schema.sql").read_text())
        c.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO nimbus_chat")
        c.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO nimbus_chat")
        assert c.execute("SELECT count(*) FROM schedules").fetchone()[0] == 0
    report["additive_migration"] = True
    update_env(CONFIG / "gateway.env", {"NIMBUS_AGENT_MODE": "1"})
    update_env(
        CONFIG / "worker.env",
        {
            "NIMBUS_AGENT_MODE": "1",
            "NIMBUS_AGENT_RUNTIME_CONFIG": str(CONFIG / "agent-runtime.json"),
        },
    )
    for name in ("nimbus-chat-worker@.service", "nimbus-chat-scheduler.service"):
        shutil.copy2(REPO / "chat-lab/deploy" / name, UNITS / name)
    run(
        "systemd-analyze",
        "--user",
        "verify",
        str(UNITS / "nimbus-chat-worker@.service"),
        str(UNITS / "nimbus-chat-scheduler.service"),
    )
    run("systemctl", "--user", "daemon-reload")
    run(
        "systemctl",
        "--user",
        "enable",
        "--now",
        "nimbus-chat-worker@a.service",
        "nimbus-chat-scheduler.service",
    )
    asyncio.run(canary(report, identity))
    run("systemctl", "--user", "enable", "--now", "nimbus-chat-gateway.service")
    for unit in (
        "nimbus-chat-postgres.service",
        "nimbus-chat-gateway.service",
        "nimbus-chat-worker@a.service",
        "nimbus-chat-scheduler.service",
    ):
        run("systemctl", "--user", "is-active", "--quiet", unit)
    report["services_active"] = True
    report["linger"] = (
        run(
            "loginctl", "show-user", pwd.getpwuid(os.getuid()).pw_name, "-p", "Linger", "--value"
        ).stdout.strip()
        == "yes"
    )
    report["live_user_telegram_acceptance"] = (
        "PENDING USER MESSAGE; no Telegram inbound or timed delivery has been accepted by this canary"
    )
    out = REPO / ".artifacts/agent-local-deployment.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    (backup / "cutover.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    try:
        apply()
    except Exception as error:
        print(
            f"CUTOVER STOPPED: {type(error).__name__}; ingress may be stopped. Inspect private backup and services; no automatic retry.",
            file=sys.stderr,
        )
        sys.exit(1)
