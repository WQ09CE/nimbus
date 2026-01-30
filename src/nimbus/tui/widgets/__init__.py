"""
TUI Dashboard Widgets

Provides all widget classes for the dashboard.
"""

from nimbus.tui.widgets.chat import ChatPanel
from nimbus.tui.widgets.dag import DAGWidget
from nimbus.tui.widgets.memory import MemoryWidget
from nimbus.tui.widgets.process import ProcessWidget
from nimbus.tui.widgets.status import StatusBar
from nimbus.tui.widgets.vcpu import VCPUWidget

__all__ = [
    "ChatPanel",
    "DAGWidget",
    "MemoryWidget",
    "ProcessWidget",
    "StatusBar",
    "VCPUWidget",
]
