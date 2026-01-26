"""Tests for Context Stack module."""

import pytest
from pathlib import Path

from nimbus.core.context import (
    ContextFrame,
    ContextStack,
    FrameFactory,
    ContextStackOverflow,
    ContextStackUnderflow,
)
from nimbus.core.memory import (
    TieredMemoryManager,
    MemoryConfig,
    PinnedItem,
    SimpleMemory,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def basic_frame():
    """Create a basic context frame for testing."""
    return ContextFrame(
        id="test-frame",
        name="test",
        purpose="Test purpose",
        system_prompt="Test system prompt",
        tools=["Read", "Grep"],
        max_tokens=1000,
        data={"key1": "value1", "key2": "value2"},
    )


@pytest.fixture
def memory():
    """Create a TieredMemoryManager for testing."""
    config = MemoryConfig(
        pinned_budget=500,
        working_budget=2000,
        episodic_budget=4000,
        semantic_budget=2000,
    )
    return TieredMemoryManager(config=config, session_id="test-session")


@pytest.fixture
def context_stack():
    """Create a basic ContextStack without memory."""
    return ContextStack(max_depth=5)


@pytest.fixture
def context_stack_with_memory(memory):
    """Create a ContextStack with TieredMemoryManager."""
    return ContextStack(memory=memory, max_depth=5)


# =============================================================================
# ContextFrame Tests
# =============================================================================


class TestContextFrame:
    """Tests for ContextFrame dataclass."""

    def test_frame_creation(self, basic_frame):
        """Test basic frame creation."""
        assert basic_frame.id == "test-frame"
        assert basic_frame.name == "test"
        assert basic_frame.purpose == "Test purpose"
        assert basic_frame.system_prompt == "Test system prompt"
        assert basic_frame.tools == ["Read", "Grep"]
        assert basic_frame.max_tokens == 1000
        assert basic_frame.data == {"key1": "value1", "key2": "value2"}
        assert basic_frame.parent_id is None
        assert basic_frame.created_at is not None

    def test_frame_get(self, basic_frame):
        """Test frame.get() method."""
        assert basic_frame.get("key1") == "value1"
        assert basic_frame.get("key2") == "value2"
        assert basic_frame.get("nonexistent") is None
        assert basic_frame.get("nonexistent", "default") == "default"

    def test_frame_set(self, basic_frame):
        """Test frame.set() method."""
        basic_frame.set("new_key", "new_value")
        assert basic_frame.get("new_key") == "new_value"

        basic_frame.set("key1", "updated_value")
        assert basic_frame.get("key1") == "updated_value"

    def test_frame_derive_basic(self, basic_frame):
        """Test deriving a child frame."""
        child = basic_frame.derive("child-frame")

        assert child.name == "child-frame"
        assert child.parent_id == "test-frame"
        assert child.id.startswith("test-frame:")
        # By default, inherits system_prompt and tools
        assert child.system_prompt == basic_frame.system_prompt
        assert child.tools == basic_frame.tools

    def test_frame_derive_with_override(self, basic_frame):
        """Test deriving with field overrides."""
        child = basic_frame.derive(
            "child-frame",
            override={
                "purpose": "Child purpose",
                "system_prompt": "Child system prompt",
                "tools": ["Write"],
                "max_tokens": 500,
                "data": {"child_key": "child_value"},
            },
        )

        assert child.name == "child-frame"
        assert child.purpose == "Child purpose"
        assert child.system_prompt == "Child system prompt"
        assert child.tools == ["Write"]
        assert child.max_tokens == 500
        assert child.get("child_key") == "child_value"
        # Inherited data is NOT present without inherit parameter
        assert child.get("key1") is None

    def test_frame_derive_with_inherit(self, basic_frame):
        """Test deriving with data inheritance."""
        child = basic_frame.derive(
            "child-frame",
            inherit=["key1"],  # Only inherit key1
            override={
                "data": {"new_key": "new_value"},
            },
        )

        # key1 is inherited
        assert child.get("key1") == "value1"
        # key2 is NOT inherited
        assert child.get("key2") is None
        # new_key is added from override
        assert child.get("new_key") == "new_value"

    def test_frame_derive_merge_data(self, basic_frame):
        """Test that override data takes precedence over inherited data."""
        child = basic_frame.derive(
            "child-frame",
            inherit=["key1", "key2"],
            override={
                "data": {"key1": "overridden_value"},  # Override inherited value
            },
        )

        assert child.get("key1") == "overridden_value"
        assert child.get("key2") == "value2"


# =============================================================================
# ContextStack Tests
# =============================================================================


class TestContextStackBasic:
    """Tests for basic ContextStack operations."""

    def test_stack_init(self, context_stack):
        """Test stack initialization with root frame."""
        assert context_stack.depth == 1
        assert context_stack.current.name == "agent"
        assert context_stack.current.id == "root"
        assert context_stack.root is context_stack.current

    def test_push_pop_basic(self, context_stack, basic_frame):
        """Test basic push and pop operations."""
        # Push frame
        context_stack.push(basic_frame)
        assert context_stack.depth == 2
        assert context_stack.current.name == "test"
        assert context_stack.current.parent_id == "root"

        # Pop frame
        popped = context_stack.pop()
        assert popped.name == "test"
        assert context_stack.depth == 1
        assert context_stack.current.name == "agent"

    def test_push_sets_parent_id(self, context_stack):
        """Test that push sets parent_id if not already set."""
        frame = ContextFrame(id="frame1", name="frame1")
        context_stack.push(frame)
        assert frame.parent_id == "root"

        frame2 = ContextFrame(id="frame2", name="frame2")
        context_stack.push(frame2)
        assert frame2.parent_id == "frame1"

    def test_push_preserves_explicit_parent_id(self, context_stack):
        """Test that push preserves explicitly set parent_id."""
        frame = ContextFrame(id="frame1", name="frame1", parent_id="custom-parent")
        context_stack.push(frame)
        assert frame.parent_id == "custom-parent"

    def test_max_depth_overflow(self, context_stack):
        """Test that ContextStackOverflow is raised at max depth."""
        # Stack starts with 1 (root), max_depth=5
        for i in range(4):
            context_stack.push(ContextFrame(id=f"frame-{i}", name=f"frame-{i}"))

        assert context_stack.depth == 5

        # Next push should raise overflow
        with pytest.raises(ContextStackOverflow) as excinfo:
            context_stack.push(ContextFrame(id="overflow", name="overflow"))

        assert "max depth 5 exceeded" in str(excinfo.value)

    def test_pop_root_underflow(self, context_stack):
        """Test that ContextStackUnderflow is raised when popping root."""
        assert context_stack.depth == 1

        with pytest.raises(ContextStackUnderflow) as excinfo:
            context_stack.pop()

        assert "Cannot pop root frame" in str(excinfo.value)


class TestContextStackFrameContextManager:
    """Tests for async context manager frame() method."""

    @pytest.mark.asyncio
    async def test_frame_context_manager(self, context_stack, basic_frame):
        """Test async context manager for frame lifecycle."""
        assert context_stack.depth == 1

        async with context_stack.frame(basic_frame) as f:
            assert context_stack.depth == 2
            assert context_stack.current.name == "test"
            assert f is basic_frame

        # Frame should be popped after exit
        assert context_stack.depth == 1
        assert context_stack.current.name == "agent"

    @pytest.mark.asyncio
    async def test_frame_context_manager_exception(self, context_stack, basic_frame):
        """Test that frame is popped even on exception."""
        assert context_stack.depth == 1

        with pytest.raises(ValueError):
            async with context_stack.frame(basic_frame):
                assert context_stack.depth == 2
                raise ValueError("Test exception")

        # Frame should still be popped
        assert context_stack.depth == 1

    @pytest.mark.asyncio
    async def test_nested_frames(self, context_stack):
        """Test nested frame context managers."""
        frame1 = ContextFrame(id="frame1", name="frame1")
        frame2 = ContextFrame(id="frame2", name="frame2")

        assert context_stack.depth == 1

        async with context_stack.frame(frame1):
            assert context_stack.depth == 2
            assert context_stack.current.name == "frame1"

            async with context_stack.frame(frame2):
                assert context_stack.depth == 3
                assert context_stack.current.name == "frame2"

            assert context_stack.depth == 2
            assert context_stack.current.name == "frame1"

        assert context_stack.depth == 1


class TestContextStackGetView:
    """Tests for get_view() method."""

    def test_get_view_basic(self, context_stack, basic_frame):
        """Test basic view generation."""
        context_stack.push(basic_frame)
        view = context_stack.get_view(include_memory=False)

        assert "Test system prompt" in view
        assert "key1: value1" in view
        assert "key2: value2" in view
        assert "Available Tools" in view
        assert "Read, Grep" in view

    def test_get_view_skips_private_data(self, context_stack):
        """Test that private data (starting with _) is skipped."""
        frame = ContextFrame(
            id="test",
            name="test",
            data={"public_key": "public", "_private_key": "private"},
        )
        context_stack.push(frame)
        view = context_stack.get_view(include_memory=False)

        assert "public_key: public" in view
        assert "_private_key" not in view
        assert "private" not in view

    def test_get_view_empty_frame(self, context_stack):
        """Test view generation for empty frame."""
        frame = ContextFrame(id="empty", name="empty")
        context_stack.push(frame)
        view = context_stack.get_view(include_memory=False)

        # Should return empty or minimal content
        assert isinstance(view, str)

    @pytest.mark.asyncio
    async def test_get_view_with_memory_planner(self, context_stack_with_memory):
        """Test view generation for planner frame with memory."""
        stack = context_stack_with_memory

        # Add some history to memory
        await stack._memory.add_turn("user", "Hello")
        await stack._memory.add_turn("assistant", "Hi there!")

        # Push planner frame
        planner_frame = FrameFactory.planner(
            goal="Read main.py",
            available_skills={"Read", "Grep"},
        )
        stack.push(planner_frame)

        view = stack.get_view(include_memory=True)

        # Planner frame should have minimal context
        assert "goal: Read main.py" in view
        assert len(view) <= 2000  # Should be truncated to max_tokens

    @pytest.mark.asyncio
    async def test_get_view_with_memory_synthesize(self, context_stack_with_memory):
        """Test view generation for synthesize frame with memory."""
        stack = context_stack_with_memory

        # Add some history
        await stack._memory.add_turn("user", "What is this file?")
        await stack._memory.add_turn("assistant", "Let me check...")

        # Push synthesize frame
        synth_frame = FrameFactory.synthesize(
            message="What is this file?",
            upstream_results={"read_result": "file contents..."},
        )
        stack.push(synth_frame)

        view = stack.get_view(include_memory=True)

        # Synthesize frame should include conversation history
        assert "message: What is this file?" in view


class TestContextStackSubagent:
    """Tests for subagent frame creation."""

    def test_create_subagent_frame_eye(self, context_stack):
        """Test creating eye subagent frame."""
        # First add some data to root frame
        context_stack.current.set("workspace", "/app")
        context_stack.current.set("session_id", "sess-123")

        frame = context_stack.create_subagent_frame(
            subagent_type="eye",
            task_prompt="Explore the codebase",
            allowed_tools=["Read", "Glob", "Grep"],
        )

        assert frame.name == "subagent:eye"
        assert frame.purpose == "Code exploration and discovery"
        assert frame.max_tokens == 1500
        assert frame.tools == ["Read", "Glob", "Grep"]
        assert frame.get("task") == "Explore the codebase"
        # Inherited data
        assert frame.get("workspace") == "/app"
        assert frame.get("session_id") == "sess-123"

    def test_create_subagent_frame_body(self, context_stack):
        """Test creating body subagent frame."""
        context_stack.current.set("workspace", "/app")

        frame = context_stack.create_subagent_frame(
            subagent_type="body",
            task_prompt="Implement the feature",
            allowed_tools=["Read", "Write", "Edit", "Bash"],
        )

        assert frame.name == "subagent:body"
        assert frame.purpose == "Code implementation"
        assert frame.max_tokens == 2000
        assert "coding agent" in frame.system_prompt.lower()

    def test_create_subagent_frame_all_types(self, context_stack):
        """Test creating all subagent types."""
        types = ["eye", "body", "mind", "tongue", "nose", "ear"]
        expected_tokens = {
            "eye": 1500,
            "body": 2000,
            "mind": 2000,
            "tongue": 1500,
            "nose": 1500,
            "ear": 1000,
        }

        for subagent_type in types:
            frame = context_stack.create_subagent_frame(
                subagent_type=subagent_type,
                task_prompt=f"Task for {subagent_type}",
                allowed_tools=["Read"],
            )

            assert frame.name == f"subagent:{subagent_type}"
            assert frame.max_tokens == expected_tokens[subagent_type]
            assert frame.parent_id == "root"

    def test_create_subagent_frame_unknown_type(self, context_stack):
        """Test that unknown type falls back to eye config."""
        frame = context_stack.create_subagent_frame(
            subagent_type="unknown",
            task_prompt="Unknown task",
            allowed_tools=["Read"],
        )

        # Falls back to eye config
        assert frame.name == "subagent:unknown"
        assert frame.max_tokens == 1500  # Same as eye


class TestContextStackStackTrace:
    """Tests for get_stack_trace() method."""

    def test_get_stack_trace_basic(self, context_stack, basic_frame):
        """Test stack trace generation."""
        context_stack.push(basic_frame)

        trace = context_stack.get_stack_trace()

        assert len(trace) == 2

        # Root frame
        assert trace[0]["id"] == "root"
        assert trace[0]["name"] == "agent"
        assert trace[0]["depth"] == 0

        # Test frame
        assert trace[1]["id"] == "test-frame"
        assert trace[1]["name"] == "test"
        assert trace[1]["depth"] == 1
        assert trace[1]["tools_count"] == 2
        assert "key1" in trace[1]["data_keys"]

    def test_get_stack_trace_includes_parent_id(self, context_stack, basic_frame):
        """Test that stack trace includes parent_id."""
        context_stack.push(basic_frame)
        trace = context_stack.get_stack_trace()

        assert trace[0]["parent_id"] is None  # Root has no parent
        assert trace[1]["parent_id"] == "root"


# =============================================================================
# FrameFactory Tests
# =============================================================================


class TestFrameFactoryPlanner:
    """Tests for FrameFactory.planner()."""

    def test_planner_frame(self):
        """Test planner frame creation."""
        frame = FrameFactory.planner(
            goal="Read the main.py file",
            available_skills={"Read", "Grep", "Glob"},
        )

        assert frame.name == "planner"
        assert frame.id.startswith("planner:")
        assert frame.purpose == "Task planning and DAG generation"
        assert frame.max_tokens == 500  # Planner is minimal
        assert frame.get("goal") == "Read the main.py file"
        assert set(frame.tools) == {"Read", "Grep", "Glob"}
        assert "task planner" in frame.system_prompt.lower()

    def test_planner_frame_empty_skills(self):
        """Test planner frame with no skills."""
        frame = FrameFactory.planner(goal="Test", available_skills=set())

        assert frame.tools == []
        assert frame.get("goal") == "Test"


class TestFrameFactoryToolExecution:
    """Tests for FrameFactory.tool_execution()."""

    def test_tool_execution_frame(self):
        """Test tool execution frame creation."""
        frame = FrameFactory.tool_execution(
            tool_name="Read",
            params={"path": "/app/main.py"},
            workspace=Path("/app"),
        )

        assert frame.name == "tool:Read"
        assert frame.id.startswith("tool:Read:")
        assert frame.purpose == "Execute Read tool"
        assert frame.max_tokens == 1000
        assert frame.tools == ["Read"]
        assert frame.get("workspace") == "/app"
        assert frame.get("params") == {"path": "/app/main.py"}


class TestFrameFactorySynthesize:
    """Tests for FrameFactory.synthesize()."""

    def test_synthesize_frame(self):
        """Test synthesize frame creation."""
        frame = FrameFactory.synthesize(
            message="What is in main.py?",
            upstream_results={
                "read_result": "def main(): pass",
                "grep_result": ["line 1", "line 2"],
            },
        )

        assert frame.name == "synthesize"
        assert frame.id.startswith("synthesize:")
        assert frame.purpose == "Generate final response to user"
        assert frame.max_tokens == 4000  # Synthesize has larger budget
        assert frame.tools == []  # No tools for synthesize
        assert frame.get("message") == "What is in main.py?"
        assert "read_result" in frame.get("results")


class TestFrameFactoryOther:
    """Tests for other FrameFactory methods."""

    def test_context_analyzer_frame(self):
        """Test context analyzer frame creation."""
        frame = FrameFactory.context_analyzer()

        assert frame.name == "analyzer"
        assert frame.id.startswith("analyzer:")
        assert frame.max_tokens == 300  # Most minimal

    def test_router_frame(self):
        """Test router frame creation."""
        frame = FrameFactory.router(goal="Route this request")

        assert frame.name == "router"
        assert frame.id.startswith("router:")
        assert frame.max_tokens == 400
        assert frame.get("goal") == "Route this request"


# =============================================================================
# Integration Tests
# =============================================================================


class TestContextStackIntegration:
    """Integration tests for context stack with memory."""

    @pytest.mark.asyncio
    async def test_full_workflow(self, context_stack_with_memory):
        """Test complete workflow: planner -> tool -> synthesize."""
        stack = context_stack_with_memory

        # Setup memory
        stack._memory.pin(
            PinnedItem(id="instruction", type="instruction", content="Be helpful")
        )
        await stack._memory.add_turn("user", "Read main.py")

        # 1. Planner phase
        planner_frame = FrameFactory.planner(
            goal="Read main.py", available_skills={"Read"}
        )
        async with stack.frame(planner_frame):
            assert stack.depth == 2
            view = stack.get_view()
            assert stack.current.max_tokens == 500

        # 2. Tool execution phase
        tool_frame = FrameFactory.tool_execution(
            tool_name="Read",
            params={"path": "main.py"},
            workspace=Path("/app"),
        )
        async with stack.frame(tool_frame):
            assert stack.depth == 2
            view = stack.get_view()
            assert stack.current.max_tokens == 1000

        # 3. Synthesize phase
        synth_frame = FrameFactory.synthesize(
            message="Read main.py",
            upstream_results={"content": "print('hello')"},
        )
        async with stack.frame(synth_frame):
            assert stack.depth == 2
            view = stack.get_view()
            assert stack.current.max_tokens == 4000

        # Back to root
        assert stack.depth == 1

    @pytest.mark.asyncio
    async def test_subagent_isolation(self, context_stack_with_memory):
        """Test that subagent frames are properly isolated."""
        stack = context_stack_with_memory

        # Set up parent context
        stack.current.set("workspace", "/app")
        stack.current.set("session_id", "parent-sess")
        stack.current.set("sensitive_data", "should_not_inherit")

        # Create subagent frame
        subagent_frame = stack.create_subagent_frame(
            subagent_type="eye",
            task_prompt="Explore",
            allowed_tools=["Read"],
        )

        async with stack.frame(subagent_frame):
            # Inherited data
            assert stack.current.get("workspace") == "/app"
            assert stack.current.get("session_id") == "parent-sess"
            # Non-inherited data
            assert stack.current.get("sensitive_data") is None
            # Subagent-specific data
            assert stack.current.get("task") == "Explore"

        assert stack.depth == 1

    def test_stack_with_simple_memory(self):
        """Test context stack with SimpleMemory."""
        memory = SimpleMemory(max_turns=10)
        memory.add_turn("user", "Hello")
        memory.add_turn("assistant", "Hi!")

        stack = ContextStack(memory=memory)

        synth_frame = FrameFactory.synthesize(
            message="test", upstream_results={}
        )
        stack.push(synth_frame)

        view = stack.get_view(include_memory=True)
        # SimpleMemory context should be included
        assert isinstance(view, str)
