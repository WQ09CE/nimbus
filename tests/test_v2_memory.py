"""
Tests for Nimbus v2 Memory Management Unit (MMU).

Run with: pytest tests/test_v2_memory.py -v
"""

import pytest

from nimbus.core.memory import (
    PinnedContext,
    StackFrame,
    Message,
    MMU,
    MMUConfig,
)
from nimbus.core.memory.context import create_root_frame, create_sub_frame


# =============================================================================
# Message Tests
# =============================================================================

class TestMessage:
    """Tests for Message."""

    def test_create_message(self):
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_to_dict(self):
        msg = Message(role="assistant", content="Hi there!")
        d = msg.to_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "Hi there!"

    def test_tool_message(self):
        msg = Message(
            role="tool",
            content="File content here",
            name="Read",
            tool_call_id="tc_001"
        )
        d = msg.to_dict()
        assert d["role"] == "tool"
        assert d["name"] == "Read"
        assert d["tool_call_id"] == "tc_001"

    def test_token_estimate_english(self):
        msg = Message(role="user", content="a" * 100)
        # 100 chars / 4 = 25 tokens
        assert msg.token_estimate() == 25
    
    def test_token_estimate_chinese(self):
        # 中文字符使用更保守的估算：1.5 chars/token
        msg = Message(role="user", content="你好世界")  # 4 Chinese chars
        # 4 chars / 1.5 ≈ 2.67 → int(2.67) = 2
        estimate = msg.token_estimate()
        assert estimate >= 2  # 中文应该估算更多 tokens
    
    def test_token_estimate_mixed(self):
        # 混合中英文
        msg = Message(role="user", content="Hello 你好 World")  # 5 en + 2 zh + 5 en
        estimate = msg.token_estimate()
        # 中文 2 chars / 1.5 ≈ 1, 英文 12 chars / 4 = 3
        assert estimate >= 3


# =============================================================================
# PinnedContext Tests
# =============================================================================

class TestPinnedContext:
    """Tests for PinnedContext."""

    def test_create_pinned(self):
        pinned = PinnedContext(
            system_rules="Be helpful and honest.",
            workspace_info="Working in /project",
            capabilities="Tools: Read, Write, Bash"
        )
        assert "helpful" in pinned.system_rules
        assert "/project" in pinned.workspace_info

    def test_to_system_message(self):
        pinned = PinnedContext(
            system_rules="Rule 1",
            workspace_info="Info 1"
        )
        msg = pinned.to_system_message()
        assert msg.role == "system"
        assert "Rule 1" in msg.content
        assert "Info 1" in msg.content
        assert msg.meta.get("pinned") is True

    def test_add_anchor(self):
        pinned = PinnedContext()
        pinned.add_anchor("Custom anchor content")
        assert "Custom anchor content" in pinned.custom_anchors

    def test_update_workspace(self):
        pinned = PinnedContext()
        pinned.update_workspace("New workspace info")
        assert pinned.workspace_info == "New workspace info"


# =============================================================================
# StackFrame Tests
# =============================================================================

class TestStackFrame:
    """Tests for StackFrame."""

    def test_create_root_frame(self):
        frame = create_root_frame("main task")
        assert frame.goal == "main task"
        assert frame.parent_frame_id is None
        assert frame.meta.get("is_root") is True

    def test_create_sub_frame(self):
        frame = create_sub_frame("parent-001", "subtask")
        assert frame.goal == "subtask"
        assert frame.parent_frame_id == "parent-001"
        assert frame.meta.get("is_root") is False

    def test_add_messages(self):
        frame = StackFrame()
        frame.add_user_message("Hello")
        frame.add_assistant_message("Hi!")

        assert len(frame.messages) == 2
        assert frame.messages[0].role == "user"
        assert frame.messages[1].role == "assistant"

    def test_add_tool_result(self):
        frame = StackFrame()
        frame.add_tool_result("tc_001", "Read", "File content")

        assert len(frame.messages) == 1
        assert frame.messages[0].role == "tool"
        assert frame.messages[0].name == "Read"

    def test_complete_frame(self):
        frame = StackFrame(goal="test")
        frame.complete("success")

        assert frame.state == "COMPLETED"
        assert frame.result == "success"

    def test_fail_frame(self):
        frame = StackFrame(goal="test")
        frame.fail("error occurred")

        assert frame.state == "FAILED"
        assert frame.result == "error occurred"

    def test_to_context_messages(self):
        frame = create_sub_frame("parent-001", "find files")
        frame.add_user_message("Search for .py files")

        messages = frame.to_context_messages()

        # Should have goal message + user message
        assert len(messages) == 2
        assert "[Subtask]" in messages[0].content
        assert "find files" in messages[0].content


