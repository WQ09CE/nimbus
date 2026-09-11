# Agent acceptance — 2026-09-11 (Asia/Shanghai)

**Latest, 09:22:** conversational UX upgrade is deployed. Dennis confirmed Telegram
creation/enablement, immediate execution and report receipt. His single existing daily
job was preserved. Dennis subsequently reran the workflow and confirmed a substantially
better experience. The actual future 08:00 delivery remains unverified.
See [CONVERSATION_UX.md](CONVERSATION_UX.md) for parallel lanes, quiet replies and context.
No subscription or digest rerun was created by either deployment operation.

This supersedes the earlier S1-only assessment. Model smoke, installed services and
an operator canary are intentionally distinguished from Telegram user acceptance.

## Current measured results

| Check | Result | Evidence under repository `.artifacts/` |
|---|---|---|
| Current lab tests, actual isolated PG and subprocess fault drills | **75 passed** | `conversation-unit.xml`, `conversation-unit.txt`; earlier Agent suite: 67 |
| Existing Nimbus core regression | **540 passed, 3 skipped** | `agent-core.xml`; external/slow tests excluded |
| Native Astra → AgentOS → real gVisor → memory → daily create/disable → run_now → background/outbox | PASS | `agent-model-smoke/verification.json`; **mock Telegram, isolated PG** |
| Native Astra tool call → Pi xAI OAuth → X Search, final uses provider source | PASS | `agent-search-smoke/verification.json`; one actual X Search call, one provider source; **mock Telegram** |
| Pi xAI OAuth web search | PASS | `web-search-proof.json`; three actual web-search calls, two provider sources |
| Real gVisor containment and cancellation | PASS | `sandbox-drills.json`, `sandbox-service-drill.txt` |
| Native Agent in actual production worker user unit and PG | PASS | `agent-local-deployment.json`; **operator-injected synthetic turn, Telegram delivery suppressed** |
| Pre-cutover PG dump and independent PG 18 restore | PASS | Protected `backups/agent-cutover-20260910T173305Z/`; row counts checked |
| Restricted production role; clean Agent service restart | PASS | `agent-post-cutover-health.json` |
| Repository and model-artifact scan for actual local DSN/password/bot token | PASS | Same health evidence; values were never printed |
| Real-user task creation, immediate run, report and follow-up | USER CONFIRMED | Dennis's morning transcript; quality problems prompted this UX upgrade |
| Updated conversational UX | USER CONFIRMED | Dennis reran the workflow and reported a much better experience |
| Complete draft/cancel/new cycle | **PENDING** | The positive UX report does not certify every control path |
| Actual next 08:00 delivery | **PENDING** | User-enabled existing task preserved; next planned delivery September 12 |
| Cold boot/logout, host-loss recovery, forced host resource exhaustion | **NOT TESTED** | Not inferred from unit settings or canaries |

Ruff, diff whitespace and installed systemd unit syntax are also checked. The older
visual browser tests (3 Node tests and real Astra visual receipt `PASS-852B10E0`)
remain separate developer-tool evidence; they do not certify Telegram browser access.

## What the production canary actually established

1. Stopped ingress/drained the old worker; made a private backup and restored it to
   a newly named PG 18 database, never over the production database.
2. Applied additive Agent tables/views as the no-login owner and granted only DML /
   sequence access to the existing app role. No app superuser, role/database creation
   or schema CREATE rights. The exact single allowlist remained unchanged.
3. Installed Agent worker and scheduler units. A synthetic turn in isolated history
   epoch `-1` was handled by the **real worker service**, not a foreground substitute.
4. Actual Astra tools wrote/read a nonce in gVisor and wrote/read persistent memory;
   exactly one attempt and one pending, never-attempted final notification existed.
5. Removed the exact synthetic turn/events/attempt/outbox, nonce memory and matching
   workspace before reopening ingress. No Telegram update/cursor was forged and
   no user message was sent. No schedule was created, even temporarily.
