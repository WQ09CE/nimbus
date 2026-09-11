# Integrated Agent — deployment and runbook

**Latest (09:22):** see [CONVERSATION_UX.md](CONVERSATION_UX.md). The user has since
created/enabled his daily job and confirmed the immediate report. Chat is now separate
from two background slots. The initial cutover narrative below remains historical.

## Actual local deployment

The 2026-09-11 cutover replaced text-only worker A with `nimbus-agent`, enabled the
persistent scheduler and Agent help, and retained the confirmed private identity.
No daily task was created. Evidence: `.artifacts/agent-local-deployment.json` and
`agent-post-cutover-health.json`. The original source worktrees remain untouched.

| Component | Location / setting |
|---|---|
| PG 18.6 | User `nimbus-chat-postgres.service`, Unix socket port 55432 only |
| Gateway | User `nimbus-chat-gateway.service`, exact existing private allowlist |
| Agent | User `nimbus-chat-worker@a.service`, `--engine nimbus-agent` |
| Scheduler | User `nimbus-chat-scheduler.service`, database-clock polling |
| Workers B/C | Two background slots; A is reserved for chat (09:22 UX update) |
| Lifetime | All four units enabled, `Linger=yes`; independent of this development Pi session |
| Worker env | `~/.config/nimbus-chat-lab/worker.env`, 0600; DSN, Agent mode/config path, no bot token |
| Gateway env/token | Same 0700 config directory; separate 0600 files |
| Runtime config | `agent-runtime.json`, 0600; paths, digest and verified runtime manifest, no credentials |
| gVisor | `~/.local/share/nimbus-chat-lab/gvisor/release-20260831.0/` |
| Backup | `~/.local/share/nimbus-chat-lab/backups/agent-cutover-20260910T173305Z/`, 0700 |

The system default `postgresql.service` is not used. No public database listener,
Docker socket permission change, personal browser profile or developer browser lab
is needed. Linger does not wake a suspended/powered-off machine or unlock its disk.
Actual logout/cold boot and a real future timed Telegram delivery remain untested.

## Architecture and scope

Astra plans using real Nimbus `AgentOS` native calls. The explicit tool registry is
`workspace`, `search`, `memory`, `schedule`, `activity`, `clock`. Pi handles one model/search
request per private child process, using normal OAuth resolution/refresh. It cannot
execute client tools. No plugin, skill or project-context auto-loading occurs there.

Grok research uses the native xAI Responses endpoint, `store:false`, X/web server
search tools, provider citations and usage. Query/results are public-research data,
not authority. The production bot does not use a copied API key or a custom auth
impersonation scheme. OAuth success does not settle the provider's billing policy.

Code/file tools enter rootless Podman + verified gVisor. They have no network and
no host-writable workspace mount. A bounded ZIP is restored into tmpfs and committed
to PG only after a fresh ownership/lease check. Native parallel workspace calls
serialize across the full read/run/snapshot boundary. No host fallback is possible.

Memory is scoped to the confirmed user/chat. Each turn receives a small saved-key
index and may retrieve relevant values. `/new` clears recent conversational history,
not explicit long-term memory, schedules or files. Background tasks can read memory /
schedule metadata but cannot mutate it or recursively create tasks.

Daily schedules run arbitrary supported instructions, not a digest-specific program.
They support timezone/hour/minute/lead, list/update/disable and an immediate run.
Weekly/arbitrary cron triggers, Telegram attachments, desktop/browser access,
networked package installation and arbitrary external writes are not available.

## Bounds and semantics

- 64 active/admitted turns globally; one running per lane. Chat buffers eight messages;
  two background workers handle separate schedule lanes. Context also includes sent reports.
- Turn bound 900 s; 20 model iterations; three search requests and 24 workspace
  operations per turn. xAI `max_turns=4` is not a hard search-count or spend cap.
- Memory: 64 keys per identity, values up to 8000 characters. Schedule count: 16,
  including disabled records; immediate-run backlog: four per identity.
- Workspace: 32 MiB uncompressed, 16 MiB archive, 2000 files. Reads/command text output
  are bounded; this is not a general large-file/artifact transfer channel.
- Sandbox: workspace tmpfs 64 MiB, temporary tmpfs 16 MiB, rootfs read-only, network
  none, all capabilities dropped. Unit: 768 MiB / 256 tasks / one CPU / 70 s runtime;
  command limit 45 s. Actual sentry cgroup membership and limits were checked.
