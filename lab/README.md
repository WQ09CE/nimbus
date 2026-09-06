# nimbus-lab — cloud agent execution experiment platform

Two-arm fault-injection lab on one Linux box (no docker; every component is a
systemd **user** unit so `MemoryMax=`, `kill -s STOP`, restarts are native verbs).

```
lab-ctl (faults) ── kill -9 · SIGSTOP/CONT · SIGTERM · net blip · MemoryMax OOM · vcompute recycle
   ├─ nimbus pod-a :8000 ┐  RoutingBackend → vcompute :8793 (bwrap sandbox, lease/snapshot/chaos)
   ├─ nimbus pod-b :8001 ┤
   │                     ├─ Valkey :6379        ledger: session log stream / ownership epoch / pod heartbeats
   │                     └─ NATS JetStream :4222 handoff / resume / watchdog messages (AsyncMQ stand-in)
   └─ Temporal dev :7233 (UI :8233) — same multi-step turn as workflow+activities: the textbook arm
```

Infra: `./up.sh` · `./status.sh` · `./down.sh` (data under `~/.local/share/nimbus-lab/`).

## Rounds (predict → inject → observe → diff table vs Temporal → change nimbus → memex card)
1. Death morphology: kill -9 / SIGSTOP / SIGTERM — who notices, how fast, who takes over, what is lost.
2. Zombie writer + fencing: freeze pod-a mid-turn, pod-b takes over, SIGCONT a → epoch at the single write point (XADD).
3. Consistency cut + sandbox ownership: layer-3 binding moved from PAUSE to dirty step seams; crash-path restore.
4. Memory/CPU: MemoryMax OOMKill, event-loop stalls, precursors (PSI / GC / late heartbeats) with memray + py-spy.
5. Mixed-version rolling handoff vs Temporal worker versioning.

Retrofits land per round, never up front: Valkey Streams log store + orphan scanner (R1),
ownership ledger + epoch check (R2), first-tier resume + binding at dirty seams (R3).

## Console

```
./lab/labctl.py pods up|down|status          pods (auto allow_always Bash/Write/Edit — rules are per process)
./lab/labctl.py turn a "lab steps 3 sleep 1" deterministic N-step Bash turn via MockLLM, prints the SSE trace
./lab/labctl.py kill|term|freeze|thaw a      SIGKILL / SIGTERM / SIGSTOP / SIGCONT the pod's cgroup
./lab/labctl.py mem a 300M                   MemoryMax on the pod unit (runtime property)
./lab/labctl.py vc health|recycle|chaos '{"fail_next":2}'
./lab/labctl.py perms a | respond a REQ allow_once|deny
```

Workload: MockLLM rule `lab steps N [sleep S]` → N sequential Bash calls
(`sleep S; echo step-k >> lab_steps.txt; cat lab_steps.txt`) in the vcompute lease
(isolated form, `NIMBUS_VCOMPUTE_MOUNT=0`), then `LAB_DONE N`. Session logs (shared
by both pods): `~/.local/share/nimbus-lab/sessions/`; lease workspaces:
`~/.local/share/nimbus-lab/vcompute/leases/`.

## LLM rails

Each pod runs on one of two rails (`lab/pods/<pod>.env`, flip with `./lab/labctl.py llm <pod> mock|real`):

- **mock** — `NIMBUS_LLM=mock`, deterministic MockLLM; drills are repeatable and free.
- **real** — `pi-codex/gpt-5.6-luna` through the pi-ai sidecar (`lab-pi-sidecar`, :8799, ChatGPT
  subscription login in `~/.pi/agent/auth.json`); acceptance runs under a real model.

Default: pod-a mock, pod-b real.

## R1 retrofit — ledger (record only)

`nimbus.infra.ledger.Ledger` (extra `nimbus[ledger]`, enabled by `NIMBUS_LEDGER_URL`):
pod heartbeat `pod:{id}` (5s, EX 15s), turn ownership `turn:{session}` claimed/released around
`stream_chat`, and an orphan scanner on every live pod (10s) that records `orphan:{session}`
once (HSETNX) when an owner pod's key has expired. Nothing is resumed or cancelled — Phase 1a
numbers only. View: `./lab/labctl.py ledger` (`ledger reset` clears). Measured: kill -9 →
DEAD at t+15s, ORPHAN recorded at t+20s (heartbeat expiry + scan interval).

## R2 retrofit — Valkey Stream log + epoch fence at the single write point

