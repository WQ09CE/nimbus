from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

@dataclass
class SkillToolArg:
    """Definition for a single argument of a skill tool."""
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None

@dataclass
class SkillToolConfig:
    """Configuration for a single tool within a skill."""
    name: str
    entrypoint: str
    description: str = ""
    args: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillToolConfig":
        return cls(
            name=data["name"],
            entrypoint=data.get("entrypoint") or data.get("script", ""), # Compatible with proposal v1/v2
            description=data.get("description", ""),
            args=data.get("args", {})
        )

@dataclass
class SkillManifest:
    """The complete definition of a Skill loaded from SKILL.md."""
    name: str
    version: str
    description: str
    tools: List[SkillToolConfig]
    instructions: str = ""
    root_path: Optional[Path] = None

    @classmethod
    def from_yaml(cls, frontmatter: Dict[str, Any], body: str, root_path: Optional[Path] = None) -> "SkillManifest":
        tools_data = frontmatter.get("tools", [])
        tools = [SkillToolConfig.from_dict(t) for t in tools_data]
        
        return cls(
            name=frontmatter.get("name", "unknown-skill"),
            version=str(frontmatter.get("version", "0.0.1")),
            description=frontmatter.get("description", ""),
            tools=tools,
            instructions=body,
            root_path=root_path
        )
