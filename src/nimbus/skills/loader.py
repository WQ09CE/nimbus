from pathlib import Path

import yaml

from nimbus.skills.models import SkillManifest


class SkillLoaderError(Exception):
    """Exception raised when loading a skill fails."""
    pass

def load_skill_manifest(skill_dir: Path) -> SkillManifest:
    """
    Load a skill manifest from a directory.
    
    Args:
        skill_dir: Path to the skill directory containing SKILL.md
        
    Returns:
        The loaded SkillManifest
        
    Raises:
        SkillLoaderError: If SKILL.md is missing or invalid
    """
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        raise SkillLoaderError(f"SKILL.md not found in {skill_dir}")

    try:
        content = skill_file.read_text(encoding="utf-8")

        # Parse Frontmatter
        # Format expects:
        # ---
        # yaml...
        # ---
        # markdown...

        parts = content.split("---", 2)
        if len(parts) < 3:
            # Case: No frontmatter or empty frontmatter
            if content.strip().startswith("---"):
                 # maybe just yaml?
                 raise SkillLoaderError(f"Invalid SKILL.md format in {skill_dir}: Missing closing '---'")
            else:
                # No frontmatter
                raise SkillLoaderError(f"Invalid SKILL.md format in {skill_dir}: Missing frontmatter")

        # parts[0] should be empty string (before first ---)
        # parts[1] is yaml
        # parts[2] is markdown body

        yaml_text = parts[1]
        body = parts[2].strip()

        try:
            frontmatter = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            raise SkillLoaderError(f"Invalid YAML in {skill_file}: {e}")

        if not isinstance(frontmatter, dict):
             raise SkillLoaderError(f"Invalid YAML in {skill_file}: Root must be a dictionary")

        return SkillManifest.from_yaml(frontmatter, body, root_path=skill_dir)

    except Exception as e:
        if isinstance(e, SkillLoaderError):
            raise
        raise SkillLoaderError(f"Failed to load skill from {skill_dir}: {e}")
