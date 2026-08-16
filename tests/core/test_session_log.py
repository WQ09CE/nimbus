"""Tests for the Phase 0 session event log — dual-write trace beside the
authoritative JSON snapshot: seq contract, turn/step brackets with reasons on
every exit path, MMU message events, and snapshot equivalence."""

import asyncio
import json
import threading
import time
from typing import List

import pytest

from nimbus.core.loop import RuntimeLoop
from nimbus.core.mmu import MMU, MMUConfig
from nimbus.core.session_log import (
    SessionEvent,
    SessionLog,
    check_invariants,
    derive_messages,
    derive_state,
    interrupted_turn_closers,
)
from nimbus.core.storage import SessionStorage

from .test_loop import MockVCPU, make_step


def _types(log: SessionLog) -> List[str]:
    return [e.type for e in log.events]


def _turn_end_reasons(log: SessionLog) -> List[str]:
    return [e.data["reason"]["kind"] for e in log.events if e.type == "turn/end"]


# =============================================================================
# SessionLog primitives
# =============================================================================


class TestSessionLog:
    def test_seq_is_contiguous(self):
        log = SessionLog()
        for i in range(5):
            e = log.append("x", {"i": i})
            assert e.seq == i
        assert [e.seq for e in log.events] == [0, 1, 2, 3, 4]

    def test_jsonl_roundtrip(self, tmp_path):
        path = tmp_path / "s.jsonl"
        log = SessionLog(path)
        log.append("turn/start", {"turn": 1})
        log.append("user/message", {"message": {"role": "user", "content": "中文"}})
        log.flush()  # user/message is non-causal — buffered until flushed
        loaded = SessionLog.load(path)
        assert [e.to_dict() for e in loaded.events] == [e.to_dict() for e in log.events]

    def test_torn_tail_is_dropped(self, tmp_path):
        path = tmp_path / "s.jsonl"
        log = SessionLog(path)
        log.append("turn/start", {"turn": 1})
        log.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"seq": 2, "type": "user/mes')  # crash mid-write
        loaded = SessionLog.load(path)
        assert len(loaded.events) == 2

    def test_missing_file_loads_empty(self, tmp_path):
        loaded = SessionLog.load(tmp_path / "absent.jsonl")
        assert loaded.events == []


# =============================================================================
# MMU event sink
# =============================================================================


class TestMMUEventSink:
    def test_add_methods_emit_events(self):
        log = SessionLog()
        mmu = MMU()
        mmu.event_sink = log.append
        mmu.add_user_message("hi")
        mmu.add_assistant_with_tool_calls("thinking", [{"id": "c1", "function": {"name": "Read", "arguments": "{}"}}])
        mmu.add_tool_result("c1", "Read", "contents", ui_detail={"x": 1})
        mmu.add_assistant_message("done")
        assert _types(log) == [
            "user/message", "assistant/message", "tool/result", "assistant/message",
        ]

    def test_equivalence_derived_vs_mmu(self):
        """The core Phase 0 invariant: projecting the log reproduces _messages."""
        log = SessionLog()
        mmu = MMU()
        mmu.event_sink = log.append
        mmu.add_user_message("fix the bug")
        mmu.add_assistant_with_tool_calls(None, [{"id": "c1", "function": {"name": "Bash", "arguments": '{"command": "ls"}'}}])
        mmu.add_tool_result("c1", "Bash", "a.py b.py")
        mmu.add_system_message("compaction notice")
        mmu.add_assistant_message("fixed")
        assert derive_messages(log.events) == [m.to_dict() for m in mmu._messages]

    def test_compaction_emits_event(self):
        import asyncio
        log = SessionLog()
        mmu = MMU(MMUConfig(max_context_tokens=2000))
        mmu.event_sink = log.append
        for i in range(30):
            mmu.add_user_message(f"message {i} " + "x" * 200)
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            mmu.archive_and_reset()
        )
        assert "compaction/applied" in _types(log)

    def test_sink_failure_never_raises(self):
        mmu = MMU()
        def broken(t, d):
            raise RuntimeError("sink down")
        mmu.event_sink = broken
        mmu.add_user_message("still works")  # must not raise
        assert mmu.message_count == 1


