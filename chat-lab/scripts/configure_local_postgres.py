"""One-time, operator-invoked dedicated user PostgreSQL provisioning.

Refuses existing state/roles or configured DSNs. Never touches the system cluster,
prints credentials, grants Telegram identities, or starts a gateway/worker.
"""

import argparse
import json
import os
import pwd
import re
import secrets
import stat
import subprocess
import sys
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

HOME = Path.home()
CONFIG = HOME / ".config/nimbus-chat-lab"
STATE = HOME / ".local/share/nimbus-chat-lab"
DATA = STATE / "postgres"
SOCKET = Path(f"/run/user/{os.getuid()}/nimbus-chat-postgres")
PORT = 55432
ADMIN = pwd.getpwuid(os.getuid()).pw_name


def private_write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def prepare():
    if DATA.exists() or any((CONFIG / name).exists() for name in ["postgres.conf", "pg_hba.conf"]):
        raise RuntimeError("Dedicated PostgreSQL state/config already exists; refusing overwrite")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(
        p.is_symlink() or p.stat().st_uid != os.getuid() or p.stat().st_mode & 0o077
        for p in [STATE, CONFIG]
    ):
        raise RuntimeError("State/config directories must be owned, mode 0700, not symlinks")
    subprocess.run(
        [
            "/usr/bin/initdb",
            "-D",
            str(DATA),
            "-U",
            ADMIN,
            "--encoding=UTF8",
            "--locale=C.UTF-8",
            "--auth-local=peer",
            "--auth-host=scram-sha-256",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def literal(path):
        return str(path).replace("'", "''")

    private_write(
        CONFIG / "postgres.conf",
        f"""# Dedicated Nimbus lab cluster; no TCP listener.
listen_addresses = ''
port = {PORT}
unix_socket_directories = '{literal(SOCKET)}'
unix_socket_permissions = 0700
hba_file = '{literal(CONFIG / "pg_hba.conf")}'
ident_file = '{literal(DATA / "pg_ident.conf")}'
password_encryption = 'scram-sha-256'
max_connections = 32
shared_buffers = '128MB'
work_mem = '4MB'
statement_timeout = '5s'
idle_in_transaction_session_timeout = '10s'
# Do not put SQL/parameters or prompt content into the service journal.
log_statement = 'none'
log_min_error_statement = 'panic'
log_parameter_max_length_on_error = 0
log_error_verbosity = 'terse'
""",
    )
    private_write(
        CONFIG / "pg_hba.conf",
        f"""# Administrative access is local OS peer identity; app requires SCRAM.
local all {ADMIN} peer
local nimbus_chat nimbus_chat scram-sha-256
local all all reject
host all all 0.0.0.0/0 reject
host all all ::0/0 reject
""",
    )
    print(json.dumps({"prepared": True, "port": PORT, "network_listener": False}))


def bootstrap():
    # Read/validate env files before creating roles; no secrets enter output.
    envs = {}
    for name in ["gateway.env", "worker.env"]:
        path = CONFIG / name
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as f:
            st = os.fstat(f.fileno())
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_size > 65536
                or st.st_uid != os.getuid()
                or st.st_mode & 0o077
            ):
                raise RuntimeError("Operator env file must be owned and private")
            body = f.read(65536)
        entries = re.findall(r"^NIMBUS_LAB_DSN=(.*)$", body, re.M)
        if (
            len(entries) != 1
            or entries[0].strip().strip("\"'") not in ("",)
            and "REPLACE" not in entries[0]
        ):
            raise RuntimeError("DSN already configured or invalid; refusing rotation")
        envs[path] = body
    admin = make_conninfo(host=str(SOCKET), port=PORT, user=ADMIN, dbname="postgres")
    password = secrets.token_urlsafe(36)
    with psycopg.connect(admin, autocommit=True) as c:
        if (
            c.execute(
                "SELECT 1 FROM pg_roles WHERE rolname IN ('nimbus_chat','nimbus_chat_owner')"
            ).fetchone()
            or c.execute("SELECT 1 FROM pg_database WHERE datname='nimbus_chat'").fetchone()
        ):
            raise RuntimeError("Dedicated roles/database already exist; refusing reinitialization")
        c.execute(
            "CREATE ROLE nimbus_chat_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        c.execute(
            sql.SQL(
                "CREATE ROLE nimbus_chat LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Literal(password))
        )
        c.execute("CREATE DATABASE nimbus_chat OWNER nimbus_chat_owner")
        c.execute("REVOKE ALL ON DATABASE nimbus_chat FROM PUBLIC")
        c.execute("GRANT CONNECT ON DATABASE nimbus_chat TO nimbus_chat")
    with psycopg.connect(make_conninfo(admin, dbname="nimbus_chat")) as c:
        c.execute("SET ROLE nimbus_chat_owner")
        c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        c.execute(files("nimbus_chat_lab").joinpath("schema.sql").read_text())
        c.execute(files("nimbus_chat_lab").joinpath("agent_schema.sql").read_text())
        c.execute(files('nimbus_chat_lab').joinpath('conversation_schema.sql').read_text())
        c.execute("GRANT USAGE ON SCHEMA public TO nimbus_chat")
        c.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO nimbus_chat")
        c.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO nimbus_chat")
        c.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO nimbus_chat"
        )
        c.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO nimbus_chat"
        )
    dsn = f"postgresql://nimbus_chat:{quote(password, safe='')}@/nimbus_chat?host={quote(str(SOCKET), safe='')}&port={PORT}"
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT version FROM schema_version").fetchone() == (1,)
        assert c.execute(
            "SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
        ).fetchone() == (False, False, False)
        assert not c.execute(
            "SELECT has_schema_privilege(current_user,'public','CREATE')"
        ).fetchone()[0]
    backup = CONFIG / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup.mkdir(parents=True, mode=0o700)
    backup.parent.chmod(0o700)
    for path, body in envs.items():
        private_write(backup / path.name, body)
        updated = re.sub(r"^NIMBUS_LAB_DSN=.*$", "NIMBUS_LAB_DSN=" + dsn, body, flags=re.M)
        temporary = path.with_name(path.name + ".new")
        private_write(temporary, updated)
        temporary.replace(path)
    print(
        json.dumps(
            {
                "bootstrapped": True,
                "database": "nimbus_chat",
                "app_superuser": False,
                "app_schema_create": False,
                "credentials_written": True,
                "telegram_allowlist": "not modified",
            }
        )
    )


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "bootstrap"])
    args = parser.parse_args()
    try:
        {"prepare": prepare, "bootstrap": bootstrap}[args.action]()
    except Exception as error:
        print(
            f"FAILED: {type(error).__name__}; inspect local setup without printing credentials.",
            file=sys.stderr,
        )
        sys.exit(1)
