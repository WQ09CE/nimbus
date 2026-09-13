"""Operator-only metadata projections. Never renew, recover, dispatch or load user bodies.

A run with an attached turn is ONE logical task. Unattached runs also count. This
projection is not a new execution authority and does not claim worker liveness.
"""

import json
import os
import stat
from collections import Counter
from pathlib import Path
from uuid import UUID, uuid4

from .telemetry import sanitize_receipt

TASKS = """
WITH tasks AS (
 SELECT 'turn:'||t.id::text AS task_id,t.id AS turn_id,NULL::uuid AS run_id,
 t.created_at,NULL::text AS run_state,NULL::timestamptz AS slot,NULL::timestamptz AS expires_at
 FROM turns t WHERE NOT EXISTS (SELECT 1 FROM schedule_runs r WHERE r.turn_id=t.id)
 UNION ALL
 SELECT 'run:'||r.id::text,r.turn_id,r.id,r.created_at,r.state,r.slot,r.expires_at
 FROM schedule_runs r
), details AS (
 SELECT k.*,t.session_id,t.state AS execution_state,t.generation,t.finished_at,t.attempt_id,
 a.incarnation,a.lease_until,a.state AS attempt_state,
 CASE WHEN s.lane='chat' THEN 'chat' ELSE 'jobs' END AS lane
 FROM tasks k LEFT JOIN turns t ON t.id=k.turn_id
 LEFT JOIN sessions s ON s.id=t.session_id LEFT JOIN attempts a ON a.id=t.attempt_id
)
"""
ACTIVE = {"running", "cancel_requested"}
TERMINAL = {"succeeded", "failed", "interrupted", "cancelled"}


def task_id(value):
    if not isinstance(value, str) or len(value) > 41:
        raise ValueError("Invalid task identity")
    kind, _, identifier = value.partition(":")
    if kind not in {"turn", "run"} or str(UUID(identifier)) != identifier:
        raise ValueError("Invalid task identity")
    return value


def cohort_ids(cohort):
    if (
        not isinstance(cohort, dict)
        or type(cohort.get("version")) is not int
        or cohort["version"] != 1
    ):
        raise ValueError("Unsupported cohort")
    ids = cohort.get("task_ids")
    if not isinstance(ids, list) or len(ids) > 1000:
        raise ValueError("Cohort bound")
    ids = [task_id(i) for i in ids]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate cohort identities")
    return ids


def write_cohort(path, cohort):
    cohort_ids(cohort)
    data = json.dumps(cohort, indent=2).encode()
    if len(data) > 100000:
        raise ValueError("Cohort file bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def private_bytes(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > limit
        ):
            raise ValueError("Expected an owned private bounded regular file")
        data = f.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Private file bound")
    return data


def local_dsn():
    # Read DATA, never shell-source a systemd EnvironmentFile (URI '&' is shell syntax).
    # This accepts only the bootstrap script's single-line unquoted representation.
    path = Path.home() / ".config/nimbus-chat-lab/worker.env"
    lines = private_bytes(path, 4096).decode().splitlines()
    values = [
        line.partition("=")[2].strip() for line in lines if line.startswith("NIMBUS_LAB_DSN=")
    ]
    if len(values) != 1 or not values[0] or values[0][0] in "\"'" or "\\" in values[0]:
        raise ValueError(
            "Unsupported local DSN representation; use an already provisioned environment"
        )
    return values[0]


def read_cohort(path):
    cohort = json.loads(private_bytes(path, 100000))
    cohort_ids(cohort)
    return cohort


