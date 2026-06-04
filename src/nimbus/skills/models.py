"""Skill manifest models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class SkillManifest:
    """A loaded skill from a SKILL.md directory."""

    name: str
    description: str
    instructions: str
    path: Path
    version: str = ""
    default_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def render_instructions(self, context: Dict[str, Any] | None = None) -> str:
        """Render skill instructions with lightweight context placeholders."""
        context = context or {}
        text = self.instructions
        replacements = {
            "skill_name": self.name,
            "session_id": str(context.get("session_id", "")),
            "workspace": str(context.get("workspace", "")),
            "scratchpad": str(context.get("scratchpad", "")),
            "goal": str(context.get("goal", "")),
        }
        for key, value in replacements.items():
            text = text.replace("{" + key + "}", value)
        return text.strip()

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "default_enabled": self.default_enabled,
            "path": str(self.path),
        }
