"""Schedule user-authorized AgentOS turns; publication reuses the existing durable outbox."""

import argparse
import asyncio
import os
import signal
from datetime import timedelta, timezone
from uuid import uuid4

from .agent_state import LOCK, next_slot, recent_slot
from .store import Store


class Scheduler:
    def __init__(self, store):
        self.store = store

    async def tick(self):
        async with await self.store.connect() as c:
            await c.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            now = (await (await c.execute("SELECT clock_timestamp() AS t")).fetchone())["t"]
            due = await (
                await c.execute(
                    "SELECT * FROM schedules WHERE enabled AND next_run-lead_minutes*interval '1 minute'<=clock_timestamp() ORDER BY next_run FOR UPDATE LIMIT 64"
                )
            ).fetchall()
            for s in due:
                allowed = await (
                    await c.execute(
                        "SELECT 1 FROM allowlist WHERE bot_id=%s AND user_id=%s AND chat_id=%s",
                        (s["bot_id"], s["user_id"], s["chat_id"]),
                    )
                ).fetchone()
                if not allowed:
                    await c.execute(
                        "UPDATE schedules SET enabled=false,generation=generation+1 WHERE id=%s",
                        (s["id"],),
                    )
                    continue
                target = max(
                    s["next_run"],
                    recent_slot(
                        now + timedelta(minutes=s["lead_minutes"]),
                        s["timezone"],
                        s["hour"],
                        s["minute"],
                    ),
                )
                if target > s["next_run"]:
                    await c.execute(
                        "INSERT INTO schedule_runs(id,schedule_id,generation,slot,state,expires_at) VALUES (%s,%s,%s,%s,'expired',%s) ON CONFLICT DO NOTHING",
                        (
                            uuid4(),
                            s["id"],
                            s["generation"],
                            s["next_run"],
                            s["next_run"] + timedelta(hours=4),
                        ),
                    )
                expiry = target + timedelta(hours=4)
                await c.execute(
                    "INSERT INTO schedule_runs(id,schedule_id,generation,slot,state,expires_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (
                        uuid4(),
                        s["id"],
                        s["generation"],
                        target,
                        "queued" if expiry > now else "expired",
                        expiry,
                    ),
                )
                # Advance beyond this slot, not beyond the 5-minute early preparation time.
                nxt = next_slot(
                    max(now, target) + timedelta(seconds=1),
                    s["timezone"],
                    s["hour"],
                    s["minute"],
                )
                await c.execute("UPDATE schedules SET next_run=%s WHERE id=%s", (nxt, s["id"]))
            runs = await (
                await c.execute(
                    """SELECT r.*,s.bot_id,s.user_id,s.chat_id,s.name,s.instructions,s.data_scope,s.generation AS current_generation,s.enabled
                    FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id LEFT JOIN turns t ON t.id=r.turn_id
                    WHERE r.state IN ('queued','attached') AND (
                      r.expires_at<=clock_timestamp() OR r.generation<>s.generation
                      OR (NOT s.enabled AND r.manual_key IS NULL)
                      OR NOT EXISTS (SELECT 1 FROM allowlist a WHERE (a.bot_id,a.user_id,a.chat_id)=(s.bot_id,s.user_id,s.chat_id))
                      OR (r.state='attached' AND r.slot<=clock_timestamp() AND t.state IN ('succeeded','failed','interrupted','cancelled'))
                      OR (r.state='queued' AND NOT EXISTS (
                        SELECT 1 FROM turns active JOIN sessions sess ON sess.id=active.session_id
                        WHERE (sess.bot_id,sess.user_id,sess.chat_id,sess.thread_id)=(s.bot_id,s.user_id,s.chat_id,0)
                        AND sess.lane='job:'||s.id::text AND active.state IN ('queued','running','cancel_requested')))
                    ) ORDER BY r.created_at,r.id FOR UPDATE OF r LIMIT 64"""
                )
            ).fetchall()
            for r in runs:
                identity = (r["bot_id"], r["user_id"], r["chat_id"])
                allowed = await (
                    await c.execute(
                        "SELECT 1 FROM allowlist WHERE bot_id=%s AND user_id=%s AND chat_id=%s FOR KEY SHARE",
                        identity,
                    )
                ).fetchone()
                invalid = (
                    not allowed
                    or r["generation"] != r["current_generation"]
                    or (not r["enabled"] and r["manual_key"] is None)
                )
                expired = r["expires_at"] <= now
                if invalid or expired:
                    await c.execute(
                        "UPDATE schedule_runs SET state=%s WHERE id=%s",
                        ("expired" if expired else "cancelled", r["id"]),
                    )
                    if r["turn_id"]:
                        await c.execute(
                            "UPDATE turns SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'cancel_requested' END WHERE id=%s AND state IN ('queued','running')",
                            (r["turn_id"],),
                        )
                    continue
                if r["state"] == "queued":
                    await c.execute(
                        "INSERT INTO sessions(id,bot_id,user_id,chat_id,thread_id,lane) VALUES (%s,%s,%s,%s,0,%s) ON CONFLICT DO NOTHING",
                        (uuid4(), *identity, "job:" + str(r["schedule_id"])),
                    )
                    session = await (
                        await c.execute(
                            "SELECT * FROM sessions WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND thread_id=0 AND lane=%s FOR UPDATE",
                            (*identity, "job:" + str(r["schedule_id"])),
                        )
                    ).fetchone()
                    busy = await (
                        await c.execute(
                            "SELECT 1 FROM turns WHERE session_id=%s AND state IN ('queued','running','cancel_requested')",
                            (session["id"],),
                        )
                    ).fetchone()
                    count = (
                        await (
                            await c.execute(
                                "SELECT count(*) AS n FROM turns WHERE state IN ('queued','running','cancel_requested')"
                            )
                        ).fetchone()
                    )["n"]
                    if busy or count >= self.store.queue_limit:
                        continue
                    tid = uuid4()
                    prompt = (
                        f"Scheduled task: {r['name']}\nTask ID: {r['schedule_id']}\nPlanned delivery UTC: {r['slot'].astimezone(timezone.utc).isoformat()}\n"
                        f"Execution began UTC: {now.astimezone(timezone.utc).isoformat()}. Use actual current dates, not training-time dates.\n"
                        "Produce the requested deliverable as your final answer. The platform handles delivery; do not create another schedule or claim you sent a message yourself.\n\n"
                        + r["instructions"]
                    )
                    await c.execute(
                        "INSERT INTO turns(id,session_id,epoch,state,input,data_scope) VALUES (%s,%s,%s,'queued',%s,%s)",
                        (tid, session["id"], session["epoch"], prompt, r["data_scope"]),
                    )
                    await c.execute(
                        "UPDATE schedule_runs SET state='attached',turn_id=%s WHERE id=%s",
                        (tid, r["id"]),
                    )
                else:
                    turn = await (
                        await c.execute(
                            "SELECT state,result FROM turns WHERE id=%s FOR UPDATE", (r["turn_id"],)
                        )
                    ).fetchone()
                    if (
                        turn["state"] in ("queued", "running", "cancel_requested")
                        or now < r["slot"]
                    ):
                        continue
                    text = (
                        turn["result"]
                        if turn["state"] == "succeeded"
                        else f"{r['name']}这次没有完成，已经停下，没有自动重跑。你可以让我查一下或再试一次。"
                    )
                    await self.store._notify(
                        c,
                        f"scheduled:{r['id']}",
                        {
                            "bot_id": r["bot_id"],
                            "user_id": r["user_id"],
                            "chat_id": r["chat_id"],
                            "thread_id": 0,
                        },
                        text,
                        schedule_run_id=r["id"],
                        schedule_generation=r["generation"],
                    )
                    await c.execute(
                        "UPDATE schedule_runs SET state='published' WHERE id=%s", (r["id"],)
                    )

    async def run(self, stop):
        while not stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except TimeoutError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    async def execute():
        scheduler = Scheduler(Store(os.environ["NIMBUS_LAB_DSN"]))
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        if args.once:
            await scheduler.tick()
        else:
            await scheduler.run(stop)

    try:
        asyncio.run(execute())
    except Exception as e:
        print("Scheduler stopped:", type(e).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
