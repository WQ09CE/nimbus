"""Filesystem loader for Nimbus SKILL.md directories."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .models import SkillManifest

logger = logging.getLogger("nimbus.skills.loader")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class SkillLoader:
    """Load a single skill directory containing SKILL.md."""

    def load_dir(self, path: Path) -> Optional[SkillManifest]:
        skill_file = path / "SKILL.md"
        if not skill_file.exists() or not skill_file.is_file():
            return None

        try:
            raw = skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read skill %s: %s", skill_file, exc)
            return None

        metadata: Dict[str, Any] = {}
        body = raw
        match = _FRONTMATTER_RE.match(raw)
        if match:
            frontmatter, body = match.groups()
            try:
                parsed = yaml.safe_load(frontmatter) or {}
                if isinstance(parsed, dict):
                    metadata = parsed
            except yaml.YAMLError as exc:
                logger.warning("Invalid skill frontmatter in %s: %s", skill_file, exc)

        name = str(metadata.get("name") or path.name).strip()
        if not name:
            logger.warning("Ignoring unnamed skill at %s", skill_file)
            return None

        return SkillManifest(
            name=name,
            description=str(metadata.get("description") or "").strip(),
            version=str(metadata.get("version") or "").strip(),
            default_enabled=bool(metadata.get("default_enabled", False)),
            instructions=body.strip(),
            path=path,
            metadata=metadata,
        )
