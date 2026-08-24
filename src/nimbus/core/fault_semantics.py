"""Fault semantics — the single normative table for what survives each fault.

Every fault class that can cut a turn short answers the same three questions:
(1) what already-produced content survives into history, (2) how open
structures (tool-call batches, turn/step brackets) are closed, and (3) whether
the resulting history can be resumed and how. Before this module the answers
lived in five places with five vocabularies (vcpu error paths, loop interrupt
branch, session_log crash closers, storage fork cuts, budget recovery); this
table is now the spec, and those sites are its implementations.

Design lineage: the keep/close/resume split follows hax's abort-repair
three-way (agent_loop.c) — notably its two rules we adopt verbatim:
- Partial output the user has already seen must land in history WITH a marker,
  stamped via metadata rather than recognized by content (a model may
  legitimately end its answer with the marker string).
- Truncated reasoning is never kept; a retry re-issues the request rather than
  replaying half-formed state to the model.

The table is data, the enforcement lives at the call sites listed per row.
Changing a row here without touching its sites is a spec/implementation drift
— tests/core/test_fault_semantics.py pins the correspondence.
"""

from dataclasses import dataclass
from typing import Dict, Literal, Optional

# Appended to preserved partial output. Display layers strip it by the meta
# stamp (meta.origin == ORIGIN_INTERRUPTED), never by matching this string.
INTERRUPT_MARKER = "[interrupted]"

# Message-meta origin stamp for content the framework marked, not the model.
ORIGIN_INTERRUPTED = "interrupted"

FaultClass = Literal[
    "provider_error",   # LLM stream failed terminally (retries exhausted/timeout)
    "provider_retry",   # LLM stream failed, silent retry upcoming
    "steering_preempt", # In-flight stream cancelled to inject steering
    "user_interrupt",   # Explicit interrupt/abort from the user
    "pause",            # Soft stop at the next step seam (in-flight work completes)
    "budget_exceeded",  # Iteration/token budget hit (soft, recoverable)
    "crash",            # Process died; log ends inside an open turn
    "fork_cut",         # fork_session cut a parent log mid-turn
]

Resumability = Literal[
    "verbatim",      # History is balanced; a later run continues it as-is.
    "continuation",  # Balanced but mid-story; resuming must speak for the user
                     # (synthetic continuation prompt) or wait for new input.
    "retry",         # Nothing (or text only) kept; re-issuing the same request
                     # is the correct resume.
]


@dataclass(frozen=True)
class FaultPolicy:
    """One row of the semantics table."""
    # Streamed-but-unabsorbed assistant text: "marked" = append to history
    # with INTERRUPT_MARKER + ORIGIN_INTERRUPTED stamp; "none" = drop (the
    # retry/re-issue will regenerate it).
    keep_streamed_text: Literal["marked", "none"]
    # How unanswered tool calls of the latest batch are closed. "cancelled" =
    # live CANCELLED results (vcpu ACT loop); "graded" = crash-grade closers
    # (TOOL_OUTCOME_UNKNOWN for the possibly-in-flight first call,
    # TOOL_NOT_STARTED after — session_log.grade_unanswered_calls); "none" =
    # no batch can be open at this seam.
    close_open_calls: Literal["cancelled", "graded", "none"]
    # Reason recorded on turn/end. 'interrupted' is RESERVED for synthetic
    # closers (crash/fork) — a live loop must never emit it, so log readers
    # can tell repaired history from cleanly closed history.
    turn_end_reason: str
    resumability: Resumability
    # Where this row is enforced (kept human-readable; tests pin the anchors).
    enforced_by: str


SEMANTICS: Dict[str, FaultPolicy] = {
    # Terminal stream failure. Text the user watched arrive is preserved and
    # marked (display/history convergence); no tool call from the failed
    # stream exists yet, so no batch to close. Follows hax: provider-error
    # repair keeps only assistant text.
    "provider_error": FaultPolicy(
        keep_streamed_text="marked",
        close_open_calls="none",
        turn_end_reason="error",
        resumability="retry",
        enforced_by="vcpu._error_step (preserve) + loop._turn_end('error')",
    ),
    # Silent retry: keep nothing, pollute nothing — the retry re-streams the
    # whole response (hax EV_RETRY: reset assembly, account usage elsewhere).
    "provider_retry": FaultPolicy(
        keep_streamed_text="none",
        close_open_calls="none",
        turn_end_reason="",  # turn stays open
        resumability="retry",
        enforced_by="vcpu retryable-Fault branch (returns non-final step)",
    ),
    # Steering cancelled the in-flight call deliberately; the request is
    # re-issued right after injection, so the half-stream leaves no trace.
    "steering_preempt": FaultPolicy(
        keep_streamed_text="none",
        close_open_calls="none",
        turn_end_reason="",  # turn stays open
        resumability="retry",
        enforced_by="vcpu wakeup race (empty non-final step)",
    ),
    # User pressed stop. Completed work stays; the remaining batch is closed
    # with live CANCELLED results so every call is answered; partial output
    # summary is delivered as the final result. History is balanced but ends
    # mid-story: resuming needs the user (or a synthetic continuation).
    "user_interrupt": FaultPolicy(
        keep_streamed_text="marked",
        close_open_calls="cancelled",
        turn_end_reason="aborted",
        resumability="continuation",
        enforced_by="loop interrupt branch + vcpu ACT skip results",
    ),
    # Soft stop sampled ONLY at step seams (hax PAUSE): the in-flight step —
    # LLM response and its whole tool batch — completes first, so history is
    # balanced with every call paired, no marker owed, and a resume continues
    # verbatim. Abort wins over pause when both are requested.
    "pause": FaultPolicy(
        keep_streamed_text="none",   # nothing is in flight at a seam
        close_open_calls="none",     # the batch completed; nothing to close
        turn_end_reason="paused",
        resumability="verbatim",
        enforced_by="loop seam check (top of inner step loop)",
    ),
    # Soft budget stop: nothing is lost, the loop compacts and continues, or
    # ends the turn as 'max-iterations' when recovery is off the table.
    "budget_exceeded": FaultPolicy(
        keep_streamed_text="none",
        close_open_calls="none",
        turn_end_reason="max-iterations",
        resumability="verbatim",
        enforced_by="vcpu iteration check + loop compaction recovery",
    ),
    # Crash: repair-on-open appends graded closers + step/end + turn/end with
    # the reserved 'interrupted' reason. Deterministic (reuses last real
    # timestamp), append-only (never truncates).
    "crash": FaultPolicy(
        keep_streamed_text="none",  # nothing buffered survives a dead process
        close_open_calls="graded",
        turn_end_reason="interrupted",
        resumability="continuation",
        enforced_by="session_log.interrupted_turn_closers (repair-on-open) "
                    "+ storage.load_session surface grading",
    ),
    # Fork mid-turn: the child's seeded log gets the same graded closers so
    # the cut yields a balanced surface the child can build on.
    "fork_cut": FaultPolicy(
        keep_streamed_text="none",
        close_open_calls="graded",
        turn_end_reason="interrupted",
        resumability="continuation",
        enforced_by="storage.fork_session (synthetic closers before seed)",
    ),
}


def marked_partial_text(partial_text: str) -> Optional[str]:
    """Return the history-ready form of preserved partial output, or None if
    there is nothing worth keeping (empty/whitespace stream)."""
    if not partial_text or not partial_text.strip():
        return None
    return f"{partial_text}\n{INTERRUPT_MARKER}"
