"""User-authorized LOCAL PLAINTEXT error records, never model/Telegram payloads.

No environment, auth storage, request headers or traceback locals are collected.
Raw error messages/bodies/stderr can nevertheless contain sensitive upstream text.
"""

import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

MAX_RECORD = 4 * 1024 * 1024
MAX_RECORDS = 16
FAILURE_STAGES = {
    "spawn",
    "request_encode",
    "child_io",
    "child_exit",
    "missing_result",
    "duplicate_result",
    "provider_error",
    "response_validation",
    "timeout",
    "cancelled",
    "runtime",
}


class BridgeFailure(RuntimeError):
    def __init__(self, stage, request_id):
        self.stage = stage if stage in FAILURE_STAGES else "child_io"
        self.request_id = str(UUID(str(request_id)))
        self.diagnostic_saved = False
        # Only this safe correlation string travels through AgentOS and platform state.
        super().__init__(f"Bridge failure: {self.stage}; request={self.request_id}")

    def summary(self):
        return {
            "stage": self.stage,
            "request_id": self.request_id,
            "diagnostic_saved": self.diagnostic_saved,
        }


class BridgeTimeout(BridgeFailure, TimeoutError):
    """Retain the existing TimeoutError contract for native runtime timeout handling."""


def exception_record(exc):
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(exc)),
    }


def save_diagnostic(attempt_root, identifier, data):
    """Exclusive private write, bounded per attempt; I/O failure never alters task outcome."""
    try:
        identifier = str(UUID(str(identifier)))
        root = Path(attempt_root)
        if root.is_symlink():
            return False
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory = root / "diagnostics"
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return False
        if sum(1 for _ in directory.iterdir()) >= MAX_RECORDS:
            return False
        data = {
            "version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "request_id": identifier,
            **data,
        }
        raw = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8", "replace")
        if len(raw) > MAX_RECORD:
            # Keep JSON parseable even for pathological binary/control-heavy child output.
            raw = json.dumps(
                {
                    "version": 1,
                    "request_id": identifier,
                    "record_truncated": True,
                    "original_bytes": len(raw),
                    "json_prefix": raw[: MAX_RECORD // 8].decode("utf-8", "replace"),
                },
                ensure_ascii=False,
            ).encode("utf-8")
        fd = os.open(
            directory / f"{identifier}.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception:
        return False
