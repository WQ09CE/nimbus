"""
Nimbus V2 Simple TUI - A practical terminal interface

This provides a Claude Code-like experience:
- Clear prompt with visible cursor
- Streaming output display
- Side panel status updates
- No log pollution

Usage:
    python -m nimbus.tui.simple_tui
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.layout import Layout
from rich.prompt import Prompt
from rich import box

if TYPE_CHECKING:
    from nimbus.agentos import AgentOS


@dataclass
class Message:
    """A chat message."""
    role: str  # "user", "agent", "tool", "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_name: str = ""
    tool_status: str = ""  # "running", "success", "error"


class SimpleTUI:
    """
    Simple TUI that provides a practical terminal experience.

    Features:
    - Input with visible cursor (standard input)
    - Agent output streaming
    - Status panel showing V2 state
    - No log pollution
    - Multi-turn conversation with persistent context
    """

    def __init__(
        self,
        agent_os: Optional["AgentOS"] = None,
        workspace: Optional[Path] = None,
    ):
        self.os = agent_os
        self.workspace = workspace or Path.cwd()
        self.console = Console()
        self.messages: List[Message] = []
        self.is_processing = False
        self._session_id: Optional[str] = None  # For multi-turn chat
        self._suppress_logs()

    def _suppress_logs(self):
        """Suppress all logging output during TUI."""
        # Disable all loggers
        logging.disable(logging.CRITICAL)

        # Also redirect any remaining output
        for name in logging.root.manager.loggerDict:
            logger = logging.getLogger(name)
            logger.handlers = []
            logger.propagate = False

    def _restore_logs(self):
        """Restore logging after TUI exits."""
        logging.disable(logging.NOTSET)

    def _render_header(self) -> Panel:
        """Render the header bar."""
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right", ratio=1)

        grid.add_row(
            "[bold cyan]NIMBUS V2[/bold cyan]",
            "[bold white]Agent Terminal[/bold white]",
            f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]"
        )
        return Panel(grid, style="on dark_blue", box=box.SIMPLE)

    def _render_status_panel(self) -> Panel:
        """Render the right side status panel."""
        content = Text()

        if self.os is None:
            content.append("No AgentOS connected\n", style="dim")
            return Panel(content, title="Status", border_style="dim")

        try:
            state = self.os.get_state()

            # Processes
            processes = state.get("processes", {})
            content.append("PROCESSES\n", style="bold cyan")
            if processes:
                for pid, info in list(processes.items())[:3]:
                    pstate = info.get("state", "?")
                    role = info.get("role", "")
                    icon = "●" if pstate == "RUNNING" else "○"
                    color = "green" if pstate == "RUNNING" else "dim"
                    content.append(f"  {icon} ", style=color)
                    content.append(f"{pid[:8]} ", style="dim")
                    content.append(f"[{pstate}]", style=color)
                    if role:
                        content.append(f" {role}", style="dim cyan")
                    content.append("\n")
            else:
                content.append("  (none)\n", style="dim")

            content.append("\n")

            # Tools
            tools = state.get("tools", [])
            content.append("TOOLS\n", style="bold cyan")
            content.append(f"  {', '.join(tools[:5])}", style="dim")
            if len(tools) > 5:
                content.append(f" +{len(tools)-5}", style="dim")
            content.append("\n\n")

            # Events
            event_count = state.get("event_count", 0)
            content.append("EVENTS\n", style="bold cyan")
            content.append(f"  {event_count} total\n", style="dim")

        except Exception as e:
            content.append(f"Error: {e}\n", style="red")

        return Panel(content, title="Status", border_style="cyan")

    def _render_message(self, msg: Message) -> Text:
        """Render a single message."""
        text = Text()

        if msg.role == "user":
            text.append("\n❯ ", style="bold green")
            text.append(msg.content, style="white")
            text.append("\n")

        elif msg.role == "agent":
            text.append("\n◆ ", style="bold cyan")
            lines = msg.content.split("\n")
            text.append(lines[0], style="white")
            text.append("\n")
            for line in lines[1:]:
                text.append(f"  {line}\n", style="white")

        elif msg.role == "tool":
            icon = "✓" if msg.tool_status == "success" else ("✗" if msg.tool_status == "error" else "⋯")
            color = "green" if msg.tool_status == "success" else ("red" if msg.tool_status == "error" else "yellow")
            text.append(f"\n  [{msg.tool_name}] ", style="dim cyan")
            text.append(f"{icon} ", style=color)
            # Truncate long tool output
            content = msg.content
            if len(content) > 200:
                content = content[:200] + "..."
            text.append(content, style="dim")
            text.append("\n")

        elif msg.role == "system":
            text.append("\n[i] ", style="dim magenta")
            text.append(msg.content, style="dim")
            text.append("\n")

        return text

    def _render_chat(self) -> Panel:
        """Render the chat area."""
        text = Text()

        # Only show last N messages
        recent = self.messages[-20:]
        for msg in recent:
            text.append_text(self._render_message(msg))

        if self.is_processing:
            text.append("\n⟳ ", style="bold yellow")
            text.append("Processing...", style="yellow")
            text.append("\n")

        return Panel(text, title="Chat", border_style="bright_blue", padding=(0, 1))

    def print_header(self):
        """Print the header."""
        self.console.print(self._render_header())

    def print_status(self):
        """Print current status alongside chat."""
        layout = Layout()
        layout.split_row(
            Layout(self._render_chat(), name="chat", ratio=7),
            Layout(self._render_status_panel(), name="status", ratio=3),
        )
        self.console.print(layout)

    def add_message(self, role: str, content: str, **kwargs):
        """Add a message to the chat."""
        self.messages.append(Message(role=role, content=content, **kwargs))

    async def process_input(self, user_input: str):
        """Process user input through AgentOS with multi-turn context."""
        self.add_message("user", user_input)

        if self.os is None:
            self.add_message("system", "No AgentOS connected. Running in demo mode.")
            return

        self.is_processing = True
        self._refresh_display()

        try:
            # Use chat() for multi-turn conversation with persistent context
            result = await self.os.chat(user_input, session_id=self._session_id)

            # Store session_id for continuation
            if self.os._current_session_id:
                self._session_id = self.os._current_session_id

            if result.status == "OK":
                output = result.output
                if isinstance(output, str):
                    self.add_message("agent", output)
                else:
                    self.add_message("agent", str(output))
            else:
                error_msg = result.fault.message if result.fault else "Unknown error"
                self.add_message("system", f"Error: {error_msg}")

        except Exception as e:
            self.add_message("system", f"Error: {str(e)}")
        finally:
            self.is_processing = False

    def _refresh_display(self):
        """Refresh the display."""
        self.console.clear()
        self.print_header()
        self.print_status()

    def _handle_command(self, cmd: str) -> bool:
        """Handle special commands. Returns True if should exit."""
        cmd = cmd.strip().lower()

        if cmd in ("/exit", "/quit", "/q"):
            return True

        elif cmd == "/help":
            self.add_message("system", """Commands:
  /help   - Show this help
  /clear  - Clear chat history
  /status - Show AgentOS status
  /exit   - Exit (or Ctrl+C)""")

        elif cmd == "/clear":
            self.messages.clear()
            self.add_message("system", "Chat cleared.")

        elif cmd == "/status":
            if self.os:
                state = self.os.get_state()
                self.add_message("system", f"Processes: {len(state.get('processes', {}))}, Tools: {len(state.get('tools', []))}, Events: {state.get('event_count', 0)}")
            else:
                self.add_message("system", "No AgentOS connected.")
        else:
            self.add_message("system", f"Unknown command: {cmd}")

        return False

    async def run(self):
        """Main run loop."""
        self.add_message("system", "Nimbus V2 Terminal Ready. Type /help for commands.")

        if self.os:
            tools = self.os.list_tools()
            self.add_message("system", f"Tools: {', '.join(tools)}")

        try:
            while True:
                # Refresh display
                self._refresh_display()

                # Get input with visible prompt
                try:
                    self.console.print()
                    user_input = Prompt.ask("[bold green]❯[/bold green]")
                except KeyboardInterrupt:
                    self.console.print("\n[dim]Goodbye![/dim]")
                    break
                except EOFError:
                    break

                if not user_input.strip():
                    continue

                # Handle commands
                if user_input.startswith("/"):
                    if self._handle_command(user_input):
                        self.console.print("[dim]Goodbye![/dim]")
                        break
                    continue

                # Process through AgentOS
                await self.process_input(user_input)

        finally:
            self._restore_logs()


async def main():
    """Main entry point."""
    import json

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

    console = Console()

    if not api_key:
        console.print("[red]Error: No Gemini API key found.[/red]")
        console.print("Set in ~/.nimbus/config.json or GEMINI_API_KEY env var")
        return

    console.print(f"[dim]Starting Nimbus V2 with Gemini ({model})...[/dim]")

    try:
        from nimbus.agentos import create_agent_os
        from nimbus.llm import GeminiV2Client

        llm = GeminiV2Client(api_key=api_key, model=model)
        agent_os = create_agent_os(
            llm_client=llm,
            system_rules="""You are a helpful coding assistant with access to tools.
When you need to interact with files or run commands, use the available tools.
Be concise and helpful.""",
            workspace=Path.cwd(),
            register_defaults=True,
        )

        tui = SimpleTUI(agent_os=agent_os, workspace=Path.cwd())
        await tui.run()

        await llm.close()

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise


if __name__ == "__main__":
    asyncio.run(main())
