"""
TUI Dashboard Status Bar Widget

Provides StatusBar for the bottom status bar with state and shortcuts.
"""

from __future__ import annotations

import time

from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class StatusBar:
    """
    Bottom status bar with state and shortcuts.

    Visual (ready state):
    +------------------------------------------------------------+
    | > Ready for input                           [Ctrl+C: Exit] |
    +------------------------------------------------------------+

    Visual (processing state):
    +------------------------------------------------------------+
    | [Processing] Executing task...           [Ctrl+C: Cancel]  |
    +------------------------------------------------------------+
    """

    def render(self, is_processing: bool, status_text: str = "") -> Panel:
        """
        Render the status bar.

        Args:
            is_processing: Whether the system is currently processing
            status_text: Additional status text to display

        Returns:
            Rich Panel with status bar
        """
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=3)
        grid.add_column(justify="right", ratio=1)

        if is_processing:
            left = Text()
            left.append("[Processing] ", style="bold yellow")
            left.append(status_text or "Executing...", style="dim")
            right = Text("[Ctrl+C: Cancel]", style="dim red")
        else:
            left = Text()
            left.append("> ", style="bold green")
            left.append("Ready for input", style="dim")
            right = Text("[Ctrl+C: Exit]", style="dim")

        grid.add_row(left, right)

        return Panel(grid, style="on dark_blue")


class HeaderBar:
    """
    Top header bar with title and timestamp.

    Visual:
    +------------------------------------------------------------+
    | NIMBUS V2     AGENT OPERATING SYSTEM      2026-01-29 12:00 |
    +------------------------------------------------------------+
    """

    def render(self, title: str = "NIMBUS V2") -> Panel:
        """
        Render the header bar.

        Args:
            title: Title text to display

        Returns:
            Rich Panel with header bar
        """
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right", ratio=1)

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        grid.add_row(
            Text(title, style="bold cyan"),
            Text("AGENT OPERATING SYSTEM", style="bold white"),
            Text(timestamp, style="dim"),
        )

        return Panel(grid, style="on blue")
