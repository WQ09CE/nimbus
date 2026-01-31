"""
TUI Dashboard Layout Manager

Provides LayoutManager for managing the Rich Layout structure.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.layout import Layout


class LayoutManager:
    """
    Manages Rich Layout structure.

    Layout Structure:
    +------------------------------------------------------------+
    | header (size=3)                                             |
    +--------------------------------------------+----------------+
    | main.chat (ratio=7)                        | main.info      |
    |                                            | (ratio=3)      |
    |                                            | +-----------+  |
    |                                            | | processes |  |
    |                                            | +-----------+  |
    |                                            | | dag       |  |
    |                                            | +-----------+  |
    |                                            | | vcpu      |  |
    |                                            | +-----------+  |
    |                                            | | memory    |  |
    |                                            | +-----------+  |
    +--------------------------------------------+----------------+
    | footer (size=3)                                             |
    +------------------------------------------------------------+
    """

    def __init__(self, chat_ratio: int = 7, info_ratio: int = 3):
        """
        Initialize LayoutManager.

        Args:
            chat_ratio: Ratio for chat panel width (default: 7)
            info_ratio: Ratio for info panel width (default: 3)
        """
        self._chat_ratio = chat_ratio
        self._info_ratio = info_ratio
        self.layout = Layout()
        self._build_layout()

    def _build_layout(self) -> None:
        """Build the layout structure."""
        # Main vertical split
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3),
        )

        # Horizontal split for main area
        self.layout["main"].split_row(
            Layout(name="chat", ratio=self._chat_ratio),
            Layout(name="info", ratio=self._info_ratio),
        )

        # Vertical split for info panel
        self.layout["info"].split(
            Layout(name="processes", ratio=2),
            Layout(name="dag", ratio=2),
            Layout(name="vcpu", ratio=2),
            Layout(name="memory", ratio=2),
        )

    def update(
        self,
        header: RenderableType,
        chat: RenderableType,
        processes: RenderableType,
        dag: RenderableType,
        vcpu: RenderableType,
        memory: RenderableType,
        footer: RenderableType,
    ) -> Layout:
        """
        Update all layout regions.

        Args:
            header: Header content
            chat: Chat panel content
            processes: Process widget content
            dag: DAG widget content
            vcpu: VCPU widget content
            memory: Memory widget content
            footer: Footer content

        Returns:
            Updated Layout
        """
        self.layout["header"].update(header)
        self.layout["chat"].update(chat)
        self.layout["processes"].update(processes)
        self.layout["dag"].update(dag)
        self.layout["vcpu"].update(vcpu)
        self.layout["memory"].update(memory)
        self.layout["footer"].update(footer)
        return self.layout

    def get_layout(self) -> Layout:
        """Get the layout."""
        return self.layout
