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
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nimbus.session_log")

# Every reason a live loop may assign to turn/end. 'interrupted' is absent
# by design: it is reserved for crash-repair synthesis (see module docstring).
# Keep lifecycle additions explicit here so the invariant checker remains a
# strict compatibility boundary for persisted logs.
LIVE_TURN_END_KINDS = (
    "completed",
    "aborted",
    "error",
    "max-iterations",
    "paused",
)


# Contract version of the log this code writes and understands. Bumped only when the
# event shapes change (not on every deploy — that is the pod generation). A reader may
# open a log only if its contract >= the log's: a rollout that rolls the contract
# BACK must drain first, or the older pods refuse the newer sessions (nimbus-lab R5.2).
SESSION_LOG_CONTRACT = 1


class ContractNewerError(Exception):
    """The log was written under a newer contract than this reader understands.
    Deliberately not a ValueError: open() must not quarantine such a log."""

    def __init__(self, log_contract: int, mine: int, where: str = ""):
        super().__init__(f"session log contract {log_contract} > reader contract {mine}" + (f" ({where})" if where else ""))
        self.log_contract = log_contract
        self.mine = mine


def log_contract(events: List["SessionEvent"]) -> int:
    """The highest contract any turn of this log was written under (1 when unstamped)."""
    c = 1
    for e in events:
        if e.type == "turn/start":
            try:
                c = max(c, int(e.data.get("contract", 1) or 1))
            except (TypeError, ValueError):
                pass
    return c


def _check_contract(events: List["SessionEvent"], where: str) -> None:
    c = log_contract(events)
    if c > SESSION_LOG_CONTRACT:
        raise ContractNewerError(c, SESSION_LOG_CONTRACT, where)


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
        if t in (
            "turn/end",
            "tool/result",
            "compaction/applied",
            "seed/applied",
            # Authorization must be durable before an approved side effect
            # starts; permission requests must survive refresh/restart while a
            # human decision is pending.
            "policy/requested",
            "policy/decision",
        ):
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
        _check_contract(log._events, str(path))
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
            msg = event.data["message"]
            if event.type == "tool/result" and event.data.get("replaces_seq") is not None:
                # Interrupted-turn resume: the re-executed call's real result replaces
                # the synthetic placeholder logged for the same call id — the surface
                # must hold one result per call, or the next resume (and a strict
                # provider) sees a duplicate tool_call_id.
                cid = msg.get("tool_call_id")
                for i in range(len(surface) - 1, -1, -1):
                    if surface[i].get("role") == "tool" and surface[i].get("tool_call_id") == cid:
                        surface[i] = msg
                        break
                else:
                    surface.append(msg)
            else:
                surface.append(msg)
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
            if call_id and call_id not in pending_call_ids and event.data.get("replaces_seq") is None:
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
TOOL_RESUMABLE = "TOOL_RESUMABLE"

# Process-wide resolver: tool name -> repeat class ("free" | "keyed" | "once").
# Installed by the ToolRegistry that owns the catalog; the default treats every
# tool as 'once' (never rerun automatically) — the conservative reading.
_repeat_resolver = None


def set_repeat_resolver(fn) -> None:
    global _repeat_resolver
    _repeat_resolver = fn


def repeat_of(tool_name: str) -> str:
    """Repeat class for crash grading. Uses the installed registry resolver;
    before any AgentOS exists in this process (e.g. a pod deciding about
    another pod's orphaned turn) it falls back to the builtin declarations,
    with the same lab override. Unknown → once."""
    if _repeat_resolver is not None:
        try:
            return _repeat_resolver(tool_name) or "once"
        except Exception:
            return "once"
    try:
        from .tools import builtin_tool_traits  # lazy: tools import this module
        from .tools.registry import repeat_override

        forced = repeat_override(tool_name)
        if forced:
            return forced
        traits = builtin_tool_traits().get(tool_name)
        return getattr(traits, "repeat", "once") or "once"
    except Exception:
        return "once"

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
_RESUMABLE_TEXT = (
    "[TOOL_RESUMABLE] The session crashed while this tool call may have been "
    "executing. The tool is safe to run again (read-only or idempotent); a "
    "resumed run re-executes it automatically, otherwise retry it if still needed."
)


