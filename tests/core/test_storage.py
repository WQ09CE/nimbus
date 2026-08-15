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
