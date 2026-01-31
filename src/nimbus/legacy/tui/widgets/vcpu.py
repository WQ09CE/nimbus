"""
TUI Dashboard VCPU Widget

Provides VCPUWidget for displaying VCPU execution state.
"""

from __future__ import annotations

from typing import Dict

from rich.panel import Panel
from rich.text import Text


class VCPUWidget:
    """
    Displays VCPU execution state.

    Visual:
    +-- VCPU ------------------------------+
    | Iteration: 3/50                      |
    | Status: RUNNING                      |
    |                                      |
    | Timing:                              |
    |   Think:   1.2s                      |
    |   Decode:  0.01s                     |
    |   Execute: 0.5s                      |
    +--------------------------------------+
    """

    def render(
        self,
        iteration: int,
        max_iterations: int,
        is_running: bool,
        timing: Dict[str, int],
    ) -> Panel:
        """
        Render the VCPU widget.

        Args:
            iteration: Current iteration number
            max_iterations: Maximum iterations allowed
            is_running: Whether VCPU is currently running
            timing: Dict of timing metrics in milliseconds

        Returns:
            Rich Panel with VCPU status
        """
        content = Text()

        # Iteration progress
        content.append("Iteration: ", style="dim")
        content.append(f"{iteration}", style="bold cyan")
        content.append(f"/{max_iterations}\n", style="dim")

        # Status indicator
        status = "RUNNING" if is_running else "IDLE"
        status_style = "bold green" if is_running else "dim"
        content.append("Status: ", style="dim")
        content.append(f"{status}\n", style=status_style)

        # Timing breakdown (if available)
        if timing:
            content.append("\nTiming:\n", style="dim")
            for key, ms in timing.items():
                seconds = ms / 1000
                content.append(f"  {key.capitalize()}: ", style="dim")
                content.append(f"{seconds:.2f}s\n", style="cyan")
        else:
            # Show placeholder timing
            content.append("\nTiming:\n", style="dim")
            content.append("  Think:   ", style="dim")
            content.append("--\n", style="dim")
            content.append("  Execute: ", style="dim")
            content.append("--\n", style="dim")

        return Panel(content, title="VCPU", border_style="cyan")
