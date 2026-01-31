"""
TUI Dashboard Configuration

Provides DashboardConfig dataclass with all configurable parameters
for the TUI Dashboard.
"""

from dataclasses import dataclass


@dataclass
class DashboardConfig:
    """
    Configuration for TUI Dashboard.

    Attributes:
        refresh_rate: UI refresh rate per second (default: 4.0)
        chat_ratio: Chat panel width ratio (default: 0.7)
        max_chat_history: Maximum messages to keep in chat (default: 100)
        max_events: Maximum events to display (default: 50)
        debounce_ms: State update debounce in milliseconds (default: 100)
        min_terminal_width: Minimum recommended terminal width (default: 120)
        min_terminal_height: Minimum recommended terminal height (default: 30)
    """

    refresh_rate: float = 4.0
    chat_ratio: float = 0.7
    max_chat_history: int = 100
    max_events: int = 50
    debounce_ms: int = 100
    min_terminal_width: int = 120
    min_terminal_height: int = 30
