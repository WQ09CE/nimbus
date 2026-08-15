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


# =============================================================================
# Invariants (Phase 1)
# =============================================================================


def check_invariants(events: List[SessionEvent], allow_open_tail: bool = False) -> List[str]:
    """Validate the structural contract of a session log.

    Returns a list of violation strings (empty == log is well-formed):
    - seq is contiguous from 0
    - turn numbers increase by exactly 1; brackets never nest
    - step/start only inside an open turn; step numbers increase by 1 per turn
    - every turn/end reason kind is known
    - every tool/result answers a tool_call_id requested by the latest
      assistant message with tool_calls

    An open turn/step at the tail is the crash signature, not corruption:
    it is a violation only when allow_open_tail is False. Crash repair
    (interrupted_turn_closers) consumes logs where it is allowed.
    """
    violations: List[str] = []
    open_turn: Optional[int] = None
    open_step: Optional[int] = None
    last_turn = 0
    last_step = 0
    pending_call_ids: set = set()

    for i, event in enumerate(events):
        if event.seq != i:
            violations.append(f"seq gap at index {i}: got {event.seq}")
        t = event.type
        if t == "turn/start":
            if open_turn is not None:
                violations.append(f"seq {event.seq}: turn/start inside open turn {open_turn}")
            turn = event.data.get("turn")
            if turn != last_turn + 1:
                violations.append(f"seq {event.seq}: turn {turn} not monotonic (last {last_turn})")
            open_turn = turn
            last_turn = turn if isinstance(turn, int) else last_turn + 1
            last_step = 0
        elif t == "turn/end":
            if open_turn is None:
                violations.append(f"seq {event.seq}: turn/end without open turn")
            if open_step is not None:
                violations.append(f"seq {event.seq}: turn/end with step {open_step} still open")
            kind = (event.data.get("reason") or {}).get("kind")
            if kind not in LIVE_TURN_END_KINDS + ("interrupted",):
                violations.append(f"seq {event.seq}: unknown turn/end reason {kind!r}")
            open_turn = None
        elif t == "step/start":
            if open_turn is None:
                violations.append(f"seq {event.seq}: step/start outside any turn")
            if open_step is not None:
                violations.append(f"seq {event.seq}: step/start inside open step {open_step}")
            step = event.data.get("step")
            if step != last_step + 1:
                violations.append(f"seq {event.seq}: step {step} not monotonic (last {last_step})")
            open_step = step
            last_step = step if isinstance(step, int) else last_step + 1
        elif t == "step/end":
            if open_step is None:
                violations.append(f"seq {event.seq}: step/end without open step")
            open_step = None
        elif t == "assistant/message":
            msg = event.data.get("message", {})
            for tc in msg.get("tool_calls") or []:
                if tc.get("id"):
                    pending_call_ids.add(tc["id"])
        elif t == "tool/result":
            call_id = event.data.get("message", {}).get("tool_call_id")
            if call_id and call_id not in pending_call_ids:
                violations.append(f"seq {event.seq}: tool/result for unknown call id {call_id!r}")
            pending_call_ids.discard(call_id)

    if not allow_open_tail:
        if open_step is not None:
            violations.append(f"log ends with step {open_step} open")
        if open_turn is not None:
            violations.append(f"log ends with turn {open_turn} open")
    return violations


# =============================================================================
# Crash repair (Phase 1) — synthetic closers, never truncation
# =============================================================================

# Two risk grades for a tool call left unanswered by a crash. nimbus's gate
# executes a step's tool calls serially, so within one assistant message the
# FIRST unanswered call may have been in flight (outcome unknown), while every
# later one had provably not started yet.
TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"
TOOL_NOT_STARTED = "TOOL_NOT_STARTED"

_OUTCOME_UNKNOWN_TEXT = (
    "[TOOL_OUTCOME_UNKNOWN] The session crashed while this tool call may have "
    "been executing; its result was never recorded. If the operation is "
    "read-only or idempotent you may retry it. If it has side effects, verify "
    "the current external state first (read the file, check the environment) "
    "or ask the user. Do not retry blindly."
)
_NOT_STARTED_TEXT = (
    "[TOOL_NOT_STARTED] The session crashed before this tool call started "
    "executing. It was not run. Retry it if it is still needed."
)


def grade_unanswered_calls(tool_calls: List[Dict[str, Any]], answered_ids: set) -> List[tuple]:
    """Grade a serial batch's unanswered calls: (call, code, content) triples.

    First unanswered call in request order → TOOL_OUTCOME_UNKNOWN; the rest →
    TOOL_NOT_STARTED (serial execution had not reached them).
    """
    graded = []
    first = True
    for tc in tool_calls:
        if tc.get("id") in answered_ids:
            continue
        code = TOOL_OUTCOME_UNKNOWN if first else TOOL_NOT_STARTED
        text = _OUTCOME_UNKNOWN_TEXT if first else _NOT_STARTED_TEXT
        graded.append((tc, code, text))
        first = False
    return graded


def interrupted_turn_closers(events: List[SessionEvent]) -> List[SessionEvent]:
    """Synthesize the closing events for a log that ends inside an open turn.

    Returns [] for a balanced log (idempotent). Otherwise, in order:
    1. one graded tool/result per unanswered tool call of the latest
       assistant message (see grade_unanswered_calls),
    2. step/end for an open step,
    3. turn/end with the reserved reason kind 'interrupted'.

    Deterministic: synthetic events reuse the LAST real event's timestamp
    (repair never invents a future time) and continue its seq run.
    """
    open_turn: Optional[int] = None
    open_step: Optional[int] = None
    last_calls: List[Dict[str, Any]] = []
    answered: set = set()

    for event in events:
        t = event.type
        if t == "turn/start":
            open_turn = event.data.get("turn")
        elif t == "turn/end":
            open_turn = None
        elif t == "step/start":
            open_step = event.data.get("step")
        elif t == "step/end":
            open_step = None
        elif t == "assistant/message":
            calls = event.data.get("message", {}).get("tool_calls") or []
            if calls:
                last_calls = calls
                answered = set()
        elif t == "tool/result":
            answered.add(event.data.get("message", {}).get("tool_call_id"))

    if open_turn is None:
        return []

    seq = len(events)
    stamp = events[-1].time if events else 0.0
    closers: List[SessionEvent] = []

    def _mk(event_type: str, data: Dict[str, Any]) -> None:
        nonlocal seq
        closers.append(SessionEvent(seq=seq, type=event_type, time=stamp, data=data))
        seq += 1

    if open_step is not None:
        for tc, code, text in grade_unanswered_calls(last_calls, answered):
            _mk("tool/result", {
                "message": {
                    "role": "tool",
                    "content": text,
                    "name": tc.get("function", {}).get("name"),
                    "tool_call_id": tc.get("id"),
                },
                "synthetic": True,
                "code": code,
            })
        _mk("step/end", {"turn": open_turn, "step": open_step, "synthetic": True})
    _mk("turn/end", {
        "turn": open_turn,
        "reason": {"kind": "interrupted"},
        "synthetic": True,
    })
    return closers
