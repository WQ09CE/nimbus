"""
TUI Dashboard Process Widget

Provides ProcessWidget for displaying the process list.
"""

from __future__ import annotations

from typing import Any, Dict

from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class ProcessWidget:
    """
    Displays process list.

    Visual:
    +-- Processes ----------------------------+
    | > proc-a1b2 [RUNNING] eye               |
    |   proc-c3d4 [PENDING] body              |
    |   proc-e5f6 [SUCCEEDED]                 |
    +-----------------------------------------+
    """

    # State color mapping
    STATE_COLORS: Dict[str, str] = {
        "RUNNING": "bold green",
        "PENDING": "yellow",
        "SUCCEEDED": "dim green",
        "FAILED": "red",
        "CANCELLED": "dim red",
    }

    def render(self, processes: Dict[str, Dict[str, Any]]) -> Panel:
        """
        Render the process widget.

        Args:
            processes: Dict of process_id -> process_info
                       process_info should have 'state' and optionally 'role', 'goal'

        Returns:
            Rich Panel with process list
        """
        if not processes:
            return Panel(
                Text("No active processes", style="dim"),
                title="Processes",
                border_style="cyan",
            )

        table = Table(box=None, expand=True, show_header=False, padding=(0, 1))
        table.add_column("Indicator", width=1)
        table.add_column("PID", max_width=12)
        table.add_column("State", max_width=12)
        table.add_column("Role", max_width=10)

        for pid, info in processes.items():
            state = info.get("state", "PENDING")
            role = info.get("role", "")

            # Running process gets indicator
            indicator = ">" if state == "RUNNING" else " "
            state_style = self.STATE_COLORS.get(state, "white")

            table.add_row(
                Text(indicator, style="bold cyan"),
                Text(pid[:12], style="dim"),
                Text(f"[{state}]", style=state_style),
                Text(role, style="dim cyan") if role else Text("", style="dim"),
            )

        return Panel(table, title="Processes", border_style="cyan")
