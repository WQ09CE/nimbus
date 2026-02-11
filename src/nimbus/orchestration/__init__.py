"""
Nimbus Orchestration Layer.

Provides high-level tools and prompts for Agent orchestration.
(Legacy DualAgentOrchestrator has been merged into AgentOS kernel).
"""

from .dispatch_tool import DispatchTool, DispatchToolConfig
from .prompts import PromptManager

__all__ = ["DispatchTool", "DispatchToolConfig", "PromptManager"]