6. Gateway, worker A, scheduler and PG were running; all Agent application services
   were cleanly restarted afterward. `Linger=yes`; worker B remains stopped.

The post-cutover database had zero schedules, memories, workspaces or active turns.
That is a point-in-time deployment fact, not permission to delete future user data.

## Real sandbox checks

- Official SHA512-verified gVisor release `release-20260831.0`; per-file SHA256
  manifest enforced before execution. Installed in a permanent private data path.
- Rootless Podman 6.1.1; immutable Python image digest; gVisor systrap runtime.
- Host home/runtime sockets and credential env absent; external network and host
  loopback denied; rootfs write rejected; workspace tmpfs verified at 64 MiB.
- Actual gVisor sentry observed in the transient unit cgroup with MemoryMax 768 MiB,
  TasksMax 256, CPUQuota 100%, RuntimeMax 70 s. This was also exercised under an
  outer `NoNewPrivileges=yes` unit, matching the deployed worker's restriction.
- Workspace write/read persisted; a read did not mutate the snapshot; archive path
  traversal rejected. Missing runtime failed closed. Parallel snapshot writes have
  regression coverage. Detached in-container writers are stopped before archiving.
- Cancellation removed the owned transient unit/container; no running owned
  container remained. No Docker socket permissions or personal browser profile used.

These are observed containment/limit/cancellation checks, not an escape-proof security
certification or a forced host OOM/CPU-exhaustion drill.

## Independent review and regressions

Fresh-context **Astra** reviews—not Fable reviews—are in `agent-review*.txt`.
Concrete findings were corrected and tested:

- Backend-injected kwargs incorrectly forwarded to handlers: closed-over handler,
  explicit argument set and real error result; complete native multi-tool smoke passed.
- Revocation/request/publication/delivery gaps: current authorization views plus
  allowlist row locks at admission; regression holds revocation uncommitted and
  proves request/delivery wait, then reject after it commits.
- Disabled job revived by 429 retry: persisted run/generation metadata, validation
  at retry/claim, schedule-change serialization; legacy linkage/backstop added.
- Scheduled work admitted beyond expiry: independent authority predicates, final
  mutation checks and deadline-capped leases; no reliance on scheduler polling.
- Multi-day downtime skipping eligible catch-up: latest eligible daily slot selection.
- Updating early-prepared jobs lost replacements: daily uniqueness includes generation.
- 64 prepared future results blocking new immediate work: scan only actionable runs;
  regression uses four users × 16 prepared jobs and verifies immediate admission.
- Additional implementation check: parallel workspace native calls now serialize
  the whole snapshot operation, preventing lost sibling writes.

The earlier seven S1/core/browser review fixes remain in place: nested cancellation
cleanup, write-time lease fences, poisoned-worker fail-stop, parent-death child cleanup,
ordered/cooldown delivery, text-tool syntax handling and partial browser init rollback.

Existing fault tests cover duplicate claims/intake, invalid identities/topics,
expired/stale writers, queued/running cancellation, SIGKILL/SIGSTOP/SIGTERM, owned
test-PG restart, uncertain delivery, 429 retries and epoch isolation. They do not
prove cancellation or exactly-once effects inside an external provider.

## Deliberate remaining scope

- Updated Telegram UX and remaining controls require Dennis's observation; basic creation/manual receipt were confirmed.
- Only private text ingress is enabled. No bot desktop/browser/media/upload/download
  connector, arbitrary external writes or arbitrary cron/weekly trigger implementation.
- No automatic replay of interrupted work; no promise that sent means human-read.
- Machine awake/online and usable provider authorization are required for on-time work.
- No scheduled/off-host backup, retention, disk alarms or complete host-loss drill.
- No multi-tenant/hostile-same-OS-user security claim. OAuth usability does not establish
  subscription billing terms or a hard search-spend cap.

See [AUTHORIZATION.md](AUTHORIZATION.md) for the user-copyable acceptance message.
