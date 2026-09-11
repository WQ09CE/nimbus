# Dedicated local PostgreSQL deployment

This is an **operator-invoked** deployment, not part of `uv sync` or tests. It uses
Arch's supported PostgreSQL package, a separate user service/data directory, and
Unix sockets only. It does not initialize/start the system `postgresql.service`.
On Dennis's host the installed version on 2026-09-10 is PostgreSQL **18.6**.
The 2026-09-11 integrated Agent cutover is documented in [AGENT_MODE.md](../AGENT_MODE.md);
PG/gateway/worker A/scheduler are now enabled and `Linger=yes`.

## One-time setup

Obtain operator authorization before package installation or service activation.
Existing operator `gateway.env`/`worker.env` must have a single empty or `REPLACE`
placeholder `NIMBUS_LAB_DSN` entry, be owned regular files with mode 0600, and live
in `~/.config/nimbus-chat-lab/` (0700). Preserve existing bot ID/token-file entries.
The token must stay only in the separate protected token file, not the worker env.

```bash
omarchy pkg add postgresql                 # operator enters sudo password locally
cd ~/Projects/nimbus-telegram/chat-lab
uv run python scripts/configure_local_postgres.py prepare

# Review unit files before installation. Refuse to overwrite existing user units.
install -m 644 deploy/nimbus-chat-postgres.service ~/.config/systemd/user/
install -m 644 deploy/nimbus-chat-gateway.service ~/.config/systemd/user/
install -m 644 deploy/nimbus-chat-worker@.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nimbus-chat-postgres.service
uv run python scripts/configure_local_postgres.py bootstrap
```

`prepare` refuses existing cluster/config paths; it never reinitializes a database.
`bootstrap` refuses configured DSNs or existing named roles/database. It creates:

- `~/.local/share/nimbus-chat-lab/postgres/`, mode 0700;
- `postgres.conf` / `pg_hba.conf` in the protected operator config directory;
- `/run/user/UID/nimbus-chat-postgres/.s.PGSQL.55432`, protected runtime directory;
- `nimbus_chat_owner`, NOLOGIN schema/database owner;
- `nimbus_chat`, SCRAM-authenticated application login, not a superuser, not a
  database/role creator, no schema CREATE privilege; only required data/sequence
  privileges in this database;
- initialized S1 plus additive Agent schema/views and matching random-password DSNs in both env files;
  previous env files are saved in protected, dated `backups/` under the config dir.

The local OS user retains administrative peer access. This is database privilege
separation, **not isolation from a hostile process running as that same OS user**.
The integrated bot's code/file tools are separately isolated by verified gVisor.
Statement/parameter logging is suppressed. PostgreSQL
has no TCP listener, a 1 GiB memory limit, 128-task limit, normal durability defaults,
and fast, orderly shutdown. Package major upgrades still require a planned upgrade;
do not delete/reinitialize the data directory to make a new binary start.

Bootstrap is not an automatic retry/recovery transaction: PostgreSQL database
creation and env-file installation span separate transactions/files. On a partial
failure, inspect owned state and repair explicitly; do not drop existing data or
rotate credentials blindly. Never print env files or raw exceptions containing DSNs.
The helper deliberately reports exception classes only.

## Identity gate and startup

Bootstrap neither grants a Telegram identity nor starts a gateway/worker. Use
`identify` with the gateway stopped, obtain explicit confirmation of the numeric
bot/user/chat tuple, then run `nimbus-chat-lab allow` with the app environment loaded
**without printing it**. Bootstrap already initialized the schema as the owner;
ordinary `init` is not a migration mechanism for the restricted app role.

Only after the identity gate **and** the verified runtime/configuration in the Agent
runbook, start the integrated service set:

```bash
systemctl --user enable --now nimbus-chat-gateway.service nimbus-chat-worker@a.service nimbus-chat-scheduler.service
# Worker B is optional; currently stopped, not necessary for one private user.
```

Do not run these as a substitute for owner migration or runtime provisioning. A
worker template now explicitly selects Agent mode and requires the private runtime config.

App templates `Wants`/`After` the local PG service; they fail/restart on DB errors
rather than falling back. These dependencies assume this local deployment: remove
or override them if an operator later deliberately selects external PostgreSQL.
For maintenance, explicitly stop gateway, running workers and scheduler before PostgreSQL.
Their startup remains subject to normal user-manager lifetime. Check
`loginctl show-user "$USER" -p Linger`: when false, enabling user units means start at
user login, **not** guaranteed unattended startup after reboot or survival of logout.
This PG helper does not change lingering. It was separately enabled for Dennis's
authorized resident-Agent deployment; an actual logout/cold boot was not tested.

## Backups and status

Use `pg_dump -Fc` through the private socket, with credentials/connection settings
passed via a protected environment, never command-line URLs or logs. Dumps must be
0600 in a 0700 directory. Validate restore into a freshly named disposable database,
not by overwriting the live database. The owner/login roles must exist when restoring
these ACLs; recreate the login with a new secret through a reviewed recovery process
on a new host. Keep the Telegram token in a separate encrypted secret backup.

An initial empty-schema backup/restore smoke is not a content-retention policy or
continuous backup guarantee. Scheduled backups, encrypted off-host copies, disk
alerts, content retention and a full host-loss drill remain unimplemented operational
safeguards. The Agent deployment does not pretend those protections are already active.
Do not send company material/secrets through Telegram. A local model/mock test is
not a real Telegram reply-service acceptance test.

Host-specific observations belong in protected `.artifacts/`, not source control;
never commit token-bearing env files, dumps, messages or personal allowlist IDs.
