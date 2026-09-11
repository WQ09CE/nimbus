"""PostgreSQL is authority; no Redis assumptions and no automatic interrupted replay."""

from dataclasses import dataclass
from importlib.resources import files
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from .telegram import parse_update, split_text


@dataclass(frozen=True)
class Claim:
    turn_id: UUID
    attempt_id: UUID
    generation: int
    incarnation: UUID
    input: str
    session_id: UUID
    epoch: int


class Store:
    def __init__(
        self, dsn: str, *, lease_seconds: float = 30, queue_limit: int = 64, agent_mode=False
    ):
        if lease_seconds <= 0 or not 1 <= queue_limit <= 1000:
            raise ValueError("Invalid lease/queue budget")
        self.dsn, self.lease_seconds, self.queue_limit = dsn, lease_seconds, queue_limit
        self.agent_mode = agent_mode

    async def connect(self):
        return await psycopg.AsyncConnection.connect(
            self.dsn,
            row_factory=dict_row,
            connect_timeout=3,
            options="-c statement_timeout=5000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=10000",
        )

    async def initialize(self):
        async with await self.connect() as c:
            await c.execute(files(__package__).joinpath("schema.sql").read_text())
            await c.execute(files(__package__).joinpath("agent_schema.sql").read_text())

    async def authorize(self, bot: int, user: int, chat: int):
        async with await self.connect() as c:
            await c.execute("INSERT INTO bots(id) VALUES (%s) ON CONFLICT DO NOTHING", (bot,))
            await c.execute(
                "INSERT INTO allowlist VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (bot, user, chat)
            )

    async def offset(self, bot: int) -> int:
        async with await self.connect() as c:
            r = await (
                await c.execute("SELECT poll_offset FROM bots WHERE id=%s", (bot,))
            ).fetchone()
            if not r:
                raise ValueError("Bot has no operator-provisioned allowlist")
            return r["poll_offset"]

    async def _notify(
        self,
        c,
        key: str,
        session: dict,
        text: str,
        *,
        schedule_run_id=None,
        schedule_generation=None,
    ):
        allowed = await (
            await c.execute(
                "SELECT 1 FROM allowlist WHERE bot_id=%s AND user_id=%s AND chat_id=%s FOR KEY SHARE",
                (session["bot_id"], session["user_id"], session["chat_id"]),
            )
        ).fetchone()
        if not allowed:
            return
        parts = split_text(text[:16000], limit=3400)
        for i, part in enumerate(parts):
            if len(parts) > 1:
                part = f"[{key.split(':')[-1][:8]} · {i + 1}/{len(parts)}]\n{part}"
            await c.execute(
                """INSERT INTO outbox(id,dedupe_key,bot_id,chat_id,thread_id,text,user_id,schedule_run_id,schedule_generation)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dedupe_key) DO NOTHING""",
                (
                    uuid4(),
                    f"{key}:{i}",
                    session["bot_id"],
                    session["chat_id"],
                    session["thread_id"],
                    part,
                    session["user_id"],
                    schedule_run_id,
                    schedule_generation,
                ),
            )

    async def ingest(self, bot: int, username: str, updates: list[dict]) -> list[str]:
        """A batch, its rejected dispositions, commands and poll cursor commit together."""
        if len(updates) > 100 or any(
            type(u.get("update_id")) is not int or u["update_id"] < 0 for u in updates
        ):
            raise ValueError("Invalid update batch")
        dispositions = []
        async with await self.connect() as c:
            # Single-host ingress is also flocked. This lock bounds admission across bots.
            await c.execute("SELECT pg_advisory_xact_lock(74192631)")
            for update in sorted(updates, key=lambda u: u["update_id"]):
                uid = update["update_id"]
                inserted = await (
                    await c.execute(
                        """INSERT INTO inbox(bot_id,update_id,disposition)
                    VALUES (%s,%s,'ignored') ON CONFLICT DO NOTHING RETURNING update_id""",
                        (bot, uid),
                    )
                ).fetchone()
                if not inserted:
                    dispositions.append("duplicate")
                    continue
                try:
                    command = parse_update(update, bot, username)
                except (ValueError, TypeError, AttributeError, KeyError):
                    command = None
                disposition, turn_id = "ignored", None
                if command:
                    allowed = await (
                        await c.execute(
                            "SELECT 1 FROM allowlist WHERE bot_id=%s AND user_id=%s AND chat_id=%s FOR KEY SHARE",
                            (bot, command.user_id, command.chat_id),
                        )
                    ).fetchone()
                    disposition = "rejected"
                    if allowed:
                        await c.execute(
                            """INSERT INTO sessions(id,bot_id,user_id,chat_id,thread_id)
                            VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (uuid4(), bot, command.user_id, command.chat_id, command.thread_id),
                        )
                        session = await (
                            await c.execute(
                                """SELECT * FROM sessions
                            WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND thread_id=%s FOR UPDATE""",
                                (bot, command.user_id, command.chat_id, command.thread_id),
                            )
                        ).fetchone()
                        active = await (
                            await c.execute(
                                """SELECT * FROM turns WHERE session_id=%s
                            AND state IN ('queued','running','cancel_requested') FOR UPDATE""",
                                (session["id"],),
                            )
                        ).fetchone()
                        disposition = command.action
                        key = f"in:{bot}:{uid}"
                        if command.action == "chat":
                            count = await (
                                await c.execute(
                                    "SELECT count(*) AS n FROM turns WHERE state IN ('queued','running','cancel_requested')"
                                )
                            ).fetchone()
                            if active or count["n"] >= self.queue_limit:
                                disposition = "busy"
                                await self._notify(
                                    c,
                                    key,
                                    session,
                                    "已有任务执行中或队列已满。请用 /status 或 /cancel，稍后再试。",
                                )
                            else:
                                turn_id = uuid4()
                                await c.execute(
                                    "INSERT INTO turns(id,session_id,epoch,state,input) VALUES (%s,%s,%s,'queued',%s)",
                                    (turn_id, session["id"], session["epoch"], command.text),
                                )
                                await self._notify(
                                    c, key, session, f"[{str(turn_id)[:8]}] queued · 已持久接收"
                                )
                        elif command.action == "cancel":
                            if active:
                                state = (
                                    "cancelled"
                                    if active["state"] == "queued"
                                    else "cancel_requested"
                                )
                                await c.execute(
                                    "UPDATE turns SET state=%s, finished_at=CASE WHEN %s='cancelled' THEN clock_timestamp() ELSE NULL END WHERE id=%s",
                                    (state, state, active["id"]),
                                )
                                await self._notify(
                                    c,
                                    key,
                                    session,
                                    f"[{str(active['id'])[:8]}] {state} · "
                                    + (
                                        "尚未执行，已取消"
                                        if state == "cancelled"
                                        else "已请求停止，尚未确认"
                                    ),
                                )
                            else:
                                await self._notify(c, key, session, "没有待取消的任务。")
                        elif command.action == "new":
                            if active:
                                await self._notify(
                                    c, key, session, "当前任务尚未结束，请先 /cancel 并确认停止。"
                                )
                            else:
                                await c.execute(
                                    "UPDATE sessions SET epoch=epoch+1 WHERE id=%s",
                                    (session["id"],),
                                )
                                await self._notify(
                                    c,
                                    key,
                                    session,
                                    "已开始新对话；旧任务记录保留，但不再作为上下文。",
                                )
                        elif command.action == "status":
                            latest = (
                                active
                                or await (
                                    await c.execute(
                                        "SELECT * FROM turns WHERE session_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
                                        (session["id"],),
                                    )
                                ).fetchone()
                            )
                            text = (
                                "尚无任务。"
                                if not latest
                                else f"[{str(latest['id'])[:8]}] {latest['state']}\n{latest['result']}"
                            )
                            await self._notify(c, key, session, text)
                        elif command.action == "mem":
                            await self._notify(
                                c,
                                key,
                                session,
                                (
                                    "Agent 模式：可以通过自然语言保存、查询和删除长期记忆。/new 仅重置近期对话，不删除已保存记忆。"
                                    if self.agent_mode
                                    else "本阶段只有当前对话近期上下文；跨会话长期记忆尚未启用。/new 可清空后续上下文。"
                                ),
                            )
                        else:
                            await self._notify(
                                c,
                                key,
                                session,
                                (
                                    "Nimbus Agent 模式 · 私人白名单\n直接说目标即可：隔离工作区里的代码／文件操作、X／Web 搜索、长期记忆和持久化每日任务。\n可说：创建每天北京时间 08:00 的任务，并立即试跑。\n/status /cancel /new /mem\n定时任务需要本机开机联网；/cancel 取消当前执行，停用定时任务请在执行结束后直接告诉我。"
                                    if self.agent_mode
                                    else "Nimbus Telegram lab · 私人白名单\n直接发文字开始。/status /cancel /new /mem\n当前为无工具对话阶段，代码执行、文件和长期记忆尚未启用。"
                                ),
                            )
                await c.execute(
                    "UPDATE inbox SET disposition=%s,turn_id=%s WHERE bot_id=%s AND update_id=%s",
                    (disposition, turn_id, bot, uid),
                )
                # Do not skip an uncommitted update. A stale replay never lowers the cursor.
                await c.execute(
                    "UPDATE bots SET poll_offset=greatest(poll_offset,%s) WHERE id=%s",
                    (uid + 1, bot),
                )
                dispositions.append(disposition)
        return dispositions

    async def claim(self, incarnation: UUID) -> Claim | None:
        async with await self.connect() as c:
            t = await (
                await c.execute(
                    "SELECT * FROM turns WHERE state='queued' AND id IN (SELECT id FROM live_turn_authority) ORDER BY created_at,id LIMIT 1"
                )
            ).fetchone()
            if not t:
                return None
            if not await self._authorize_turn(c, t["id"]):
                return None
            t = await (
                await c.execute(
                    "SELECT * FROM turns WHERE id=%s AND state='queued' AND id IN (SELECT id FROM live_turn_authority) FOR UPDATE SKIP LOCKED",
                    (t["id"],),
                )
            ).fetchone()
            if not t:
                return None
            attempt, generation = uuid4(), t["generation"] + 1
            await c.execute(
                """INSERT INTO attempts(id,turn_id,generation,incarnation,lease_until,state)
                VALUES (%s,%s,%s,%s,least(clock_timestamp()+%s*interval '1 second',coalesce((SELECT expires_at FROM schedule_runs WHERE turn_id=%s),'infinity'::timestamptz)),'running')""",
                (attempt, t["id"], generation, incarnation, self.lease_seconds, t["id"]),
            )
            await c.execute(
                "UPDATE turns SET state='running',attempt_id=%s,generation=%s WHERE id=%s",
                (attempt, generation, t["id"]),
            )
            return Claim(
                t["id"], attempt, generation, incarnation, t["input"], t["session_id"], t["epoch"]
            )

    async def _authorize_turn(self, c, turn_id):
        return await (
            await c.execute(
                "SELECT 1 FROM allowlist a JOIN sessions s ON (a.bot_id,a.user_id,a.chat_id)=(s.bot_id,s.user_id,s.chat_id) JOIN turns t ON t.session_id=s.id WHERE t.id=%s FOR KEY SHARE OF a",
                (turn_id,),
            )
        ).fetchone()

    async def _owned(self, c, claim: Claim):
        if not await self._authorize_turn(c, claim.turn_id):
            return None
        t = await (
            await c.execute(
                """SELECT * FROM turns WHERE id=%s AND attempt_id=%s AND generation=%s
            AND state IN ('running','cancel_requested') AND id IN (SELECT id FROM live_turn_authority) FOR UPDATE""",
                (claim.turn_id, claim.attempt_id, claim.generation),
            )
        ).fetchone()
        if not t:
            return None
        a = await (
            await c.execute(
                """SELECT 1 FROM attempts WHERE id=%s AND incarnation=%s AND generation=%s
            AND state='running' AND lease_until > clock_timestamp()""",
                (claim.attempt_id, claim.incarnation, claim.generation),
            )
        ).fetchone()
        return t if a else None

    async def control(self, claim: Claim, *, renew=False) -> str | None:
        async with await self.connect() as c:
            t = await self._owned(c, claim)
            if t and renew:
                renewed = await (
                    await c.execute(
                        """UPDATE attempts SET lease_until=least(clock_timestamp()+%s*interval '1 second',coalesce((SELECT expires_at FROM schedule_runs r WHERE r.turn_id=attempts.turn_id),'infinity'::timestamptz))
                    WHERE id=%s AND incarnation=%s AND generation=%s AND state='running'
                    AND lease_until>clock_timestamp() AND turn_id IN (SELECT id FROM live_turn_authority) RETURNING id""",
                        (self.lease_seconds, claim.attempt_id, claim.incarnation, claim.generation),
                    )
                ).fetchone()
                if not renewed:
                    return None
            return t["state"] if t else None

    async def progress(self, claim: Claim, text: str) -> bool:
        async with await self.connect() as c:
            t = await self._owned(c, claim)
            if not t or t["state"] != "running":
                return False
            count = await (
                await c.execute(
                    "SELECT count(*) AS n FROM turn_events WHERE turn_id=%s", (claim.turn_id,)
                )
            ).fetchone()
            if count["n"] >= 120:
                return (
                    True  # Drop transient snapshots after the bounded budget; final still persists.
                )
            inserted = await (
                await c.execute(
                    """INSERT INTO turn_events(turn_id,attempt_id,kind,text)
                SELECT %s,id,'text',%s FROM attempts WHERE id=%s AND incarnation=%s
                AND generation=%s AND state='running' AND lease_until>clock_timestamp()
                AND turn_id IN (SELECT id FROM live_turn_authority) RETURNING seq""",
                    (
                        claim.turn_id,
                        text[:16000],
                        claim.attempt_id,
                        claim.incarnation,
                        claim.generation,
                    ),
                )
            ).fetchone()
            return inserted is not None

    async def finish(self, claim: Claim, state: str, text: str) -> bool:
        if state not in {"succeeded", "failed", "cancelled", "interrupted"}:
            raise ValueError("Invalid terminal state")
        async with await self.connect() as c:
            t = await self._owned(c, claim)
            if not t:
                return False
            if t["state"] == "cancel_requested" and state == "succeeded":
                return False
            # This conditional transition, not the earlier read, is the write's
            # authority linearization point. The turn lock serializes scanner/cancel.
            finished = await (
                await c.execute(
                    """UPDATE attempts SET state=%s WHERE id=%s AND incarnation=%s AND generation=%s
                AND state='running' AND lease_until>clock_timestamp()
                AND turn_id IN (SELECT id FROM live_turn_authority) RETURNING id""",
                    (state, claim.attempt_id, claim.incarnation, claim.generation),
                )
            ).fetchone()
            if not finished:
                return False
            await c.execute(
                "UPDATE turns SET state=%s,result=%s,finished_at=clock_timestamp() WHERE id=%s",
                (state, text[:16000], claim.turn_id),
            )
            session = await (
                await c.execute("SELECT * FROM sessions WHERE id=%s", (claim.session_id,))
            ).fetchone()
            scheduled = await (
                await c.execute("SELECT 1 FROM schedule_runs WHERE turn_id=%s", (claim.turn_id,))
            ).fetchone()
            if not scheduled:
                await self._notify(
                    c,
                    f"final:{claim.turn_id}",
                    session,
                    f"[{str(claim.turn_id)[:8]}] {state}\n{text}",
                )
            return True

    async def recover(self) -> int:
        async with await self.connect() as c:
            await c.execute("SELECT 1 FROM allowlist ORDER BY bot_id,user_id,chat_id FOR KEY SHARE")
            await c.execute(
                "UPDATE turns SET state='cancelled',finished_at=clock_timestamp() WHERE state='queued' AND id NOT IN (SELECT id FROM live_turn_authority)"
            )
            rows = await (
                await c.execute("""SELECT t.* FROM turns t JOIN attempts a ON a.id=t.attempt_id
                WHERE t.state IN ('running','cancel_requested') AND a.lease_until <= clock_timestamp()
                FOR UPDATE OF t SKIP LOCKED""")
            ).fetchall()
            recovered = 0
            for t in rows:
                expired = await (
                    await c.execute(
                        "SELECT 1 FROM attempts WHERE id=%s AND state='running' AND lease_until<=clock_timestamp()",
                        (t["attempt_id"],),
                    )
                ).fetchone()
                if not expired:
                    continue
                text = "执行者租约已失效；任务中断，不自动重放。取消/外部效果未确认，请查询后再决定是否重试。"
                await c.execute(
                    "UPDATE turns SET state='interrupted',result=%s,generation=generation+1,finished_at=clock_timestamp() WHERE id=%s",
                    (text, t["id"]),
                )
                await c.execute(
                    "UPDATE attempts SET state='interrupted' WHERE id=%s", (t["attempt_id"],)
                )
                session = await (
                    await c.execute("SELECT * FROM sessions WHERE id=%s", (t["session_id"],))
                ).fetchone()
                scheduled = await (
                    await c.execute("SELECT 1 FROM schedule_runs WHERE turn_id=%s", (t["id"],))
                ).fetchone()
                if not scheduled:
                    await self._notify(
                        c, f"final:{t['id']}", session, f"[{str(t['id'])[:8]}] interrupted\n{text}"
                    )
                recovered += 1
            # Sending may have taken effect before the sender died. Never auto-retry it.
            await c.execute(
                "UPDATE outbox SET state='uncertain',error_class='sender_lost' WHERE state='sending' AND claimed_at < clock_timestamp()-interval '60 seconds'"
            )
            return recovered

    async def history(self, claim: Claim) -> list[dict]:
        async with await self.connect() as c:
            rows = await (
                await c.execute(
                    """SELECT input,result FROM turns WHERE session_id=%s AND epoch=%s
                AND state='succeeded' AND id<>%s ORDER BY created_at DESC,id DESC LIMIT 4""",
                    (claim.session_id, claim.epoch, claim.turn_id),
                )
            ).fetchall()
            return [
                m
                for r in reversed(rows)
                for m in (
                    {"role": "user", "content": r["input"][:2000]},
                    {"role": "assistant", "content": r["result"][:4000]},
                )
            ]

    async def claim_delivery(self, bot: int):
        async with await self.connect() as c:
            # Same lock as schedule changes: admission linearizes before disable/update.
            await c.execute("SELECT pg_advisory_xact_lock(74192631)")
            await c.execute(
                "SELECT 1 FROM allowlist WHERE bot_id=%s ORDER BY user_id,chat_id FOR KEY SHARE",
                (bot,),
            )
            await c.execute(
                "UPDATE outbox SET state='failed',error_class='authorization_changed' WHERE bot_id=%s AND state='pending' AND id NOT IN (SELECT id FROM live_delivery_authority)",
                (bot,),
            )
            r = await (
                await c.execute(
                    """SELECT o.* FROM outbox o JOIN bots b ON b.id=o.bot_id
                    WHERE o.bot_id=%s AND o.state='pending' AND o.available_at<=clock_timestamp()
                    AND b.send_after<=clock_timestamp()
                    AND o.id IN (SELECT id FROM live_delivery_authority)
                    AND NOT EXISTS (SELECT 1 FROM outbox prior WHERE prior.bot_id=o.bot_id
                      AND prior.chat_id=o.chat_id AND prior.seq<o.seq AND prior.state IN ('pending','sending'))
                    ORDER BY o.seq FOR UPDATE OF o SKIP LOCKED LIMIT 1""",
                    (bot,),
                )
            ).fetchone()
            if not r:
                return None
            await c.execute(
                "UPDATE outbox SET state='sending',tries=tries+1,claimed_at=clock_timestamp() WHERE id=%s",
                (r["id"],),
            )
            r["tries"] += 1
            return r

    async def settle_delivery(self, row, state: str, *, message_id=None, delay=0, error_class=""):
        if state not in {"pending", "sent", "failed", "uncertain"}:
            raise ValueError("Invalid delivery disposition")
        async with await self.connect() as c:
            if error_class == "rate_limit":
                await c.execute(
                    "UPDATE bots SET send_after=greatest(send_after,clock_timestamp()+%s*interval '1 second') WHERE id=%s",
                    (delay, row["bot_id"]),
                )
            if (
                state == "pending"
                and not await (
                    await c.execute(
                        "SELECT 1 FROM live_delivery_authority WHERE id=%s", (row["id"],)
                    )
                ).fetchone()
            ):
                state, error_class = "failed", "authorization_changed"
            await c.execute(
                """UPDATE outbox SET state=%s,message_id=%s,error_class=%s,
                available_at=clock_timestamp()+%s*interval '1 second'
                WHERE id=%s AND state='sending' AND tries=%s""",
                (state, message_id, error_class, delay, row["id"], row["tries"]),
            )

    async def cooldown(self, bot: int, seconds: int):
        async with await self.connect() as c:
            await c.execute(
                "UPDATE bots SET send_after=greatest(send_after,clock_timestamp()+%s*interval '1 second') WHERE id=%s",
                (seconds, bot),
            )

    async def drafts(self, bot: int):
        async with await self.connect() as c:
            return await (
                await c.execute(
                    """SELECT t.id,t.attempt_id,s.chat_id,s.thread_id,e.seq,e.text FROM turns t
                JOIN sessions s ON s.id=t.session_id JOIN bots b ON b.id=s.bot_id JOIN attempts a ON a.id=t.attempt_id
                JOIN allowlist u ON (u.bot_id,u.user_id,u.chat_id)=(s.bot_id,s.user_id,s.chat_id)
                JOIN LATERAL (SELECT seq,text FROM turn_events WHERE turn_id=t.id AND attempt_id=t.attempt_id ORDER BY seq DESC LIMIT 1) e ON true
                WHERE s.bot_id=%s AND s.chat_id>0 AND t.state='running' AND a.state='running'
                AND a.lease_until>clock_timestamp() AND b.send_after<=clock_timestamp()
                AND t.id IN (SELECT id FROM live_turn_authority)
                AND NOT EXISTS (SELECT 1 FROM schedule_runs r WHERE r.turn_id=t.id AND r.manual_key IS NULL)
                FOR KEY SHARE OF u""",
                    (bot,),
                )
            ).fetchall()
