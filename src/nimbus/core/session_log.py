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
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nimbus.session_log")

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
    """Append-only event log with bounded synchronous write-behind.

    Non-causal events are flushed by a short daemon timer even when no later
    append occurs.  A re-entrant lock serializes append/flush/close and timer
    callbacks, so this synchronous class is safe in threads and both with and
    without a running asyncio loop.  ``close`` is the lifecycle barrier that
    cancels the timer and durably drains pending events.
    """

    FLUSH_WINDOW_SEC = 0.2

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._events: List[SessionEvent] = []
        self._pending: List[SessionEvent] = []
        self._pending_since: float = 0.0
        self._lock = threading.RLock()
        self._flush_timer: Optional[threading.Timer] = None
        self._timer_generation = 0
        self._closed = False
        # Set by load(): open() repairs a torn tail before appending, and a
        # valid final JSON record without LF needs one separator before append.
        self._truncate_at: Optional[int] = None
        self._needs_separator = False

    @property
    def events(self) -> List[SessionEvent]:
        with self._lock:
            return list(self._events)

    def append(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> SessionEvent:
        """Append one event. seq == len(log) before the append (contiguous)."""
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot append to a closed SessionLog")
            event = SessionEvent(
                seq=len(self._events),
                type=event_type,
                time=time.time(),
                data=data or {},
            )
            self._append_event_locked(event)
            return event

    def _append_event(self, event: SessionEvent) -> None:
        """Append a pre-built event verbatim (used by crash repair)."""
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot append to a closed SessionLog")
            self._append_event_locked(event)

    def _append_event_locked(self, event: SessionEvent) -> None:
        if event.seq != len(self._events):
            raise ValueError(
                f"seq contract violated: appending {event.seq} at {len(self._events)}"
            )
        self._events.append(event)
        if self.path is None:
            return
        if not self._pending:
            self._pending_since = time.monotonic()
        self._pending.append(event)
        if self._is_causal(event):
            self._flush_locked()
        elif self._flush_timer is None:
            self._schedule_flush_locked()

    def _schedule_flush_locked(self) -> None:
        self._timer_generation += 1
        generation = self._timer_generation
        timer = threading.Timer(self.FLUSH_WINDOW_SEC, self._timer_flush, (generation,))
        timer.daemon = True
        self._flush_timer = timer
        timer.start()

    def _timer_flush(self, generation: int) -> None:
        with self._lock:
            # A cancelled callback may already be running. Generation guards
            # it from clearing or flushing on behalf of a replacement timer.
            if generation != self._timer_generation:
                return
            self._flush_timer = None
            if not self._closed:
                self._flush_locked()

    @staticmethod
    def _is_causal(event: SessionEvent) -> bool:
        t = event.type
        if t in ("turn/end", "tool/result", "compaction/applied", "seed/applied"):
            return True
        # The assistant's decision record must hit disk BEFORE its tool calls
        # execute (side effects) — dsh's checkpoint-before-effects barrier.
        if t == "assistant/message" and event.data.get("message", {}).get("tool_calls"):
            return True
        return False

    def flush(self) -> None:
        """Synchronously write all buffered events; safe from any thread."""
        with self._lock:
            self._cancel_timer_locked()
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._pending or self.path is None:
            return
        lines = "".join(
            json.dumps(e.to_dict(), ensure_ascii=False) + "\n" for e in self._pending
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                if self._needs_separator:
                    f.write("\n")
                    self._needs_separator = False
                f.write(lines)
        except OSError:
            # Keep pending events for a later retry. A failed trace write must
            # never kill the loop; snapshot completeness guards remain fallback.
            if self._flush_timer is None and not self._closed:
                self._schedule_flush_locked()
            return
        del self._pending[:]
        self._pending_since = 0.0

    def _cancel_timer_locked(self) -> None:
        timer = self._flush_timer
        self._flush_timer = None
        self._timer_generation += 1
        if timer is not None and timer is not threading.current_thread():
            timer.cancel()

    def close(self) -> None:
        """Cancel background work and synchronously drain pending events."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_timer_locked()
            self._flush_locked()

    def __enter__(self) -> "SessionLog":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    @classmethod
    def load(cls, path: Path) -> "SessionLog":
        """Load a log from disk. A torn (undecodable) final line is dropped;
        a torn line anywhere else is corruption and raises."""
        log = cls(path=None)  # don't re-append to the file while loading
        path = Path(path)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            log.path = path
            return log

        # Keep byte offsets: open() must physically remove a torn tail before
        # continuation. Merely ignoring it here makes every later append part of
        # the same malformed line and permanently poisons the log.
        lines = raw.splitlines(keepends=True)
        offset = 0
        for i, raw_line in enumerate(lines):
            line_start = offset
            offset += len(raw_line)
            try:
                line = raw_line.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                if i == len(lines) - 1:
                    log._truncate_at = line_start
                    break
                raise ValueError(f"corrupt session log at line {i + 1}: {path}")
            if not line.strip():
                continue
            try:
                event = SessionEvent.from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError):
                if i == len(lines) - 1:
                    log._truncate_at = line_start
                    break
                raise ValueError(f"corrupt session log at line {i + 1}: {path}")
            if event.seq != len(log._events):
                raise ValueError(
                    f"seq gap in session log at line {i + 1}: "
                    f"expected {len(log._events)}, got {event.seq}"
                )
            log._events.append(event)
        log.path = path
        log._needs_separator = bool(raw and log._truncate_at is None and not raw.endswith((b"\n", b"\r")))
        return log

    @classmethod
    def open(cls, path: Path) -> "SessionLog":
        """Load (or create) the log at path, repairing a crashed tail in place.

        Continuing a session appends to the SAME file, so seq and turn
        numbering must resume from the recorded events — a bare
        SessionLog(path) restarts seq at 0 and corrupts the file. If the log
        ends inside an open turn (the crash signature), the synthetic closers
        are appended to disk here (repair-on-open) so the log satisfies
        invariants before any new turn starts.

        A corrupt log cannot be continued and must never kill the loop:
        it is quarantined (renamed *.corrupt — evidence, never truncation)
        and the session starts a fresh log; the snapshot fallback in
        load_session covers the messages.
        """
        path = Path(path)
        try:
            log = cls.load(path)
        except ValueError as e:
            quarantine = path.with_name(path.name + ".corrupt")
            path.replace(quarantine)
            logger.warning(
                "Corrupt session log quarantined to %s (%s); starting fresh",
                quarantine, e,
            )
            log = cls(path)
        if log._truncate_at is not None:
            # Repair the physical file before any synthetic closer/new event is
            # appended. The offset always starts the malformed final record.
            with open(path, "r+b") as f:
                f.truncate(log._truncate_at)
            log._truncate_at = None
            # line_start follows the prior record's newline (or is zero).
            log._needs_separator = False
        for event in interrupted_turn_closers(log._events):
            log._append_event(event)
        return log

    @property
    def last_turn(self) -> int:
        """Highest turn number recorded (0 for a fresh log). A resuming
        RuntimeLoop continues numbering from here."""
        for event in reversed(self._events):
            if event.type in ("turn/start", "turn/end"):
                turn = event.data.get("turn")
                if isinstance(turn, int):
                    return turn
        return 0


def derive_state(events: List[SessionEvent]) -> Dict[str, Any]:
    """Project the log into the MMU's live state: {"messages", "summary"}.

    Phase 2 equivalence contract: for ANY session — compacted or not —
    derive_state(log.events) must equal
    ([m.to_dict() for m in mmu.messages_view()], mmu._global_summary).

    compaction/applied is a surface replace: kept_indices are the survivors'
    positions in the pre-compaction surface (non-contiguous under smart-drop),
    and the event's summary becomes the new merged summary. Phase 0 logs
    predate kept_indices; for those the best effort is keeping the last
    `kept` messages (exact for summarize mode, approximate for smart-drop).
    """
    surface: List[Dict[str, Any]] = []
    summary = ""
    plan = ""
    for event in events:
        if event.type == "seed/applied":
            # Fork/resume seed: the initial surface of a forked session
            # (see SessionStorage.fork_session). Always the first event.
            surface = list(event.data.get("messages", []))
            summary = event.data.get("summary", "")
            plan = event.data.get("plan", "")
        elif event.type in ("user/message", "assistant/message", "tool/result"):
            surface.append(event.data["message"])
        elif event.type == "compaction/applied":
            kept_indices = event.data.get("kept_indices")
            if kept_indices is None:
                kept = event.data.get("kept", len(surface))
                surface = surface[len(surface) - kept:] if kept else []
            else:
                surface = [surface[i] for i in kept_indices]
            summary = event.data.get("summary", summary)
        elif event.type == "plan/updated":
            # Agent-authored plan anchor (update_plan tool) — anchor state,
            # last write wins.
            plan = event.data.get("plan", plan)
    return {"messages": surface, "summary": summary, "plan": plan}


def derive_messages(events: List[SessionEvent]) -> List[Dict[str, Any]]:
    """Project the post-compaction message surface (see derive_state)."""
    return derive_state(events)["messages"]


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
        elif t == "seed/applied":
            if i != 0:
                violations.append(
                    f"seq {event.seq}: seed/applied only allowed as the first event"
                )
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
                    "meta": {"synthetic": True, "code": code},
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
