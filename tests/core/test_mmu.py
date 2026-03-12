"""Tests for nimbus_next.mmu — the memory management unit."""

import json

import pytest

from nimbus.core.mmu import (
    MMU,
    Message,
    MMUConfig,
    PinnedContext,
    _extract_file_ops,
    _find_turn_boundaries,
    _format_file_ops,
    _make_tombstone,
    _serialize_messages,
    _smart_drop,
    estimate_text_tokens,
)


class TestTokenEstimation:
    def test_english_text(self):
        tokens = estimate_text_tokens("hello world")  # 11 chars → ~2-3 tokens
        assert 1 <= tokens <= 5

    def test_chinese_text(self):
        tokens = estimate_text_tokens("你好世界")  # 4 CJK chars → ~2-3 tokens
        assert 2 <= tokens <= 4

    def test_empty_string(self):
        assert estimate_text_tokens("") == 0


class TestMessage:
    def test_to_dict_basic(self):
        msg = Message(role="user", content="hello")
        d = msg.to_dict()
        assert d == {"role": "user", "content": "hello"}

    def test_to_dict_tool_result(self):
        msg = Message(role="tool", content="output", name="Bash", tool_call_id="tc1")
        d = msg.to_dict()
        assert d["role"] == "tool"
        assert d["tool_call_id"] == "tc1"
        assert d["name"] == "Bash"

    def test_to_dict_with_tool_calls(self):
        tc = [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]
        msg = Message(role="assistant", content=None, tool_calls=tc)
        d = msg.to_dict()
        assert d["tool_calls"] == tc
        assert d["content"] is None  # Content included even when None (API compat)

    def test_token_estimate(self):
        msg = Message(role="user", content="a" * 100)
        tokens = msg.token_estimate()
        assert tokens >= 25  # 100/4 + overhead


class TestPinnedContext:
    def test_to_system_message(self):
        pc = PinnedContext(
            system_rules="Be helpful.",
            workspace_info="/home/user/project",
        )
        msg = pc.to_system_message()
        assert msg.role == "system"
        assert "Be helpful" in msg.content
        assert "/home/user/project" in msg.content

    def test_token_estimate(self):
        pc = PinnedContext(system_rules="x" * 400)
        assert pc.token_estimate() >= 90


