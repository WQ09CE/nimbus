# Nimbus Telegram Chat Lab — first runnable increment

Private single-host conversational bot. Telegram is the decided ingress; no Discord,
K3s, broker or Redis in this increment. PostgreSQL is task/attempt authority.

**Status:** S1 implementation and local acceptance. On 2026-09-10, the operator
reported Telegram connection validation, and a dedicated PostgreSQL 18.6 user service
was provisioned. Dennis confirmed the exact private-chat identity; the gateway and
worker A are now enabled/running. The pending `/start` reply has a Telegram send receipt;
a fresh model conversation and user receipt confirmation are still pending. Worker B
remains stopped; full live reply acceptance is not PASS.
See [ACCEPTANCE.md](ACCEPTANCE.md), [AUTHORIZATION.md](AUTHORIZATION.md), and the
[local PostgreSQL deployment guide](deploy/LOCAL_POSTGRES.md).

Source base: Nimbus `377b6065` (`refactor/core-hardening`, committed state only).
Development worktree: `~/Projects/nimbus-telegram`, branch `feat/telegram-lab`.
The dirty `~/Projects/nimbus` and `nimbus-v2` recovery worktree were left untouched.

## What works

- Native Pi screenshot browser tool: [setup](../tools/pi-browser-lab/README.md).
- Real Nimbus `AgentOS` + a text-only Pi CLI model adapter using the existing
  `openai-codex/gpt-6-astra` provider. No extra API-key model loop, token copying,
  SDK impersonation headers, or changes to Nimbus's existing sidecar.
- Telegram long polling, exact numeric bot/user/chat allowlist, UTF-16-correct group
  mention parsing, private/group topic separation; original messages only.
- Transactional inbox, rejected dispositions, task admission and polling cursor.
  Duplicate updates do not create more work. One live turn per conversation;
  at most 64 queued/running/cancel-requested turns across this database.
- PostgreSQL `FOR UPDATE SKIP LOCKED` claims; fresh attempt/incarnation/generation.
  Default lease 30 s, renewal at most 1 s, scanner 1 s, using database time.
  **No renewal after expiry**, no stale progress/completion, no interrupted replay.
- `/status`, `/cancel`, `/new`, `/help`, `/mem` (honest capability/status response).
  Cancellation request is separate from confirmed stop; queued tasks never launch.
- Recent completed context (last four turns with bounded text), keyed by exact user,
  chat, topic and conversation epoch. `/new` starts an empty epoch, preserving audit.
  This is not automatic long-term memory or hidden-engine checkpoint continuation.
- Immutable terminal result + notification outbox in one transaction. Telegram
  failures retry **delivery**, not Nimbus. Lost/ambiguous send receipts become
  `uncertain`, not an uncontrolled send loop or an exactly-once claim.
- Optional private `sendMessageDraft` streaming snapshots; durable `sendMessage`
  final output. Plain text, conservative UTF-16 chunks, backoff on `retry_after`.
- Single-active gateway guarded by a PostgreSQL session advisory lock, not by
  assuming Telegram's 409 conflict is sufficient election. Losing the lock
  connection shuts down all gateway loops. No webhook or public listening port.
- Two worker process identities and graceful drain templates. The dedicated PG,
  gateway and worker A user services are enabled locally after the identity gate;
  worker B remains stopped pending the first real model conversation.

## Intentionally NOT enabled

**The bot has NO filesystem, shell, browser, search, plugin, spawn-agent or publication
tools in this increment.** Both Nimbus's explicit empty registry/allowlist and the
model adapter reject tools. An unavailable Linux sandbox never falls back to local
execution. The developer's `browser_lab` extension is not loaded in bot workers.

Waiting for the next gate: verified rootless Podman + gVisor sandbox, all-tool routing,
remote-operation identity/TTL/cleanup, durable approvals, file/image handling,
long-term memory, group streaming edits and live Telegram verification. There is no
web UI or claim of transparent worker resume. See the original memex design's C0–C12
matrix for the later full acceptance bar, not just the S1 subset tested here.

## Local development

```bash
cd ~/Projects/nimbus-telegram/chat-lab
uv sync --frozen
uv run pytest -q
uv run ruff check src tests scripts
uv run nimbus-chat-lab doctor

# Explicitly consumes existing Codex subscription usage; NEVER contacts Telegram.
uv run python scripts/live_smoke.py

# Browser unit tests, then a real model/UI smoke:
cd ../tools/pi-browser-lab
npm ci --ignore-scripts --no-audit --no-fund
npm test
node smoke.mjs
```

The lab pins Python 3.12 because the isolated PostgreSQL test wheel currently only
ships through CPython 3.12. This does not change Nimbus's global Python version or
its existing virtualenv. `uv.lock` is checked in.

Tests start **real PostgreSQL 16.2** from the pinned `pgserver` test dependency in a
private temporary directory, listening on a Unix socket only. They create/drop only
random test databases and stop the owned server afterward. This old bundled version
is for synthetic tests, **not a recommended live database release**. Deploy a current
supported/patched PostgreSQL independently; `uv sync --no-dev` excludes test binaries.
No Docker socket, root, existing PG database or global service is used by these tests.

The process drills only signal subprocesses that the test just created and whose
attempt-start evidence was observed. No broad `pkill`, Redis flush, node OOM or
changes to existing lab services. The PG restart drill targets only the test server.

## Deployment after operator authorization

1. Choose a supported, patched PostgreSQL and create an empty, dedicated `nimbus_chat`
   database and limited application role. Do not point this at the old Nimbus or any
   company database. No migration of the engine JSON/JSONL store is implied.