# =============================================================================
# RuntimeLoop brackets
# =============================================================================


class TestLoopBrackets:
    async def test_completed_turn_brackets(self, tmp_path):
        mmu = MMU()
        vcpu = MockVCPU([make_step(is_final=True, output="done")])
        loop = RuntimeLoop(vcpu, mmu, storage=SessionStorage(str(tmp_path)))
        await loop.run()
        types = _types(loop.session_log)
        assert types[0] == "turn/start"
        assert types[-1] == "turn/end"
        assert types.count("step/start") == types.count("step/end") == 1
        assert _turn_end_reasons(loop.session_log) == ["completed"]

    async def test_multi_step_brackets_balanced(self, tmp_path):
        mmu = MMU()
        vcpu = MockVCPU([make_step(), make_step(), make_step(is_final=True, output="done")])
        loop = RuntimeLoop(vcpu, mmu, storage=SessionStorage(str(tmp_path)))
        await loop.run()
        types = _types(loop.session_log)
        assert types.count("turn/start") == types.count("turn/end") == 1
        assert types.count("step/start") == types.count("step/end") == 3

    async def test_vcpu_crash_closes_with_error(self, tmp_path):
        class ExplodingVCPU(MockVCPU):
            async def step(self):
                raise RuntimeError("boom")
        mmu = MMU()
        loop = RuntimeLoop(ExplodingVCPU([]), mmu, storage=SessionStorage(str(tmp_path)))
        await loop.run()
        types = _types(loop.session_log)
        assert types.count("step/start") == types.count("step/end") == 1
        assert _turn_end_reasons(loop.session_log) == ["error"]

    async def test_followup_opens_new_turn(self, tmp_path):
        mmu = MMU()
        vcpu = MockVCPU([
            make_step(is_final=True, output="first"),
            make_step(is_final=True, output="second"),
        ])
        loop = RuntimeLoop(vcpu, mmu, storage=SessionStorage(str(tmp_path)))
        loop.followup_queue.follow_up("and then do this")
        await loop.run()
        types = _types(loop.session_log)
        assert types.count("turn/start") == types.count("turn/end") == 2
        assert _turn_end_reasons(loop.session_log) == ["completed", "completed"]
        turns = [e.data["turn"] for e in loop.session_log.events if e.type == "turn/start"]
        assert turns == [1, 2]

    async def test_interruption_closes_as_aborted(self, tmp_path):
        mmu = MMU()
        vcpu = MockVCPU([make_step(is_final=True, output="never reached")])
        loop = RuntimeLoop(vcpu, mmu, storage=SessionStorage(str(tmp_path)))
        loop.request_interruption()
        await loop.run()
        assert _turn_end_reasons(loop.session_log) == ["aborted"]

    async def test_cancellation_closes_step_and_turn(self, tmp_path):
        """A timeout/cancel escapes the step bracket's `except Exception`
        (CancelledError is a BaseException); the backstop must close the
        open STEP before the turn or the log violates its own invariant."""
        import asyncio

        class HangingVCPU(MockVCPU):
            async def step(self):
                await asyncio.sleep(60)

        mmu = MMU()
        loop = RuntimeLoop(HangingVCPU([]), mmu, storage=SessionStorage(str(tmp_path)))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(loop.run(), timeout=0.1)
        types = [e.type for e in loop.session_log.events]
        assert types.count("step/start") == types.count("step/end") == 1
        assert types[-1] == "turn/end"
        assert check_invariants(loop.session_log.events) == []
        assert _turn_end_reasons(loop.session_log) == ["aborted"]

    async def test_real_run_passes_invariants(self, tmp_path):
        mmu = MMU()
        vcpu = MockVCPU([make_step(), make_step(is_final=True, output="done")])
        loop = RuntimeLoop(vcpu, mmu, storage=SessionStorage(str(tmp_path)))
        loop.followup_queue.follow_up("more")
        await loop.run()
        assert check_invariants(loop.session_log.events) == []

    async def test_log_written_to_disk_and_equivalent(self, tmp_path):
        """End-to-end: run, reload the jsonl from disk, project messages,
        compare with the MMU (Phase 0 equivalence, no compaction)."""
        mmu = MMU()
        vcpu = MockVCPU([make_step(is_final=True, output="done")])
        storage = SessionStorage(str(tmp_path))
        loop = RuntimeLoop(vcpu, mmu, storage=storage, session_id="sess_eq")
        mmu.add_user_message("the goal")
        await loop.run()
        loaded = SessionLog.load(tmp_path / "sess_eq.jsonl")
        assert derive_messages(loaded.events) == [m.to_dict() for m in mmu._messages]
        # every event on disk, in order, with contiguous seq
        assert [e.seq for e in loaded.events] == list(range(len(loaded.events)))


