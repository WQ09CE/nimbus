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
4. Memory/CPU: MemoryMax OOMKill with a victim and a suspect turn on one pod, rescue cascade, same-name restart,
   event-loop stalls (false death, takeover storm), precursors (cgroup PSI / GC / late heartbeats) via the probe.
5. Rolling deploy under load (new pod ids replace old ones, surge vs unavailable), deploy generations vs
   Temporal worker versioning; then schema skew between generations (R5.2).

Retrofits land per round, never up front: Valkey Streams log store + orphan scanner (R1),
ownership ledger + epoch check (R2), first-tier resume + binding at dirty seams (R3), incarnations +
per-epoch orphans + ingest/attempts admission + heartbeat facts (R4).

## Console

```
./lab/labctl.py pods up|down|status [a b ..] pods a–f (auto allow_always Bash/Write/Edit — rules are per process)
./lab/load.py start|report --tag T           N sessions over a pod list, SSE captured; report = per-session owners/hops/outcome
./lab/labctl.py turn a "lab steps 3 sleep 1" deterministic N-step Bash turn via MockLLM, prints the SSE trace
./lab/labctl.py kill|term|freeze|thaw a      SIGKILL / SIGTERM / SIGSTOP / SIGCONT the pod's cgroup
./lab/labctl.py mem a 300M                   MemoryMax + MemorySwapMax=0 on the pod unit (runtime; `infinity` resets)
./lab/probe.py --tag NAME --pods a,b         1 Hz sampler: cgroup mem/peak/swap/oom_kill, PSI, heartbeat age + pod facts, /health ms
./lab/labctl.py vc health|recycle|chaos '{"fail_next":2}'
./lab/labctl.py perms a | respond a REQ allow_once|deny
```

Workload: MockLLM rule `lab steps N [sleep S] [bloat K] [stall T]` → N sequential Bash calls
(`sleep S; echo step-k >> lab_steps.txt; cat lab_steps.txt`) in the vcompute lease
(isolated form, `NIMBUS_VCOMPUTE_MOUNT=0`), then `LAB_DONE N`; `bloat K` adds K bytes of base64 noise
to every step's output (the 0829/0903 ingest amplifier), `stall T` blocks the pod's event loop for T s
inside each model call (sync pickle / GC stand-in). A turn is at most 8 Bash steps: the loop's same-tool
streak guard nudges the model after 8 and the mock then ends the turn. Session logs (shared
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

## R4 — memory / CPU: OOM collateral, rescue cascade, same-name restart, stalls

The 0903 shape on one pod pair (`lab/drills/r4-oom.sh [MemoryMax] [bloat]`): pod-a runs a **victim**
(plain steps) and a **suspect** (`bloat 12M` steps) under `MemoryMax=230M`. Measured before the retrofit:
the suspect's step-2 result spike (~45 MB transient for a 12 MB output: chunks + join + JSON + SSE copies)
OOM-kills pod-a at t+7 s — no precursor at 1 Hz, the kill is inside one step; pod-b's scanner resumes BOTH
turns at t+22 s and dies of the suspect's replay 5 s later (the rescuer dies of the rescued; the victim
dies a second time mid-turn); restarting both pods under the same ids hid everything: the successor's
heartbeat made the predecessor's turns look owned-and-alive, and the once-per-session orphan record
(HSETNX) could not even register the second death. Also: without `MemorySwapMax=0` the limit is a
swap-thrash plateau (peak pinned at the limit, PSI up, /health 100 ms, steps 15 s) rather than a kill.

Retrofit:
- **Incarnation** — `Ledger.inc` per process; a turn's owner is alive iff `pod:{id}` exists with the
  same `inc`; a successor under the same pod id exposes its predecessor's turns at once (no grace).
- **Orphans once per epoch** (`orphan:{sid}` field `e{epoch}`) — a turn whose rescuer dies is an orphan again.
- **Heartbeat facts** — `rss_mb`, cgroup `mem_pct`, event-loop `lag_ms` (a late heartbeat confesses the stall
  it could not report), longest gen-2 `gc2_ms`, `prev_oom_kills` (the successor reads its cgroup's
  `memory.events`); kept in `podlast:{id}` without expiry as the pod's last words.
- **Ingest per turn** (`turn:{sid}.bytes`) metered **at the door** — as tool output streams in, posted past
  1 MiB — not at the step seam: a pod that dies of a result never reaches the seam (first attempt charged
  at seams read 0 for the suspect).
