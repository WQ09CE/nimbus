"""
TUI Dashboard Chat Panel Widget

Provides ChatPanel for rendering the chat conversation area.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from rich.panel import Panel
from rich.text import Text

if TYPE_CHECKING:
    from nimbus.tui.state import ChatMessage


class ChatPanel:
    """
    Renders the chat conversation area.

    Visual Design:
    +-- Chat ----------------------------------------------+
    |                                                      |
    |  > User: Help me analyze this code                   |
    |                                                      |
    |  * Agent: Let me analyze the code...                 |
    |    - First, I'll read the file                       |
    |    - Then examine the structure                      |
    |                                                      |
    |  [Tool: Read] src/main.py                            |
    |  + Success (234 lines)                               |
    |                                                      |
    |  * Agent: The code contains...                       |
    |                                                      |
    +------------------------------------------------------+
    """

    def __init__(self, max_history: int = 100):
        """
        Initialize ChatPanel.

        Args:
            max_history: Maximum messages to display
        """
        self.max_history = max_history
        self._scroll_offset = 0

    def render(
        self,
        messages: List["ChatMessage"],
        height: int = 30,
        input_text: str = "",
    ) -> Panel:
        """
        Render the chat panel.

        Args:
            messages: List of chat messages
            height: Available height for the panel
            input_text: Current input text (for display)

        Returns:
            Rich Panel with rendered chat
        """
        text = Text()

        # Render messages
        for msg in messages[-self.max_history :]:
            self._render_message(text, msg)

        # Add input indicator at the bottom if there's input
        if input_text:
            text.append("\n")
            text.append("> ", style="bold green")
            text.append(input_text, style="white")
            text.append("_", style="blink white")

        return Panel(
            text,
            title="Chat",
            border_style="bright_blue",
            padding=(1, 2),
        )

    def _render_message(self, text: Text, msg: "ChatMessage") -> None:
        """
        Render a single message.

        Args:
            text: Text object to append to
            msg: Message to render
        """
        role = msg.role

        if role == "user":
            text.append("\n> ", style="bold yellow")
            text.append("User: ", style="bold yellow")
            text.append(f"{msg.content}\n", style="white")

        elif role == "agent":
            text.append("\n* ", style="bold green")
            text.append("Agent: ", style="bold green")
            # Handle multiline content
            content = msg.content
            if content:
                lines = content.split("\n")
                text.append(f"{lines[0]}\n", style="white")
                for line in lines[1:]:
                    text.append(f"  {line}\n", style="white")

        elif role == "tool":
            tool_name = msg.tool_name or "Unknown"
            tool_status = msg.tool_status

            text.append(f"\n[Tool: {tool_name}] ", style="dim cyan")
            text.append(f"{msg.content}\n", style="dim")

            if tool_status:
                if tool_status == "success":
                    text.append("+ Success\n", style="green")
                elif tool_status == "error":
                    text.append("- Error\n", style="red")
                elif tool_status == "running":
                    text.append("~ Running...\n", style="yellow")

        elif role == "system":
            text.append("\n[System] ", style="dim magenta")
            text.append(f"{msg.content}\n", style="dim")

    def scroll_up(self, lines: int = 1) -> None:
        """Scroll up by specified number of lines."""
        self._scroll_offset = max(0, self._scroll_offset - lines)

    def scroll_down(self, lines: int = 1) -> None:
        """Scroll down by specified number of lines."""
        self._scroll_offset += lines

    def reset_scroll(self) -> None:
        """Reset scroll to bottom."""
        self._scroll_offset = 0