# =============================================================================
# MMU Tests
# =============================================================================

class TestMMU:
    """Tests for MMU."""

    def test_create_mmu(self):
        mmu = MMU(process_id="proc-001")
        assert mmu.process_id == "proc-001"
        assert mmu.stack_depth == 1  # Root frame
        assert mmu.is_root_frame is True

    def test_set_pinned(self):
        mmu = MMU()
        pinned = PinnedContext(system_rules="Be helpful")
        mmu.set_pinned(pinned)

        assert mmu.get_pinned() is not None
        assert "helpful" in mmu.get_pinned().system_rules

    def test_update_pinned_parts(self):
        mmu = MMU()
        mmu.update_system_rules("New rules")
        mmu.update_workspace_info("New workspace")
        mmu.update_capabilities("New caps")

        pinned = mmu.get_pinned()
        assert pinned.system_rules == "New rules"
        assert pinned.workspace_info == "New workspace"
        assert pinned.capabilities == "New caps"

    def test_add_messages(self):
        mmu = MMU()
        mmu.add_user_message("Hello")
        mmu.add_assistant_message("Hi!")

        assert len(mmu.current_frame.messages) == 2

    def test_push_frame(self):
        mmu = MMU()
        mmu.add_user_message("Main task")

        # Push sub-frame
        frame_id = mmu.push_frame("subtask goal")

        assert mmu.stack_depth == 2
        assert mmu.is_root_frame is False
        assert mmu.current_frame.goal == "subtask goal"
        assert mmu.current_frame.frame_id == frame_id

    def test_pop_frame(self):
        mmu = MMU()
        mmu.add_user_message("Main task")

        # Push and pop
        mmu.push_frame("subtask")
        mmu.add_user_message("Subtask work")
        result = mmu.pop_frame("subtask done")

        assert result == "subtask done"
        assert mmu.stack_depth == 1
        assert mmu.is_root_frame is True
        # Parent frame should have the result message
        assert any("Subtask completed" in m.content for m in mmu.current_frame.messages if isinstance(m.content, str))

    def test_pop_root_frame(self):
        mmu = MMU()
        result = mmu.pop_frame("cannot pop root")
        assert result is None
        assert mmu.stack_depth == 1

    def test_nested_frames(self):
        mmu = MMU()

        # Push two levels
        mmu.push_frame("level 1")
        mmu.push_frame("level 2")

        assert mmu.stack_depth == 3
        assert mmu.current_frame.goal == "level 2"

        # Pop back
        mmu.pop_frame("done 2")
        assert mmu.stack_depth == 2
        assert mmu.current_frame.goal == "level 1"

        mmu.pop_frame("done 1")
        assert mmu.stack_depth == 1
        assert mmu.is_root_frame is True

    def test_assemble_context_simple(self):
        mmu = MMU()
        mmu.set_pinned(PinnedContext(system_rules="Be helpful"))
        mmu.add_user_message("Hello")
        mmu.add_assistant_message("Hi!")

        context = mmu.assemble_context()

        # Should have: system + user + assistant
        assert len(context) >= 3
        assert context[0]["role"] == "system"
        assert "helpful" in context[0]["content"]

    def test_assemble_context_with_subframe(self):
        mmu = MMU()
        mmu.set_pinned(PinnedContext(system_rules="Rules"))
        mmu.add_user_message("Main task")

        mmu.push_frame("subtask")
        mmu.add_user_message("Subtask question")

        context = mmu.assemble_context()

        # Should include pinned + root messages + subtask messages
        assert len(context) >= 4
        # Should have subtask goal marker
        assert any("[Subtask]" in str(m.get("content", "")) for m in context)

    def test_estimate_tokens(self):
        mmu = MMU()
        mmu.set_pinned(PinnedContext(system_rules="a" * 400))  # ~100 tokens
        mmu.add_user_message("b" * 200)  # ~50 tokens

        tokens = mmu.estimate_tokens()
        assert tokens > 100  # Should be significant

    def test_needs_compression(self):
        config = MMUConfig(max_context_tokens=100, compress_threshold=0.5)
        mmu = MMU(config=config)

        # Add content to exceed threshold
        mmu.add_user_message("x" * 400)  # ~100 tokens, exceeds 50 (50%)

        assert mmu.needs_compression() is True

    def test_get_state(self):
        mmu = MMU(process_id="proc-001")
        mmu.add_user_message("Hello")
        mmu.push_frame("subtask")

        state = mmu.get_state()

        assert state["process_id"] == "proc-001"
        assert state["stack_depth"] == 2
        assert state["total_messages"] >= 1

    def test_clear(self):
        mmu = MMU()
        mmu.set_pinned(PinnedContext(system_rules="Rules"))
        mmu.add_user_message("Hello")
        mmu.push_frame("subtask")

        mmu.clear()

        assert mmu.get_pinned() is None
        assert mmu.stack_depth == 1
        assert mmu.is_root_frame is True