# =============================================================================
# Invariants & crash repair (Phase 1)
# =============================================================================


def _crashed_log(answered: int = 1) -> SessionLog:
    """A log that dies mid-step: 3 tool calls requested, `answered` results in."""
    log = SessionLog()
    log.append("turn/start", {"turn": 1})
    log.append("step/start", {"turn": 1, "step": 1})
    calls = [
        {"id": f"c{i}", "function": {"name": "Bash", "arguments": "{}"}}
        for i in range(1, 4)
    ]
    log.append("assistant/message", {"message": {
        "role": "assistant", "content": None, "tool_calls": calls,
    }})
    for i in range(1, answered + 1):
        log.append("tool/result", {"message": {
            "role": "tool", "content": "ok", "name": "Bash", "tool_call_id": f"c{i}",
        }})
    return log  # crash: no step/end, no turn/end


class TestInvariants:
    def test_open_tail_flagged_unless_allowed(self):
        log = _crashed_log()
        violations = check_invariants(log.events)
        assert any("open" in v for v in violations)
        assert check_invariants(log.events, allow_open_tail=True) == []

    def test_detects_bad_structure(self):
        log = SessionLog()
        log.append("step/start", {"turn": 1, "step": 1})       # step outside turn
        log.append("turn/start", {"turn": 5})                  # non-monotonic turn
        log.append("turn/end", {"turn": 5, "reason": {"kind": "whatever"}})  # bad reason
        violations = check_invariants(log.events)
        assert len(violations) >= 3

    def test_tool_result_must_match_a_request(self):
        log = SessionLog()
        log.append("turn/start", {"turn": 1})
        log.append("tool/result", {"message": {"role": "tool", "tool_call_id": "ghost"}})
        assert any("ghost" in v for v in check_invariants(log.events, allow_open_tail=True))


class TestCrashRepair:
    def test_balanced_log_needs_no_repair(self):
        log = SessionLog()
        log.append("turn/start", {"turn": 1})
        log.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
        assert interrupted_turn_closers(log.events) == []

    def test_closers_grade_by_serial_order(self):
        log = _crashed_log(answered=1)  # c1 answered; c2 in flight; c3 not started
        closers = interrupted_turn_closers(log.events)
        results = [e for e in closers if e.type == "tool/result"]
        assert [e.data["message"]["tool_call_id"] for e in results] == ["c2", "c3"]
        assert results[0].data["code"] == "TOOL_OUTCOME_UNKNOWN"
        assert results[1].data["code"] == "TOOL_NOT_STARTED"
        assert closers[-2].type == "step/end"
        assert closers[-1].type == "turn/end"
        assert closers[-1].data["reason"]["kind"] == "interrupted"

    def test_repaired_log_is_balanced_and_deterministic(self):
        log = _crashed_log(answered=0)
        closers = interrupted_turn_closers(log.events)
        # deterministic: synthetic events reuse the last real timestamp
        assert all(e.time == log.events[-1].time for e in closers)
        # seq continues the run
        assert [e.seq for e in closers] == list(range(len(log.events), len(log.events) + len(closers)))
        repaired = log.events + closers
        assert check_invariants(repaired) == []
        # idempotent: repairing a repaired log is a no-op
        assert interrupted_turn_closers(repaired) == []


# =============================================================================
# Phase 2: derive_state — surface-replace equivalence for compacted sessions
# =============================================================================