- Worker: 1 GiB / 256 tasks / NoNewPrivileges. Drain 30 s, systemd stop backstop 45 s.
- Lease 30 s, renew no slower than once per second, capped by scheduled expiry.
  Expired ownership cannot be renewed or committed. Current identity is also fenced.
- Five-minute early preparation by default; final outbox publication waits until
  delivery slot. Latest eligible missed slot catches up within four hours; older
  missed slots are not all replayed. DST gaps are skipped and first fold used.
- Update/disable changes generation; old work and unsent notifications are invalidated.
  Replacement generations may reuse an early-prepared daily slot. Future prepared
  results and busy sessions do not monopolize the actionable scheduler scan.
- `run_now` is idempotent per requesting turn and independent of daily enablement.
  `published` means queued to outbox; `sent` means Telegram acknowledged the send,
  not human receipt. Ambiguous sends are not automatically retried.
- Cancelling/stopping clients cannot retract admitted/in-flight provider requests,
  remote billing or already delivered messages. Interrupted model work is terminal.

## Setup and changes

The current host has already been switched. **Do not rerun bootstrap/cutover blindly.**
`scripts/deploy_agent_local.py --apply` is an explicit one-time S1 migration, not an
idempotent package installer or general upgrade tool. It now refuses Agent-mode envs.
It assumes the already confirmed identity, runtime/image and private configuration.
It drains ingress, backs up/restores PG, migrates as owner, starts services, executes
and cleans an operator canary, then opens ingress. Partial failure requires inspection.

For a different machine, review/provision a supported PG, rootless Podman and the
complete official gVisor bundle independently. Verify its official archive checksum;
pin all runtime files in the private manifest and the image by digest. A bare copied
`runsc` is insufficient for this release's helper-binary layout. Runtime updates are
manual reviewed operations; no automatic updater is installed.

Fresh PG bootstrap now installs both `schema.sql` and `agent_schema.sql` as owner.
Existing deployments require owner-applied additive migrations with ingress/workers
stopped. The app role cannot run ordinary `init` as a migration mechanism.

```bash
# Inspect metadata only; never print env/token files.
systemctl --user show nimbus-chat-worker@a.service nimbus-chat-scheduler.service \
  -p ActiveState -p SubState -p NRestarts
loginctl show-user "$USER" -p Linger

# Maintenance: stop admission, then workers/scheduler, before PG if needed.
systemctl --user stop nimbus-chat-gateway.service
systemctl --user stop nimbus-chat-worker@{a,b,c}.service nimbus-chat-scheduler.service
# ...owner migration/maintenance...
systemctl --user start nimbus-chat-worker@{a,b,c}.service nimbus-chat-scheduler.service nimbus-chat-gateway.service
```

A new model request during shutdown may be interrupted after the drain period; it
will not silently replay. If a cancellation-resistant engine poisons a worker, the
CLI fail-stops and database lease recovery, not early local reuse, owns interruption.

## Roll back to text-only mode

1. Stop gateway, all three workers and scheduler; disable B/C for text-only fallback. Do not remove data or rewind the polling cursor.
2. Disable the scheduler unit. Restore the backed-up `worker.env`, `gateway.env` and
   `nimbus-chat-worker@.service` from the cutover directory, preserving 0600 secret-file
   modes. They select the earlier text-only engine. Do not print their contents.
3. `systemctl --user daemon-reload`, then start worker A and gateway.
4. Keep the additive Agent tables/views; current shared Store code references them
   even in text-only mode. Do **not** restore the pre-cutover dump over live state.
5. If future users already created jobs, explicitly preserve/disable their records
   under maintenance before returning to Agent mode; do not silently reactivate them.

This is a reviewed rollback procedure, not a claim a destructive rollback was run
against the live database. The separate backup-restore test did run successfully.

## User acceptance and operational gaps

Use the copyable message in [AUTHORIZATION.md](AUTHORIZATION.md). Confirm real task ID,
`Asia/Shanghai`, next 08:00 delivery, enabled state, immediate output and original-post
links. Then verify an actual future delivery and a natural-language disable. Dennis has already enabled the daily job; do not create a duplicate during UX checks.

No automatic/off-host encrypted backup, retention, disk alerting or host-loss recovery
has been configured. Local backups share the host's failure domain. Protected logs
contain content, and a hostile process under the same OS user remains outside this
security claim. Do not submit company material or credentials through Telegram.
