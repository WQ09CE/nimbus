"""
Tests for Textual TUI App
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nimbus.tui.textual_app import (
    NimbusTUI,
    ChatView,
    ChatMessage,
    StatusPanel,
    ProcessingIndicator,
)


class TestChatMessage:
    """Test ChatMessage widget."""

    def test_user_message(self):
        msg = ChatMessage("user", "Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_agent_message(self):
        msg = ChatMessage("agent", "Hi there")
        assert msg.role == "agent"
        assert msg.content == "Hi there"

    def test_tool_message(self):
        msg = ChatMessage("tool", "Result", tool_name="Read")
        assert msg.role == "tool"
        assert msg.tool_name == "Read"

    def test_system_message(self):
        msg = ChatMessage("system", "Ready")
        assert msg.role == "system"

    def test_error_message(self):
        msg = ChatMessage("error", "Something failed")
        assert msg.role == "error"


class TestProcessingIndicator:
    """Test ProcessingIndicator widget."""

    def test_init(self):
        pi = ProcessingIndicator(id="test")
        assert pi._processing is False

    @pytest.mark.asyncio
    async def test_set_processing_true(self):
        """Test setting processing state (requires app context)."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            pi = app.query_one("#processing", ProcessingIndicator)
            pi.set_processing(True)
            assert pi._processing is True

    @pytest.mark.asyncio
    async def test_set_processing_false(self):
        """Test clearing processing state (requires app context)."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            pi = app.query_one("#processing", ProcessingIndicator)
            pi.set_processing(True)
            pi.set_processing(False)
            assert pi._processing is False


class TestStatusPanel:
    """Test StatusPanel widget."""

    def test_init_no_agent(self):
        panel = StatusPanel(agent_os=None, id="test")
        assert panel.agent_os is None
        assert panel._session_id is None

    def test_init_with_agent(self):
        mock_agent = MagicMock()
        panel = StatusPanel(agent_os=mock_agent, id="test")
        assert panel.agent_os == mock_agent


class TestNimbusTUI:
    """Test NimbusTUI app."""

    @pytest.mark.asyncio
    async def test_app_creation(self):
        """Test app can be created."""
        app = NimbusTUI(agent_os=None)
        assert app.TITLE == "Nimbus V2"
        assert app.SUB_TITLE == "Agent Terminal"

    @pytest.mark.asyncio
    async def test_widgets_exist(self):
        """Test all widgets are created."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            assert app.query_one("#chat-view") is not None
            assert app.query_one("#status-panel") is not None
            assert app.query_one("#chat-input") is not None
            assert app.query_one("#processing") is not None
            assert app.query_one("#left-panel") is not None
            assert app.query_one("#right-panel") is not None

    @pytest.mark.asyncio
    async def test_help_command(self):
        """Test /help command."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-view", ChatView)
            initial_count = len(list(chat.query("ChatMessage")))

            await app._handle_command("/help")
            await pilot.pause()

            messages = list(chat.query("ChatMessage"))
            assert len(messages) > initial_count

    @pytest.mark.asyncio
    async def test_status_command_no_agent(self):
        """Test /status command without agent."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-view", ChatView)
            initial_count = len(list(chat.query("ChatMessage")))

            await app._handle_command("/status")
            await pilot.pause()

            messages = list(chat.query("ChatMessage"))
            assert len(messages) > initial_count

    @pytest.mark.asyncio
    async def test_clear_command(self):
        """Test /clear command."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-view", ChatView)

            # Add some messages first
            await app._handle_command("/help")
            await pilot.pause()

            # Clear
            await app._handle_command("/clear")
            await pilot.pause()

            # Should only have "Chat cleared" message
            messages = list(chat.query("ChatMessage"))
            assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_new_command(self):
        """Test /new command."""
        app = NimbusTUI(agent_os=None)
        app._session_id = "old-session"

        async with app.run_test() as pilot:
            await app._handle_command("/new")
            await pilot.pause()

            assert app._session_id is None

    @pytest.mark.asyncio
    async def test_unknown_command(self):
        """Test unknown command."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-view", ChatView)
            initial_count = len(list(chat.query("ChatMessage")))

            await app._handle_command("/unknown")
            await pilot.pause()

            messages = list(chat.query("ChatMessage"))
            assert len(messages) > initial_count

    @pytest.mark.asyncio
    async def test_process_input_no_agent(self):
        """Test processing input without agent."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-view", ChatView)

            # Call _process_input directly (not via decorator)
            app._process_input("Hello")
            await pilot.pause()
            await asyncio.sleep(0.1)

            messages = list(chat.query("ChatMessage"))
            # Should have user message and system message
            assert len(messages) >= 2

    @pytest.mark.asyncio
    async def test_process_input_with_mock_agent(self):
        """Test processing input with mocked agent."""
        # Create mock AgentOS
        mock_result = MagicMock()
        mock_result.status = "OK"
        mock_result.output = "Hello from agent"

        mock_agent = MagicMock()
        mock_agent.chat = AsyncMock(return_value=mock_result)
        mock_agent._current_session_id = "test-session"

        app = NimbusTUI(agent_os=mock_agent)
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-view", ChatView)

            # Process input
            app._process_input("Hello")
            await pilot.pause()
            await asyncio.sleep(0.2)

            # Verify agent.chat was called
            mock_agent.chat.assert_called_once()

            # Session should be set
            assert app._session_id == "test-session"

    @pytest.mark.asyncio
    async def test_process_input_with_error(self):
        """Test processing input when agent returns error."""
        # Create mock AgentOS with error
        mock_fault = MagicMock()
        mock_fault.message = "Test error"

        mock_result = MagicMock()
        mock_result.status = "ERROR"
        mock_result.fault = mock_fault

        mock_agent = MagicMock()
        mock_agent.chat = AsyncMock(return_value=mock_result)
        mock_agent._current_session_id = None

        app = NimbusTUI(agent_os=mock_agent)
        async with app.run_test() as pilot:
            app._process_input("Hello")
            await pilot.pause()
            await asyncio.sleep(0.2)

            # Verify error was handled
            mock_agent.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_action_clear(self):
        """Test clear action (Ctrl+L)."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-view", ChatView)

            # Add messages
            await app._handle_command("/help")
            await pilot.pause()

            # Use action
            app.action_clear()
            await pilot.pause()

            messages = list(chat.query("ChatMessage"))
            assert len(messages) == 1


class TestChatView:
    """Test ChatView widget."""

    @pytest.mark.asyncio
    async def test_add_message(self):
        """Test adding messages to chat view."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-view", ChatView)

            chat.add_message("user", "Hello")
            await pilot.pause()

            messages = list(chat.query("ChatMessage"))
            assert len(messages) >= 1

    @pytest.mark.asyncio
    async def test_add_tool_message(self):
        """Test adding tool message."""
        app = NimbusTUI(agent_os=None)
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-view", ChatView)

            chat.add_message("tool", "File content", tool_name="Read")
            await pilot.pause()

            messages = list(chat.query("ChatMessage"))
            assert len(messages) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