def project(row, now, delivery, telemetry):
    execution, run = row["execution_state"], row["run_state"]
    attention, gaps = [], ["external_effect_outcome_unavailable"]
    lease = "not_applicable"
    if execution in ACTIVE:
        if row["lease_until"] is None or row["attempt_state"] != "running":
            lease = "unknown"
            attention.append("active_attempt_missing_or_inconsistent")
        elif row["lease_until"] <= now:
            lease = "expired"
            attention.append("lease_expired")
        else:
            lease = "unexpired_not_liveness_proof"
    if execution in {"failed", "interrupted"}:
        attention.append(execution)
    if run in {"queued", "attached"} and row["expires_at"] <= now:
        attention.append("schedule_deadline_passed")
    if delivery.get("uncertain"):
        attention.append("delivery_uncertain")
    if delivery.get("failed"):
        attention.append("delivery_failed")
    starts = {r["span_id"]: r for r in telemetry if r["event"] == "start"}
    finishes = {r["span_id"]: r for r in telemetry if r["event"] == "finish"}
    unclosed = [
        {"span_id": key, "phase": r["phase"]} for key, r in starts.items() if key not in finishes
    ]
    usage = Counter()
    for receipt in finishes.values():
        usage.update(receipt.get("usage", {}))
    if not telemetry:
        gaps.append("phase_timing_unavailable")
    if unclosed:
        gaps.append("phase_receipts_incomplete_not_liveness_proof")
    if len(telemetry) >= 256:
        gaps.append("telemetry_window_at_bound")
    if execution in {"succeeded", "failed", "interrupted"} and run != "attached" and not delivery:
        gaps.append("delivery_record_absent")
    if execution == "cancel_requested":
        waiting = "client_stop_confirmation"
    elif execution == "running":
        waiting = "runtime"
    elif execution == "queued":
        waiting = "worker_admission"
    elif run == "queued":
        waiting = "scheduler_admission"
    elif run == "attached" and execution in TERMINAL:
        waiting = "publication_time" if row["slot"] > now else "scheduler_publication"
    elif delivery.get("uncertain") or delivery.get("failed"):
        waiting = "delivery_reconciliation"
    elif delivery.get("pending") or delivery.get("sending"):
        waiting = "delivery"
    elif run in {"cancelled", "expired"} or execution in TERMINAL:
        waiting = "none"
    else:
        waiting = "unknown"
        gaps.append("unclassified_state")
    return {
        "task_id": row["task_id"],
        "turn_id": str(row["turn_id"]) if row["turn_id"] else None,
        "session_id": str(row["session_id"]) if row["session_id"] else None,
        "attempt_id": str(row["attempt_id"]) if row["attempt_id"] else None,
        "incarnation": str(row["incarnation"]) if row["incarnation"] else None,
        "generation": row["generation"],
        "lane": row["lane"],
        "execution_state": execution,
        "schedule_state": run,
        "created_at": row["created_at"].isoformat(),
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "age_seconds": max(0, int((now - row["created_at"]).total_seconds())),
        "lease_observation": lease,
        "waiting_for": waiting,
        "external_effects": "not_established",
        "delivery": delivery,
        "telemetry": telemetry,
        "unclosed_spans": unclosed,
        "usage_observed": dict(usage),
        "attention": attention,
        "evidence_gaps": gaps,
    }


