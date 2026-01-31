"""
TUI Dashboard Memory Widget

Provides MemoryWidget for displaying memory/token usage.
"""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text


class MemoryWidget:
    """
    Displays memory/token usage.

    Visual:
    +-- Memory ----------------------------+
    | Tokens: ========-- 8.2K/16K          |
    | Stack:  2 frames                     |
    +--------------------------------------+
    """

    def render(
        self,
        tokens: int,
        max_tokens: int,
        stack_depth: int,
    ) -> Panel:
        """
        Render the memory widget.

        Args:
            tokens: Current token usage
            max_tokens: Maximum token budget
            stack_depth: Current stack depth (frames)

        Returns:
            Rich Panel with memory status
        """
        content = Text()

        # Token usage bar
        if max_tokens > 0:
            ratio = min(1.0, tokens / max_tokens)
            bar_width = 15
            filled = int(ratio * bar_width)
            bar = "=" * filled + "-" * (bar_width - filled)

            # Format token counts
            tokens_k = tokens / 1000
            max_k = max_tokens / 1000

            # Color based on usage
            if ratio < 0.7:
                bar_style = "green"
            elif ratio < 0.9:
                bar_style = "yellow"
            else:
                bar_style = "red"

            content.append("Tokens: ", style="dim")
            content.append(f"{bar} ", style=bar_style)
            content.append(f"{tokens_k:.1f}K/{max_k:.0f}K\n", style="dim")
        else:
            content.append("Tokens: ", style="dim")
            content.append("--/--\n", style="dim")

        # Stack depth
        content.append("Stack:  ", style="dim")
        content.append(f"{stack_depth} ", style="cyan")
        content.append("frames\n", style="dim")

        return Panel(content, title="Memory", border_style="cyan")
