"""Tests for the Phase 0 session event log — dual-write trace beside the
authoritative JSON snapshot: seq contract, turn/step brackets with reasons on
every exit path, MMU message events, and snapshot equivalence."""

from typing import List

from nimbus.core.loop import RuntimeLoop
from nimbus.core.mmu import MMU, MMUConfig
from nimbus.core.session_log import SessionLog, derive_messages
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
