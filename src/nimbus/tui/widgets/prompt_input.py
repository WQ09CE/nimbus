"""
Nimbus v2 TUI - Prompt Input Widget

A text input widget for entering messages to the agent.
Supports multi-line input with ctrl+j or alt+enter to submit.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual import events, on
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import TextArea


class PromptInput(TextArea):
    """
    A text input widget for entering prompts.

    Features:
    - Multi-line input support
    - Submit with ctrl+j or alt+enter
    - Submit blocked indicator when waiting for response
    - Markdown syntax highlighting
    """

    @dataclass
    class PromptSubmitted(Message):
        """Message sent when the user submits a prompt."""

        text: str
        prompt_input: "PromptInput"

    @dataclass
    class CursorEscapingTop(Message):
        """Message sent when cursor escapes from the top."""

        pass

    BINDINGS = [
        Binding(
            "enter",
            "submit_prompt",
            "Send",
            key_display="⏎",
        ),
        Binding(
            "ctrl+j,alt+enter",
            "newline",
            "Newline",
            key_display="^j",
            show=False,
        ),
    ]

    submit_ready = reactive(True)

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        """
        Initialize the prompt input.

        Args:
            name: Widget name
            id: Widget ID
            classes: CSS classes
            disabled: Whether the widget is disabled
        """
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            language="markdown",
        )

    def on_key(self, event: events.Key) -> None:
        """Handle key events for cursor navigation and submission."""
        # Enter to submit (intercept before TextArea handles it)
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.action_submit_prompt()
            return

        # Up arrow at top to escape
        if self.cursor_location == (0, 0) and event.key == "up":
            event.prevent_default()
            self.post_message(self.CursorEscapingTop())
            event.stop()

    def watch_submit_ready(self, submit_ready: bool) -> None:
        """Watch for submit_ready changes to update styling."""
        self.set_class(not submit_ready, "-submit-blocked")

    def on_mount(self) -> None:
        """Set up the prompt input when mounted."""
        self.border_title = "Enter your message..."

    @on(TextArea.Changed)
    async def prompt_changed(self, event: TextArea.Changed) -> None:
        """Handle text changes to update UI hints."""
        text_area = event.text_area
        if text_area.text.strip() != "":
            text_area.border_subtitle = "[[white]Enter[/]] Send | [[white]^j[/]] Newline"
        else:
            text_area.border_subtitle = None

        # Add multiline class when content has multiple lines
        line_count = text_area.text.count("\n") + 1
        text_area.set_class(line_count > 1, "multiline")

        # Refresh parent to handle height changes
        if self.parent:
            self.parent.refresh()

    def action_submit_prompt(self) -> None:
        """Submit the current prompt."""
        if self.text.strip() == "":
            self.notify("Cannot send empty message!", severity="warning")
            return

        if self.submit_ready:
            message = self.PromptSubmitted(self.text, prompt_input=self)
            self.clear()
            self.post_message(message)
        else:
            self.app.bell()
            self.notify("Please wait for response to complete.", severity="warning")

    def action_newline(self) -> None:
        """Insert a newline at cursor position."""
        self.insert("\n")

    def set_submit_ready(self, ready: bool) -> None:
        """
        Set whether the prompt is ready to submit.

        Args:
            ready: True if ready to submit, False if waiting
        """
        self.submit_ready = ready
