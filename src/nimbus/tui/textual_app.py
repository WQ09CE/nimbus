"""
Nimbus V2 Textual TUI - Modern Terminal Interface

A Claude Code-style TUI built with Textual framework.

Features:
- Real input with cursor and history
- Scrollable chat area
- Live status panels
- Keyboard shortcuts
- Multi-turn conversation support

Usage:
    python -m nimbus.tui.textual_app
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Static, Label, Rule
from textual.message import Message

if TYPE_CHECKING:
    from nimbus.v2.agentos import AgentOS


# Suppress ALL logging during TUI (both standard logging and loguru)
logging.disable(logging.CRITICAL)

# Disable loguru logger
try:
    from loguru import logger as loguru_logger
    loguru_logger.disable("nimbus")  # Disable all nimbus.* loggers
    loguru_logger.disable("")  # Disable root logger
except ImportError:
    pass


# =============================================================================
# Custom Widgets
# =============================================================================


class ChatMessage(Static):
    """A single chat message widget."""

    def __init__(
        self,
        role: str,
        content: str,
        tool_name: str = "",
        timestamp: Optional[datetime] = None,
    ) -> None:
        super().__init__()
        self.role = role
        self.content = content
        self.tool_name = tool_name
        self.timestamp = timestamp or datetime.now()

    def compose(self) -> ComposeResult:
        text = Text()

        if self.role == "user":
            text.append("❯ ", style="bold green")
            text.append(self.content, style="white")

        elif self.role == "agent":
            text.append("◆ ", style="bold cyan")
            text.append(self.content, style="white")

        elif self.role == "tool":
            text.append(f"  [{self.tool_name}] ", style="dim cyan")
            content = self.content[:200] + "..." if len(self.content) > 200 else self.content
            text.append(content, style="dim")

        elif self.role == "system":
            text.append("[i] ", style="dim magenta")
            text.append(self.content, style="dim")

        elif self.role == "error":
            text.append("[!] ", style="bold red")
            text.append(self.content, style="red")

        yield Static(text, classes=f"message message-{self.role}")


class ChatView(VerticalScroll):
    """Scrollable chat history view."""

    def add_message(
        self,
        role: str,
        content: str,
        tool_name: str = "",
    ) -> None:
        """Add a message to the chat."""
        msg = ChatMessage(role, content, tool_name)
        self.mount(msg)
        self.scroll_end(animate=False)


class StatusPanel(Static):
    """Right-side status panel showing AgentOS state."""

    def __init__(self, agent_os: Optional["AgentOS"] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.agent_os = agent_os
        self._session_id: Optional[str] = None
        self._last_tool_calls: list[dict] = []
        self._iteration_count: int = 0
        self._start_time: Optional[datetime] = None

    def compose(self) -> ComposeResult:
        yield Static("", id="status-content")

    def on_mount(self) -> None:
        """Initialize status content on mount."""
        self.update_status()

    def update_status(
        self,
        session_id: Optional[str] = None,
        tool_call: Optional[dict] = None,
        iteration: Optional[int] = None,
    ) -> None:
        """Update the status display."""
        self._session_id = session_id

        if tool_call:
            self._last_tool_calls.append(tool_call)
            # Keep only last 5 tool calls
            if len(self._last_tool_calls) > 5:
                self._last_tool_calls = self._last_tool_calls[-5:]

        if iteration is not None:
            self._iteration_count = iteration
            if iteration == 1:
                self._start_time = datetime.now()

        content = self._build_status_content()
        status_widget = self.query_one("#status-content", Static)
        status_widget.update(content)

    def clear_history(self) -> None:
        """Clear tool call history for new session."""
        self._last_tool_calls = []
        self._iteration_count = 0
        self._start_time = None

    def _build_status_content(self) -> Text:
        """Build the status panel content."""
        text = Text()

        # Header
        text.append("AGENT STATUS\n", style="bold magenta")
        text.append("─" * 18 + "\n", style="dim")

        if self.agent_os is None:
            text.append("\n⚠ No AgentOS\n", style="dim yellow")
            text.append("  Demo mode\n", style="dim")
            return text

        try:
            state = self.agent_os.get_state()

            # Session info
            text.append("\n● SESSION\n", style="bold cyan")
            if self._session_id:
                text.append(f"  {self._session_id[:12]}\n", style="green")
            else:
                text.append("  (none)\n", style="dim")

            # vCPU State
            text.append("\n● vCPU\n", style="bold cyan")
            text.append(f"  Iterations: ", style="dim")
            text.append(f"{self._iteration_count}\n", style="yellow")

            if self._start_time:
                elapsed = (datetime.now() - self._start_time).total_seconds()
                text.append(f"  Elapsed: ", style="dim")
                text.append(f"{elapsed:.1f}s\n", style="yellow")

            # Process info
            processes = state.get("processes", {})
            running = sum(1 for p in processes.values() if p.get("state") == "RUNNING")
            text.append("\n● PROCESSES\n", style="bold cyan")
            text.append(f"  Total: {len(processes)}", style="dim")
            if running > 0:
                text.append(f" (", style="dim")
                text.append(f"{running} running", style="green")
                text.append(")\n", style="dim")
            else:
                text.append("\n", style="dim")

            # Recent tool calls
            text.append("\n● RECENT TOOLS\n", style="bold cyan")
            if self._last_tool_calls:
                for tc in reversed(self._last_tool_calls[-3:]):
                    name = tc.get("name", "?")[:10]
                    status = tc.get("status", "?")
                    icon = "✓" if status == "OK" else "✗"
                    color = "green" if status == "OK" else "red"
                    text.append(f"  {icon} ", style=color)
                    text.append(f"{name}\n", style="dim")
            else:
                text.append("  (none)\n", style="dim")

            # Available tools
            tools = state.get("tools", [])
            text.append("\n● TOOLS\n", style="bold cyan")
            text.append(f"  {len(tools)} available\n", style="dim")
            # Show first few tool names
            for t in tools[:4]:
                text.append(f"  • {t}\n", style="dim")
            if len(tools) > 4:
                text.append(f"  ... +{len(tools)-4} more\n", style="dim")

            # Events
            event_count = state.get("event_count", 0)
            text.append("\n● EVENTS\n", style="bold cyan")
            text.append(f"  {event_count} total\n", style="dim")

        except Exception as e:
            text.append(f"\n⚠ Error: {e}\n", style="red")

        return text


class ProcessingIndicator(Static):
    """Shows processing state."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._processing = False

    def set_processing(self, is_processing: bool) -> None:
        self._processing = is_processing
        if is_processing:
            self.update(Text("⟳ Processing...", style="bold yellow"))
        else:
            self.update("")


