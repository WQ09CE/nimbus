"""Tests for nimbus_next.mmu — the memory management unit."""

import pytest

from nimbus_next.mmu import MMU, MMUConfig, Message, PinnedContext, estimate_text_tokens


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
        mmu = MMU()
        for i in range(10):
            mmu.add_user_message(f"message {i}")
            mmu.add_assistant_message(f"response {i}")
        assert mmu.message_count == 20

        summary = await mmu.archive_and_reset()
        assert summary is not None
        assert mmu.message_count < 20  # most messages archived
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
