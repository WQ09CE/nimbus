"""
Tests for Nimbus v2 New Features:
1. Session Persistence
2. Context Stack 提炼
3. LLM Compaction
"""

import asyncio
import tempfile
from pathlib import Path
import pytest

from nimbus.core.session import SessionManager, InMemorySessionManager, SessionEntry
from nimbus.core.memory.context import Message
from nimbus.core.memory.mmu import MMU, MMUConfig, ToolCallMarker
from nimbus.core.compaction import (
    CompactionConfig,
    CompactionEngine,
    SimpleCompactionEngine,
    ContextStackAwareCompaction,
)


# =============================================================================
# Session Persistence Tests
# =============================================================================

class TestSessionManager:
    """Session 持久化测试"""
    
    def test_create_session(self):
        """测试创建新会话"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = SessionManager(session_dir=Path(tmpdir))
            session_id = sm.new_session()
            
            assert session_id is not None
            assert len(session_id) == 12
            assert sm.get_session_file() is not None
    
    def test_append_message(self):
        """测试追加消息"""
        sm = InMemorySessionManager()
        
        # 追加用户消息
        entry_id = sm.append_message(Message(role="user", content="Hello"))
        assert entry_id is not None
        
        # 追加助手消息
        entry_id2 = sm.append_message(Message(role="assistant", content="Hi there!"))
        assert entry_id2 is not None
        
        # 检查分支
        branch = sm.get_branch()
        assert len(branch) == 2
        assert branch[0].type == "user"
        assert branch[1].type == "assistant"
    
    def test_branch_navigation(self):
        """测试分支导航"""
        sm = InMemorySessionManager()
        
        # 添加一些消息
        sm.append_message(Message(role="user", content="Message 1"))
        entry_a = sm.append_message(Message(role="assistant", content="Response 1"))
        sm.append_message(Message(role="user", content="Message 2"))
        
        # 导航回 entry_a
        sm.navigate_to(entry_a)
        
        # 从 entry_a 继续添加（创建分支）
        sm.append_message(Message(role="user", content="Message 2 (branch)"))
        
        # 检查新分支
        branch = sm.get_branch()
        assert len(branch) == 3
        assert branch[-1].data["content"] == "Message 2 (branch)"
    
    def test_persistence(self):
        """测试持久化和恢复"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建会话并添加消息
            sm1 = SessionManager(session_dir=Path(tmpdir))
            sm1.new_session()
            sm1.append_message(Message(role="user", content="Test message"))
            sm1.append_message(Message(role="assistant", content="Test response"))
            
            session_file = sm1.get_session_file()
            
            # 创建新的 SessionManager 并加载
            sm2 = SessionManager(session_dir=Path(tmpdir))
            assert sm2.load_session(session_file)
            
            # 验证消息已恢复
            branch = sm2.get_branch()
            assert len(branch) == 2
            assert branch[0].data["content"] == "Test message"
    
    def test_compaction_entry(self):
        """测试压缩条目"""
        sm = InMemorySessionManager()
        
        sm.append_message(Message(role="user", content="Hello"))
        sm.append_compaction(
            summary="Previous conversation about greetings",
            first_kept_entry_id="abc",
            tokens_before=1000,
            details={"method": "llm"},
        )
        
        branch = sm.get_branch()
        assert len(branch) == 2
        assert branch[1].type == "compaction"
        assert "greetings" in branch[1].data["summary"]


# =============================================================================
# Context Stack 提炼 Tests
# =============================================================================

