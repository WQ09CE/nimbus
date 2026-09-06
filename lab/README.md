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