`NIMBUS_LOG_STORE=valkey` switches the session log store to `StreamSessionLog`
(`sess:{session}:log`, one XADD per event; `nimbus.core.session_log` factories pick the
store at the four call sites). Ownership is an **epoch**: `Ledger.claim` is a Lua
`HINCRBY turn:{session}.epoch` that never resets; the epoch rides `loop metadata.log_epoch`
into every flush, where a Lua script compares it with the current epoch before XADD. A
stale writer is rejected on its first causal flush, raises `OwnershipLostError`
(BaseException: generic handlers can't swallow it), stops executing tools, writes no core
dump, and its client gets `done {status: OWNERSHIP_LOST}` instead of a fake OK.
Measured (freeze → pod-b takes over → thaw): DUPLICATE seq none (was 22–45),
rejected flushes 1, zombie stopped at step 3 (previously ran to step 5).
`./lab/labctl.py ledger dump SID` prints a stream.

## Temporal arm (textbook column)

`lab/temporal/`: the same N-step lab turn as a Temporal workflow (`LabTurn`) whose
activities execute on the **same vcompute daemon** — so the arms differ only in the control
plane. Activities heartbeat every 1 s (`heartbeat_timeout` 15 s, `start_to_close` 60 s,
retry ≤ 3); the lease id lives in workflow history. Workers `lab-temporal-worker@a/@b`
(`labctl workers up`); faults target `worker-a`/`worker-b`; drill `lab/drills/r1-temporal.sh
kill|term|freeze` picks the worker running the pending activity (`temporal workflow describe`)
and reports attempts / worker identity / history event counts. Extra `nimbus[lab]`
(temporalio).

## R3.1 — repeat classes + first-tier resume

`ToolTraits.repeat = free | keyed | once` (default **once**; same three classes as HTTP
safe/idempotent/neither and MCP readOnlyHint/idempotentHint) is the recovery axis, orthogonal
to `side_effects` (authority axis). Crash repair grades the in-flight call by it:
`once → TOOL_OUTCOME_UNKNOWN` (never rerun), `free/keyed → TOOL_RESUMABLE`; later calls are
`TOOL_NOT_STARTED` (safe whatever their class). The ledger's orphan scanner now calls
`SessionManagerV2.on_orphan`: UNKNOWN in flight → **fast-fail** (repair under our epoch,
client gets `interrupted`), otherwise **resume here** — a new turn (`continues: N`) whose
first step re-executes the graded calls (`resume_replay` SSE) and then lets the model go on.
Lab knob `labctl repeat once|keyed` (NIMBUS_REPEAT_OVERRIDE) exercises both branches:
`lab/drills/r3-resume.sh keyed|once`. Measured: kill at t+7 → resume decided t+27, turn
finished on pod-b with no user message; once → fast-fail t+26.

## R3.2 — layer-3 binding at every dirty step seam

The loop yields `step_end` at each balanced seam; `SessionManagerV2` binds sandbox state there
(`_snapshot_sandbox_at_seam`: clean seam = metadata write reusing the last snapshot, dirty
seam = workspace snapshot). A crash-resume (`resume_interrupted`) restores the bound snapshot
before its first lease open, so the taking-over pod continues on the SAME machine state.
Measured: kill mid step 3 → pod-b's lease restored with step-1,2 → replay step 3 → 4,5: one
workspace with all five steps, step-3 exactly once. The lab step is idempotent by construction
(`grep -qx step-k || echo step-k`) so the `keyed` declaration is truthful.

## R3.3 — graceful handoff (SIGTERM → seam pause → announce → peer resumes)

`nimbus serve` installs its own SIGTERM/SIGINT handler after uvicorn starts: run the app's
graceful hooks — `SessionManagerV2.handoff_all`: leave the handoff queue group, `pause_all`
(step-seam pause + layer-3 binding), announce each paused session on NATS JetStream
(`nimbus.infra.handoff.HandoffBus`, stream NIMBUS_HANDOFF, queue group `pods`, ack_wait 15 s,
max_deliver 3) — then tear down the app and force uvicorn out (a 10 s `os._exit` backstop
covers anything that swallows cancellation; uvicorn alone would drain SSE streams forever).
Peers consume announcements and `resume_session` from the durable checkpoint + bound snapshot.
Measured: SIGTERM mid step 3 → pause at the seam ~3 s later → pod-b resumed the same second on
the restored lease → LAB_DONE 5 at t+10 s; pod-a exited within 4 s; the client of the dying
pod saw `paused` + `done`. Enabled by `NIMBUS_HANDOFF_URL`; drill `lab/drills/r3-handoff.sh`.

## Side drill — frozen consumer holds messages (NATS JetStream)

`lab/drills/mq-freeze.sh`: SIGSTOP pod-b (a queue-group member), publish 6 announcements, watch
the consumer's `ack_pending` / `redelivered`. Measured: every message routed to the frozen member
sat for one full `ack_wait` (15 s) before redelivery; with two members and random routing the
slowest message needed two cycles (~29 s). The bound is the ack timeout — a broker without one
(or a client that never pings) holds them until the frozen process dies.