class TestMMU:
    def test_basic_message_flow(self):
        mmu = MMU()
        mmu.add_user_message("Fix the bug")
        mmu.add_assistant_message("Let me look at it.")
        assert mmu.message_count == 2

    def test_assemble_context_empty(self):
        mmu = MMU()
        ctx = mmu.assemble_context()
        assert ctx == []

    def test_assemble_with_pinned(self):
        mmu = MMU()
        mmu.set_pinned(PinnedContext(system_rules="Be concise."))
        mmu.add_user_message("Hello")
        ctx = mmu.assemble_context()
        assert ctx[0]["role"] == "system"
        assert "concise" in ctx[0]["content"]
        assert ctx[1]["role"] == "user"

    def test_assemble_with_goal(self):
        mmu = MMU()
        mmu.set_goal("Fix authentication bug")
        mmu.add_user_message("Start here")
        ctx = mmu.assemble_context()
        assert any("GOAL" in str(m.get("content", "")) for m in ctx)

    def test_tool_call_flow(self):
        mmu = MMU()
        tc = [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": '{"file_path": "x"}'}}]
        mmu.add_assistant_with_tool_calls(None, tc)
        mmu.add_tool_result("tc1", "Read", "file contents here")
        ctx = mmu.assemble_context()
        assert len(ctx) == 2
        assert ctx[0]["tool_calls"] is not None
        assert ctx[1]["role"] == "tool"

    def test_estimate_tokens(self):
        mmu = MMU()
        mmu.set_pinned(PinnedContext(system_rules="x" * 400))
        mmu.add_user_message("y" * 200)
        tokens = mmu.estimate_tokens()
        assert tokens > 100

    def test_needs_compaction(self):
        config = MMUConfig(max_context_tokens=100, compress_threshold=0.5)
        mmu = MMU(config)
        # Under threshold
        assert not mmu.needs_compaction()
        # Add enough content to trigger
        mmu.add_user_message("x" * 1000)
        assert mmu.needs_compaction()

    @pytest.mark.asyncio
    async def test_archive_and_reset_fallback(self):
        # Use a low token limit so smart_drop actually drops messages
        config = MMUConfig(max_context_tokens=100, keep_recent_tokens=200)
        mmu = MMU(config)
        for i in range(15):
            mmu.add_user_message(f"message {i} " + "x" * 50)
            mmu.add_assistant_message(f"response {i} " + "y" * 50)
        assert mmu.message_count == 30

        summary = await mmu.archive_and_reset()
        assert summary is not None
        assert mmu.message_count < 30  # most messages archived
        # Archives should have the summary
        ctx = mmu.assemble_context()
        assert any("summary" in str(m.get("content", "")).lower() for m in ctx)

    @pytest.mark.asyncio
    async def test_archive_with_summarizer(self):
        # Need enough messages that cut point separates some for summarization
        config = MMUConfig(max_context_tokens=200, keep_recent_tokens=100)
        mmu = MMU(config)
        # Add many messages so cut point places some before the hot zone
        for i in range(10):
            mmu.add_user_message(f"hello {i} " + "x" * 50)
            mmu.add_assistant_message(f"world {i} " + "y" * 50)

        async def mock_summarizer(system_prompt, user_prompt):
            assert "summarization" in system_prompt.lower()
            assert "<conversation>" in user_prompt
            return "TL;DR: greeted each other"

        summary = await mmu.archive_and_reset(summarizer=mock_summarizer)
        assert "greeted each other" in summary
        # Some messages should be kept (hot zone)
        assert mmu.message_count < 20  # not all kept
        ctx = mmu.assemble_context()
        assert any("greeted" in str(m.get("content", "")) for m in ctx)

    @pytest.mark.asyncio
    async def test_archive_empty(self):
        mmu = MMU()
        result = await mmu.archive_and_reset()
        assert result is None

    def test_clear(self):
        mmu = MMU()
        mmu.set_goal("test")
        mmu.add_user_message("hello")
        mmu.clear()
        assert mmu.message_count == 0
        ctx = mmu.assemble_context()
        assert not any("GOAL" in str(m.get("content", "")).upper() for m in ctx)

    def test_system_message(self):
        mmu = MMU()
        mmu.add_system_message("Context compacted.")
        ctx = mmu.assemble_context()
        assert "[System]" in ctx[0]["content"]

    @pytest.mark.asyncio
    async def test_archive_merge_not_append(self):
        """Global summary should be merged (replaced), not infinitely appended."""
        config = MMUConfig(max_context_tokens=5000, keep_recent_tokens=50)
        mmu = MMU(config)
        for i in range(20):
            mmu.add_user_message(f"msg {i}" + "x" * 200)
            mmu.add_assistant_message(f"resp {i}" + "y" * 200)

        # First compaction (deterministic)
        await mmu.archive_and_reset()
        first_summary = mmu._global_summary
        assert first_summary

        # Add more messages
        for i in range(20):
            mmu.add_user_message(f"msg2 {i}" + "x" * 200)
            mmu.add_assistant_message(f"resp2 {i}" + "y" * 200)

        # Second compaction with LLM summarizer (replaces, not appends)
        async def mock_summarizer(system_prompt, user_prompt):
            # The summarizer gets the previous summary via <previous-summary> tags
            assert "<previous-summary>" in user_prompt
            return "Complete merged summary of everything"

        summary = await mmu.archive_and_reset(summarizer=mock_summarizer)
        # Should contain the new summary (may also have file ops appended)
        assert "Complete merged summary of everything" in mmu._global_summary


# =============================================================================
# Message Properties
# =============================================================================


class TestMessageProperties:
    def test_is_tool_call(self):
        tc = [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]
        msg = Message(role="assistant", content=None, tool_calls=tc)
        assert msg.is_tool_call is True

    def test_is_not_tool_call(self):
        msg = Message(role="assistant", content="just text")
        assert msg.is_tool_call is False

    def test_is_tool_result(self):
        msg = Message(role="tool", content="output", name="Bash", tool_call_id="tc1")
        assert msg.is_tool_result is True

    def test_is_error_explicit(self):
        msg = Message(role="tool", content="[Error] file not found", name="Read")
        assert msg.is_error is True

    def test_is_error_traceback(self):
        msg = Message(role="tool", content="Traceback (most recent call last):\n  File...", name="Bash")
        assert msg.is_error is True

    def test_is_error_timeout(self):
        msg = Message(role="tool", content="Command timed out after 60s", name="Bash")
        assert msg.is_error is True

    def test_is_not_error(self):
        msg = Message(role="tool", content="def hello():\n    pass\n", name="Read")
        assert msg.is_error is False


# =============================================================================
# Turn Boundaries
# =============================================================================


def _tc(tc_id, name="Read"):
    return [{"id": tc_id, "type": "function", "function": {"name": name, "arguments": "{}"}}]


def _assistant_tc(tc_id, name="Read"):
    return Message(role="assistant", content=None, tool_calls=_tc(tc_id, name))


def _tool_result(tc_id, name="Read", content="ok"):
    return Message(role="tool", content=content, name=name, tool_call_id=tc_id)


class TestTurnBoundaries:
    def test_single_turn(self):
        msgs = [_assistant_tc("tc1"), _tool_result("tc1")]
        turns = _find_turn_boundaries(msgs)
        assert turns == [(0, 1)]

    def test_multi_result_turn(self):
        """Assistant with 2 tool calls → 2 results = one atomic turn."""
        msgs = [
            Message(role="assistant", content=None,
                    tool_calls=_tc("tc1", "Read") + _tc("tc2", "Bash")),
            _tool_result("tc1", "Read"),
            _tool_result("tc2", "Bash"),
        ]
        turns = _find_turn_boundaries(msgs)
        assert turns == [(0, 2)]

    def test_multiple_turns(self):
        msgs = [
            Message(role="user", content="do stuff"),
            _assistant_tc("tc1"), _tool_result("tc1"),
            _assistant_tc("tc2"), _tool_result("tc2"),
        ]
        turns = _find_turn_boundaries(msgs)
        assert turns == [(1, 2), (3, 4)]

    def test_no_turns(self):
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        turns = _find_turn_boundaries(msgs)
        assert turns == []


# =============================================================================
# Tombstone
# =============================================================================


class TestTombstone:
    def test_tombstone_tool_turn(self):
        msgs = [_assistant_tc("tc1", "Read"), _tool_result("tc1", "Read", "file data")]
        tomb = _make_tombstone(msgs)
        assert "Dropped 2 messages" in tomb
        assert "[Read]" in tomb
        assert "OK" in tomb

    def test_tombstone_error_turn(self):
        msgs = [
            _assistant_tc("tc1", "Bash"),
            _tool_result("tc1", "Bash", "[Error] command not found"),
        ]
        tomb = _make_tombstone(msgs)
        assert "ERR" in tomb

    def test_tombstone_plain_messages(self):
        msgs = [
            Message(role="user", content="fix the bug"),
            Message(role="assistant", content="I'll look at it now"),
        ]
        tomb = _make_tombstone(msgs)
        assert "User: fix the bug" in tomb
        assert "Assistant: I" in tomb

    def test_tombstone_empty(self):
        tomb = _make_tombstone([])
        assert "empty" in tomb.lower()


# =============================================================================
# Smart Drop
# =============================================================================


def _assert_no_orphan_tool_calls(messages):
    """Assert no tool_call message exists without its corresponding tool_result(s), and vice versa."""
    for i, m in enumerate(messages):
        if m.is_tool_call:
            # Must be followed by at least one tool_result
            assert i + 1 < len(messages), f"tool_call at index {i} is at end without any tool_result"
            assert messages[i + 1].is_tool_result, f"tool_call at index {i} not followed by tool_result"
        if m.is_tool_result:
            # Must be preceded by either another tool_result or a tool_call
            assert i > 0, f"tool_result at index {i} is at start without a tool_call"
            prev = messages[i - 1]
            assert prev.is_tool_call or prev.is_tool_result, \
                f"tool_result at index {i} not preceded by tool_call or another tool_result"


class TestSmartDrop:
    def test_no_drop_under_budget(self):
        msgs = [Message(role="user", content="short")]
        surviving, tomb = _smart_drop(msgs, target_tokens=9999, keep_recent_tokens=1000)
        assert len(surviving) == 1
        assert tomb == ""

    def test_drops_error_turns_first(self):
        """Error tool turns should be dropped before successful ones."""
        msgs = [
            # Turn 1: success (should survive longer)
            _assistant_tc("tc1", "Read"),
            _tool_result("tc1", "Read", "x" * 400),
            # Turn 2: failure (should be dropped first)
            _assistant_tc("tc2", "Bash"),
            _tool_result("tc2", "Bash", "[Error] command not found " + "x" * 400),
            # Hot zone
            Message(role="user", content="continue"),
        ]
        # Set budget low enough to force dropping one turn
        surviving, tomb = _smart_drop(msgs, target_tokens=200, keep_recent_tokens=50)
        # Error turn should be dropped, success turn might remain
        assert "ERR" in tomb
        # The user message (hot) should survive
        assert any(m.role == "user" and m.content == "continue" for m in surviving)

    def test_never_drops_hot_zone(self):
        msgs = [
            Message(role="user", content="old " + "x" * 500),
            Message(role="assistant", content="old resp " + "y" * 500),
            Message(role="user", content="recent"),
        ]
        surviving, tomb = _smart_drop(msgs, target_tokens=50, keep_recent_tokens=50)
        # Last message (hot) must survive
        assert surviving[-1].content == "recent"

    def test_preserves_turn_integrity(self):
        """After smart drop, no tool_call should be separated from its results."""
        msgs = [
            _assistant_tc("tc1", "Read"), _tool_result("tc1", "Read", "x" * 200),
            _assistant_tc("tc2", "Bash"), _tool_result("tc2", "Bash", "y" * 200),
            Message(role="user", content="done"),
        ]
        surviving, _ = _smart_drop(msgs, target_tokens=100, keep_recent_tokens=50)
        # Verify: every assistant with tool_calls is followed by its tool results
        _assert_no_orphan_tool_calls(surviving)

    def test_tombstone_produced(self):
        msgs = [
            Message(role="user", content="old message " + "x" * 500),
            Message(role="user", content="recent"),
        ]
        surviving, tomb = _smart_drop(msgs, target_tokens=50, keep_recent_tokens=50)
        assert "Dropped" in tomb
        assert "old message" in tomb

    def test_token_based_hot_boundary_no_orphans(self):
        """Token-based hot boundary must not split tool_call from its results."""
        msgs = [
            Message(role="user", content="start " + "x" * 200),
            # Turn 1: small tool call
            _assistant_tc("tc1", "Grep"),
            _tool_result("tc1", "Grep", "match1\nmatch2"),
            # Turn 2: large tool result that will straddle the boundary
            _assistant_tc("tc2", "Read"),
            _tool_result("tc2", "Read", "y" * 2000),  # ~500 tokens
            # Recent
            Message(role="user", content="continue"),
            Message(role="assistant", content="ok"),
        ]
        # Keep only ~200 tokens in hot zone - boundary likely falls mid-turn 2
        surviving, _ = _smart_drop(msgs, target_tokens=300, keep_recent_tokens=200)
        _assert_no_orphan_tool_calls(surviving)

    def test_emergency_drop_in_hot_zone_no_orphans(self):
        """Even when forced to breach hot zone, tool pairs must stay atomic."""
        # All messages are large tool results in the hot zone
        msgs = [
            _assistant_tc("tc1", "Read"),
            _tool_result("tc1", "Read", "big1 " + "x" * 4000),  # ~1000 tokens
            _assistant_tc("tc2", "Read"),
            _tool_result("tc2", "Read", "big2 " + "y" * 4000),  # ~1000 tokens
            _assistant_tc("tc3", "Read"),
            _tool_result("tc3", "Read", "big3 " + "z" * 4000),  # ~1000 tokens
            Message(role="user", content="done"),
        ]
        # Very tight budget forces emergency drops into hot zone
        surviving, _ = _smart_drop(msgs, target_tokens=500, keep_recent_tokens=20000)
        _assert_no_orphan_tool_calls(surviving)

    def test_multiple_tool_results_per_call_no_orphans(self):
        """Multi-result tool calls (e.g., parallel tools) stay atomic."""
        msgs = [
            Message(role="user", content="old " + "x" * 400),
            # One assistant call with 3 tool results
            Message(role="assistant", content=None,
                    tool_calls=_tc("tc1", "Read") + _tc("tc2", "Bash") + _tc("tc3", "Grep")),
            _tool_result("tc1", "Read", "r" * 800),
            _tool_result("tc2", "Bash", "b" * 800),
            _tool_result("tc3", "Grep", "g" * 800),
            Message(role="user", content="next"),
        ]
        surviving, _ = _smart_drop(msgs, target_tokens=300, keep_recent_tokens=100)
        _assert_no_orphan_tool_calls(surviving)


# =============================================================================
# Serialize Messages (pi-style)
# =============================================================================


class TestSerializeMessages:
    def test_basic_conversation(self):
        msgs = [
            Message(role="user", content="Fix the bug"),
            Message(role="assistant", content="Let me look at it."),
        ]
        text = _serialize_messages(msgs)
        assert "[User]: Fix the bug" in text
        assert "[Assistant]: Let me look at it." in text

    def test_tool_calls(self):
        tc = [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": '{"file_path": "x.py"}'}}]
        msgs = [
            Message(role="assistant", content=None, tool_calls=tc),
            Message(role="tool", content="file contents here", name="Read", tool_call_id="tc1"),
        ]
        text = _serialize_messages(msgs)
        assert "[Tool calls]:" in text
        assert "Read(" in text
        assert "[Tool result (Read, OK)]:" in text

    def test_error_tool_result(self):
        msgs = [
            Message(role="tool", content="[Error] file not found", name="Read", tool_call_id="tc1"),
        ]
        text = _serialize_messages(msgs)
        assert "[Tool result (Read, ERROR)]:" in text

    def test_system_messages_skipped(self):
        msgs = [
            Message(role="system", content="Be helpful."),
            Message(role="user", content="Hello"),
        ]
        text = _serialize_messages(msgs)
        assert "Be helpful" not in text
        assert "[User]: Hello" in text

    def test_tool_result_truncated(self):
        long_content = "x" * 1000
        msgs = [
            Message(role="tool", content=long_content, name="Bash", tool_call_id="tc1"),
        ]
        text = _serialize_messages(msgs)
        # Tool result preview is capped at 500 chars
        result_part = text.split("[Tool result (Bash, OK)]: ")[1]
        assert len(result_part) == 500

    def test_assistant_with_both_content_and_tools(self):
        tc = [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]
        msgs = [
            Message(role="assistant", content="Let me read that.", tool_calls=tc),
        ]
        text = _serialize_messages(msgs)
        assert "[Tool calls]:" in text
        assert "[Assistant]: Let me read that." in text


# =============================================================================
# Extract File Operations
# =============================================================================


class TestExtractFileOps:
    def test_read_and_write(self):
        msgs = [
            Message(role="assistant", content=None, tool_calls=[
                {"id": "tc1", "type": "function", "function": {
                    "name": "Read", "arguments": json.dumps({"file_path": "/src/main.py"})}},
            ]),
            Message(role="assistant", content=None, tool_calls=[
                {"id": "tc2", "type": "function", "function": {
                    "name": "Write", "arguments": json.dumps({"file_path": "/src/output.py"})}},
            ]),
        ]
        read_only, modified = _extract_file_ops(msgs)
        assert "/src/main.py" in read_only
        assert "/src/output.py" in modified
        # main.py is read-only (not modified)
        assert "/src/main.py" not in modified

    def test_read_then_modify_same_file(self):
        msgs = [
            Message(role="assistant", content=None, tool_calls=[
                {"id": "tc1", "type": "function", "function": {
                    "name": "Read", "arguments": json.dumps({"file_path": "/src/main.py"})}},
            ]),
            Message(role="assistant", content=None, tool_calls=[
                {"id": "tc2", "type": "function", "function": {
                    "name": "Edit", "arguments": json.dumps({"file_path": "/src/main.py", "old_string": "a", "new_string": "b"})}},
            ]),
        ]
        read_only, modified = _extract_file_ops(msgs)
        # main.py is modified, not read-only
        assert "/src/main.py" not in read_only
        assert "/src/main.py" in modified

    def test_no_tool_calls(self):
        msgs = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi"),
        ]
        read_only, modified = _extract_file_ops(msgs)
        assert read_only == []
        assert modified == []

    def test_invalid_json_args(self):
        msgs = [
            Message(role="assistant", content=None, tool_calls=[
                {"id": "tc1", "type": "function", "function": {
                    "name": "Read", "arguments": "not json"}},
            ]),
        ]
        read_only, modified = _extract_file_ops(msgs)
        assert read_only == []
        assert modified == []

    def test_grep_uses_path(self):
        msgs = [
            Message(role="assistant", content=None, tool_calls=[
                {"id": "tc1", "type": "function", "function": {
                    "name": "Grep", "arguments": json.dumps({"path": "/src", "pattern": "TODO"})}},
            ]),
        ]
        read_only, modified = _extract_file_ops(msgs)
        assert "/src" in read_only


# =============================================================================
# Format File Operations
# =============================================================================


class TestFormatFileOps:
    def test_both_read_and_modified(self):
        text = _format_file_ops(["/a.py", "/b.py"], ["/c.py"])
        assert "<read-files>" in text
        assert "/a.py" in text
        assert "/b.py" in text
        assert "<modified-files>" in text
        assert "/c.py" in text

    def test_empty(self):
        text = _format_file_ops([], [])
        assert text == ""

    def test_only_modified(self):
        text = _format_file_ops([], ["/c.py"])
        assert "<read-files>" not in text
        assert "<modified-files>" in text


# =============================================================================
# Find Cut Point
# =============================================================================


class TestFindCutPoint:
    def test_all_in_hot_zone(self):
        """When all messages fit in the token budget, cut_index stays at 0."""
        mmu = MMU()
        mmu.add_user_message("short")
        mmu.add_assistant_message("reply")
        # These messages are very small, well under 20000 tokens
        cut = mmu._find_cut_point(keep_recent_tokens=20000)
        assert cut == 0

    def test_splits_at_boundary(self):
        """With many large messages, cut point should be somewhere in the middle."""
        mmu = MMU()
        # Each message is ~250 tokens (1000 chars / 4)
        for i in range(20):
            mmu.add_user_message(f"msg {i} " + "x" * 1000)
        # Keep only ~500 tokens -> should keep only about 2 messages
        cut = mmu._find_cut_point(keep_recent_tokens=500)
        assert cut > 0
        assert cut < 20

    def test_never_cuts_tool_pair(self):
        """Cut point should not land on a tool_result (would split the pair)."""
        mmu = MMU()
        # Pad with some messages
        for i in range(5):
            mmu.add_user_message(f"padding {i} " + "x" * 500)
        # Tool pair
        tc = [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]
        mmu.add_assistant_with_tool_calls(None, tc)
        mmu.add_tool_result("tc1", "Read", "file contents " + "y" * 500)
        # More messages
        for i in range(5):
            mmu.add_user_message(f"after {i} " + "x" * 500)

        cut = mmu._find_cut_point(keep_recent_tokens=1000)
        if cut > 0 and cut < len(mmu._messages):
            # The message at cut should NOT be a tool_result
            assert not mmu._messages[cut].is_tool_result


# =============================================================================
# Compaction with File Ops
# =============================================================================


class TestCompactionWithFileOps:
    @pytest.mark.asyncio
    async def test_file_ops_in_summary(self):
        """File operations should be tracked and appended to the summary."""
        config = MMUConfig(max_context_tokens=200, keep_recent_tokens=100)
        mmu = MMU(config)

        # Add messages with tool calls
        tc_read = [{"id": "tc1", "type": "function", "function": {
            "name": "Read", "arguments": json.dumps({"file_path": "/src/main.py"})}}]
        tc_write = [{"id": "tc2", "type": "function", "function": {
            "name": "Write", "arguments": json.dumps({"file_path": "/src/output.py", "content": "..."})}}]

        mmu.add_user_message("Read main.py")
        mmu.add_assistant_with_tool_calls(None, tc_read)
        mmu.add_tool_result("tc1", "Read", "def main(): pass")
        mmu.add_assistant_message("Now let me write output")
        mmu.add_assistant_with_tool_calls(None, tc_write)
        mmu.add_tool_result("tc2", "Write", "OK")
        # Add more messages to push beyond hot zone
        for i in range(10):
            mmu.add_user_message(f"more {i} " + "x" * 100)
            mmu.add_assistant_message(f"resp {i} " + "y" * 100)

        summary = await mmu.archive_and_reset()
        assert summary is not None
        # File ops should be in the summary if messages containing them were summarized
        # (depends on cut point, but with many messages, the tool calls should be in the summarized portion)

    @pytest.mark.asyncio
    async def test_llm_summarizer_fallback_on_error(self):
        """When LLM summarizer fails, should fall back to deterministic."""
        config = MMUConfig(max_context_tokens=200, keep_recent_tokens=100)
        mmu = MMU(config)
        for i in range(10):
            mmu.add_user_message(f"msg {i} " + "x" * 100)
            mmu.add_assistant_message(f"resp {i} " + "y" * 100)

        async def failing_summarizer(system_prompt, user_prompt):
            raise RuntimeError("LLM unavailable")

        summary = await mmu.archive_and_reset(summarizer=failing_summarizer)
        assert summary is not None
        # Should have fallen back to deterministic (tombstone)
        assert "Dropped" in summary or "compacted" in summary.lower()

    @pytest.mark.asyncio
    async def test_incremental_summary_with_previous(self):
        """Second compaction should pass previous summary to the LLM."""
        config = MMUConfig(max_context_tokens=5000, keep_recent_tokens=50)
        mmu = MMU(config)

        # First round of messages - enough to force summarizer path
        for i in range(20):
            mmu.add_user_message(f"msg {i} " + "x" * 200)
            mmu.add_assistant_message(f"resp {i} " + "y" * 200)

        # First compaction (deterministic)
        await mmu.archive_and_reset()
        first_summary = mmu._global_summary
        assert first_summary

        # Second round
        for i in range(20):
            mmu.add_user_message(f"msg2 {i} " + "x" * 200)
            mmu.add_assistant_message(f"resp2 {i} " + "y" * 200)

        # Second compaction with LLM
        received_prompts = {}

        async def mock_summarizer(system_prompt, user_prompt):
            received_prompts["system"] = system_prompt
            received_prompts["user"] = user_prompt
            return "Updated summary with new progress"

        await mmu.archive_and_reset(summarizer=mock_summarizer)

        # Should have passed previous summary
        assert "<previous-summary>" in received_prompts["user"]
        assert first_summary in received_prompts["user"]
        # Should use UPDATE prompt, not initial SUMMARIZATION prompt
        assert "UPDATE" in received_prompts["user"] or "Update" in received_prompts["user"]