# =============================================================================
# Main Application
# =============================================================================


class NimbusTUI(App):
    """Nimbus V2 TUI Application."""

    TITLE = "Nimbus V2"
    SUB_TITLE = "Agent Terminal"

    CSS = """
    Screen {
        layout: horizontal;
    }

    #left-panel {
        width: 3fr;
        height: 100%;
        background: $surface;
    }

    #right-panel {
        width: 1fr;
        min-width: 24;
        max-width: 30;
        height: 100%;
        background: $surface-darken-1;
        padding: 1;
        border-left: solid $primary;
    }

    #chat-view {
        height: 1fr;
        padding: 1;
        background: $surface;
    }

    #input-area {
        height: auto;
        max-height: 5;
        padding: 1;
        background: $surface-darken-1;
    }

    #chat-input {
        dock: bottom;
    }

    #processing {
        height: 1;
        padding-left: 1;
    }

    .message {
        margin-bottom: 1;
    }

    .message-user {
        color: white;
    }

    .message-agent {
        color: white;
    }

    .message-system {
        color: gray;
    }

    .message-error {
        color: red;
    }

    #status-content {
        height: 100%;
    }

    Input {
        border: solid $success;
    }

    Input:focus {
        border: solid $primary;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Exit"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("escape", "blur_input", "Unfocus", show=False),
    ]

    def __init__(
        self,
        agent_os: Optional["AgentOS"] = None,
        workspace: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.agent_os = agent_os
        self.workspace = workspace or Path.cwd()
        self._session_id: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal():
            # Left panel: Chat + Input
            with Vertical(id="left-panel"):
                yield ChatView(id="chat-view")
                with Container(id="input-area"):
                    yield ProcessingIndicator(id="processing")
                    yield Input(placeholder="Type your message... (Ctrl+C to exit)", id="chat-input")

            # Right panel: Status
            with Vertical(id="right-panel"):
                yield Label("Status", classes="panel-title")
                yield Rule()
                yield StatusPanel(self.agent_os, id="status-panel")

        yield Footer()

    def on_mount(self) -> None:
        """Called when app is mounted."""
        chat = self.query_one("#chat-view", ChatView)
        chat.add_message("system", "Nimbus V2 Terminal Ready")

        if self.agent_os:
            tools = self.agent_os.list_tools()
            chat.add_message("system", f"Tools: {', '.join(tools)}")
            chat.add_message("system", "Type your message and press Enter")
        else:
            chat.add_message("system", "No AgentOS connected (demo mode)")

        # Focus input
        self.query_one("#chat-input", Input).focus()

        # Update status panel
        self._update_status()

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission."""
        user_input = event.value.strip()
        if not user_input:
            return

        # Clear input
        event.input.value = ""

        # Handle commands
        if user_input.startswith("/"):
            self.run_worker(self._handle_command_async(user_input))
            return

        # Process through AgentOS (uses @work decorator)
        self._process_input(user_input)

    async def _handle_command_async(self, cmd: str) -> None:
        """Async wrapper for command handling."""
        await self._handle_command(cmd)

    async def _handle_command(self, cmd: str) -> None:
        """Handle special commands."""
        chat = self.query_one("#chat-view", ChatView)
        cmd = cmd.lower().strip()

        if cmd in ("/exit", "/quit", "/q"):
            self.exit()

        elif cmd == "/clear":
            # Clear chat view
            chat.remove_children()
            chat.add_message("system", "Chat cleared")

        elif cmd == "/help":
            chat.add_message("system", """Commands:
  /help   - Show this help
  /clear  - Clear chat
  /status - Show status
  /new    - Start new session
  /exit   - Exit""")

        elif cmd == "/status":
            if self.agent_os:
                state = self.agent_os.get_state()
                chat.add_message("system",
                    f"Session: {self._session_id or 'none'}\n"
                    f"Processes: {len(state.get('processes', {}))}\n"
                    f"Tools: {len(state.get('tools', []))}\n"
                    f"Events: {state.get('event_count', 0)}"
                )
            else:
                chat.add_message("system", "No AgentOS connected")

        elif cmd == "/new":
            self._session_id = None
            chat.add_message("system", "Started new session")
            self._update_status()

        else:
            chat.add_message("system", f"Unknown command: {cmd}")

    @work(exclusive=True)
    async def _process_input(self, user_input: str) -> None:
        """Process user input through AgentOS."""
        chat = self.query_one("#chat-view", ChatView)
        processing = self.query_one("#processing", ProcessingIndicator)
        status = self.query_one("#status-panel", StatusPanel)

        # Add user message
        chat.add_message("user", user_input)

        if self.agent_os is None:
            chat.add_message("system", "No AgentOS connected")
            return

        # Show processing indicator
        processing.set_processing(True)

        # Clear previous tool history for new turn
        status.clear_history()

        try:
            # Use chat() for multi-turn conversation
            result = await self.agent_os.chat(user_input, session_id=self._session_id)

            # Store session_id
            if self.agent_os._current_session_id:
                self._session_id = self.agent_os._current_session_id

            # Try to get iteration and tool info from the process
            iteration = 0
            try:
                if self._session_id and self._session_id in self.agent_os._processes:
                    proc = self.agent_os._processes[self._session_id]
                    if proc.vcpu:
                        iteration = proc.vcpu._iteration
            except Exception:
                pass

            # Update status with tool call info
            tool_info = {"name": "chat", "status": result.status}
            status.update_status(
                session_id=self._session_id,
                tool_call=tool_info,
                iteration=iteration,
            )

            # Add response
            if result.status == "OK":
                output = result.output
                if isinstance(output, str):
                    chat.add_message("agent", output)
                else:
                    chat.add_message("agent", str(output))
            else:
                error_msg = result.fault.message if result.fault else "Unknown error"
                chat.add_message("error", error_msg)

        except Exception as e:
            chat.add_message("error", str(e))
            status.update_status(
                session_id=self._session_id,
                tool_call={"name": "error", "status": "ERROR"},
            )

        finally:
            processing.set_processing(False)
            # Refocus input
            self.query_one("#chat-input", Input).focus()

    def _update_status(self) -> None:
        """Update the status panel."""
        status = self.query_one("#status-panel", StatusPanel)
        status.update_status(session_id=self._session_id)

    def action_clear(self) -> None:
        """Clear chat action."""
        chat = self.query_one("#chat-view", ChatView)
        chat.remove_children()
        chat.add_message("system", "Chat cleared")

    def action_blur_input(self) -> None:
        """Blur input field."""
        self.query_one("#chat-input", Input).blur()


# =============================================================================
# Entry Point
# =============================================================================


async def main() -> None:
    """Main entry point."""
    # Load config
    config_path = Path.home() / ".nimbus" / "config.json"
    config = {}
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)

    # Get Gemini config
    gemini_config = config.get("llm", {}).get("providers", {}).get("gemini", {})
    api_key = gemini_config.get("api_key") or os.environ.get("GEMINI_API_KEY")
    model = gemini_config.get("model", "gemini-2.0-flash")

    agent_os = None

    if api_key:
        try:
            from nimbus.v2.agentos import create_agent_os
            from nimbus.v2.llm import GeminiV2Client

            llm = GeminiV2Client(api_key=api_key, model=model)
            agent_os = create_agent_os(
                llm_client=llm,
                system_rules="You are a helpful coding assistant. Be concise.",
                workspace=Path.cwd(),
                register_defaults=True,
            )
        except Exception as e:
            print(f"Warning: Could not create AgentOS: {e}")

    app = NimbusTUI(agent_os=agent_os, workspace=Path.cwd())
    await app.run_async()


def run() -> None:
    """Synchronous entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