def grade_unanswered_calls(tool_calls: List[Dict[str, Any]], answered_ids: set) -> List[tuple]:
    """Grade a serial batch's unanswered calls: (call, code, content) triples.

    First unanswered call in request order may have been in flight: its grade
    follows the tool's repeat class — 'once' → TOOL_OUTCOME_UNKNOWN (never rerun
    automatically), 'free'/'keyed' → TOOL_RESUMABLE (a resumed run re-executes
    it). Later calls had provably not started → TOOL_NOT_STARTED (safe to run,
    whatever their class).
    """
    graded = []
    first = True
    for tc in tool_calls:
        if tc.get("id") in answered_ids:
            continue
        if first:
            name = tc.get("function", {}).get("name", "")
            if repeat_of(name) == "once":
                code, text = TOOL_OUTCOME_UNKNOWN, _OUTCOME_UNKNOWN_TEXT
            else:
                code, text = TOOL_RESUMABLE, _RESUMABLE_TEXT
        else:
            code, text = TOOL_NOT_STARTED, _NOT_STARTED_TEXT
        graded.append((tc, code, text))
        first = False
    return graded


RERUNNABLE_CODES = (TOOL_RESUMABLE, TOOL_NOT_STARTED)


def resumable_calls(events: List["SessionEvent"]) -> List[Dict[str, Any]]:
    """After crash repair: the synthetic results of the last interrupted turn
    that a resumed run must re-execute — [{tool_call, code, seq}] in order.
    Empty when the last turn was not an interrupted one."""
    last_turn_start = None
    for i in range(len(events) - 1, -1, -1):
        if events[i].type == "turn/start":
            last_turn_start = i
            break
    if last_turn_start is None:
        return []
    tail = events[last_turn_start:]
    if not any(e.type == "turn/end" and e.data.get("reason", {}).get("kind") == "interrupted" for e in tail):
        return []
    calls_by_id: Dict[str, Dict[str, Any]] = {}
    for e in tail:
        if e.type == "assistant/message":
            for tc in e.data.get("message", {}).get("tool_calls") or []:
                calls_by_id[tc.get("id")] = tc
    out = []
    for e in tail:
        if e.type == "tool/result" and e.data.get("synthetic") and e.data.get("code") in RERUNNABLE_CODES:
            tc = calls_by_id.get(e.data.get("message", {}).get("tool_call_id"))
            if tc is not None:
                out.append({"tool_call": tc, "code": e.data.get("code"), "seq": e.seq})
    return out


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


# =============================================================================
# Valkey Stream store + ownership fence (nimbus-lab R2)
# =============================================================================


class OwnershipLostError(BaseException):
    """This process no longer owns the session's turn: a newer epoch exists.

    Deliberately a BaseException (like CancelledError): generic ``except
    Exception`` handlers in the loop must not swallow it, save a core dump or
    keep executing tools — a fenced writer has to stop, not recover.
    """