class Operations:
    def __init__(self, store):
        self.store = store

    async def capture(self, incarnation):
        """All durably registered attempts of this incarnation, including terminal ones.

        This is NOT proof that ingress registered everything. Independent accepted
        input receipts are the fault-test denominator. Capture is bounded, not truncated.
        """
        incarnation = UUID(str(incarnation))
        async with await self.store.connect() as c:
            await c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            now = (await (await c.execute("SELECT transaction_timestamp() AS t")).fetchone())["t"]
            rows = await (
                await c.execute(
                    TASKS
                    + """
                SELECT task_id FROM tasks k WHERE EXISTS (
                  SELECT 1 FROM attempts a WHERE a.turn_id=k.turn_id AND a.incarnation=%s)
                ORDER BY task_id LIMIT 1001
                """,
                    (incarnation,),
                )
            ).fetchall()
            if len(rows) > 1000:
                raise ValueError("Cohort exceeds bound; narrow the incident scope")
            return {
                "version": 1,
                "incident_id": str(uuid4()),
                "captured_at": now.isoformat(),
                "incarnation": str(incarnation),
                "task_ids": [r["task_id"] for r in rows],
            }

    async def report(self, *, cohort=None, limit=100, after=""):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Page bound")
        if after:
            task_id(after)
        ids = cohort_ids(cohort) if cohort is not None else None
        scope = " WHERE (%s::text[] IS NULL OR task_id=ANY(%s::text[]))"
        async with await self.store.connect() as c:
            await c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            now = (await (await c.execute("SELECT transaction_timestamp() AS t")).fetchone())["t"]
            totals = await (
                await c.execute(
                    TASKS
                    + """
                SELECT count(*) AS total_tasks,
                count(*) FILTER (WHERE execution_state IN ('running','cancel_requested') AND lease_until<=%s) AS expired_leases,
                count(*) FILTER (WHERE execution_state IN ('failed','interrupted')) AS execution_failures
                FROM details"""
                    + scope,
                    (now, ids, ids),
                )
            ).fetchone()
            missing = []
            if ids is not None:
                found = await (
                    await c.execute(TASKS + "SELECT task_id FROM tasks" + scope, (ids, ids))
                ).fetchall()
                missing = sorted(set(ids) - {r["task_id"] for r in found})
            page = await (
                await c.execute(
                    TASKS
                    + "SELECT * FROM details"
                    + scope
                    + " AND task_id>%s ORDER BY task_id LIMIT %s",
                    (ids, ids, after, limit + 1),
                )
            ).fetchall()
            more, page = len(page) > limit, page[:limit]
            delivery_cte = (
                TASKS
                + """,
                deliveries AS (
                  SELECT k.task_id,o.state FROM tasks k JOIN outbox o ON k.task_id=CASE
                    WHEN o.schedule_run_id IS NOT NULL THEN 'run:'||o.schedule_run_id::text
                    WHEN split_part(o.dedupe_key,':',1)='final'
                      THEN 'turn:'||split_part(o.dedupe_key,':',2)
                    ELSE NULL END
                ) """
            )
            delivery_totals = await (
                await c.execute(
                    delivery_cte
                    + "SELECT state,count(*) AS n FROM deliveries"
                    + scope
                    + " GROUP BY state",
                    (ids, ids),
                )
            ).fetchall()
            delivery_rows = await (
                await c.execute(
                    delivery_cte + "SELECT task_id,state,count(*) AS n FROM deliveries "
                    "WHERE task_id=ANY(%s::text[]) GROUP BY task_id,state",
                    ([r["task_id"] for r in page],),
                )
            ).fetchall()
            deliveries = {}
            for row in delivery_rows:
                deliveries.setdefault(row["task_id"], {})[row["state"]] = row["n"]
            events = {}
            # Metadata events only. Never load text snapshots, research bodies, prompts or ZIPs.
            tids = [r["turn_id"] for r in page if r["turn_id"]]
            receipts = await (
                await c.execute(
                    """
                SELECT e.* FROM unnest(%s::uuid[]) AS t(id) CROSS JOIN LATERAL (
                  SELECT turn_id,attempt_id,seq,created_at,text FROM turn_events
                  WHERE turn_id=t.id AND kind='telemetry' ORDER BY seq DESC LIMIT 256
                ) e ORDER BY e.seq
                """,
                    (tids,),
                )
            ).fetchall()
            for receipt in receipts:
                try:
                    safe = sanitize_receipt(json.loads(receipt["text"]))
                except (ValueError, TypeError, KeyError):
                    continue
                safe.update(
                    seq=receipt["seq"],
                    at=receipt["created_at"].isoformat(),
                    attempt_id=str(receipt["attempt_id"]),
                )
                events.setdefault(receipt["turn_id"], []).append(safe)
            tasks = [
                project(r, now, deliveries.get(r["task_id"], {}), events.get(r["turn_id"], []))
                for r in page
            ]
            alerts = Counter({r["state"]: r["n"] for r in delivery_totals})
            return {
                "version": 1,
                "observed_at": now.isoformat(),
                "read_only": True,
                "scope": "fixed_cohort" if ids is not None else "registered_tasks",
                "total_tasks": totals["total_tasks"],
                "expected_tasks": len(ids) if ids is not None else None,
                "missing_tasks": missing,
                "coverage_complete": not missing if ids is not None else None,
                "next_after": page[-1]["task_id"] if more else None,
                "alerts": {
                    "expired_leases": totals["expired_leases"],
                    "execution_failures": totals["execution_failures"],
                    "uncertain_deliveries": alerts["uncertain"],
                    "failed_deliveries": alerts["failed"],
                },
                "tasks": tasks,
                "limitations": [
                    "registered_records_only_not_ingress_coverage_proof",
                    "pagination_is_live_not_a_cross_page_snapshot",
                    "lease_is_not_process_or_provider_liveness",
                    "usage_is_partial_not_a_bill",
                    "no_automatic_recovery",
                ],
            }
