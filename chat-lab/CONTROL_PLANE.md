# Control-plane evolution

Implementation starts with observation and conservative fault accounting, not a generic runtime SDK.
Production schedules, execution semantics, and automatic replay remain unchanged.

## Delivery gates

1. **Observation / accounting (implemented, production instrumentation enabled 2026-09-11 05:10 UTC)**: metadata-only, read-only operations report;
   fixed incident cohort; optional bounded phase/usage receipts; independent accepted-input
   baseline in mixed-state fault tests. Missing evidence must remain unknown.
2. **Strict recovery vertical (not implemented here)**: one real Nimbus runtime, no unknown
   external writes; quiesce, checkpoint + versioned workspace binding, replacement worker,
   dependency validation, continuation. Missing/corrupt/incompatible dependencies must block
   before model/tool dispatch. No fallback to an empty workspace.
3. **Environment / worker governance (not implemented here)**: explicit drain/stop/handoff,
   resource ownership and unknown creation reconciliation, shared recovery capacity.
4. **Execution contracts (not implemented here)**: extract rules from actual consumers;
   second runtime only after capability/lifecycle conformance tests exist.

Each stage has an independent acceptance gate. Passing observation tests does not authorize
automatic recovery, prove Telegram delivery, or establish host-loss recovery. Existing
`SessionManagerV2` snapshot/restore experiments are inputs, not this application's recovery path.

No production migration, service restart, model invocation, or schedule rerun is part of the
first-stage development tests. New instrumentation is opt-in; diagnostics never call `scan`.

## Stage 1: operator interface

From `chat-lab/`, use the already provisioned local worker configuration without copying a
DSN or exposing it in command arguments:

```sh
uv run nimbus-chat-lab ops --local report
uv run nimbus-chat-lab ops --local capture --incarnation <uuid> --output /private/path/incident.json
uv run nimbus-chat-lab ops --local report --cohort /private/path/incident.json --limit 100
# Follow next_after if non-null:
uv run nimbus-chat-lab ops --local report --cohort /private/path/incident.json --after <task-id>
```

Without `--local`, the command uses an already provisioned `NIMBUS_LAB_DSN` environment.
`--local` reads the owned mode-0600 worker file **as data** and supports the bootstrap
script's unquoted single-line DSN representation. Do not shell-source systemd EnvironmentFiles:
a PostgreSQL URI's `&` has different meaning in a shell. No token file or provider credentials
are needed by these commands.

- Database transactions are `REPEATABLE READ READ ONLY`; no migration or lock-based claim.
- Foreground identity is `turn:<uuid>`, scheduled/background identity is `run:<uuid>`.
  Attaching a turn does not double-count a run. Unattached scheduled runs remain discoverable.
- Rows include execution/run state, owner incarnation, generation, lease observation,
  publication/delivery waiting, and evidence gaps. No prompt, result, progress body, research
  content, schedule name, workspace archive or credential is selected/exported.
- Pages are bounded to 200 tasks and 256 telemetry receipts per task. Aggregate counts are
  computed in PG, not by loading all task payloads. Statements retain the existing 5-second
  timeout. This is an on-demand operator report, not a high-frequency metrics scraper.
- A captured incarnation cohort includes **all registered attempts of that incarnation**,
  including already terminal work, not just its active tasks at the instant of failure.
  It excludes never-claimed queued tasks; use a broader explicit accepted-task cohort when
  auditing whole-ingress coverage. Capture refuses more than 1000 identities rather than
  silently truncating. Files are private, exclusive-create and file-fsynced; a failed or
  interrupted file write must be recaptured into a new file, not trusted as a completed cohort.
- The cohort freezes identities, not states. Each report rereads current states and lists
  missing identities. `coverage_complete` means these registered identities were found,
  **not** that they all completed/recovered or that every ingress originally registered them.
  Each page is its own consistent DB snapshot; concurrent state changes across pages are
  explicitly not a cross-page snapshot. Future attempts must not change the denominator.
- Delivery counts cover task-linked final/report messages, not unrelated command ACKs.
  Execution success, outbox pending and Telegram sent are separate facts. `uncertain` requires
  reconciliation, not an automatic retry. Alert fields are counters in the chosen scope;
  they do not install an alert delivery service. Exit 0 means the report succeeded, not that
  all tasks are healthy or the incident is resolved.

## Optional instrumentation

`worker --observe` enables phase receipts. The CLI default remains off; the deployed and
repository worker service templates now explicitly pass `--observe` for A/B/C.
No schema migration is needed: bounded, sanitized `kind=telemetry` events use the
existing `turn_events` table, fenced by current turn/attempt/incarnation/generation/lease.
There are at most 256 telemetry events per attempt, separate from the 120 text-snapshot cap.