class TestContextStackExtraction:
    """Context Stack 提炼测试"""
    
    def test_mark_tool_call(self):
        """测试标记 tool call"""
        mmu = MMU(config=MMUConfig(auto_detect_failures=False))
        
        # 添加一些 tool results
        mmu.add_tool_result("tc-1", "Read", "File content...")
        mmu.add_tool_result("tc-2", "Read", "[Error] File not found")
        
        # 手动标记
        mmu.mark_tool_call("tc-2", "failed", reason="file_not_found")
        
        # 检查标记
        markers = mmu.get_tool_markers()
        assert "tc-2" in markers
        assert markers["tc-2"].value == "failed"
    
    def test_auto_detect_failures(self):
        """测试自动检测失败"""
        mmu = MMU(config=MMUConfig(auto_detect_failures=True))
        
        # 添加失败的 tool result（应该自动检测）
        mmu.add_tool_result("tc-1", "Read", "[Error] Permission denied")
        
        # 检查是否被自动标记
        assert mmu.get_discardable_count() == 1
    
    def test_filter_discardable_messages(self):
        """测试过滤无价值消息"""
        mmu = MMU(config=MMUConfig(auto_detect_failures=False))
        
        # 添加一些消息
        mmu.add_user_message("Find the auth module")
        mmu.add_assistant_with_tool_calls(None, [{"id": "tc-1", "function": {"name": "Read"}}])
        mmu.add_tool_result("tc-1", "Read", "Not found here")
        mmu.add_assistant_with_tool_calls(None, [{"id": "tc-2", "function": {"name": "Read"}}])
        mmu.add_tool_result("tc-2", "Read", "Found it!")
        
        # 标记 tc-1 为探索性调用
        mmu.mark_tool_call("tc-1", "exploratory", reason="wrong_direction")
        
        # 组装上下文（应该过滤 tc-1）
        context = mmu.assemble_context(filter_discardable=True)
        
        # 检查结果：tc-1 的 tool result 应该被过滤
        tool_results = [m for m in context if m.get("role") == "tool"]
        assert len(tool_results) == 1
        assert "Found it!" in tool_results[0]["content"]
    
    def test_pop_frame_extraction(self):
        """测试 pop_frame 时的内容提炼"""
        mmu = MMU(config=MMUConfig(
            auto_extract_on_pop=True,
            auto_detect_failures=True,
        ))
        
        # 创建一个子 frame
        mmu.push_frame("Find the auth module")
        
        # 模拟一些探索（有失败的）
        mmu.add_user_message("Find the auth module")
        mmu.add_tool_result("tc-1", "Read", "[Error] Not found in /src")
        mmu.add_tool_result("tc-2", "Read", "Found auth module at /lib/auth.py!")
        mmu.add_assistant_message("Found the auth module at /lib/auth.py")
        
        # Pop frame（应该自动提炼）
        result = mmu.pop_frame()
        
        # 检查结果包含有价值的内容
        assert result is not None
        assert "auth" in result.lower() or "Found" in result
        
        # 检查父 frame 收到了精炼的结果
        messages = mmu.current_frame.messages
        assert any("Subtask completed" in str(m.content) for m in messages)
    
    def test_batch_mark_recent_calls(self):
        """测试批量标记最近的 tool calls"""
        mmu = MMU(config=MMUConfig(auto_detect_failures=False))
        
        # 添加多个 tool results
        for i in range(5):
            mmu.add_tool_result(f"tc-{i}", "Read", f"Result {i}")
        
        # 批量标记最近 3 个为 exploratory
        marked = mmu.mark_recent_tool_calls("exploratory", count=3)
        
        assert marked == 3
        assert mmu.get_discardable_count() == 3


# =============================================================================
# Compaction Tests
# =============================================================================

