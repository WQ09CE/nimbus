"""
Nimbus V2 TUI

Claude Code-style TUI for Nimbus V2 AgentOS.

Two TUI implementations available:
1. **NimbusTUI** (Textual) - Modern TUI with widgets, recommended
2. **SimpleTUI** (Rich) - Simpler fallback

Usage:
    # Textual TUI (recommended)
    from nimbus.tui import NimbusTUI
    from nimbus.agentos import create_agent_os

    agent_os = create_agent_os(llm)
    app = NimbusTUI(agent_os=agent_os)
    await app.run_async()

    # Simple TUI (fallback)
    from nimbus.tui import SimpleTUI
    tui = SimpleTUI(agent_os=agent_os)
    await tui.run()

CLI:
    python -m nimbus.tui.cli             # Textual TUI with Gemini
    python -m nimbus.tui.cli --simple    # Simple TUI fallback
    python -m nimbus.tui.cli --demo      # Demo mode (no LLM)
"""

from nimbus.tui.config import DashboardConfig
from nimbus.tui.textual_app import NimbusTUI
from nimbus.tui.simple_tui import SimpleTUI

__all__ = [
    "NimbusTUI",
    "SimpleTUI",
    "DashboardConfig",
]

__version__ = "2.0.0"
