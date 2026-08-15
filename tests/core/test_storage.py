"""Tests for SessionStorage load-time crash recovery — graded synthetic
tool results for the latest unanswered tool_calls batch."""

from nimbus.core.storage import SessionStorage


def _dump_with(messages, tmp_path):
    storage = SessionStorage(str(tmp_path))
    storage.save_session(
        session_id="sess_t",
        status="suspended",
        messages=messages,
        vcpu_state={},
    )
    return storage


def _calls(n):
    return [
        {"id": f"c{i}", "function": {"name": "Bash", "arguments": "{}"}}
        for i in range(1, n + 1)
    ]


class TestCrashRecovery:
    def test_fully_unanswered_batch_gets_graded_results(self, tmp_path):
        storage = _dump_with([
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": _calls(2)},
        ], tmp_path)
        dump = storage.load_session("sess_t")
        tools = [m for m in dump["messages"] if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in tools] == ["c1", "c2"]
        assert tools[0]["meta"]["code"] == "TOOL_OUTCOME_UNKNOWN"
        assert tools[1]["meta"]["code"] == "TOOL_NOT_STARTED"

    def test_partially_answered_batch_is_repaired(self, tmp_path):
        """Pre-existing gap: a crash after some results used to be ignored,
        leaving an unbalanced batch that breaks the LLM API contract."""
        storage = _dump_with([
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": _calls(3)},
            {"role": "tool", "tool_call_id": "c1", "name": "Bash", "content": "ok"},
        ], tmp_path)
        dump = storage.load_session("sess_t")
        synthetic = [m for m in dump["messages"] if m.get("meta", {}).get("synthetic")]
        assert [m["tool_call_id"] for m in synthetic] == ["c2", "c3"]
        assert synthetic[0]["meta"]["code"] == "TOOL_OUTCOME_UNKNOWN"

    def test_completed_session_untouched(self, tmp_path):
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": _calls(1)},
            {"role": "tool", "tool_call_id": "c1", "name": "Bash", "content": "ok"},
            {"role": "assistant", "content": "all done"},
        ]
        storage = _dump_with(messages, tmp_path)
        dump = storage.load_session("sess_t")
        assert dump["messages"] == messages


# =============================================================================
# Phase 2: authority inversion — the event log is the truth for messages
# =============================================================================

from nimbus.core.session_log import SessionLog  # noqa: E402


def _write_log(tmp_path, events):
    log = SessionLog(tmp_path / "sess_t.jsonl")
    for etype, data in events:
        log.append(etype, data)
    return log


class TestLogAuthority:
    def test_log_derived_messages_win_over_snapshot(self, tmp_path):
        # Snapshot lags (crash before the final save); log has one more turn.
        storage = _dump_with([{"role": "user", "content": "go"}], tmp_path)
        _write_log(tmp_path, [
            ("turn/start", {"turn": 1}),
            ("user/message", {"message": {"role": "user", "content": "go"}}),
            ("assistant/message", {"message": {"role": "assistant", "content": "done"}}),
            ("turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
        ])
        dump = storage.load_session("sess_t")
        assert [m["content"] for m in dump["messages"]] == ["go", "done"]

    def test_log_summary_overrides_snapshot_mmu_state(self, tmp_path):
        storage = SessionStorage(str(tmp_path))
        storage.save_session(
            session_id="sess_t", status="suspended",
            messages=[{"role": "user", "content": "go"}], vcpu_state={},
            metadata={"mmu_state": {"global_summary": "stale", "goal": "g"}},
        )
        _write_log(tmp_path, [
            ("user/message", {"message": {"role": "user", "content": "go"}}),
            ("compaction/applied",
             {"mode": "summarize", "kept": 1, "kept_indices": [0], "summary": "fresh"}),
        ])
        dump = storage.load_session("sess_t")
        assert dump["metadata"]["mmu_state"]["global_summary"] == "fresh"
        assert dump["metadata"]["mmu_state"]["goal"] == "g"  # snapshot keeps the rest

    def test_incomplete_log_falls_back_to_snapshot(self, tmp_path):
        # Log lost writes (append swallows I/O errors) — snapshot is ahead.
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "done"},
        ]
        storage = _dump_with(messages, tmp_path)
        _write_log(tmp_path, [
            ("turn/start", {"turn": 1}),
            ("user/message", {"message": {"role": "user", "content": "go"}}),
            ("turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
        ])
        dump = storage.load_session("sess_t")
        assert dump["messages"] == messages

    def test_corrupt_log_falls_back_to_snapshot(self, tmp_path):
        messages = [{"role": "user", "content": "go"}]
        storage = _dump_with(messages, tmp_path)
        (tmp_path / "sess_t.jsonl").write_text(
            '{"seq": 5, "type": "user/message", "time": 1, "data": {}}\n'
            'not json at all\n'
        )
        dump = storage.load_session("sess_t")
        assert dump["messages"] == messages

    def test_crashed_log_tail_yields_graded_results(self, tmp_path):
        # Snapshot missed the whole last step; the log's repaired view supplies
        # the assistant batch AND its graded synthetic results, and the
        # back-scan normalization must not duplicate them.
        storage = _dump_with([{"role": "user", "content": "go"}], tmp_path)
        _write_log(tmp_path, [
            ("turn/start", {"turn": 1}),
            ("user/message", {"message": {"role": "user", "content": "go"}}),
            ("step/start", {"turn": 1, "step": 1}),
            ("assistant/message", {"message": {
                "role": "assistant", "content": None,
                "tool_calls": _calls(2),
            }}),
            ("tool/result", {"message": {
                "role": "tool", "content": "ok", "name": "Bash", "tool_call_id": "c1",
            }}),
        ])
        dump = storage.load_session("sess_t")
        synthetic = [m for m in dump["messages"] if (m.get("meta") or {}).get("synthetic")]
        assert [m["tool_call_id"] for m in synthetic] == ["c2"]
        assert synthetic[0]["meta"]["code"] == "TOOL_OUTCOME_UNKNOWN"