class TestCompaction:
    """Compaction 测试"""
    
    def test_should_compact(self):
        """测试 should_compact 检测"""
        engine = CompactionEngine(config=CompactionConfig(
            threshold_ratio=0.8,
            min_messages_to_compact=3,
        ))
        
        # 创建一些消息（每条约 250 tokens）
        messages = [
            Message(role="user", content="A" * 1000),
            Message(role="assistant", content="B" * 1000),
            Message(role="user", content="C" * 1000),
        ]
        # 总共约 750 tokens
        
        # 低 token 限制应该触发（750 > 500 * 0.8 = 400）
        assert engine.should_compact(messages, max_tokens=500) is True
        
        # 高 token 限制不应该触发（750 < 100000 * 0.8）
        assert engine.should_compact(messages, max_tokens=100000) is False
    
    def test_prepare_compaction(self):
        """测试准备压缩"""
        engine = CompactionEngine(config=CompactionConfig(
            keep_recent_messages=2,
            min_messages_to_compact=2,
        ))
        
        messages = [
            Message(role="user", content="Message 1"),
            Message(role="assistant", content="Response 1"),
            Message(role="user", content="Message 2"),
            Message(role="assistant", content="Response 2"),
            Message(role="user", content="Message 3"),
        ]
        
        prep = engine.prepare(messages)
        
        assert prep is not None
        assert len(prep.messages_to_compact) == 3
        assert len(prep.messages_to_keep) == 2
    
    @pytest.mark.asyncio
    async def test_simple_compaction(self):
        """测试简单（规则）压缩"""
        engine = SimpleCompactionEngine(config=CompactionConfig(
            keep_recent_messages=2,
            min_messages_to_compact=2,
        ))
        
        messages = [
            Message(role="user", content="Message 1"),
            Message(role="assistant", content="Response 1"),
            Message(role="user", content="Message 2"),
            Message(role="assistant", content="Response 2"),
            Message(role="user", content="Message 3"),
        ]
        
        new_messages, result = await engine.compact(messages)
        
        # 检查结果
        assert result.messages_removed == 3
        assert len(new_messages) == 3  # 1 summary + 2 kept
        assert new_messages[0].role == "system"  # Summary message
    
    def test_context_stack_aware_compaction(self):
        """测试 Context Stack 感知的压缩"""
        csc = ContextStackAwareCompaction()
        
        # 创建一些消息，包括失败的 tool calls
        messages = [
            Message(role="user", content="Find file"),
            Message(role="assistant", content=None, tool_calls=[{"id": "tc-1"}]),
            Message(role="tool", content="[Error] Not found", tool_call_id="tc-1"),
            Message(role="assistant", content=None, tool_calls=[{"id": "tc-2"}]),
            Message(role="tool", content="Found it!", tool_call_id="tc-2"),
        ]
        
        # 自动检测失败
        count = csc.auto_detect_failed_tools(messages)
        assert count == 1
        
        # 过滤消息
        filtered = csc.filter_messages(messages)
        
        # tc-1 的 tool result 应该被过滤
        tool_results = [m for m in filtered if m.role == "tool"]
        assert len(tool_results) == 1
        assert "Found it!" in tool_results[0].content


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """集成测试"""
    
    def test_mmu_with_session(self):
        """测试 MMU 和 Session 集成"""
        sm = InMemorySessionManager()
        mmu = MMU(config=MMUConfig())
        
        # 添加消息到 MMU 并同步到 Session
        mmu.add_user_message("Hello")
        sm.append_message(Message(role="user", content="Hello"))
        
        mmu.add_assistant_message("Hi there!")
        sm.append_message(Message(role="assistant", content="Hi there!"))
        
        # 验证两者一致
        assert len(mmu.current_frame.messages) == 2
        assert len(sm.get_branch()) == 2
    
    def test_context_stack_full_flow(self):
        """测试完整的 Context Stack 流程"""
        mmu = MMU(config=MMUConfig(
            auto_extract_on_pop=True,
            auto_detect_failures=True,
        ))
        
        # 主任务
        mmu.add_user_message("Refactor the auth module")
        
        # 子任务 1：探索代码
        frame1_id = mmu.push_frame("Explore code structure")
        mmu.add_tool_result("tc-1", "Read", "[Error] /src/auth.py not found")
        mmu.add_tool_result("tc-2", "Read", "Found at /lib/auth/main.py")
        mmu.add_assistant_message("Auth module is at /lib/auth/")
        
        # Pop（应该提炼出有价值的内容）
        result1 = mmu.pop_frame()
        assert "auth" in result1.lower()
        
        # 检查主 frame 的上下文是干净的
        context = mmu.assemble_context()
        
        # 不应该包含失败的 tool call 详情
        context_str = str(context)
        assert "[Error] /src/auth.py not found" not in context_str


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