- **Admission gains two budgets** next to the repeat class: `NIMBUS_RESUME_INGEST_BUDGET_MB` (default 8; over
  it → `interrupted reason=oom_suspect`) and `NIMBUS_RESUME_MAX_ATTEMPTS` (default 3, Temporal's
  `maximum_attempts`; over it → `reason=max_attempts`).
- **Projection honors `replaces_seq`** — a second-generation resume rebuilt the surface from the log with
  both the synthetic placeholder and the replay's result for one call id (the mock skipped a step; a strict
  provider would reject the duplicate tool_call_id).

Measured after: pod-a dies at t+7 s as before; at t+22 s pod-b resumes the victim (45 B ingested) and
quarantines the suspect (12 MB, `oom_suspect`, hint names the pod and its last memory %); the victim
finishes on pod-b at t+35 s; pod-b lives. Same-name restart (`r4-restart.sh`): kill -9 pod-b at t+7, start it
again 2 s later → pod-a resumes the turn 12 s after the restart (previously never). Stall (`r4-stall.sh 20|30`):
a 20 s stall is a coin flip for the 10 s scanner (5 s dead window) — caught once (takeover, the woken pod's
flush rejected, client told OWNERSHIP_LOST), missed once (turn completes, each heartbeat afterwards carries
`lag_ms≈16000`); a 30 s stall on a turn that stalls every owner produced a takeover storm (18 epochs in
340 s, 16 rejected writes, 8 OWNERSHIP_LOST per client) until `max_attempts` closed it at epoch 3.
Temporal column: a 12 MB step result is refused by the server at once — `PayloadsTooLarge [TMPRL1103]`,
workflow failed, nothing retried (`lab/temporal/run_turn.py 3 1 12M`): the size budget is in the contract.

## R5.1 — rolling deploy under load: new pod ids, surge vs unavailable, deploy generations

`lab/drills/r5-deploy.sh surge|unavailable`: 20 sessions (7-step turns, 8 s per step) across pods a,b,c;
the rollout replaces them with d,e,f one at a time (10 s gaps) — names change, old ids never return (k8s
Deployment shape). `lab/load.py` drives the sessions and reports, per session, the ledger's claim history
(`turn:{sid}.owners`, "pod/epoch …"), turns, results/replays, end reasons, the bound workspace's steps and the
outcome; plus takeovers per pod, scanner-fallback use and the NATS consumer counters.

Measured before the retrofit (all 20 completed both times, every takeover via NATS, scanner fallback 0,
rejected writes 0; an old pod leaves 2–10 s after SIGTERM — the pause waits for the step seam):

| mode | handed off | takeovers (extra hops) | first hop landed on an old pod | slowest session |
|---|---|---|---|---|
| surge (new up, then old term) | 14 | 20 (6) | 9 of 14 | 75.6 s vs 65–67 s untouched (8-step turns) |
| unavailable (old term, then new up) | 17 | 28 (11), one session had 4 owners | 15 of 17 | 67.3 s vs 56.6 s |

The queue group routes each announcement to a random member; during a rollout most members are the pods
about to be terminated, so a session pauses, restores and pauses again (~+8–10 s per extra hop). Found on
the way: the completion core dump (loop metadata captured at run start) put the old pod's pause binding
back over the seam bindings the new pod had written — a crash in the next turn would have restored a stale
workspace. Fixed (`_save_sandbox_binding` keeps the running loop's metadata in step; test in
`tests/test_orphan_admission.py`).

Retrofit — **deploy generations** (`NIMBUS_GENERATION`, heartbeat fact `gen`): once a live pod of a newer
generation beats, an older pod is *superseded* — it leaves the handoff queue group and stops scanning for
orphans; it finishes what it has and hands it off on SIGTERM (`Ledger._check_generation`, hook wired in
`server/app.py`). This is what Temporal's worker versioning does with build ids: old builds only drain.

| mode | takeovers (extra hops) | slowest session |
|---|---|---|
| surge, gen 1 → 2 | 14 (0): every handoff went to d or e exactly once | 59.2 s vs 56.6–58.9 s |
| unavailable, gen 1 → 2 | 24 (7): only a's 7 sessions hopped twice — no gen-2 pod existed when a died | 66.7 s for those, 58.6–59.3 s for the rest |

The remaining double hop is inherent to maxUnavailable: the first batch's announcements have no new-generation
consumer yet. The alternative is for the rollout to announce its intent first (old pods leave the group before
the first SIGTERM; announcements wait in JetStream until a gen-2 pod appears — visible as `pending > 0`),
trading the extra hop for a resume delayed until the new pod is up. Not implemented; a policy choice.
Client side: the old pod's SSE client saw `paused` + `done PAUSED` and had to reattach to the new pod — the
stream does not follow the session.
