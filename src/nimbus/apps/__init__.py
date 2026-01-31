"""
Nimbus Application Layer.

Architecture Layer: 2 (Application)
Von Neumann Role: Process Definition

This package contains pre-built applications built on top of Agent OS Kernel.
Each application provides a specific functionality pattern:

- CodeAgent: Code exploration, modification, and execution
- (Future) ChatAgent: Conversational assistant
- (Future) TaskAgent: Multi-step task execution
"""

__layer__ = 2
__role__ = "Application"

from nimbus.apps.code_agent import CodeAgent

__all__ = ["CodeAgent"]
