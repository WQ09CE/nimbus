"""
Nimbus v2 TUI - Main Application

A simplified TUI for interacting with Nimbus v2 AgentOS.
Single-screen design with chat area and prompt input.

Usage:
    python -m nimbus.tui.app
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from textual import on, work, events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import Footer, Static, Label, Rule

from nimbus.tui.widgets.chatbox import Chatbox
from nimbus.tui.widgets.prompt_input import PromptInput

if TYPE_CHECKING:
    from nimbus.agentos import AgentOS


# Suppress logging during TUI
logging.disable(logging.CRITICAL)

try:
    from loguru import logger as loguru_logger
    loguru_logger.disable("nimbus")
    loguru_logger.disable("")
except ImportError:
    pass


class NimbusApp(App[None]):
    """
    Nimbus v2 TUI Application.

    A single-screen chat interface for interacting with AgentOS.

    Features:
    - Markdown rendering for agent responses
    - Multi-line input with ctrl+j to submit
    - Keyboard navigation
    - Session persistence
    """

    TITLE = "Nimbus v2"
    ENABLE_COMMAND_PALETTE = False
    CSS_PATH = Path(__file__).parent / "nimbus.tcss"

    BINDINGS = [
        Binding("ctrl+c", "quit", "Exit", show=True),
        Binding("ctrl+l", "clear", "Clear", show=True),
        Binding("escape", "focus_prompt", "Focus prompt", show=False),
    ]

    def __init__(
        self,
        agent_os: Optional["AgentOS"] = None,
        model_name: str = "gemini-2.0-flash",
    ) -> None:
        """
        Initialize the Nimbus TUI.

        Args:
            agent_os: AgentOS instance (optional, will show demo mode if None)
            model_name: Model name to display in header
        """
        super().__init__()
        self.agent_os = agent_os
        self.model_name = model_name
        self._session_id: Optional[str] = None
        self._processing = False

    def compose(self) -> ComposeResult:
        """Compose the UI layout."""
        # Header
        with Container(id="header"):
            yield Label("[b]Nimbus v2[/b]", id="title")
            yield Label(f"[{self.model_name}]", id="model-label")

        yield Rule()

        # Main chat area
        with Container(id="main-container"):
            yield VerticalScroll(id="chat-area")

        # Input area
        with Container(id="input-area"):
            yield Static("", id="processing")
            yield PromptInput(id="prompt")

        yield Footer()

    def on_mount(self) -> None:
        """Set up the app when mounted."""
        # Add welcome message
        self._add_message(
            "Welcome to Nimbus v2 TUI. Type your message and press Enter to send.",
            role="system",
        )

        if self.agent_os:
            tools = self.agent_os.list_tools()
            if tools:
                self._add_message(
                    f"Available tools: {', '.join(tools[:8])}{'...' if len(tools) > 8 else ''}",
                    role="system",
                )
        else:
            self._add_message(
                "No AgentOS connected - running in demo mode.",
                role="system",
            )

        # Focus the prompt
        self.query_one("#prompt", PromptInput).focus()

    def _add_message(
        self,
        content: str,
        role: str = "user",
    ) -> Chatbox:
        """
        Add a message to the chat area.

        Args:
            content: Message content
            role: Message role (user, assistant, system, error)

        Returns:
            The created Chatbox widget
        """
        chat_area = self.query_one("#chat-area", VerticalScroll)
        chatbox = Chatbox(content=content, role=role)  # type: ignore
        chat_area.mount(chatbox)
        chat_area.scroll_end(animate=False)
        return chatbox

    def _set_processing(self, is_processing: bool) -> None:
        """Set the processing state."""
        self._processing = is_processing
        processing_widget = self.query_one("#processing", Static)
        prompt = self.query_one("#prompt", PromptInput)

        if is_processing:
            processing_widget.update("[bold yellow]Processing...[/]")
            prompt.set_submit_ready(False)
        else:
            processing_widget.update("")
            prompt.set_submit_ready(True)

    @on(PromptInput.PromptSubmitted)
    def on_prompt_submitted(self, event: PromptInput.PromptSubmitted) -> None:
        """Handle prompt submission."""
        text = event.text.strip()
        if not text:
            return

        # Handle commands
        if text.startswith("/"):
            self._handle_command(text)
            return

        # Process through AgentOS
        self._process_message(text)

    def _handle_command(self, cmd: str) -> None:
        """Handle slash commands."""
        cmd_lower = cmd.lower().strip()

        if cmd_lower in ("/exit", "/quit", "/q"):
            self.exit()

        elif cmd_lower == "/clear":
            self.action_clear()

        elif cmd_lower == "/help":
            self._add_message(
                "Commands:\n"
                "  /help  - Show this help\n"
                "  /clear - Clear chat\n"
                "  /new   - Start new session\n"
                "  /exit  - Exit",
                role="system",
            )

        elif cmd_lower == "/new":
            self._session_id = None
            self._add_message("Started new session.", role="system")

        else:
            self._add_message(f"Unknown command: {cmd}", role="error")

    @work(exclusive=True)
    async def _process_message(self, text: str) -> None:
        """Process a user message through AgentOS."""
        # Add user message
        self._add_message(text, role="user")

        if self.agent_os is None:
            self._add_message(
                "No AgentOS connected. Cannot process message.",
                role="error",
            )
            return

        # Show processing state
        self._set_processing(True)

        try:
            # Send to AgentOS
            result = await self.agent_os.chat(text, session_id=self._session_id)

            # Update session ID
            if self.agent_os._current_session_id:
                self._session_id = self.agent_os._current_session_id

            # Add response
            if result.status == "OK":
                output = result.output
                if isinstance(output, str):
                    self._add_message(output, role="assistant")
                else:
                    self._add_message(str(output), role="assistant")
            else:
                error_msg = result.fault.message if result.fault else "Unknown error"
                self._add_message(f"Error: {error_msg}", role="error")

        except Exception as e:
            self._add_message(f"Exception: {e}", role="error")

        finally:
            self._set_processing(False)
            # Refocus prompt
            self.query_one("#prompt", PromptInput).focus()

    @on(PromptInput.CursorEscapingTop)
    def on_cursor_escaping_top(self) -> None:
        """Handle cursor escaping from prompt to chat."""
        chat_area = self.query_one("#chat-area", VerticalScroll)
        chatboxes = chat_area.query(Chatbox)
        if chatboxes:
            chatboxes.last().focus()

    def action_clear(self) -> None:
        """Clear the chat area."""
        chat_area = self.query_one("#chat-area", VerticalScroll)
        chat_area.remove_children()
        self._add_message("Chat cleared.", role="system")

    def action_focus_prompt(self) -> None:
        """Focus the prompt input."""
        self.query_one("#prompt", PromptInput).focus()

    def on_click(self, event: events.Click) -> None:
        """Keep focus on prompt when clicking anywhere."""
        # Always keep focus on the prompt input
        self.set_timer(0.01, self._refocus_prompt)

    def _refocus_prompt(self) -> None:
        """Refocus the prompt after a short delay."""
        try:
            prompt = self.query_one("#prompt", PromptInput)
            if not prompt.has_focus:
                prompt.focus()
        except Exception:
            pass


# =============================================================================
# Entry Point
# =============================================================================


def load_config() -> dict:
    """Load configuration from ~/.nimbus/config.json."""
    config_path = Path.home() / ".nimbus" / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


async def create_agent_os_from_config() -> tuple[Optional["AgentOS"], str]:
    """
    Create an AgentOS instance from configuration.

    Returns:
        Tuple of (AgentOS or None, model_name)
    """
    config = load_config()

    # Get Gemini config
    gemini_config = config.get("llm", {}).get("providers", {}).get("gemini", {})
    api_key = gemini_config.get("api_key") or os.environ.get("GEMINI_API_KEY")
    model = gemini_config.get("model", "gemini-2.0-flash")

    if not api_key:
        return None, model

    try:
        from nimbus.agentos import create_agent_os
        from nimbus.llm import GeminiV2Client

        llm = GeminiV2Client(api_key=api_key, model=model)
        agent_os = create_agent_os(
            llm_client=llm,
            system_rules="You are a helpful coding assistant. Be concise and helpful.",
            workspace=Path.cwd(),
            register_defaults=True,
        )
        return agent_os, model

    except Exception as e:
        print(f"Warning: Could not create AgentOS: {e}")
        return None, model


async def main_async() -> None:
    """Async main entry point."""
    # 启用临时日志来显示初始化状态
    import sys
    print("Initializing Nimbus v2 TUI...", file=sys.stderr)

    agent_os, model = await create_agent_os_from_config()

    if agent_os:
        tools = agent_os.list_tools()
        print(f"✓ AgentOS ready with {len(tools)} tools", file=sys.stderr)
        print(f"  Model: {model}", file=sys.stderr)
    else:
        print("⚠ No AgentOS - check API key configuration", file=sys.stderr)

    print("Starting TUI...", file=sys.stderr)
    app = NimbusApp(agent_os=agent_os, model_name=model)
    await app.run_async()


def main() -> None:
    """Synchronous main entry point."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
