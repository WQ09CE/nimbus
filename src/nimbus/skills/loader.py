"""Skill loader for loading skills from Markdown files with YAML frontmatter.

This module provides functionality to load skill definitions from Markdown
files that contain YAML frontmatter for metadata and parameters.
"""

import re
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .schema import SkillDefinition, SkillParameter, SkillRegistry


class SkillLoadError(Exception):
    """Exception raised when skill loading fails."""

    def __init__(self, message: str, path: Optional[Path] = None):
        self.path = path
        full_message = f"{message}" if path is None else f"{path}: {message}"
        super().__init__(full_message)


class SkillLoader:
    """Loader for skill definitions from Markdown files.

    Skills are loaded from Markdown files (.md) that contain:
    - YAML frontmatter (between --- delimiters) with skill metadata
    - Optional Markdown body with documentation
    - Optional Python code block with implementation

    Example skill file:
        ---
        name: get_weather
        description: Get current weather for a location
        parameters:
          - name: location
            type: string
            required: true
        ---

        ## Description
        This skill fetches weather data.

        ## Implementation
        ```python
        async def get_weather(location: str) -> str:
            return f"Weather in {location}"
        ```

    Attributes:
        skill_dirs: List of directories to search for skills.
        registry: SkillRegistry containing loaded skills.
    """

    # Pattern to match YAML frontmatter
    FRONTMATTER_PATTERN = re.compile(
        r"^---\s*\n(.*?)\n---\s*\n?",
        re.DOTALL,
    )

    # Pattern to extract Python implementation from code blocks
    IMPLEMENTATION_PATTERN = re.compile(
        r"```python\s*\n(.*?)\n```",
        re.DOTALL,
    )

    # Default global skills directory
    DEFAULT_GLOBAL_DIR = Path.home() / ".nimbus" / "skills"

    def __init__(self, skill_dirs: Optional[List[Path]] = None):
        """Initialize the skill loader.

        Args:
            skill_dirs: List of directories to search for skills.
                       If None, uses default paths:
                       1. ~/.nimbus/skills/ (global)
                       2. ./nimbus/skills/ (project-level, detected from cwd)
        """
        if skill_dirs is None:
            skill_dirs = self._get_default_dirs()
        self.skill_dirs = [Path(d) for d in skill_dirs]
        self.registry = SkillRegistry()

    def _get_default_dirs(self) -> List[Path]:
        """Get default skill directories.

        Returns:
            List of paths to search for skills.
        """
        dirs: List[Path] = []

        # Global directory
        global_dir = self.DEFAULT_GLOBAL_DIR
        if global_dir.exists():
            dirs.append(global_dir)

        # Project-level directory (current working directory)
        cwd = Path.cwd()
        project_skill_dir = cwd / "nimbus" / "skills"
        if project_skill_dir.exists() and project_skill_dir not in dirs:
            dirs.append(project_skill_dir)

        return dirs

    def add_skill_dir(self, path: Path) -> None:
        """Add a directory to search for skills.

        Args:
            path: Directory path to add.
        """
        path = Path(path)
        if path not in self.skill_dirs:
            self.skill_dirs.append(path)

    def _parse_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        """Parse YAML frontmatter from Markdown content.

        Args:
            content: Full Markdown file content.

        Returns:
            Tuple of (frontmatter dict, remaining content).

        Raises:
            SkillLoadError: If frontmatter is invalid or missing.
        """
        match = self.FRONTMATTER_PATTERN.match(content)
        if not match:
            raise SkillLoadError("Missing or invalid YAML frontmatter")

        frontmatter_str = match.group(1)
        remaining = content[match.end():]

        try:
            frontmatter = yaml.safe_load(frontmatter_str)
            if not isinstance(frontmatter, dict):
                raise SkillLoadError("Frontmatter must be a YAML dictionary")
        except yaml.YAMLError as e:
            raise SkillLoadError(f"Invalid YAML in frontmatter: {e}")

        return frontmatter, remaining

    def _extract_implementation(self, content: str) -> Optional[str]:
        """Extract Python implementation from Markdown content.

        Looks for a Python code block, typically under an "Implementation" heading.

        Args:
            content: Markdown content (after frontmatter).

        Returns:
            Python code string, or None if not found.
        """
        match = self.IMPLEMENTATION_PATTERN.search(content)
        if match:
            return match.group(1).strip()
        return None

    def load_skill(self, path: Path) -> SkillDefinition:
        """Load a skill from a single Markdown file.

        Args:
            path: Path to the .md skill file.

        Returns:
            Loaded SkillDefinition.

        Raises:
            SkillLoadError: If loading fails.
            FileNotFoundError: If file doesn't exist.
        """
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Skill file not found: {path}")

        if not path.suffix.lower() == ".md":
            raise SkillLoadError("Skill files must have .md extension", path)

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            raise SkillLoadError(f"Failed to read file: {e}", path)

        try:
            frontmatter, body = self._parse_frontmatter(content)
        except SkillLoadError as e:
            raise SkillLoadError(str(e), path)

        # Validate required fields
        if "name" not in frontmatter:
            raise SkillLoadError("Missing required field: name", path)

        # Extract implementation from body if not in frontmatter
        if "implementation" not in frontmatter:
            impl = self._extract_implementation(body)
            if impl:
                frontmatter["implementation"] = impl

        # Add source path
        frontmatter["source_path"] = str(path.absolute())

        # Create skill definition
        try:
            skill = SkillDefinition.from_dict(frontmatter)
        except Exception as e:
            raise SkillLoadError(f"Failed to create skill definition: {e}", path)

        return skill

    def load_from_directory(self, directory: Path) -> Dict[str, SkillDefinition]:
        """Load all skills from a directory.

        Searches for:
        1. *.md files directly in the directory
        2. SKILL.md files in subdirectories (for skills with multiple files)

        Args:
            directory: Directory to search.

        Returns:
            Dictionary mapping skill names to definitions.
        """
        directory = Path(directory)
        skills: Dict[str, SkillDefinition] = {}

        if not directory.exists():
            return skills

        # Load .md files directly in directory
        for md_file in directory.glob("*.md"):
            if md_file.name.lower() == "readme.md":
                continue  # Skip README files
            try:
                skill = self.load_skill(md_file)
                skills[skill.name] = skill
            except (SkillLoadError, FileNotFoundError) as e:
                # Log but continue loading other skills
                print(f"Warning: Failed to load skill from {md_file}: {e}")

        # Load SKILL.md from subdirectories
        for subdir in directory.iterdir():
            if not subdir.is_dir():
                continue

            skill_file = subdir / "SKILL.md"
            if skill_file.exists():
                try:
                    skill = self.load_skill(skill_file)
                    skills[skill.name] = skill
                except (SkillLoadError, FileNotFoundError) as e:
                    print(f"Warning: Failed to load skill from {skill_file}: {e}")

        return skills

    def load_all(self) -> Dict[str, SkillDefinition]:
        """Load all skills from all configured directories.

        Later directories override earlier ones if skills have the same name.

        Returns:
            Dictionary mapping skill names to definitions.
        """
        all_skills: Dict[str, SkillDefinition] = {}

        for skill_dir in self.skill_dirs:
            dir_skills = self.load_from_directory(skill_dir)
            all_skills.update(dir_skills)

        # Update registry
        for skill in all_skills.values():
            self.registry.register(skill)

        return all_skills

    def get_tool_definitions(self, format: str = "claude") -> List[Dict[str, Any]]:
        """Get all loaded skills as tool definitions.

        Args:
            format: Target format ("claude" or "openai").

        Returns:
            List of tool definitions ready for LLM API.
        """
        return self.registry.get_tool_definitions(format)

    def reload(self) -> Dict[str, SkillDefinition]:
        """Reload all skills from configured directories.

        Clears the registry and reloads everything.

        Returns:
            Dictionary of newly loaded skills.
        """
        self.registry = SkillRegistry()
        return self.load_all()

    def __len__(self) -> int:
        """Return number of loaded skills."""
        return len(self.registry)

    def __contains__(self, name: str) -> bool:
        """Check if a skill is loaded."""
        return name in self.registry


def create_skill_loader(
    additional_dirs: Optional[List[Path]] = None,
    include_defaults: bool = True,
) -> SkillLoader:
    """Factory function to create a configured SkillLoader.

    Args:
        additional_dirs: Extra directories to search for skills.
        include_defaults: Whether to include default skill directories.

    Returns:
        Configured SkillLoader instance.
    """
    loader = SkillLoader(skill_dirs=[] if not include_defaults else None)

    if additional_dirs:
        for dir_path in additional_dirs:
            loader.add_skill_dir(dir_path)

    return loader