def _mmu_with_log(config: MMUConfig) -> tuple:
    log = SessionLog()
    mmu = MMU(config)
    mmu.event_sink = log.append
    return mmu, log


def _compaction_mode(log: SessionLog) -> str:
    return next(e.data["mode"] for e in log.events if e.type == "compaction/applied")


class TestDeriveState:
    async def test_equivalence_after_summarize_compaction(self):
        mmu, log = _mmu_with_log(MMUConfig(max_context_tokens=2000))
        for i in range(30):
            mmu.add_user_message(f"message {i} " + "x" * 200)
        await mmu.archive_and_reset()
        assert _compaction_mode(log) == "summarize"
        state = derive_state(log.events)
        assert state["messages"] == [m.to_dict() for m in mmu._messages]
        assert state["summary"] == mmu._global_summary

    async def test_equivalence_after_smart_drop_compaction(self):
        mmu, log = _mmu_with_log(
            MMUConfig(max_context_tokens=400, keep_recent_tokens=50)
        )
        for i in range(8):
            mmu.add_user_message(f"m{i} " + "y" * 100)
        mmu.add_user_message("big1 " + "z" * 1200)
        mmu.add_user_message("big2 " + "z" * 1200)
        await mmu.archive_and_reset()
        assert _compaction_mode(log) == "smart-drop"
        state = derive_state(log.events)
        assert state["messages"] == [m.to_dict() for m in mmu._messages]
        assert state["summary"] == mmu._global_summary

    async def test_equivalence_survives_repeated_compaction(self):
        """Two rounds: surface replace must compose (post-compaction surface
        is itself the base of the next kept_indices)."""
        mmu, log = _mmu_with_log(MMUConfig(max_context_tokens=2000))
        for i in range(30):
            mmu.add_user_message(f"round1 {i} " + "x" * 200)
        await mmu.archive_and_reset()
        for i in range(30):
            mmu.add_user_message(f"round2 {i} " + "x" * 200)
        await mmu.archive_and_reset()
        assert sum(1 for e in log.events if e.type == "compaction/applied") == 2
        state = derive_state(log.events)
        assert state["messages"] == [m.to_dict() for m in mmu._messages]
        assert state["summary"] == mmu._global_summary

    def test_legacy_event_without_kept_indices_keeps_tail(self):
        log = SessionLog()
        for i in range(4):
            log.append("user/message", {"message": {"role": "user", "content": f"m{i}"}})
        log.append("compaction/applied", {"mode": "summarize", "kept": 2, "summary": "s"})
        state = derive_state(log.events)
        assert [m["content"] for m in state["messages"]] == ["m2", "m3"]
        assert state["summary"] == "s"


# =============================================================================
# Phase 2: SessionLog.open — continuation + repair-on-open
# =============================================================================


