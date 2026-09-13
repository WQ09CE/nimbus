"""Private single-account archive. No credentials or response bodies in console output."""

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

KINDS = {"hrv", "sleep", "heart_rate", "rhr", "readiness", "stress", "body_battery"}
MAX_RAW = 8 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,
 fetched_at TEXT NOT NULL, status TEXT NOT NULL, digest TEXT,
 error_code TEXT, library_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS observation_lookup ON observations(day,kind,fetched_at,id);
CREATE TABLE IF NOT EXISTS reports (
 id INTEGER PRIMARY KEY, day TEXT NOT NULL, created_at TEXT NOT NULL,
 version TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS labels (
 id INTEGER PRIMARY KEY, day TEXT NOT NULL, recorded_at TEXT NOT NULL,
 energy INTEGER NOT NULL CHECK(energy BETWEEN 1 AND 5),
 soreness INTEGER NOT NULL CHECK(soreness BETWEEN 1 AND 5),
 before_viewing_score INTEGER NOT NULL CHECK(before_viewing_score IN (0,1))
);
"""


class LocalError(RuntimeError):
    """Only fixed, nonsensitive codes are displayed by the CLI."""


def now():
    return datetime.now(timezone.utc).isoformat()


def day_string(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise LocalError("invalid_date")
    return value


def private_dir(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise LocalError("symlink_refused")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise LocalError("unsafe_directory_permissions")
    return path


def private_file(path):
    if path.is_symlink():
        raise LocalError("symlink_refused")
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        raise LocalError("unsafe_file_permissions")


class Archive:
    def __init__(self, root):
        root = Path(os.path.abspath(Path(root).expanduser()))
        if any((parent / ".git").exists() for parent in [root, *root.parents]):
            raise LocalError("health_data_inside_git_refused")
        self.root = private_dir(root)
        self.raw = private_dir(root / "raw")
        self.tokens = private_dir(root / "tokens")
        self.db = root / "health.sqlite3"
        self.lock_path = root / ".lock"

    @contextmanager
    def lock(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            private_file(self.lock_path)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LocalError("another_command_is_running") from None
            self.initialize()
            yield self
        finally:
            os.close(fd)

    def initialize(self):
        if not self.db.exists():
            fd = os.open(self.db, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        private_file(self.db)
        with self.connect() as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if tables:
                version = (
                    conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                    if "meta" in tables
                    else None
                )
                if not version or version[0] != "1":
                    raise LocalError("schema_version_unsupported")
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema','1')")

    @contextmanager
    def connect(self):
        private_file(self.db)
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def bind(self, identity):
        # Identity is only an opaque account hash; never an email or token.
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise LocalError("invalid_account_binding")
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='account'").fetchone()
            if row and row[0] != identity:
                raise LocalError("different_account_refused")
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('account',?)", (identity,))
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('region','garmin.cn')")

    def record(self, day, kind, payload, *, status="ok", error_code=None, fetched_at=None):
        day_string(day)
        if kind not in KINDS or status not in {"ok", "empty", "error", "unavailable"}:
            raise LocalError("invalid_observation")
        if error_code is not None and error_code not in {
            "auth",
            "rate_limit",
            "not_found",
            "transport",
            "unexpected",
            "access_denied",
        }:
            raise LocalError("invalid_error_code")
        digest = None
        if status in {"ok", "empty"}:
            data = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
            if len(data) > MAX_RAW:
                raise LocalError("response_size_bound")
            digest = hashlib.sha256(data).hexdigest()
            path = self.raw / f"{digest}.json"
            fd, pending = tempfile.mkstemp(prefix=".pending-", dir=self.raw)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    # Publish only a complete file; never overwrite an existing archive object.
                    os.link(pending, path, follow_symlinks=False)
                except FileExistsError:
                    private_file(path)
                    if (
                        path.stat().st_size > MAX_RAW
                        or hashlib.sha256(path.read_bytes()).hexdigest() != digest
                    ):
                        raise LocalError("archive_corrupt") from None
            finally:
                Path(pending).unlink(missing_ok=True)
            directory_fd = os.open(self.raw, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        fetched_at = fetched_at or now()
        stamp = datetime.fromisoformat(fetched_at)
        if stamp.tzinfo is None:
            raise LocalError("timestamp_timezone_required")
        fetched_at = stamp.astimezone(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO observations(day,kind,fetched_at,status,digest,error_code,library_version) "
                "VALUES (?,?,?,?,?,?,?)",
                (day, kind, fetched_at, status, digest, error_code, "0.3.13"),
            )

    def latest(self, day, cutoff=None):
        day_string(day)
        cutoff = cutoff or now()
        stamp = datetime.fromisoformat(cutoff)
        if stamp.tzinfo is None:
            raise LocalError("timestamp_timezone_required")
        cutoff = stamp.astimezone(timezone.utc).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY kind "
                "ORDER BY fetched_at DESC,id DESC) AS n FROM observations "
                "WHERE day=? AND fetched_at<=?) WHERE n=1",
                (day, cutoff),
            ).fetchall()
        result = {}
        for row in rows:
            record = dict(row)
            record.pop("n")
            record["payload"] = None
            if row["digest"]:
                digest = row["digest"]
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise LocalError("archive_corrupt")
                path = self.raw / f"{digest}.json"
                private_file(path)
                if path.stat().st_size > MAX_RAW:
                    raise LocalError("archive_corrupt")
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise LocalError("archive_corrupt")
                record["payload"] = json.loads(raw)
            result[row["kind"]] = record
        return result

    def coverage(self):
        with self.connect() as conn:
            counts = [
                dict(r)
                for r in conn.execute(
                    "SELECT kind,status,count(*) AS observations,min(day) AS first_day,max(day) AS last_day "
                    "FROM observations GROUP BY kind,status ORDER BY kind,status"
                )
            ]
            labels = conn.execute("SELECT count(DISTINCT day) FROM labels").fetchone()[0]
        return {"observations": counts, "label_days": labels}

    def save_report(self, report):
        self.save_analysis(
            report["current"]["day"], report["current"]["normalizer_version"], report
        )

    def save_analysis(self, day, version, report):
        day_string(day)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO reports(day,created_at,version,body) VALUES (?,?,?,?)",
                (
                    day,
                    now(),
                    version,
                    json.dumps(report, ensure_ascii=False, allow_nan=False),
                ),
            )

    def label(self, day, energy, soreness, before_viewing_score):
        day_string(day)
        if (
            type(energy) is not int
            or type(soreness) is not int
            or energy not in range(1, 6)
            or soreness not in range(1, 6)
            or type(before_viewing_score) is not bool
        ):
            raise LocalError("label_out_of_range")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO labels(day,recorded_at,energy,soreness,before_viewing_score) "
                "VALUES (?,?,?,?,?)",
                (day, now(), energy, soreness, int(before_viewing_score)),
            )
