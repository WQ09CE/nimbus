"""Unified timestamp utilities for Nimbus.

All internal timestamps should use UTC-aware datetimes.
User-visible display timestamps use local time.

Rules:
- Storage/transport: utcnow() -> timezone-aware UTC datetime
- Display (filenames, UI): local_now_str() -> local time string
- Unix epoch (time.time()): unchanged, already UTC
- Forbidden: datetime.utcnow() (deprecated in Python 3.12)
- Forbidden: naive datetime.now() for storage/transport
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return current time as timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def local_now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Return current local time as formatted string (for display only)."""
    return datetime.now().strftime(fmt)


def ensure_aware(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (assume UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
