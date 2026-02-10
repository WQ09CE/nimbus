"""Nimbus Skill System — directory-based agent capability extension."""

from nimbus.skills.models import SkillManifest, SkillToolConfig
from nimbus.skills.loader import load_skill_manifest, SkillLoaderError
from nimbus.skills.manager import SkillManager
from nimbus.skills.tools import ScriptTool

__all__ = [
    "SkillManifest",
    "SkillToolConfig",
    "load_skill_manifest",
    "SkillLoaderError",
    "SkillManager",
    "ScriptTool",
]
