"""Skill discovery and prompt rendering."""

from __future__ import annotations

import logging
from importlib.resources import files
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from nimbus.config import NIMBUS_HOME, NimbusConfig

from .loader import SkillLoader
from .models import SkillManifest

logger = logging.getLogger("nimbus.skills.manager")


class SkillManager:
    """Discover and load Nimbus skills from built-in and user directories."""

    def __init__(self, roots: Iterable[Path] | None = None):
        self.roots = list(roots or [])
        self._loader = SkillLoader()
        self._skills: Dict[str, SkillManifest] = {}

    @classmethod
    def from_config(cls, config: NimbusConfig) -> "SkillManager":
        roots: List[Path] = [builtin_skills_root(), NIMBUS_HOME / "skills"]
        roots.extend(Path(p).expanduser() for p in config.skill_paths)
        return cls(roots)

    def discover(self) -> Dict[str, SkillManifest]:
        skills: Dict[str, SkillManifest] = {}
        for root in self.roots:
            if not root.exists() or not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                manifest = self._loader.load_dir(child)
                if manifest:
                    skills[manifest.name] = manifest
        self._skills = skills
        return dict(skills)

    def load_enabled(self, enabled_names: Sequence[str] | None = None) -> List[SkillManifest]:
        skills = self.discover()
        if enabled_names is None:
            names = [skill.name for skill in skills.values() if skill.default_enabled]
        else:
            names = list(enabled_names)

        loaded: List[SkillManifest] = []
        for name in names:
            skill = skills.get(name)
            if not skill:
                logger.warning("Configured skill '%s' was not found", name)
                continue
            loaded.append(skill)
        return loaded

    def list_skills(self) -> List[dict]:
        return [skill.to_public_dict() for skill in self.discover().values()]

    @staticmethod
    def render_system_instructions(
        skills: Sequence[SkillManifest],
        context: dict | None = None,
    ) -> str:
        if not skills:
            return ""
        sections = ["# Active Skills"]
        for skill in skills:
            rendered = skill.render_instructions(context)
            if not rendered:
                continue
            sections.append(f"## {skill.name}\n{rendered}")
        return "\n\n".join(sections).strip()


def builtin_skills_root() -> Path:
    return Path(str(files("nimbus.skills") / "builtin"))