class TestSessionLogOpen:
    def test_open_missing_file_starts_fresh(self, tmp_path):
        log = SessionLog.open(tmp_path / "new.jsonl")
        assert log.events == []
        assert log.last_turn == 0
        log.append("turn/start", {"turn": 1})
        log.flush()
        assert (tmp_path / "new.jsonl").exists()

    def test_open_continues_seq_and_turn(self, tmp_path):
        path = tmp_path / "s.jsonl"
        first = SessionLog(path)
        first.append("turn/start", {"turn": 1})
        first.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})

        reopened = SessionLog.open(path)
        assert reopened.last_turn == 1
        e = reopened.append("turn/start", {"turn": 2})
        assert e.seq == 2  # continues, does NOT restart at 0
        reopened.append("turn/end", {"turn": 2, "reason": {"kind": "completed"}})
        assert check_invariants(SessionLog.load(path).events) == []

    def test_open_repairs_crashed_tail_on_disk(self, tmp_path):
        path = tmp_path / "s.jsonl"
        crashed = SessionLog(path)
        for e in _crashed_log(answered=1).events:
            crashed.append(e.type, e.data)

        reopened = SessionLog.open(path)
        assert check_invariants(reopened.events) == []
        reasons = [e.data["reason"]["kind"] for e in reopened.events if e.type == "turn/end"]
        assert reasons == ["interrupted"]
        # repair is durable, and the synthetic marker lives in the message meta
        reloaded = SessionLog.load(path)
        assert check_invariants(reloaded.events) == []
        synthetic = [
            e.data["message"] for e in reloaded.events
            if e.type == "tool/result" and e.data.get("synthetic")
        ]
        assert {m["meta"]["code"] for m in synthetic} == {
            "TOOL_OUTCOME_UNKNOWN", "TOOL_NOT_STARTED",
        }

    def test_open_truncates_torn_tail_before_continuation(self, tmp_path):
        path = tmp_path / "torn.jsonl"
        first = SessionLog(path)
        first.append("turn/start", {"turn": 1})
        first.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
        first.close()
        with path.open("ab") as f:
            f.write(b'{"seq":2,"type":"turn/start"')

        reopened = SessionLog.open(path)
        reopened.append("turn/start", {"turn": 2})
        reopened.append("turn/end", {"turn": 2, "reason": {"kind": "completed"}})
        reopened.close()

        loaded = SessionLog.load(path)
        assert [e.seq for e in loaded.events] == [0, 1, 2, 3]
        assert check_invariants(loaded.events) == []

    def test_open_appends_after_valid_final_line_without_newline(self, tmp_path):
        path = tmp_path / "no-newline.jsonl"
        event = SessionEvent(0, "turn/start", 1.0, {"turn": 1})
        path.write_text(json.dumps(event.to_dict()), encoding="utf-8")
        reopened = SessionLog.open(path)
        # open() first repairs the crashed open turn, then continuation appends
        # on a separate line despite the original missing newline.
        reopened.append("turn/start", {"turn": 2})
        reopened.append("turn/end", {"turn": 2, "reason": {"kind": "completed"}})
        reopened.close()
        assert check_invariants(SessionLog.load(path).events) == []

    def test_open_is_idempotent(self, tmp_path):
        path = tmp_path / "s.jsonl"
        crashed = SessionLog(path)
        for e in _crashed_log(answered=0).events:
            crashed.append(e.type, e.data)
        n = len(SessionLog.open(path).events)
        assert len(SessionLog.open(path).events) == n


