"""
Tests for TUI Dashboard module.

These tests verify the TUI Dashboard components work correctly.
"""

import asyncio
from datetime import datetime

import pytest

from nimbus.tui import (
    DashboardConfig,
    TUIDashboard,
    StateManager,
    DashboardState,
    ChatMessage,
    LayoutManager,
    InputHandler,
)
from nimbus.tui.widgets import (
    ChatPanel,
    ProcessWidget,
    DAGWidget,
    VCPUWidget,
    MemoryWidget,
    StatusBar,
)
from nimbus.tui.widgets.status import HeaderBar


# =============================================================================
# Test Config
# =============================================================================


class TestDashboardConfig:
    """Tests for DashboardConfig."""

    def test_default_config(self):
        """Test default config values."""
        config = DashboardConfig()
        assert config.refresh_rate == 4.0
        assert config.chat_ratio == 0.7
        assert config.max_chat_history == 100
        assert config.max_events == 50
        assert config.debounce_ms == 100

    def test_custom_config(self):
        """Test custom config values."""
        config = DashboardConfig(
            refresh_rate=8.0,
            chat_ratio=0.65,
            max_chat_history=200,
        )
        assert config.refresh_rate == 8.0
        assert config.chat_ratio == 0.65
        assert config.max_chat_history == 200


# =============================================================================
# Test State Manager
# =============================================================================


class TestStateManager:
    """Tests for StateManager."""

    def test_initial_state(self):
        """Test initial state is empty."""
        mgr = StateManager()
        state = mgr.get_state()

        assert isinstance(state, DashboardState)
        assert state.processes == {}
        assert state.messages == []
        assert state.is_processing is False

    def test_add_user_message(self):
        """Test adding user messages."""
        mgr = StateManager()
        mgr.add_user_message("Hello, world!")

        state = mgr.get_state()
        assert len(state.messages) == 1
        assert state.messages[0].role == "user"
        assert state.messages[0].content == "Hello, world!"

    def test_add_agent_message(self):
        """Test adding agent messages."""
        mgr = StateManager()
        mgr.add_agent_message("Hello back!")

        state = mgr.get_state()
        assert len(state.messages) == 1
        assert state.messages[0].role == "agent"

    def test_add_tool_message(self):
        """Test adding tool messages."""
        mgr = StateManager()
        mgr.add_tool_message("Read", "File contents...", "success")

        state = mgr.get_state()
        assert len(state.messages) == 1
        assert state.messages[0].role == "tool"
        assert state.messages[0].tool_name == "Read"
        assert state.messages[0].tool_status == "success"

    def test_add_system_message(self):
        """Test adding system messages."""
        mgr = StateManager()
        mgr.add_system_message("System notification")

        state = mgr.get_state()
        assert len(state.messages) == 1
        assert state.messages[0].role == "system"

    def test_message_limit(self):
        """Test message history limit."""
        mgr = StateManager(max_messages=5)

        for i in range(10):
            mgr.add_user_message(f"Message {i}")

        state = mgr.get_state()
        assert len(state.messages) == 5
        # Should keep the last 5 messages
        assert state.messages[0].content == "Message 5"

    def test_set_processing(self):
        """Test setting processing state."""
        mgr = StateManager()

        mgr.set_processing(True, "Running task...")
        state = mgr.get_state()
        assert state.is_processing is True
        assert state.status_text == "Running task..."

        mgr.set_processing(False)
        state = mgr.get_state()
        assert state.is_processing is False

    def test_clear_messages(self):
        """Test clearing messages."""
        mgr = StateManager()
        mgr.add_user_message("Test")
        mgr.add_agent_message("Response")

        mgr.clear_messages()
        state = mgr.get_state()
        assert len(state.messages) == 0

    def test_subscribe(self):
        """Test subscribing to state changes."""
        mgr = StateManager()
        callback_count = [0]

        def callback(state):
            callback_count[0] += 1

        mgr.subscribe(callback)
        mgr.add_user_message("Test")

        assert callback_count[0] == 1


# =============================================================================
# Test Widgets
# =============================================================================


class TestChatPanel:
    """Tests for ChatPanel."""

    def test_render_empty(self):
        """Test rendering empty chat."""
        panel = ChatPanel()
        rendered = panel.render([])

        assert rendered.title == "Chat"

    def test_render_with_messages(self):
        """Test rendering with messages."""
        panel = ChatPanel()
        messages = [
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="agent", content="Hi there!"),
            ChatMessage(role="tool", content="Output", tool_name="Read", tool_status="success"),
            ChatMessage(role="system", content="System message"),
        ]
        rendered = panel.render(messages)

        assert rendered.title == "Chat"


