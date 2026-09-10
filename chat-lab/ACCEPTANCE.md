# Local acceptance — 2026-09-10

**Overall:** PASS for developer browser computer use and the local S1 chat/control
increment. **BLOCKED for real Telegram deployment and S2 sandbox tools.**

This is not a C0–C12 full cloud-agent acceptance claim. No real bot token, personal
Telegram account, company data, existing database or sandbox runtime was used.

## Host and source

- Host `omarchy`: x86_64, Linux 7.2.3-arch1-3, 16 logical CPUs, Intel Core Ultra X9 388H.
- Approximately 61 GiB memory reported; ~850 GiB free on the encrypted root filesystem
  at inventory. `/dev/kvm` present, not exercised by these tests.
- Pi 0.85.1; system Chromium 152.0.7977.82; Playwright-core 1.63.0.
- Lab Python 3.12.14 with locked dependencies; existing Nimbus core suite ran in its
  existing Python 3.13 environment with PYTHONPATH pointing at this worktree.
- Base Nimbus commit `377b6065`; feature branch `feat/telegram-lab` in
  `/home/dennis/Projects/nimbus-telegram`. Existing dirty main development worktree
  and `nimbus-v2` were not edited.
- Memex successfully fast-forwarded `77776cc → 03f2973` before implementation.
  The lightweight design was used; Telegram ingress is decided.

## Results

| Check | Verdict | Evidence / scope |
|---|---|---|
| Native Pi + Astra + image tool, default auto | PASS | Previous research lab replay `auto-replay-Qj2m65` |
| Native Pi + Astra + scoped `detail:original` | PASS | Replay `auto-replay-d6v3b9`; request metadata shows original images; receipt `PASS-1F5229E2` |
| Project-local browser tool unit/security tests | PASS | 3 Node tests; actual Chromium, exact dimensions, serialized/stale observations, cross-origin/redirect denial, init rollback, cancellation/close |
| Project-local browser + Astra visual loop | PASS | `.artifacts/browser-model-gXvfma/verification.json`, receipt `PASS-852B10E0`; random canvas challenge, actual trusted mouse events, fresh screenshots |
| Nimbus AgentOS + actual Pi/Codex model + PG + mocked Telegram | PASS | `.artifacts/nimbus-model-worons89/verification.json`; one attempt after duplicate intake, 2 progress snapshots, exact nonce response, durable final notification |
| Lab Python tests | PASS | **44 passed**, `.artifacts/chat-lab-unit.xml` |
| Existing Nimbus core tests + cancellation regression | PASS | **540 passed, 3 skipped**, `.artifacts/nimbus-core.xml`; external-provider/slow markers excluded |
| New lab Ruff / touched core file Ruff / diff whitespace | PASS | `ruff check`, `git diff --check` |
| User systemd template syntax | PASS | `systemd-analyze --user verify` on both templates; not installed or enabled |
| Actual Telegram polling/draft/final/follow-up | BLOCKED | Needs Dennis's bot token and confirmed numeric allowlist |
| Podman/runsc sandbox and all-tool routing | BLOCKED | Neither binary available; Docker socket permission denied; **no local fallback** |
| Remote tool commands, sandbox effect counts, real media | NOT TESTED | No sandbox/real device operations were enabled |
| Backups, host reboot, disk/resource-pressure enforcement | NOT TESTED | Not inferred from worker/PG tests |

The visual task is a controlled synthetic smoke, not a blind computer-use benchmark.
The model could only call `browser_lab`; the host checked the runtime-generated
challenge/receipt after interaction. The final model run was rerun after review fixes.

The Nimbus model test really invokes Astra through Pi's existing Codex provider;
Telegram uses `httpx.MockTransport`. It **does not** establish real Telegram delivery
or its actual rate limits. Model cancellation tests use a controlled child executable
inside the real Nimbus task nesting, not a claim of server-side inference cancellation.

## Fault and boundary coverage

- Eight concurrent claims of one turn → exactly one live attempt.
- Duplicate Telegram update → no duplicate turn/model execution; unauthorized updates
  get durable rejected dispositions and do not block polling offset progress.
- Exact user/chat allowlist, group @ with emoji/UTF-16 offsets, forwarded/anonymous/bot
  messages, wrong bot command target and unknown approval callbacks fail closed.
- Queue cap, per-conversation active constraint, `/new` epoch, recent-context isolation.
- Queued cancellation starts no attempt. Running cancellation is only reported
  confirmed after the engine and its model process stop.
- Expired lease cannot renew, emit progress or finish. A deliberate pause **between**
  authority read and write cannot resurrect the lease: the write has its own predicate.
- Stale incarnation and resumed old attempts cannot overwrite terminal state/result.
- Real independent worker subprocesses: SIGKILL, SIGSTOP beyond lease, short SIGSTOP,
  SIGTERM drain — **each scenario ran twice**. An independent worker completed a
  follow-up after interruption; thawed worker A could not change the old result.
- The signal fixture counts its synthetic operation starts, not remote sandbox
  command starts. No remote C3 coverage is claimed.
- Real restart of the dedicated test PostgreSQL: DB loss is unknown, worker stops;
  after DB restart the scanner waits for actual lease expiry before interruption.
- A model child in a separate process group dies when its worker is SIGKILLed via
  Linux parent-death signaling. A cancellation-resistant engine retains active state;
  the CLI's fail-stop path exits instead of hanging in asyncio shutdown.
- Missing/ambiguous Telegram send receipt becomes `uncertain`; execution result stays
  durable. Lost DB settlement after send never causes automatic message or model replay.
- 429 persists cooldown, and a delayed first chunk cannot be overtaken by later
  same-chat chunks. Optional draft rejection disables drafts, not durable final sends.
- Bot identity discovery outputs numeric metadata only; it does not acknowledge
  updates, print message bodies or grant authorization.

## Independent review

A fresh-context Astra reviewer used read-only tools, without credentials or external
tool access. Initial and follow-up reports are saved locally:

- `.artifacts/review-initial.txt`
- `.artifacts/review-followup.txt`

Initial review identified seven concrete issues. All were addressed and regression
coverage added:

1. Nimbus `asyncio.wait` child-task leak on parent cancellation → structured cleanup
   in `src/nimbus/core/vcpu.py`, plus a core regression and nested-model subprocess test.
2. Lease check/write gap → conditional authority checks at the mutation itself.
3. Poisoned-worker hang / early admission release → fail-stop CLI and scanner-owned recovery.
4. SIGKILL orphan model process → Linux parent-death exec launcher and direct process test.
5. Retry reordering / partial cooldown → per-chat ordered outbox and persisted bot cooldown.
6. Tool syntax falsely rejected in ordinary chat → explicit text-only decoder; actual calls rejected.
7. Partial Chromium initialization leak → rollback and terminal fail-closed state.

Follow-up review reported all seven addressed, with no remaining concrete high-impact
issue identified in those fixes. This is static review, not a security certification.

## Changes not made

- No Pi global settings, auth files, personal browser profiles or Hyprland config edited.
- No Telegram bot created/connected, no webhook removed, no cloud API key added.
- No new system services enabled; no root escalation, Docker socket permission change,
  Podman/runsc installation, K3s/Redis/broker provisioning or existing lab-service restart.
- Test PostgreSQL, Chromium and owned model/reviewer processes were closed after runs.
- Source and safe local evidence remain; `.artifacts/`, `.runtime/` and virtualenvs are
  gitignored. Code can be continued without waiting for Telegram authorization.

For the next operator steps, see [AUTHORIZATION.md](AUTHORIZATION.md).
