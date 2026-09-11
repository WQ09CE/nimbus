# Nimbus — private Telegram personal Agent

**Current deployment (2026-09-11, Asia/Shanghai): integrated Agent mode is running.**
PostgreSQL, gateway, worker A and the persistent scheduler are enabled user services.
Linger is enabled. **No daily subscription has been created.** Real user Telegram
conversation/control acceptance and the first 08:00 delivery remain pending.

- [Capabilities, deployment and rollback](AGENT_MODE.md)
- [Measured acceptance and outstanding checks](ACCEPTANCE.md)
- [Authorization and unchanged identity boundaries](AUTHORIZATION.md)
- [Dedicated PostgreSQL setup](deploy/LOCAL_POSTGRES.md)

Worktree: `~/Projects/nimbus-telegram`, branch `feat/telegram-lab`, originally based on
Nimbus `377b6065`. The dirty original Nimbus and `nimbus-v2` worktrees are not edited.
Changes are committed locally, not pushed or merged.

## Integrated capabilities

The bot understands natural-language goals. There is no fixed digest command and no
per-capability activation sequence:

| Capability | Execution and persistence |
|---|---|
| Planning and conversation | Real Nimbus `AgentOS`, Astra through Pi's normal Codex OAuth |
| Code/files | bash/read/write/edit/list in rootless gVisor; persistent bounded `/workspace` ZIP in PG |
| Public research | Grok X Search and web search through Pi-managed xAI OAuth |
| Long-term memory | Identity-scoped key/value records; saved key index supplied to subsequent turns |
| Persistent tasks | Arbitrary supported instructions, daily timezone-aware delivery, immediate run, list/update/disable |
| Control | `/status`, `/cancel`, `/new`, `/help`, `/mem`; ordinary language invokes the tools |

**Nimbus owns all client tool execution.** Pi is a model/auth bridge, not a second
agent executor. Its `--no-tools` flag is intentional: native model tool calls return
to Nimbus's explicitly registered tools. No Telegram token/DSN is forwarded to Pi,
and no credentials, host home, writable host workspace or network enter gVisor.
There is no host-Bash fallback. Pi normally refreshes its own authorization.

The developer-only [browser computer-use tool](../tools/pi-browser-lab/README.md)
is separate and is **not exposed through Telegram**. Telegram attachments, arbitrary
package downloads, desktop control, external writes/payments and arbitrary cron
triggers are not implemented. This is not an unrestricted OpenClaw/cloud-agent clone.

## Reliability contract

- Exact numeric bot/user/chat allowlist; one active execution per conversation and
  64 queued/running/cancel-requested turns across the dedicated database.
- Transactional intake, cursor, dedupe, attempts, recent four-turn context and epochs.
  `/new` clears conversational context, not explicit long-term memory or schedules.
- DB-clock leases: default 30 s, renew at most every 1 s, no renewal after expiry.
  Scheduled leases are additionally capped by their run deadline. Revocation is
  checked and row-locked at request/delivery admission; state tools also commit-fence.
- Terminal interruption is not transparently resumed/replayed. A retry is new work.
- Ordered outbox, bounded definite delivery retries, bot-wide 429 cooldown. Ambiguous
  sends are `uncertain`, not automatically replayed. A receipt is not proof of reading.
- Daily work normally prepares five minutes early and holds results until the chosen
  delivery time. Missed runs coalesce to the latest eligible slot within four hours.
  Disable/update invalidates old generations, queued work and unsent notifications;
  it cannot recall an already admitted/in-flight message or provider request.
- Resource-limited, networkless gVisor operations; serialized workspace snapshots
  prevent parallel native calls from losing sibling writes. Stale attempts cannot
  persist a snapshot. Owned units/containers are cleaned on cancellation.

## Development and verification

```bash
cd ~/Projects/nimbus-telegram/chat-lab
uv sync --frozen
uv run pytest -q
uv run ruff check src tests scripts
uv run nimbus-chat-lab doctor

# Actual models / subscription usage, MOCK Telegram, isolated test PG:
uv run python scripts/agent_smoke.py
uv run python scripts/agent_search_smoke.py

# Actual installed gVisor, no Telegram or provider call:
uv run python scripts/sandbox_drills.py
```

The lab pins Python 3.12. Synthetic tests use isolated, Unix-socket-only PG 16.2
from `pgserver`; this is **not** the deployed database, which is supported PG 18.6.
Tests do not use Docker permissions or the original Nimbus stores. `doctor` describes
its own process environment, not the currently running systemd services.

`--engine nimbus-pi` remains an explicitly text-only fallback for operator rollback;
`echo`/`nimbus-mock` remain test modes. Deployed units select `nimbus-agent` explicitly.
The Agent unit has a 900 s turn bound, 30 s drain and 45 s systemd stop backstop.
Stopping a client cannot prove provider-side computation/billing stopped immediately.

## Operations and remaining risks

```bash
systemctl --user status nimbus-chat-{postgres,gateway,scheduler}.service nimbus-chat-worker@a.service
loginctl show-user "$USER" -p Linger
```

Services no longer depend on this development Pi session. Linger is enabled, but
08:00 delivery still requires the machine awake, online, disk unlocked and provider
credentials usable. Cold reboot/logout and a real future 08:00 delivery have not
been acceptance-tested. Worker B remains stopped; it is not required for one user.

Protected PG/session logs contain conversation/tool content. Telegram bot chats are
not end-to-end encrypted secret chats. Do not submit company material or credentials.
Local pre-cutover backup/restore passed; automatic/encrypted off-host backups,
retention, disk alerts and full host-loss recovery remain unimplemented. These are
known operational risks, not claims that those features are secretly active.

An OAuth-capable search endpoint does not prove unlimited/free subscription billing.
There are three search-request and 24 workspace-operation allowances per turn;
xAI `max_turns` is not a hard tool-count or monetary cap. Retrieval remains untrusted
model input; the sandbox is not a proof that semantic prompt injection is solved.

## Main implementation files

- `store.py`, `schema.sql`: intake, control, leases, ordered delivery and recovery.
- `agent_engine.py`, `agent_bridge.py`, `pi_bridge.ts`: Nimbus native tools and Pi auth/model bridge.
- `agent_state.py`, `agent_schema.sql`, `scheduler.py`: memory, workspace, schedules, fences and publication.
- `agent_sandbox.py`, `sandbox_runner.py`: verified runtime, limits, isolated file/code operations.
- `scripts/deploy_agent_local.py`: explicit one-time backed-up cutover and real-unit canary;
  **not** a general idempotent upgrade/retry command.
