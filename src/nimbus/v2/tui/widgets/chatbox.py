"""
Nimbus v2 TUI - Chatbox Widget

A chat message widget that supports Markdown rendering for agent responses
and plain text for user messages.
"""

from __future__ import annotations

from typing import Literal

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.syntax import Syntax
from textual.binding import Binding
from textual.widget import Widget


MessageRole = Literal["user", "assistant", "system", "error"]


class Chatbox(Widget, can_focus=False):
    """
    A chat message box widget.

    Features:
    - Markdown rendering for assistant messages
    - Plain text for user messages
    - Different border colors for different roles
    - Streaming support via append_chunk()
    """

    # No bindings - Chatbox is not focusable, input stays focused
    BINDINGS = []

    def __init__(
        self,
        content: str,
        role: MessageRole = "user",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        """
        Initialize a Chatbox.

        Args:
            content: The message content
            role: Message role (user, assistant, system, error)
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
        )
        self._content = content
        self._role = role

    def on_mount(self) -> None:
        """Set up the chatbox when mounted."""
        if self._role == "assistant":
            self.add_class("assistant-message")
            self.border_title = "Agent"
        elif self._role == "user":
            self.add_class("user-message")
            self.border_title = "You"
        elif self._role == "system":
            self.add_class("system-message")
            self.border_title = "System"
        elif self._role == "error":
            self.add_class("error-message")
            self.border_title = "Error"

    @property
    def content(self) -> str:
        """Get the message content."""
        return self._content

    @content.setter
    def content(self, value: str) -> None:
        """Set the message content."""
        self._content = value
        self.refresh(layout=True)

    @property
    def role(self) -> MessageRole:
        """Get the message role."""
        return self._role

    def render(self) -> RenderableType:
        """Render the message content."""
        if self._role == "assistant":
            # Render as Markdown for assistant messages
            return Markdown(self._content, code_theme="monokai")
        elif self._role == "user":
            # Render as plain syntax highlighted markdown for user
            return Syntax(
                self._content,
                lexer="markdown",
                word_wrap=True,
                background_color="#1e1e1e",
            )
        elif self._role == "system":
            # System messages - simple text
            return self._content
        elif self._role == "error":
            # Error messages - simple text
            return self._content
        else:
            return self._content

    def append_chunk(self, chunk: str) -> None:
        """
        Append a chunk of text to the message.

        This is used for streaming responses.

        Args:
            chunk: The text chunk to append
        """
        self._content += chunk
        self.refresh(layout=True)

    def set_content(self, content: str) -> None:
        """
        Set the entire message content.

        Args:
            content: The new content
        """
        self._content = content
        self.refresh(layout=True)
