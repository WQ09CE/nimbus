"""
Context Protocol - Structured Goal Documents for Specialist Agents.

Replaces LLM-based goal summarization with programmatic composition.
Zero information loss, deterministic, no extra LLM call.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class GoalDocument:
    """
    Structured goal document for specialist agents.

    Composed programmatically from orchestrator's tool call arguments.
    Passed verbatim to the specialist — no LLM summarization.
    """
    mission: str           # The specific task (verbatim from orchestrator)
    context: str = ""      # Relevant code/findings from prior steps
    workspace: str = ""    # Workspace path
    constraints: List[str] = field(default_factory=list)
    expected_output: str = ""

    # Context cap to prevent specialist context overflow
    MAX_CONTEXT_CHARS: int = 16_000

    def render(self) -> str:
        """Render the goal document as a structured markdown string."""
        parts = [f"## Mission\n{self.mission}"]

        if self.context:
            ctx = self.context
            if len(ctx) > self.MAX_CONTEXT_CHARS:
                ctx = ctx[:self.MAX_CONTEXT_CHARS] + "\n\n[Context truncated]"
            parts.append(f"## Context\n{ctx}")

        if self.workspace:
            parts.append(f"## Workspace\n{self.workspace}")

        if self.constraints:
            constraints_str = "\n".join(f"- {c}" for c in self.constraints)
            parts.append(f"## Constraints\n{constraints_str}")

        if self.expected_output:
            parts.append(f"## Expected Output\n{self.expected_output}")

        return "\n\n".join(parts)