class TestProcessWidget:
    """Tests for ProcessWidget."""

    def test_render_empty(self):
        """Test rendering empty process list."""
        widget = ProcessWidget()
        rendered = widget.render({})

        assert rendered.title == "Processes"

    def test_render_with_processes(self):
        """Test rendering with processes."""
        widget = ProcessWidget()
        processes = {
            "proc-123": {"state": "RUNNING", "role": "eye"},
            "proc-456": {"state": "PENDING", "role": "body"},
            "proc-789": {"state": "SUCCEEDED", "role": ""},
        }
        rendered = widget.render(processes)

        assert rendered.title == "Processes"


class TestDAGWidget:
    """Tests for DAGWidget."""

    def test_render_no_dag(self):
        """Test rendering with no active DAG."""
        widget = DAGWidget()
        rendered = widget.render(None, None, None)

        assert rendered.title == "DAG"

    def test_render_with_dag(self):
        """Test rendering with active DAG."""
        widget = DAGWidget()
        status = {"total": 5, "succeeded": 2, "running": 1, "pending": 2, "failed": 0}
        tasks = {"SCAN": "SUCCEEDED", "PLAN": "RUNNING", "CODE": "PENDING"}
        rendered = widget.render("dag-123", status, tasks)

        assert "dag-123" in rendered.title


class TestVCPUWidget:
    """Tests for VCPUWidget."""

    def test_render_idle(self):
        """Test rendering idle VCPU."""
        widget = VCPUWidget()
        rendered = widget.render(0, 50, False, {})

        assert rendered.title == "VCPU"

    def test_render_running(self):
        """Test rendering running VCPU."""
        widget = VCPUWidget()
        timing = {"think": 1200, "execute": 500}
        rendered = widget.render(5, 50, True, timing)

        assert rendered.title == "VCPU"


class TestMemoryWidget:
    """Tests for MemoryWidget."""

    def test_render_empty(self):
        """Test rendering with zero tokens."""
        widget = MemoryWidget()
        rendered = widget.render(0, 0, 0)

        assert rendered.title == "Memory"

    def test_render_with_data(self):
        """Test rendering with data."""
        widget = MemoryWidget()
        rendered = widget.render(8200, 128000, 2)

        assert rendered.title == "Memory"


class TestStatusBar:
    """Tests for StatusBar."""

    def test_render_ready(self):
        """Test rendering ready state."""
        widget = StatusBar()
        rendered = widget.render(False)

        assert rendered is not None

    def test_render_processing(self):
        """Test rendering processing state."""
        widget = StatusBar()
        rendered = widget.render(True, "Running task...")

        assert rendered is not None


class TestHeaderBar:
    """Tests for HeaderBar."""

    def test_render_default(self):
        """Test rendering with default title."""
        widget = HeaderBar()
        rendered = widget.render()

        assert rendered is not None

    def test_render_custom_title(self):
        """Test rendering with custom title."""
        widget = HeaderBar()
        rendered = widget.render("Custom Title")

        assert rendered is not None


# =============================================================================
# Test Layout Manager
# =============================================================================


class TestLayoutManager:
    """Tests for LayoutManager."""

    def test_create_layout(self):
        """Test creating layout."""
        mgr = LayoutManager()
        layout = mgr.get_layout()

        assert layout is not None

    def test_update_layout(self):
        """Test updating layout."""
        from rich.panel import Panel
        from rich.text import Text

        mgr = LayoutManager()

        # Create dummy content
        header = Panel("Header")
        chat = Panel("Chat")
        processes = Panel("Processes")
        dag = Panel("DAG")
        vcpu = Panel("VCPU")
        memory = Panel("Memory")
        footer = Panel("Footer")

        updated = mgr.update(
            header=header,
            chat=chat,
            processes=processes,
            dag=dag,
            vcpu=vcpu,
            memory=memory,
            footer=footer,
        )

        assert updated is not None


# =============================================================================
# Test Input Handler
# =============================================================================


class TestInputHandler:
    """Tests for InputHandler."""

    def test_create_handler(self):
        """Test creating input handler."""
        handler = InputHandler()

        assert handler is not None
        assert handler.is_running is False

    def test_history_management(self):
        """Test history management."""
        handler = InputHandler(max_history=5)

        # Simulate adding to history
        handler._add_to_history("cmd1")
        handler._add_to_history("cmd2")
        handler._add_to_history("cmd3")

        history = handler.get_history()
        assert len(history) == 3
        assert history == ["cmd1", "cmd2", "cmd3"]

    def test_history_dedup(self):
        """Test history deduplication."""
        handler = InputHandler()

        handler._add_to_history("cmd1")
        handler._add_to_history("cmd1")  # Duplicate

        history = handler.get_history()
        assert len(history) == 1

    def test_clear_history(self):
        """Test clearing history."""
        handler = InputHandler()
        handler._add_to_history("cmd1")
        handler._add_to_history("cmd2")

        handler.clear_history()
        assert handler.get_history() == []


# =============================================================================
# Test TUIDashboard
# =============================================================================