# =============================================================================
# Integration Tests
# =============================================================================

class TestMMUIntegration:
    """Integration tests for MMU with realistic scenarios."""

    def test_realistic_conversation(self):
        """Simulate a realistic multi-turn conversation with subprocesses."""
        mmu = MMU(process_id="agent-001")

        # Setup pinned context
        mmu.set_pinned(PinnedContext(
            system_rules="You are a helpful coding assistant.",
            workspace_info="Working directory: /project\nLanguage: Python",
            capabilities="Available tools: Read, Write, Bash, Glob"
        ))

        # Main conversation
        mmu.add_user_message("Help me refactor the auth module")
        mmu.add_assistant_message("I'll help you refactor. Let me first explore the codebase.")

        # Spawn subprocess to explore
        mmu.push_frame("Explore the codebase to find auth-related files")
        mmu.add_user_message("Find all auth-related files")
        mmu.add_assistant_message("Found: src/auth/login.py, src/auth/session.py")
        result1 = mmu.pop_frame("Found 2 auth files: login.py, session.py")

        # Continue main conversation
        mmu.add_assistant_message(f"I found the auth files. {result1}")

        # Spawn another subprocess
        mmu.push_frame("Analyze the login.py file")
        mmu.add_user_message("Read and analyze login.py")
        mmu.add_assistant_message("The file has 3 functions: login(), logout(), verify()")
        mmu.pop_frame("login.py has 3 functions")

        # Final context assembly
        context = mmu.assemble_context()

        # Verify structure
        assert len(context) > 0
        assert context[0]["role"] == "system"

        # Verify pinned content is present
        system_content = context[0]["content"]
        assert "coding assistant" in system_content
        assert "/project" in system_content

    def test_deep_nesting(self):
        """Test deeply nested subprocess calls."""
        mmu = MMU()

        # Create 5 levels of nesting
        for i in range(5):
            mmu.push_frame(f"level {i}")
            mmu.add_user_message(f"Work at level {i}")

        assert mmu.stack_depth == 6  # root + 5 levels

        # Pop all frames
        for i in range(5):
            mmu.pop_frame(f"done level {4-i}")

        assert mmu.stack_depth == 1
        assert mmu.is_root_frame is True
