"""Append-only session event log — Phase 0 of the event-sourcing migration.

Dual-write posture: the JSON snapshot (storage.py) REMAINS authoritative;
this log is an auditable trace written alongside it. Once turn/step brackets
and the message events prove equivalent to the snapshot (see
derive_messages), later phases can invert authority (MMU derives from the
log) and build fork/resume on top.

Design notes (dsh-aligned):
- seq == index is a hard contract: contiguous, no holes.
- turn/start .. turn/end{reason} bracket one driver run; step/start ..
  step/end bracket one VCPU step. Every exit path assigns a reason.
- 'interrupted' is RESERVED for crash-repair synthesis — no live loop ever
  emits it. Live cancellation is 'aborted'.
- Metadata (model, cwd, lineage) stays in the snapshot header, not the log.
- Compaction logs an informational 'compaction/applied' event; message-level
  equivalence via derive_messages() is only guaranteed for sessions without
  compaction (surface-replace semantics arrive in a later phase).
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Every reason a live loop may assign to turn/end. 'interrupted' is absent
# by design: it is reserved for crash-repair synthesis (see module docstring).
LIVE_TURN_END_KINDS = ("completed", "aborted", "error", "max-iterations")


@dataclass
class SessionEvent:
    """One log entry. seq is assigned by the log (== index at append time)."""
    seq: int
    type: str
    time: float  # epoch seconds
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"seq": self.seq, "type": self.type, "time": self.time, "data": self.data}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionEvent":
        return cls(seq=d["seq"], type=d["type"], time=d["time"], data=d.get("data", {}))


class SessionLog:
    """Append-only event log, one JSON line per event.

    Appends write through to disk immediately (open/write/close per event —
    bounded batching is a later-phase concern; Phase 0 event rates are low).
    A torn final line on crash is skipped at load time.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._events: List[SessionEvent] = []

    @property
    def events(self) -> List[SessionEvent]:
        return self._events

    def append(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> SessionEvent:
        """Append one event. seq == len(log) before the append (contiguous)."""
        event = SessionEvent(
            seq=len(self._events),
            type=event_type,
            time=time.time(),
            data=data or {},
        )
        self._events.append(event)
        if self.path is not None:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            except OSError:
                # The log is a trace, not the truth (snapshot is authoritative
                # in Phase 0) — never let trace I/O kill the loop.
                pass
        return event

    @classmethod
    def load(cls, path: Path) -> "SessionLog":
        """Load a log from disk. A torn (undecodable) final line is dropped;
        a torn line anywhere else is corruption and raises."""
        log = cls(path=None)  # don't re-append to the file while loading
        try:
            raw_lines = Path(path).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            log.path = Path(path)
            return log
        for i, line in enumerate(raw_lines):
            if not line.strip():
                continue
            try:
                event = SessionEvent.from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError):
                if i == len(raw_lines) - 1:
                    break  # torn tail from a crash mid-write: drop it
                raise ValueError(f"corrupt session log at line {i + 1}: {path}")
            if event.seq != len(log._events):
                raise ValueError(
                    f"seq gap in session log at line {i + 1}: "
                    f"expected {len(log._events)}, got {event.seq}"
                )
            log._events.append(event)
        log.path = Path(path)
        return log


def derive_messages(events: List[SessionEvent]) -> List[Dict[str, Any]]:
    """Project message events back into the MMU message-dict shape.

    This is the Phase 0 equivalence check: for a session without compaction,
    derive_messages(log.events) must equal [m.to_dict() for m in mmu._messages].
    """
    messages: List[Dict[str, Any]] = []
    for event in events:
        if event.type in ("user/message", "assistant/message", "tool/result"):
            messages.append(event.data["message"])
    return messages