class StreamSessionLog(SessionLog):
    """SessionLog whose durable store is a Valkey/Redis Stream, one entry per event.

    The write point is a Lua script that compares the caller's ownership epoch
    with ``turn:{session}.epoch`` and appends all pending events atomically —
    a stale writer (zombie pod) is rejected on its first flush and never again
    reaches the stream. Reads are XRANGE; seq contiguity is checked like the
    file backend. The in-memory model, write-behind and causal barriers are
    inherited unchanged.
    """

    _XADD_FENCED = """
local cur = redis.call('HGET', KEYS[1], 'epoch')
if ARGV[1] ~= '' and cur and cur ~= ARGV[1] then
  redis.call('INCR', 'ledger:rejected_writes')
  return redis.error_reply('OWNERSHIP_LOST current=' .. cur .. ' mine=' .. ARGV[1])
end
for i = 2, #ARGV do
  redis.call('XADD', KEYS[2], '*', 'e', ARGV[i])
end
return #ARGV - 1
"""

    def __init__(self, client: Any, session_id: str, epoch: Optional[int] = None):
        super().__init__(path=None)
        self._r = client
        self.session_id = session_id
        self.epoch = epoch
        self.fenced = False
        self.rejected = 0
        self._loss_raised = False
        # non-None path = "durable store attached": the base class only buffers
        # pending events when a path is set. Never used as a filesystem path.
        self.path = Path(f"valkey://{self.stream_key}")

    @property
    def stream_key(self) -> str:
        return f"sess:{self.session_id}:log"

    @property
    def turn_key(self) -> str:
        return f"turn:{self.session_id}"

    def _append_event_locked(self, event: SessionEvent) -> None:
        if self.fenced:
            # First append after the fence raises so the loop stops; later
            # appends (backstop closers, audit trace) are dropped silently.
            if not self._loss_raised:
                self._loss_raised = True
                raise OwnershipLostError(f"session {self.session_id}: epoch {self.epoch} is stale")
            return
        super()._append_event_locked(event)

    def _flush_locked(self) -> None:
        if self.fenced:
            del self._pending[:]
            return
        if not self._pending:
            return
        payload = [json.dumps(e.to_dict(), ensure_ascii=False) for e in self._pending]
        try:
            self._r.eval(self._XADD_FENCED, 2, self.turn_key, self.stream_key,
                         "" if self.epoch is None else str(self.epoch), *payload)
        except Exception as e:
            if "OWNERSHIP_LOST" in str(e):
                self.fenced = True
                self.rejected += len(self._pending)
                del self._pending[:]
                logger.warning("session %s: %d event(s) rejected — %s", self.session_id, self.rejected, e)
                if not self._loss_raised:
                    self._loss_raised = True
                    raise OwnershipLostError(str(e)) from None
                return
            # store unreachable: keep pending for a later retry (same policy as OSError on files)
            if self._flush_timer is None and not self._closed:
                self._schedule_flush_locked()
            return
        del self._pending[:]
        self._pending_since = 0.0

    @classmethod
    def load(cls, client: Any, session_id: str, epoch: Optional[int] = None) -> "StreamSessionLog":  # type: ignore[override]
        log = cls(client, session_id, epoch)
        for _id, fields in client.xrange(log.stream_key):
            event = SessionEvent.from_dict(json.loads(fields["e"]))
            if event.seq != len(log._events):
                raise ValueError(
                    f"seq gap in session stream {log.stream_key} at {_id}: "
                    f"expected {len(log._events)}, got {event.seq}"
                )
            log._events.append(event)
        _check_contract(log._events, log.stream_key)
        return log

    @classmethod
    def open(cls, client: Any, session_id: str, epoch: Optional[int] = None) -> "StreamSessionLog":  # type: ignore[override]
        """Load + repair-on-open (same contract as SessionLog.open). A corrupt
        stream is renamed aside (evidence, never truncated) and a fresh one started."""
        try:
            log = cls.load(client, session_id, epoch)
        except ValueError as e:
            quarantine = f"sess:{session_id}:log.corrupt.{int(time.time())}"
            client.rename(f"sess:{session_id}:log", quarantine)
            logger.warning("Corrupt session stream quarantined to %s (%s); starting fresh", quarantine, e)
            log = cls(client, session_id, epoch)
        for event in interrupted_turn_closers(log._events):
            log._append_event(event)
        return log


_stream_client = None


def _client() -> Any:
    global _stream_client
    if _stream_client is None:
        import redis  # optional extra: nimbus[ledger]

        _stream_client = redis.Redis.from_url(
            os.environ.get("NIMBUS_LEDGER_URL", "redis://127.0.0.1:6379"), decode_responses=True
        )
    return _stream_client


def _use_stream() -> bool:
    return os.environ.get("NIMBUS_LOG_STORE", "file") == "valkey"


def open_session_log(base_dir: Path, session_id: str, epoch: Optional[int] = None) -> SessionLog:
    """Continue (or create) the session's log for writing; store chosen by NIMBUS_LOG_STORE."""
    if _use_stream():
        return StreamSessionLog.open(_client(), session_id, epoch)
    return SessionLog.open(Path(base_dir) / f"{session_id}.jsonl")


def load_session_log(base_dir: Path, session_id: str) -> SessionLog:
    """Read-only load (no repair written back)."""
    if _use_stream():
        return StreamSessionLog.load(_client(), session_id)
    return SessionLog.load(Path(base_dir) / f"{session_id}.jsonl")


def new_session_log(base_dir: Path, session_id: str) -> SessionLog:
    """A fresh log for a new session (fork seeding)."""
    if _use_stream():
        return StreamSessionLog(_client(), session_id)
    return SessionLog(Path(base_dir) / f"{session_id}.jsonl")