Phases: history loading, runtime envelope, model bridge, search bridge, and individual native
workspace/memory/schedule/activity/clock handlers. Start/end share a random span identity;
elapsed duration uses a monotonic clock, event timestamps use PG time. Model usage is the
existing adapter's normalized token counts; search usage is the returned server search-call
counts. Unknown usage is omitted, not estimated. No price, quota entitlement, or provider bill
is inferred. The report sums only observed finish receipts; failed/late/dropped receipts make
this partial accounting. It does not retroactively reconstruct old provider calls.

Observation is not execution authority. A receipt write gets a 250-ms timeout and failures
are best-effort; a provider success must not become a retry because telemetry failed. The
normal execution authority checks still apply independently. Lost authority can prevent a
finish receipt. An unclosed span is unknown evidence, not proof the worker/provider is alive.
Nested runtime/tool/provider spans overlap and must not be summed into a total execution time.
Precise accepted-to-claim queue latency is not yet recorded; task age is not queue latency.

Rollback: leave/remove `--observe` on newly started workers. Do not remove fencing, change
schema, erase events, replay interrupted work, or reset schedule state to disable observation.
Production opt-in was performed in the separately authorized rollout below. The isolated
latency baseline passed; a before/after production workload latency comparison remains
outstanding and must not be inferred from healthy service startup.

## Verification and scope (2026-09-11)

- Lab suite: **93 passed** (75 existing + 18 new). Core suite: **540 passed, 3 skipped**.
  Ruff and `git diff --check` passed.
- Real isolated PostgreSQL and subprocess `SIGKILL`/`SIGSTOP`/`SIGCONT`; 12-task and 100-task
  cohorts, each with two affected live workers, one successful/uncertain-delivery task, one
  queued cancellation and remaining queued tasks. Independent ingress acceptance baseline,
  committed-but-ACK-lost duplicate ingestion, competing cleanup scanners, and zombie fencing.
  Zero missing expected tasks; no new attempts or synthetic operation replay on cleanup.
  The 100-task test raises admission only in its disposable store, not production's 64 limit.
- Separate cases cover unattached/attached scheduled runs, held future publication, fixed
  cohorts and pagination, missing identities, private-file handling, metadata sanitization,
  disabled zero-DB-I/O observation, bounded/fenced receipts, observation failure/cancellation,
  and actual CLI invocation. Real AgentOS/native memory/clock/search wiring is tested with a
  **scripted provider and synthetic research**, not paid provider requests or Telegram.
- Isolated overhead baseline: 30 samples per mode around a synthetic 10-ms await; disabled
  p95 10.169 ms, enabled p95 19.633 ms (difference 9.465 ms); one-task report 5.339 ms;
  60 receipts / 6960 payload bytes. Passed the explicit <100-ms p95-difference and <500-ms
  one-task-report budgets. Not production load, WAL/index storage accounting or provider SLOs.
- A separate **production read-only** CLI report at 2026-09-11 03:07:55 UTC found eight registered
  logical tasks, no expired leases, failed/interrupted executions, or failed/uncertain
  task-linked deliveries. This is a timestamped observation, not a continuing health promise.
  All six services were active/running with NRestarts=0. No task, schedule or service changed.
- Private ignored evidence: `.artifacts/control-plane/` at repository root. Reproduce the
  synthetic overhead check with `uv run python scripts/observation_smoke.py`.

## Next gate: strict recovery, not another start/resume-shaped wrapper

Before claiming Stage 2, test a concrete Nimbus runtime at a completed tool-batch seam and a
versioned gVisor workspace archive. The existing runtime's paused event alone is insufficient:
core-dump save failures can be logged without failing the pause, and the old server restore
path can continue degraded. Validate durable artifacts against the stopped runtime, bind
both versions atomically under authority, and refuse missing/corrupt/newer dependencies before
any new model/tool call. Prove replacement-worker continuation and stale-owner rejection in
isolation. Unknown external writes, durable HITL and arbitrary cross-runtime migration stay
out of that first recovery whitelist. No automatic continuation path has been added here.

## Authorized service restart (2026-09-11 03:46 UTC)

After the user explicitly requested a restart, gateway, scheduler and workers A/B/C were
stopped/started with intake closed and admitted work drained first. All five process IDs
changed and services returned active/running; PostgreSQL's PID was unchanged. Schedule
configuration/generation/next delivery and identity were unchanged, with no manual task run,
schema migration or configuration edit. The workers now load the updated code, but
`--observe` remains absent and automatic continuation remains unsupported. This is a
restart/health check, not a new Telegram/model or future scheduled-delivery acceptance.
Private evidence: `.artifacts/control-plane/service-restart.json`.