2. Create `~/.config/nimbus-chat-lab/` with mode 0700. Make separate gateway/worker
   env files based on `deploy/*.env.example`, mode 0600. Keep the bot token only in
   `telegram-token` (owned regular file, mode 0600, no symlink).
3. Provision the schema and exact numeric allowlist using the same dedicated DSN:

   ```bash
   # Environment populated securely by the operator; do not paste secrets in chat.
   uv run nimbus-chat-lab init
   uv run nimbus-chat-lab allow --bot BOT_ID --user DENNIS_USER_ID --chat PRIVATE_CHAT_ID
   # Add a group only after deciding who may invoke it, with a separate exact pair:
   # uv run nimbus-chat-lab allow --bot BOT_ID --user DENNIS_USER_ID --chat NEGATIVE_GROUP_ID
   ```

   Use the operator-only `identify` command after sending `/start` to a new bot if
   numeric IDs are not known; see [AUTHORIZATION.md](AUTHORIZATION.md). It neither
   acknowledges pending updates nor automatically admits/authorizes their senders.

4. In the bot's intended worker account, verify ordinary Pi subscription login.
   The tested adapter launches Pi with no tools/extensions/skills/context files and
   forwards only PATH/HOME/LANG, not Telegram/DB environment secrets. Credential
   access belongs to the trusted worker, never a future code sandbox.
5. Review paths, resource limits and authorization, then install the provided **user
   systemd templates**. See the local deployment guide for opt-in PG provisioning;
   gateway/workers are not enabled by that helper. Start gateway
   and one worker first, then worker B after the real initial conversation succeeds.
6. Test private chat, `/status`, `/cancel`, `/new`, group @ and a real follow-up after
   an intentionally interrupted test turn. Only then label live acceptance PASS.

Manual foreground equivalents (without installing services):

```bash
uv run nimbus-chat-lab gateway --bot BOT_ID --drafts
uv run nimbus-chat-lab worker --engine nimbus-pi --state /APPROVED/STATE/worker-a
uv run nimbus-chat-lab worker --engine nimbus-pi --state /APPROVED/STATE/worker-b
```

`--engine echo` and `--engine nimbus-mock` are explicitly labelled test modes. The
engine choice is required so startup cannot silently pretend a fixture is a model.
SIGTERM stops new claims, drains up to 120 seconds, then cancels and records an honest
interruption when it still has authority. Nimbus now joins its nested model/wakeup
coroutines on parent cancellation. A Linux `PR_SET_PDEATHSIG` exec launcher also kills
the direct Pi child if the worker is killed, even though Pi has its own process group.
Systemd's cgroup remains the backstop for descendants; this is not a general remote
sandbox cancellation mechanism. If an engine refuses to stop, the CLI fail-stops
without releasing local admission early; lease recovery owns the interruption.
Stopping the Pi client does not prove the provider immediately stops server-side
inference or billing. Do not kill a Pi child and assume a future remote command also stopped.

## Operations and limitations

- State names: `queued/running/cancel_requested/succeeded/failed/interrupted/cancelled`.
  `interrupted` is terminal in S1. Retrying is a new explicitly submitted user turn.
- Outbox `sending` abandoned for 60 s becomes `uncertain`. Definite 429/connect
  failures retry up to five times. Same-chat pending/sending predecessors block later
  fragments; multipart responses carry part labels. A 429 persists a conservative
  bot-wide cooldown, shared by final messages and drafts. 5xx/read/write ambiguity
  does **not** auto-retry.
  `/status` remains database-authoritative; a successful model run can have failed
  or uncertain notification delivery. There is no auto-publication or push credential.
- Final notifications are globally paced at 3.2 s (conservative for groups); optional
  private drafts update at most once per maintenance tick and honor 429. Real service
  limits still need live acceptance. Group partial streaming is deliberately deferred.
- `can_stop` is NOT enabled. Telegram's current official API calls the update field
  `stopped_message_generation`; it needs durable mapping before exposing that button.
  `/cancel` is implemented now. Unknown callbacks/edits/media are durably ignored.
- Fixed UI strings currently use Chinese and machine state values remain English;
  dynamic locale catalogs are deferred. User-facing errors show an error class,
  never raw token-bearing URLs, DB DSNs or payloads.
- Nimbus body logs are disabled in CLI operation. Protected engine session logs,
  PostgreSQL conversation data, draft snapshots and screenshots **do contain content**.
  Do not send company material or credentials through this bot; Telegram bots are not
  end-to-end encrypted secret chats. Retention, disk quotas and encrypted off-host
  backup/restore remain pre-daily-use gates, not implemented promises.
- The current process boundary is a correctness/isolation-of-lifetime boundary,
  **not a hostile-worker security boundary**. Gateway/worker DB roles and same-user
  filesystem access still need deployment review. No multi-tenant certification.
- Test runtime knobs use deliberately short leases. The 30 s default is not a measured
  production SLA. Event/output/queue/run-time bounds do not replace host memory,
  PID, disk or future sandbox resource limits.

## Files

- `src/nimbus_chat_lab/schema.sql`, `store.py`: authority, inbox/outbox, queue and recovery.
- `telegram.py`, `gateway.py`: transport/auth parsing, polling, delivery and drafts.
- `engine.py`, `worker.py`: Nimbus/Pi text adapter and process execution lifecycle.
- `tests/`: real PG tests, mock Telegram wire tests, real subprocess signal drills.
- `scripts/live_smoke.py`: actual Nimbus + Pi/Codex, synthetic update and mocked Telegram.
- `deploy/`: reviewed-before-use templates, no installation automation that changes the host.

Reference design: `~/.memex/knowledge/projects/nimbus/architecture/chat-agent-lab-lite.md`.
Code and acceptance evidence take precedence over assumptions in that proposal.
