"""Plugin data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nimbus.core.tools.registry import ToolDefinition
from nimbus.skills.models import SkillManifest


@dataclass(frozen=True)
class PluginManifest:
    """Runtime manifest returned by a plugin hook."""

    name: str
    version: str = ""
    description: str = ""
    default_enabled: bool = False
    trusted: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "default_enabled": self.default_enabled,
            "trusted": self.trusted,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PluginDescriptor:
    """Statically discovered plugin without executing plugin code."""

    name: str
    source: str
    version: str = ""
    description: str = ""
    path: Optional[Path] = None
    entry: str = ""
    default_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "version": self.version,
            "description": self.description,
            "path": str(self.path) if self.path else None,
            "entry": self.entry,
            "default_enabled": self.default_enabled,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PluginContext:
    """Activation context passed to plugin hooks."""

    plugin_name: str
    generation: int
    session_id: str = ""
    workspace: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolContribution:
    """Tool exposed by a plugin."""

    definition: ToolDefinition
    handler: Callable[..., Any]
    plugin_name: str = ""
    requires_approval: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillContribution:
    """Skill exposed by a plugin."""

    manifest: SkillManifest
    plugin_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginSnapshot:
    """Immutable generation of activated plugin contributions."""

    generation: int
    manifests: Dict[str, PluginManifest] = field(default_factory=dict)
    tools: List[ToolContribution] = field(default_factory=list)
    skills: List[SkillManifest] = field(default_factory=list)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "generation": self.generation,
            "plugins": {
                name: manifest.to_public_dict()
                for name, manifest in self.manifests.items()
            },
            "tools": [
                {
                    "name": tool.definition.name,
                    "description": tool.definition.description,
                    "plugin_name": tool.plugin_name,
                    "requires_approval": tool.requires_approval,
                }
                for tool in self.tools
            ],
            "skills": [skill.to_public_dict() for skill in self.skills],
        }