class TestLoopContinuation:
    async def test_rebuilt_loop_continues_turn_numbering(self, tmp_path):
        """Regression: a rebuilt RuntimeLoop (same session_id) used to restart
        seq at 0 and corrupt the shared jsonl."""
        storage = SessionStorage(str(tmp_path))
        loop1 = RuntimeLoop(
            MockVCPU([make_step(is_final=True, output="a")]), MMU(),
            storage=storage, session_id="sess_x",
        )
        await loop1.run()

        loop2 = RuntimeLoop(
            MockVCPU([make_step(is_final=True, output="b")]), MMU(),
            storage=storage, session_id="sess_x",
        )
        await loop2.run()

        merged = SessionLog.load(tmp_path / "sess_x.jsonl")
        assert check_invariants(merged.events) == []
        turns = [e.data["turn"] for e in merged.events if e.type == "turn/start"]
        assert turns == [1, 2]

    def test_open_quarantines_corrupt_log(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_text(
            '{"seq": 0, "type": "turn/start", "time": 1, "data": {"turn": 1}}\n'
            '{"seq": 0, "type": "turn/start", "time": 2, "data": {"turn": 1}}\n'
        )
        log = SessionLog.open(path)
        assert log.events == []  # fresh start
        assert (tmp_path / "s.jsonl.corrupt").exists()  # evidence kept
        log.append("turn/start", {"turn": 1})
        log.flush()
        assert len(SessionLog.load(path).events) == 1


# =============================================================================
# Phase 3: bounded write-behind — flush barriers at causal points
# =============================================================================


class TestWriteBehind:
    def test_non_causal_events_are_buffered(self, tmp_path):
        path = tmp_path / "s.jsonl"
        log = SessionLog(path)
        log.append("turn/start", {"turn": 1})
        log.append("user/message", {"message": {"role": "user", "content": "hi"}})
        on_disk = path.read_text() if path.exists() else ""
        assert on_disk == ""  # nothing durable yet
        log.flush()
        assert len(SessionLog.load(path).events) == 2

    def test_causal_events_flush_immediately(self, tmp_path):
        path = tmp_path / "s.jsonl"
        log = SessionLog(path)
        log.append("turn/start", {"turn": 1})
        log.append("step/start", {"turn": 1, "step": 1})
        # The decision record (assistant + tool_calls) must be durable BEFORE
        # its side effects run — and it carries the whole buffer with it.
        log.append("assistant/message", {"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "function": {"name": "Bash", "arguments": "{}"}}],
        }})
        assert len(SessionLog.load(path).events) == 3

    def test_plain_assistant_text_is_not_causal(self, tmp_path):
        path = tmp_path / "s.jsonl"
        log = SessionLog(path)
        log.append("assistant/message", {"message": {"role": "assistant", "content": "just text"}})
        assert not path.exists() or path.read_text() == ""

    def test_idle_event_flushes_within_bounded_window(self, tmp_path):
        path = tmp_path / "s.jsonl"
        log = SessionLog(path)
        started = time.monotonic()
        log.append("user/message", {"message": {"role": "user", "content": "hi"}})
        deadline = started + SessionLog.FLUSH_WINDOW_SEC + 0.15
        while time.monotonic() < deadline and (
            not path.exists() or path.stat().st_size == 0
        ):
            time.sleep(0.005)
        assert path.exists() and path.stat().st_size > 0
        assert time.monotonic() - started <= SessionLog.FLUSH_WINDOW_SEC + 0.15
        assert len(SessionLog.load(path).events) == 1
        log.close()

    @pytest.mark.asyncio
    async def test_idle_flush_inside_running_asyncio_loop(self, tmp_path):
        path = tmp_path / "async.jsonl"
        log = SessionLog(path)
        log.append("user/message", {"message": {"role": "user", "content": "hi"}})
        await asyncio.sleep(SessionLog.FLUSH_WINDOW_SEC + 0.08)
        assert len(SessionLog.load(path).events) == 1
        log.close()

    def test_concurrent_append_and_close_are_serialized(self, tmp_path):
        path = tmp_path / "threaded.jsonl"
        log = SessionLog(path)
        threads = [
            threading.Thread(target=lambda n=i: [log.append("x", {"n": n}) for _ in range(20)])
            for i in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        log.close()
        loaded = SessionLog.load(path)
        assert len(loaded.events) == 80
        assert [e.seq for e in loaded.events] == list(range(80))
        with pytest.raises(RuntimeError):
            log.append("x")

    def test_close_cancels_timer_and_flushes(self, tmp_path):
        path = tmp_path / "closed.jsonl"
        log = SessionLog(path)
        log.append("user/message", {"message": {"role": "user", "content": "hi"}})
        timer = log._flush_timer
        log.close()
        assert timer is not None and not timer.is_alive()
        assert len(SessionLog.load(path).events) == 1

    def test_turn_end_flushes(self, tmp_path):
        path = tmp_path / "s.jsonl"
        log = SessionLog(path)
        log.append("turn/start", {"turn": 1})
        log.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
        assert len(SessionLog.load(path).events) == 2


# =============================================================================
# Phase 3: seed/applied — fork/resume/replay as one primitive
# =============================================================================


class TestSeed:
    def test_derive_state_starts_from_seed(self):
        log = SessionLog()
        log.append("seed/applied", {
            "messages": [{"role": "user", "content": "seeded"}],
            "summary": "prior work",
            "lineage": {"parent": "sess_p", "at_seq": None},
        })
        log.append("user/message", {"message": {"role": "user", "content": "new"}})
        state = derive_state(log.events)
        assert [m["content"] for m in state["messages"]] == ["seeded", "new"]
        assert state["summary"] == "prior work"

    def test_seed_must_be_first_event(self):
        log = SessionLog()
        log.append("turn/start", {"turn": 1})
        log.append("seed/applied", {"messages": [], "summary": ""})
        assert any("seed" in v for v in check_invariants(log.events, allow_open_tail=True))