class TestTUIDashboard:
    """Tests for TUIDashboard."""

    def test_create_dashboard(self):
        """Test creating dashboard without AgentOS."""
        dashboard = TUIDashboard()

        assert dashboard is not None
        assert dashboard.os is None

    def test_create_with_config(self):
        """Test creating dashboard with config."""
        config = DashboardConfig(refresh_rate=8.0)
        dashboard = TUIDashboard(config=config)

        assert dashboard.config.refresh_rate == 8.0

    def test_update_layout(self):
        """Test layout update."""
        dashboard = TUIDashboard()
        state = DashboardState()

        # Should not raise
        dashboard._update_layout(state)

    @pytest.mark.asyncio
    async def test_handle_help_command(self):
        """Test handling /help command."""
        dashboard = TUIDashboard()

        await dashboard._handle_command("/help")

        state = dashboard.state_manager.get_state()
        assert len(state.messages) > 0
        # Should have a system message with help text
        assert any("help" in m.content.lower() for m in state.messages if m.role == "system")

    @pytest.mark.asyncio
    async def test_handle_clear_command(self):
        """Test handling /clear command."""
        dashboard = TUIDashboard()

        # Add some messages first
        dashboard.state_manager.add_user_message("Test message")
        dashboard.state_manager.add_agent_message("Response")

        await dashboard._handle_command("/clear")

        state = dashboard.state_manager.get_state()
        # Should have only the "Chat cleared" message
        assert len(state.messages) == 1
        assert "cleared" in state.messages[0].content.lower()

    @pytest.mark.asyncio
    async def test_handle_status_command_no_agentos(self):
        """Test handling /status command without AgentOS."""
        dashboard = TUIDashboard()

        await dashboard._handle_command("/status")

        state = dashboard.state_manager.get_state()
        assert any("not connected" in m.content.lower() for m in state.messages if m.role == "system")

    @pytest.mark.asyncio
    async def test_handle_unknown_command(self):
        """Test handling unknown command."""
        dashboard = TUIDashboard()

        await dashboard._handle_command("/unknown")

        state = dashboard.state_manager.get_state()
        assert any("unknown" in m.content.lower() for m in state.messages if m.role == "system")

    @pytest.mark.asyncio
    async def test_handle_input_without_agentos(self):
        """Test handling input without AgentOS."""
        dashboard = TUIDashboard()

        await dashboard.handle_input("Test input")

        state = dashboard.state_manager.get_state()
        # Should have user message and system message about demo mode
        assert any(m.role == "user" for m in state.messages)
        assert any("demo mode" in m.content.lower() for m in state.messages if m.role == "system")

    def test_stop_dashboard(self):
        """Test stopping dashboard."""
        dashboard = TUIDashboard()
        dashboard._running = True

        dashboard.stop()

        assert dashboard._running is False


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for TUI Dashboard."""

    def test_full_state_flow(self):
        """Test full state update flow."""
        # Create dashboard
        dashboard = TUIDashboard()

        # Simulate state updates
        dashboard.state_manager.add_user_message("Explore the codebase")
        dashboard.state_manager.set_processing(True, "Scanning files...")

        # Simulate process state
        dashboard.state_manager._state.processes = {
            "proc-abc": {"state": "RUNNING", "role": "eye"},
        }
        dashboard.state_manager._state.current_dag_id = "dag-123"
        dashboard.state_manager._state.dag_status = {"total": 3, "succeeded": 1, "running": 1, "pending": 1}
        dashboard.state_manager._state.vcpu_iteration = 2
        dashboard.state_manager._state.vcpu_is_running = True

        # Update layout
        state = dashboard.state_manager.get_state()
        dashboard._update_layout(state)

        # Verify layout was updated
        assert dashboard.layout_manager.layout is not None

    def test_render_full_dashboard(self):
        """Test rendering the full dashboard."""
        from rich.console import Console

        # Create dashboard with test data
        dashboard = TUIDashboard()

        # Add test messages
        dashboard.state_manager.add_system_message("Welcome!")
        dashboard.state_manager.add_user_message("Hello")
        dashboard.state_manager.add_agent_message("Hi there!")
        dashboard.state_manager.add_tool_message("Glob", "Found 42 files", "success")

        # Set test state
        state = dashboard.state_manager.get_state()
        state.processes = {"proc-1": {"state": "RUNNING", "role": "eye"}}
        state.current_dag_id = "dag-test"
        state.dag_status = {"total": 2, "succeeded": 1, "running": 1}
        state.dag_tasks = {"SCAN": "SUCCEEDED", "CODE": "RUNNING"}
        state.vcpu_iteration = 3
        state.vcpu_is_running = True
        state.mmu_tokens = 5000
        state.mmu_max_tokens = 128000

        # Update and render
        dashboard._update_layout(state)

        # Create console and render
        console = Console(force_terminal=True, width=120, height=50)
        with console.capture() as capture:
            console.print(dashboard.layout_manager.layout)

        output = capture.get()
        assert len(output) > 0
        assert "Chat" in output
        assert "Processes" in output
        assert "DAG" in output
        assert "VCPU" in output
        assert "Memory" in output
