"""
Agent Profile Definitions.

Defines the "personality" and capabilities of an AgentOS instance.
This unifies the configuration for Single Agent, Core Agent, and Executor Agent.
"""

from dataclasses import dataclass, field
from typing import List

# Delayed import to avoid circular dependency with AgentOS -> orchestration
# from nimbus.orchestration.prompts import PromptManager

@dataclass
class AgentProfile:
    """
    Configuration profile for an Agent.
    """
    name: str
    role: str  # "core", "executor", "standard", "reviewer"

    # Tool Access Control
    allowed_tools: List[str] = field(default_factory=list)  # Whitelist of tool names
    kernel_tools: bool = False  # Whether to load kernel tools (deprecated concept, but kept for compat)

    # Prompting
    system_prompt: str = ""  # The generated system prompt

    # Runtime Config
    max_iterations: int = 20
    max_consecutive_thoughts: int = 1  # Text-only response = final answer, stop immediately

    @classmethod
    def create_standard(cls, model_id: str = "default") -> "AgentProfile":
        """Create a standard single-agent profile (All tools)."""
        from nimbus.orchestration.prompts import PromptManager
        # Standard agent gets all default tools usually registered by the system
        return cls(
            name="standard",
            role="standard",
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"], # Explicit list or "ALL"
            system_prompt=PromptManager.get_system_prompt("executor", model_id), # Behaves like an executor but standalone
            max_iterations=50,
            max_consecutive_thoughts=1
        )

    @classmethod
    def create_core(cls, model_id: str = "default") -> "AgentProfile":
        """Create a Core Agent profile (Orchestrator)."""
        from nimbus.orchestration.prompts import PromptManager
        return cls(
            name="core",
            role="core",
            # Bash replaces CoreBash (Review P0)
            allowed_tools=["Read", "Bash", "Dispatch", "Verify", "ReviewCommittee", "Memo"],
            system_prompt=PromptManager.get_system_prompt("core", model_id),
            max_iterations=30,
            max_consecutive_thoughts=1
        )

    @classmethod
    def create_executor(cls, model_id: str = "default") -> "AgentProfile":
        """Create an Executor Agent profile (Implementer)."""
        from nimbus.orchestration.prompts import PromptManager
        return cls(
            name="executor",
            role="executor",
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            system_prompt=PromptManager.get_system_prompt("executor", model_id),
            max_iterations=20,
            max_consecutive_thoughts=1
        )
