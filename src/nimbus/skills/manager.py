from pathlib import Path
from typing import Any, Dict, List, Optional
from nimbus.skills.loader import load_skill_manifest, SkillLoaderError
from nimbus.skills.models import SkillManifest
from nimbus.skills.tools import ScriptTool
from loguru import logger

class SkillManager:
    """Manages loading and registry of skills."""
    
    def __init__(self, skill_dirs: List[Path]):
        self.skill_dirs = skill_dirs
        self.skills: Dict[str, SkillManifest] = {}
        self.tools: Dict[str, ScriptTool] = {} 

    def load_all(self) -> None:
        """Scan configured directories and load all skills."""
        for root_dir in self.skill_dirs:
            if not root_dir.exists():
                logger.warning(f"Skill directory not found: {root_dir}")
                continue
                
            if not root_dir.is_dir():
                logger.warning(f"Skill path is not a directory: {root_dir}")
                continue

            # Iterate through subdirectories (each sub is a potential skill)
            for skill_dir in root_dir.iterdir():
                if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                    try:
                        manifest = load_skill_manifest(skill_dir)
                        self.register_skill(manifest)
                        logger.info(f"Loaded skill: {manifest.name} (v{manifest.version})")
                    except SkillLoaderError as e:
                        logger.error(f"Failed to load skill from {skill_dir}: {e}")
                    except Exception as e:
                        logger.exception(f"Unexpected error loading skill {skill_dir}: {e}")

    def register_skill(self, manifest: SkillManifest) -> None:
        """Register a loaded skill manifest."""
        self.skills[manifest.name] = manifest
        
        # Register tools
        for tool_config in manifest.tools:
            if tool_config.name in self.tools:
                logger.warning(f"Overwrite tool warning: {tool_config.name} (from {manifest.name}) replaces existing.")
            
            # Create tool wrapper
            tool_wrapper = ScriptTool(tool_config, manifest.root_path)
            self.tools[tool_config.name] = tool_wrapper

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get OpenAI-compatible tool definitions for all loaded skills."""
        return [tool.tool_definition for tool in self.tools.values()]

    def get_tool_func(self, name: str) -> Optional[Any]:
        """Get the callable function for a tool name."""
        tool = self.tools.get(name)
        if tool:
            return tool.__call__
        return None

    def get_skill_by_tool(self, tool_name: str) -> Optional[SkillManifest]:
        """Find which skill provides a given tool."""
        for skill in self.skills.values():
            for t in skill.tools:
                if t.name == tool_name:
                    return skill
        return None
