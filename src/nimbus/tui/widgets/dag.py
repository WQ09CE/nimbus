"""
TUI Dashboard DAG Widget

Provides DAGWidget for displaying DAG execution progress.
"""

from __future__ import annotations

from typing import Dict, Optional

from rich.panel import Panel
from rich.text import Text


class DAGWidget:
    """
    Displays DAG execution progress.

    Visual:
    +-- DAG: job-12345 -----------------------+
    | Progress: ========-- 8/10               |
    |                                         |
    | SCAN [OK]  PLAN [OK]  CODE [RUN]        |
    +-----------------------------------------+
    """

    # State icon mapping
    STATE_ICONS: Dict[str, str] = {
        "SUCCEEDED": "[OK]",
        "RUNNING": "[>>]",
        "FAILED": "[XX]",
        "PENDING": "[..]",
        "READY": "[>>]",
        "CANCELLED": "[--]",
    }

    # State style mapping
    STATE_STYLES: Dict[str, str] = {
        "SUCCEEDED": "green",
        "RUNNING": "bold yellow",
        "FAILED": "red",
        "PENDING": "dim",
        "READY": "cyan",
        "CANCELLED": "dim red",
    }

    def render(
        self,
        dag_id: Optional[str],
        status: Optional[Dict[str, int]],
        tasks: Optional[Dict[str, str]],
    ) -> Panel:
        """
        Render the DAG widget.

        Args:
            dag_id: Current DAG ID (or None if no active DAG)
            status: Dict with task counts by state (total, succeeded, failed, etc.)
            tasks: Dict of task_id -> task_state

        Returns:
            Rich Panel with DAG status
        """
        if not dag_id:
            return Panel(
                Text("No active DAG", style="dim"),
                title="DAG",
                border_style="cyan",
            )

        content = Text()

        # Progress bar
        if status:
            total = status.get("total", 0)
            succeeded = status.get("succeeded", 0)
            failed = status.get("failed", 0)
            running = status.get("running", 0)

            if total > 0:
                completed = succeeded + failed
                progress = completed / total
                bar_width = 20
                filled = int(progress * bar_width)
                bar = "=" * filled + "-" * (bar_width - filled)

                # Color based on state
                if failed > 0:
                    bar_style = "red"
                elif running > 0:
                    bar_style = "yellow"
                else:
                    bar_style = "cyan"

                content.append("Progress: ", style="dim")
                content.append(f"{bar} ", style=bar_style)
                content.append(f"{completed}/{total}\n", style="cyan")

        # Task status line
        if tasks:
            content.append("\n")
            task_items = list(tasks.items())

            # Show tasks in a compact format
            for tid, state in task_items[:6]:  # Limit to 6 tasks for space
                state_icon = self.STATE_ICONS.get(state, "[??]")
                state_style = self.STATE_STYLES.get(state, "dim")

                # Truncate task ID for display
                display_id = tid[:8] if len(tid) > 8 else tid
                content.append(f"{display_id} ", style="dim")
                content.append(f"{state_icon} ", style=state_style)

            # Indicate if there are more tasks
            if len(task_items) > 6:
                content.append(f"\n... +{len(task_items) - 6} more", style="dim")

        # Truncate DAG ID for title
        title_dag_id = dag_id[:16] if len(dag_id) > 16 else dag_id
        return Panel(
            content,
            title=f"DAG: {title_dag_id}",
            border_style="cyan",
        )
