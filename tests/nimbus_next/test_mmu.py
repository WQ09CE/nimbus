"""Tests for nimbus_next.mmu — the memory management unit."""

import pytest

from nimbus_next.mmu import (
    MMU, MMUConfig, Message, PinnedContext, estimate_text_tokens,
    _find_turn_boundaries, _find_safe_cut_point, _make_tombstone, _smart_drop,
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

    def test_custom_anchors(self):
        pc = PinnedContext(custom_anchors={"CLAUDE.md": "Use ruff for linting."})
        msg = pc.to_system_message()
        assert "CLAUDE.md" in msg.content
        assert "ruff" in msg.content

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
        assert any("Goal" in str(m.get("content", "")) for m in ctx)

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
        config = MMUConfig(max_context_tokens=100, keep_recent_messages=4)
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
        mmu = MMU()
        mmu.add_user_message("hello")
        mmu.add_assistant_message("world")

        async def mock_summarizer(messages):
            return "TL;DR: greeted each other"

        summary = await mmu.archive_and_reset(summarizer=mock_summarizer)
        assert summary == "TL;DR: greeted each other"
        assert mmu.message_count == 0  # cleared after summarization
        ctx = mmu.assemble_context()
        assert any("greeted" in str(m.get("content", "")) for m in ctx)

    @pytest.mark.asyncio
    async def test_archive_empty(self):
        mmu = MMU()
        result = await mmu.archive_and_reset()
        assert result is None

    def test_rollback_incomplete_turn(self):
        mmu = MMU()
        tc = [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]
        mmu.add_assistant_with_tool_calls(None, tc)
        mmu.add_tool_result("tc1", "Read", "output")
        # Add orphaned tool result (no assistant message before it)
        mmu.add_tool_result("tc2", "Bash", "orphan")
        assert mmu.message_count == 3
        removed = mmu.rollback_incomplete_turn()
        assert removed >= 1

    def test_clear(self):
        mmu = MMU()
        mmu.set_goal("test")
        mmu.add_user_message("hello")
        mmu.clear()
        assert mmu.message_count == 0
        ctx = mmu.assemble_context()
        assert not any("Goal" in str(m.get("content", "")) for m in ctx)

    def test_system_message(self):
        mmu = MMU()
        mmu.add_system_message("Context compacted.")
        ctx = mmu.assemble_context()
        assert "[System]" in ctx[0]["content"]

    @pytest.mark.asyncio
    async def test_archive_merge_not_append(self):
        """Global summary should be merged (replaced), not infinitely appended."""
        mmu = MMU()
        for i in range(10):
            mmu.add_user_message(f"msg {i}" + "x" * 200)
            mmu.add_assistant_message(f"resp {i}" + "y" * 200)

        # First compaction
        await mmu.archive_and_reset()
        first_summary = mmu._global_summary
        assert first_summary

        # Add more messages
        for i in range(10):
            mmu.add_user_message(f"msg2 {i}" + "x" * 200)
            mmu.add_assistant_message(f"resp2 {i}" + "y" * 200)

        # Second compaction with LLM summarizer (replaces, not appends)
        async def mock_summarizer(messages):
            return "Complete merged summary of everything"

        await mmu.archive_and_reset(summarizer=mock_summarizer)
        # Should be the new summary, not old + new concatenated
        assert mmu._global_summary == "Complete merged summary of everything"

    def test_validate_turn_integrity_clean(self):
        """Complete tool-use turns should have no issues."""
        mmu = MMU()
        tc = [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]
        mmu.add_assistant_with_tool_calls(None, tc)
        mmu.add_tool_result("tc1", "Read", "contents")
        assert mmu.validate_turn_integrity() == []

    def test_validate_turn_integrity_orphan(self):
        """Orphaned tool result should be flagged."""
        mmu = MMU()
        mmu.add_tool_result("tc99", "Bash", "orphan output")
        issues = mmu.validate_turn_integrity()
        assert len(issues) == 1
        assert "orphaned" in issues[0]

    def test_validate_turn_integrity_missing_result(self):
        """Tool call without matching result should be flagged."""
        mmu = MMU()
        tc = [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]
        mmu.add_assistant_with_tool_calls(None, tc)
        # No tool result added
        mmu.add_user_message("next")
        issues = mmu.validate_turn_integrity()
        assert len(issues) == 1
        assert "missing results" in issues[0]


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


class TestSafeCutPoint:
    def test_cut_between_turns(self):
        msgs = [
            _assistant_tc("tc1"), _tool_result("tc1"),  # turn 0-1
            Message(role="user", content="next"),        # idx 2
            _assistant_tc("tc2"), _tool_result("tc2"),   # turn 3-4
        ]
        # Cutting at 2 (user message) is safe
        assert _find_safe_cut_point(msgs, 2) == 2

    def test_cut_inside_turn_pushes_forward(self):
        msgs = [
            _assistant_tc("tc1"), _tool_result("tc1"),  # turn 0-1
            _assistant_tc("tc2"), _tool_result("tc2"),  # turn 2-3
        ]
        # Cutting at 3 (inside turn 2-3) → push to 4
        assert _find_safe_cut_point(msgs, 3) == 4

    def test_cut_on_orphan_tool_result(self):
        msgs = [
            Message(role="user", content="x"),
            _tool_result("tc1"),  # orphaned
            Message(role="user", content="y"),
        ]
        # Cutting at 1 (orphan tool) → push past it
        assert _find_safe_cut_point(msgs, 1) == 2

    def test_cut_at_zero(self):
        msgs = [_assistant_tc("tc1"), _tool_result("tc1")]
        assert _find_safe_cut_point(msgs, 0) == 0

    def test_cut_at_end(self):
        msgs = [Message(role="user", content="x")]
        assert _find_safe_cut_point(msgs, 5) == 1


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


class TestSmartDrop:
    def test_no_drop_under_budget(self):
        msgs = [Message(role="user", content="short")]
        surviving, tomb = _smart_drop(msgs, target_tokens=9999, keep_recent=1)
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
        surviving, tomb = _smart_drop(msgs, target_tokens=200, keep_recent=1)
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
        surviving, tomb = _smart_drop(msgs, target_tokens=50, keep_recent=1)
        # Last message (hot) must survive
        assert surviving[-1].content == "recent"

    def test_preserves_turn_integrity(self):
        """After smart drop, no tool_call should be separated from its results."""
        msgs = [
            _assistant_tc("tc1", "Read"), _tool_result("tc1", "Read", "x" * 200),
            _assistant_tc("tc2", "Bash"), _tool_result("tc2", "Bash", "y" * 200),
            Message(role="user", content="done"),
        ]
        surviving, _ = _smart_drop(msgs, target_tokens=100, keep_recent=1)
        # Verify: every assistant with tool_calls is followed by its tool results
        for i, m in enumerate(surviving):
            if m.is_tool_call:
                assert i + 1 < len(surviving), "tool_call at end without result"
                assert surviving[i + 1].is_tool_result, "tool_call not followed by result"

    def test_tombstone_produced(self):
        msgs = [
            Message(role="user", content="old message " + "x" * 500),
            Message(role="user", content="recent"),
        ]
        surviving, tomb = _smart_drop(msgs, target_tokens=50, keep_recent=1)
        assert "Dropped" in tomb
        assert "old message" in tomb
