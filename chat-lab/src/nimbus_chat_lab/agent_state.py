"""Identity-bound, fenced agent state. No model-supplied identity or credentials."""

import json
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from .worker import AuthorityLost

LOCK = 74192631  # Same short transaction lock/order as Telegram intake.


def next_slot(now, zone, hour, minute, lead=5):
    tz = ZoneInfo(zone)
    local = now.astimezone(tz)
    # A schedule is a daily local wall time. Resolve DST by round-tripping; skip nonexistent times.
    for day in range(3):
        candidate = (local + timedelta(days=day)).replace(
            hour=hour, minute=minute, second=0, microsecond=0, fold=0
        )
        utc = candidate.astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) != candidate.replace(tzinfo=None):
            continue
        if utc > now:
            return utc
    raise ValueError("No next daily slot")


def recent_slot(now, zone, hour, minute):
    tz = ZoneInfo(zone)
    local = now.astimezone(tz)
    for day in range(3):
        candidate = (local - timedelta(days=day)).replace(
            hour=hour, minute=minute, second=0, microsecond=0, fold=0
        )
        utc = candidate.astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) == candidate.replace(tzinfo=None) and utc <= now:
            return utc
    raise ValueError("No recent daily slot")


class AgentState:
    def __init__(self, store, claim):
        self.store, self.claim = store, claim
        self.identity = None
        self.background = False
        self.data_scope = "public"
        self.report_slot = None

    async def initialize(self):
        async with await self.store.connect() as c:
            s = await (
                await c.execute(
                    "SELECT bot_id,user_id,chat_id,thread_id,lane FROM sessions WHERE id=%s",
                    (self.claim.session_id,),
                )
            ).fetchone()
            if not s or s["user_id"] != s["chat_id"] or s["thread_id"]:
                raise PermissionError("Agent mode is private-chat only")
            self.identity = tuple(s[k] for k in ("bot_id", "user_id", "chat_id"))
            self.lane = s["lane"]
            scope = await (
                await c.execute("SELECT data_scope FROM turns WHERE id=%s", (self.claim.turn_id,))
            ).fetchone()
            self.data_scope = scope["data_scope"]
            scheduled = await (
                await c.execute(
                    "SELECT slot FROM schedule_runs WHERE turn_id=%s", (self.claim.turn_id,)
                )
            ).fetchone()
            self.report_slot = scheduled["slot"] if scheduled else None
            self.background = bool(
                await (
                    await c.execute(
                        "SELECT 1 FROM schedule_runs WHERE turn_id=%s", (self.claim.turn_id,)
                    )
                ).fetchone()
            )

    @asynccontextmanager
    async def transaction(self):
        async with await self.store.connect() as c:
            await c.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            t = await self.store._owned(c, self.claim)
            if not t or t["state"] != "running":
                raise AuthorityLost()
            if not await (
                await c.execute(
                    "SELECT 1 FROM allowlist WHERE bot_id=%s AND user_id=%s AND chat_id=%s FOR KEY SHARE",
                    self.identity,
                )
            ).fetchone():
                raise PermissionError("Identity no longer allowed")
            yield c
            # Commit fence: every mutation in this transaction rolls back if authority expired.
            row = await (
                await c.execute(
                    "UPDATE attempts SET lease_until=lease_until WHERE id=%s AND incarnation=%s AND state='running' AND lease_until>clock_timestamp() AND turn_id IN (SELECT id FROM live_turn_authority) RETURNING id",
                    (self.claim.attempt_id, self.claim.incarnation),
                )
            ).fetchone()
            if not row:
                raise AuthorityLost()

    async def enter_health(self):
        if self.background and self.data_scope != "health":
            raise PermissionError("Public background tasks cannot access health data")
        async with self.transaction() as c:
            await c.execute(
                "UPDATE turns SET data_scope='health' WHERE id=%s", (self.claim.turn_id,)
            )
        self.data_scope = "health"

    async def memory(self, action, key="", value=""):
        if action not in ("list", "get", "set", "delete"):
            raise ValueError("Invalid memory action")
        if action != "list" and not 1 <= len(key) <= 64:
            raise ValueError("Invalid key")
        if len(value) > 8000:
            raise ValueError("Memory value too large")
        if self.background and action in ("set", "delete"):
            raise PermissionError("Background jobs cannot change user memory")
        async with self.transaction() as c:
            if action == "list":
                return await (
                    await c.execute(
                        "SELECT key,length(value) AS characters FROM agent_memory WHERE bot_id=%s AND user_id=%s AND chat_id=%s ORDER BY key",
                        self.identity,
                    )
                ).fetchall()
            if action == "get":
                return await (
                    await c.execute(
                        "SELECT key,value FROM agent_memory WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND key=%s",
                        (*self.identity, key),
                    )
                ).fetchone()
            if action == "delete":
                await c.execute(
                    "DELETE FROM agent_memory WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND key=%s",
                    (*self.identity, key),
                )
            else:
                count = await (
                    await c.execute(
                        "SELECT count(*) AS n FROM agent_memory WHERE bot_id=%s AND user_id=%s AND chat_id=%s",
                        self.identity,
                    )
                ).fetchone()
                exists = await (
                    await c.execute(
                        "SELECT 1 FROM agent_memory WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND key=%s",
                        (*self.identity, key),
                    )
                ).fetchone()
                if count["n"] >= 64 and not exists:
                    raise ValueError("Memory key limit")
                await c.execute(
                    "INSERT INTO agent_memory(bot_id,user_id,chat_id,key,value) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(bot_id,user_id,chat_id,key) DO UPDATE SET value=EXCLUDED.value,updated_at=clock_timestamp()",
                    (*self.identity, key, value),
                )
            return {"saved": True, "key": key}

    async def schedule(self, action, spec):
        if action not in ("create", "list", "update", "disable", "run_now"):
            raise ValueError("Invalid schedule action")
        if not isinstance(spec, dict):
            raise ValueError("spec must be an object")
        if self.background and action != "list":
            raise PermissionError("Scheduled jobs cannot create or modify schedules")
        async with self.transaction() as c:
            now = (await (await c.execute("SELECT clock_timestamp() AS t")).fetchone())["t"]
            if action == "list":
                rows = await (
                    await c.execute(
                        "SELECT * FROM schedules WHERE bot_id=%s AND user_id=%s AND chat_id=%s ORDER BY created_at",
                        self.identity,
                    )
                ).fetchall()
                for row in rows:
                    if row["data_scope"] == "health":
                        row["instructions"] = (
                            "Private health task. Content is not loaded into public context."
                        )
                        row["name"] = "身体快报（私有健康任务）"
                    latest = await (
                        await c.execute(
                            "SELECT id,turn_id,state,slot FROM schedule_runs WHERE schedule_id=%s ORDER BY created_at DESC LIMIT 1",
                            (row["id"],),
                        )
                    ).fetchone()
                    if latest:
                        if latest["turn_id"]:
                            latest["execution"] = await (
                                await c.execute(
                                    "SELECT state FROM turns WHERE id=%s", (latest["turn_id"],)
                                )
                            ).fetchone()
                        latest["delivery"] = await (
                            await c.execute(
                                "SELECT state,count(*) AS n FROM outbox WHERE dedupe_key LIKE %s GROUP BY state",
                                (f"scheduled:{latest['id']}:%",),
                            )
                        ).fetchall()
                    row["last_run"] = latest
                return json.loads(json.dumps(rows, default=str))
            if action == "create":
                if set(spec) - {
                    "name",
                    "instructions",
                    "timezone",
                    "hour",
                    "minute",
                    "lead_minutes",
                    "data_scope",
                }:
                    raise ValueError("Unexpected schedule fields")
                name = spec.get("name", "")
                instructions = spec.get("instructions", "")
                if (
                    not isinstance(name, str)
                    or not 1 <= len(name) <= 100
                    or not isinstance(instructions, str)
                    or not 1 <= len(instructions) <= 6000
                ):
                    raise ValueError("Name/instructions bounds")
                existing = await (
                    await c.execute(
                        "SELECT * FROM schedules WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND name=%s",
                        (*self.identity, name),
                    )
                ).fetchone()
                if existing:
                    if existing["data_scope"] == "health":
                        existing["instructions"] = "Private health task; content withheld."
                        existing["name"] = "身体快报（私有健康任务）"
                    return json.loads(
                        json.dumps({"created": False, "existing": existing}, default=str)
                    )
                count = (
                    await (
                        await c.execute(
                            "SELECT count(*) AS n FROM schedules WHERE bot_id=%s AND user_id=%s AND chat_id=%s",
                            self.identity,
                        )
                    ).fetchone()
                )["n"]
                if count >= 16:
                    raise ValueError("Schedule limit")
                zone = spec.get("timezone", "Asia/Shanghai")
                hour = spec.get("hour", 8)
                minute = spec.get("minute", 0)
                lead = spec.get("lead_minutes", 5)
                scope = spec.get("data_scope", "public")
                if scope not in ("public", "health"):
                    raise ValueError("Invalid data scope")
                if scope == "health":
                    await c.execute(
                        "UPDATE turns SET data_scope='health' WHERE id=%s", (self.claim.turn_id,)
                    )
                    self.data_scope = "health"
                self.check_time(hour, minute, lead)
                slot = next_slot(now, zone, hour, minute)
                row = await (
                    await c.execute(
                        "INSERT INTO schedules(id,bot_id,user_id,chat_id,name,instructions,timezone,hour,minute,lead_minutes,enabled,next_run,data_scope) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s) RETURNING *",
                        (
                            uuid4(),
                            *self.identity,
                            name,
                            instructions,
                            zone,
                            hour,
                            minute,
                            lead,
                            slot,
                            scope,
                        ),
                    )
                ).fetchone()
                return json.loads(json.dumps({"created": True, "schedule": row}, default=str))
            identifier = UUID(spec["id"])
            row = await (
                await c.execute(
                    "SELECT * FROM schedules WHERE id=%s AND bot_id=%s AND user_id=%s AND chat_id=%s FOR UPDATE",
                    (identifier, *self.identity),
                )
            ).fetchone()
            if not row:
                raise ValueError("Schedule not found for this user")
            if action == "run_now":
                existing = await (
                    await c.execute(
                        "SELECT * FROM schedule_runs WHERE schedule_id=%s AND manual_key=%s",
                        (identifier, self.claim.turn_id),
                    )
                ).fetchone()
                if existing:
                    return json.loads(json.dumps(existing, default=str))
                pending = await (
                    await c.execute(
                        "SELECT count(*) AS n FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id WHERE s.bot_id=%s AND s.user_id=%s AND s.chat_id=%s AND r.state IN ('queued','attached')",
                        self.identity,
                    )
                ).fetchone()
                if pending["n"] >= 4:
                    raise ValueError("Too many pending scheduled runs")
                result = await (
                    await c.execute(
                        "INSERT INTO schedule_runs(id,schedule_id,generation,slot,manual_key,state,expires_at) VALUES (%s,%s,%s,%s,%s,'queued',%s) RETURNING *",
                        (
                            uuid4(),
                            identifier,
                            row["generation"],
                            now,
                            self.claim.turn_id,
                            now + timedelta(hours=4),
                        ),
                    )
                ).fetchone()
                return json.loads(json.dumps(result, default=str))
            if action == "disable":
                values = {**row, "enabled": False}
            else:
                if set(spec) - {
                    "id",
                    "instructions",
                    "timezone",
                    "hour",
                    "minute",
                    "lead_minutes",
                    "enabled",
                }:
                    raise ValueError("Unexpected schedule fields")
                values = {**row, **spec}
                if (
                    type(values["enabled"]) is not bool
                    or not isinstance(values["instructions"], str)
                    or not 1 <= len(values["instructions"]) <= 6000
                ):
                    raise ValueError("Invalid schedule values")
                self.check_time(values["hour"], values["minute"], values["lead_minutes"])
            slot = next_slot(now, values["timezone"], values["hour"], values["minute"])
            await c.execute(
                "UPDATE schedules SET instructions=%s,timezone=%s,hour=%s,minute=%s,lead_minutes=%s,enabled=%s,next_run=%s,generation=generation+1 WHERE id=%s",
                (
                    values["instructions"],
                    values["timezone"],
                    values["hour"],
                    values["minute"],
                    values["lead_minutes"],
                    values["enabled"],
                    slot,
                    identifier,
                ),
            )
            await c.execute(
                "UPDATE turns t SET state=CASE WHEN t.state='queued' THEN 'cancelled' ELSE 'cancel_requested' END FROM schedule_runs r WHERE r.schedule_id=%s AND r.turn_id=t.id AND t.state IN ('queued','running')",
                (identifier,),
            )
            await c.execute(
                "UPDATE schedule_runs SET state='cancelled' WHERE schedule_id=%s AND state IN ('queued','attached')",
                (identifier,),
            )
            await c.execute(
                "UPDATE outbox o SET state='failed',error_class='schedule_changed' FROM schedule_runs r WHERE r.schedule_id=%s AND o.schedule_run_id=r.id AND o.state='pending'",
                (identifier,),
            )
            return {
                "id": str(identifier),
                "enabled": values["enabled"],
                "next_delivery": slot.isoformat(),
                "in_flight_delivery_recall_guaranteed": False,
            }

    async def record_research(self, query, source, result):
        receipt = {
            "query": query[:1500],
            "source": source,
            "sources": result.get("sources", [])[:20],
            "tool_usage": result.get("tool_usage", {}),
            "text": result.get("text", "")[:5000],
        }
        observation = result.get("search_observation")
        if isinstance(observation, dict):
            modes = observation.get("keyword_modes", [])
            receipt["search_observation"] = {
                "trace_status": "observed"
                if observation.get("trace_status") == "observed"
                else "unavailable",
                "keyword_modes": [m for m in ("Top", "Latest") if isinstance(modes, list) and m in modes],
                "truncated": observation.get("truncated") is True,
                "ranking_by_views_verified": False,
                "engagement_filter_verified": False,
            }
        text = json.dumps(receipt, ensure_ascii=False)
        if len(text) > 15000:
            receipt["sources"] = receipt["sources"][:5]
            receipt["text"] = receipt["text"][:1000]
            text = json.dumps(receipt, ensure_ascii=False)
        async with self.transaction() as c:
            await c.execute(
                "INSERT INTO turn_events(turn_id,attempt_id,kind,text) VALUES (%s,%s,'research',%s)",
                (self.claim.turn_id, self.claim.attempt_id, text[:16000]),
            )

    async def record_search_event(self, receipt):
        # Caller constructs a bounded safe receipt; no upstream body/stack/auth details.
        text = json.dumps(receipt, ensure_ascii=False)
        if len(text) > 4000:
            raise ValueError("Research receipt bound")
        async with self.transaction() as c:
            count = (
                await (
                    await c.execute(
                        "SELECT count(*) AS n FROM turn_events WHERE turn_id=%s AND kind='search_attempt'",
                        (self.claim.turn_id,),
                    )
                ).fetchone()
            )["n"]
            if count >= 32:
                return
            await c.execute(
                "INSERT INTO turn_events(turn_id,attempt_id,kind,text) VALUES (%s,%s,'search_attempt',%s)",
                (self.claim.turn_id, self.claim.attempt_id, text),
            )

    async def activity(self, action="list", run_id=""):
        if action not in ("list", "cancel"):
            raise ValueError("Unknown activity action")
        async with self.transaction() as c:
            if action == "cancel":
                if self.background:
                    raise PermissionError("Background cannot cancel other work")
                run = await (
                    await c.execute(
                        "SELECT r.turn_id FROM schedule_runs r JOIN schedules j ON j.id=r.schedule_id WHERE r.id=%s AND (j.bot_id,j.user_id,j.chat_id)=(%s,%s,%s)",
                        (UUID(run_id), *self.identity),
                    )
                ).fetchone()
                if not run:
                    raise ValueError("Run not found")
                if run["turn_id"] is None:
                    changed = await (
                        await c.execute(
                            "UPDATE schedule_runs SET state='cancelled' WHERE id=%s AND state='queued' AND turn_id IS NULL RETURNING state",
                            (UUID(run_id),),
                        )
                    ).fetchone()
                    return {
                        "requested": bool(changed),
                        "execution": changed,
                        "daily_schedule_unchanged": True,
                    }
                changed = await (
                    await c.execute(
                        "UPDATE turns SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'cancel_requested' END WHERE id=%s AND state IN ('queued','running','cancel_requested') RETURNING state",
                        (run["turn_id"],),
                    )
                ).fetchone()
                return {
                    "requested": bool(changed),
                    "execution": changed,
                    "daily_schedule_unchanged": True,
                }
            rows = await (
                await c.execute(
                    """SELECT r.id AS run_id,j.name,r.state AS run_state,t.id AS turn_id,t.state AS execution,t.result,r.slot,coalesce(t.data_scope,j.data_scope) AS data_scope,
                extract(epoch FROM coalesce(t.finished_at,clock_timestamp())-coalesce(t.created_at,r.created_at))::int AS elapsed_seconds
                FROM schedule_runs r JOIN schedules j ON j.id=r.schedule_id LEFT JOIN turns t ON t.id=r.turn_id
                WHERE (j.bot_id,j.user_id,j.chat_id)=(%s,%s,%s)
                ORDER BY CASE WHEN r.state IN ('queued','attached') THEN 0 ELSE 1 END,r.created_at DESC LIMIT 4""",
                    self.identity,
                )
            ).fetchall()
            for row in rows:
                if row["data_scope"] == "health":
                    row["name"] = "身体快报（私有健康任务）"
                    row["result"] = "Private health content withheld; use the garmin tool."
                    row["research"] = []
                    row["search_attempts"] = []
                    row["progress"] = ""
                    row["delivery"] = await (
                        await c.execute(
                            "SELECT state,count(*) AS parts FROM outbox WHERE schedule_run_id=%s GROUP BY state",
                            (row["run_id"],),
                        )
                    ).fetchall()
                    continue
                # Completed results are conversational data only once publication is due.
                row["result"] = (
                    row["result"][:8000]
                    if row["result"] and row["run_state"] == "published"
                    else ""
                )
                row["research"] = []
                row["search_attempts"] = []
                if row["turn_id"]:
                    attempts = await (
                        await c.execute(
                            "SELECT text FROM turn_events WHERE turn_id=%s AND kind='search_attempt' ORDER BY seq DESC LIMIT 32",
                            (row["turn_id"],),
                        )
                    ).fetchall()
                    seen = set()
                    for item in attempts:
                        try:
                            receipt = json.loads(item["text"])
                            key = receipt["request_id"]
                            if key in seen or len(seen) >= 12:
                                continue
                            seen.add(key)
                            receipt["query"] = receipt.get("query", "")[:200]
                            row["search_attempts"].append(receipt)
                        except (ValueError, KeyError, TypeError):
                            continue
                    row["search_attempts"].reverse()
                    events = await (
                        await c.execute(
                            "SELECT text FROM turn_events WHERE turn_id=%s AND kind='research' ORDER BY seq DESC LIMIT 3",
                            (row["turn_id"],),
                        )
                    ).fetchall()
                    progress = await (
                        await c.execute(
                            "SELECT text FROM turn_events WHERE turn_id=%s AND kind='text' ORDER BY seq DESC LIMIT 1",
                            (row["turn_id"],),
                        )
                    ).fetchone()
                    row["progress"] = progress["text"][:800] if progress else ""
                    for e in reversed(events):
                        try:
                            receipt = json.loads(e["text"])
                            receipt["query"] = receipt.get("query", "")[:600]
                            receipt["text"] = receipt.get("text", "")[:600]
                            receipt["sources"] = receipt.get("sources", [])[:4]
                            row["research"].append(receipt)
                        except ValueError:
                            pass
                row["delivery"] = await (
                    await c.execute(
                        "SELECT state,count(*) AS parts FROM outbox WHERE schedule_run_id=%s GROUP BY state",
                        (row["run_id"],),
                    )
                ).fetchall()
            return json.loads(json.dumps(rows, default=str))

    @staticmethod
    def check_time(hour, minute, lead):
        if (
            type(hour) is not int
            or not 0 <= hour <= 23
            or type(minute) is not int
            or not 0 <= minute <= 59
            or type(lead) is not int
            or not 0 <= lead <= 30
        ):
            raise ValueError("Invalid daily time")

    async def archive(self, data=None):
        async with self.transaction() as c:
            if data is None:
                row = await (
                    await c.execute(
                        "SELECT archive FROM agent_workspaces WHERE bot_id=%s AND user_id=%s AND chat_id=%s AND lane=%s",
                        (*self.identity, self.lane),
                    )
                ).fetchone()
                return bytes(row["archive"]) if row else b""
            if len(data) > 16 * 1024 * 1024:
                raise ValueError("Workspace archive exceeds 16 MiB")
            await c.execute(
                "INSERT INTO agent_workspaces(bot_id,user_id,chat_id,lane,archive) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(bot_id,user_id,chat_id,lane) DO UPDATE SET archive=EXCLUDED.archive",
                (*self.identity, self.lane, data),
            )