## Real-request observation follow-up (2026-09-11 06:16 UTC)

Read-only investigation of a user-reported failure confirmed 52 production telemetry receipts
(26 finish events), including successful requests and one failed first-model span. Collection
is therefore exercised by real requests, not just enabled at startup. That failure took about
5.3 seconds, with a valid lease, unchanged worker incarnation, one admission/attempt/delivery,
and subsequent successes on the same worker. The underlying provider/bridge error was erased
by generic error conversion, so a timing race or upstream outage is not established.

An isolated scripted-adapter failure also reproduced a separate native runtime projection
problem: final result ERROR but snapshot/turn-end completed. The platform correctly persisted
failed. No production replay/restart or code fix was performed during the investigation.
Failure diagnostics and native terminal projection were still open at this investigation;
the subsequent 06:55 deployment below addresses them. Production latency comparison remains
outstanding. Private evidence: `.artifacts/control-plane/incident-20260911T061126Z.*`
and `error-projection-probe.json`.

## Authorized observation opt-in (2026-09-11 05:10 UTC)

The user explicitly requested another restart with observation enabled. Added `--observe`
to the existing local and repository worker templates, preserving all other arguments and
B/C's lane overrides. Closed intake, confirmed no admitted work remained, restarted all
three workers after daemon-reload, then reopened scheduler/gateway. All six services were
active/running after startup; PostgreSQL's PID and schedule/identity state were unchanged.
Actual process arguments confirm observation enabled on A/B/C; the nonsecret lane value
confirms A=chat, B/C=jobs. No credentials were printed or copied to runtime tools.

Seven targeted telemetry tests passed again before rollout. There were zero production
telemetry receipts before and immediately after startup because no new task ran during
verification. Thus this verifies configuration activation, not first real-request receipt
persistence or production latency. No synthetic production task, provider request, Telegram
message, or daily rerun was injected just to manufacture an observation event. Subsequent
ordinary user tasks will exercise collection. Automatic continuation is still unsupported.

Backups and evidence are private under `.artifacts/control-plane/observation-enable-backup/`
and `.artifacts/control-plane/observation-enabled.json`. To roll back only instrumentation,
remove `--observe` from the templates and perform a controlled drain/reload/restart; do not
replay tasks, erase receipts, or change database schema or schedules.

## Authorized diagnostic/projection fixes (2026-09-11 06:55 UTC)

At the user's explicit request, [plaintext local error diagnostics](DIAGNOSTICS.md) now retain
original Pi error details/stacks, bounded stderr/failed stdout, child exit and request identity.
Safe metadata correlation survives model → native result → AgentEngine → worker telemetry.
Timeout exceptions retain the existing native timeout contract; an exhausted non-OK native
result is not confused with the worker's own deadline. No automatic task replay was added.

RuntimeLoop now projects the actual terminal result instead of unconditionally recording
completed, including failure paths through unsuccessful context/budget compaction and explicit
follow-up turns. Old native records are not rewritten. This is a bookkeeping fix, not proof
that it caused the earlier model failure or that strict recovery is now possible.

Regression tests: **108 lab passed**, **551 core passed / 3 skipped**, plus lint/diff checks.
The original terminal regression first failed 8 of 9 cases against the old implementation;
the real TypeScript handler regression also failed against the old generic-error envelope.
Tests use scripted providers/fetch and isolated PG, with real child processes and AgentOS.
A separate actual Pi-loader smoke deliberately used an unsupported operation: its original
error/stack passed through the deployed child and was saved privately, without a model call,
Telegram update, synthetic production task, or paid provider smoke.

Closed intake, drained work, stopped all workers, promoted the tested extension, restarted
A/B/C and reopened scheduler/gateway. All five application PIDs changed and all six services
were active/running. Observation and lanes remain A=chat/B,C=jobs. PostgreSQL PID, identity,
schedule generation/next delivery, and the maintenance cursor were preserved. No schema,
unit/config or daily task change was made. Finished at **06:55:47 UTC / 14:55:47 Shanghai**.
Private evidence: `.artifacts/error-fix-stage/{lab.txt,core.txt,deployment.json}` and the local
Pi-loader diagnostic. This proves the capture path, not the old incident's unknown root cause
or a new real upstream failure. Global error-log retention/disk alerts remain unimplemented.
