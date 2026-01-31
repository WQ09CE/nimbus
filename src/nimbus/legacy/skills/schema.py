"""Skill data model definitions for the Nimbus Agent Framework.

This module defines the data structures for skills that can be loaded from
YAML frontmatter + Markdown files and converted to various LLM tool formats.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SkillParameter:
    """Definition of a skill parameter.

    Attributes:
        name: Parameter name (identifier).
        type: Parameter type (string, number, boolean, array, object).
        description: Human-readable description.
        required: Whether the parameter is required.
        enum: Optional list of allowed values.
        default: Optional default value.
        items: Optional item type for array parameters.
        properties: Optional property definitions for object parameters.
    """

    name: str
    type: str  # string, number, boolean, array, object
    description: str
    required: bool = False
    enum: Optional[List[str]] = None
    default: Optional[Any] = None
    items: Optional[Dict[str, Any]] = None  # For array types
    properties: Optional[Dict[str, Any]] = None  # For object types

    def to_json_schema(self) -> Dict[str, Any]:
        """Convert parameter to JSON Schema format.

        Returns:
            Dictionary conforming to JSON Schema specification.
        """
        # Map skill types to JSON Schema types
        type_mapping = {
            "string": "string",
            "number": "number",
            "integer": "integer",
            "boolean": "boolean",
            "array": "array",
            "object": "object",
        }

        schema: Dict[str, Any] = {
            "type": type_mapping.get(self.type, self.type),
            "description": self.description,
        }

        if self.enum:
            schema["enum"] = self.enum

        if self.default is not None:
            schema["default"] = self.default

        if self.type == "array" and self.items:
            schema["items"] = self.items

        if self.type == "object" and self.properties:
            schema["properties"] = self.properties

        return schema

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillParameter":
        """Create a SkillParameter from a dictionary.

        Args:
            data: Dictionary with parameter data.

        Returns:
            SkillParameter instance.
        """
        return cls(
            name=data["name"],
            type=data.get("type", "string"),
            description=data.get("description", ""),
            required=data.get("required", False),
            enum=data.get("enum"),
            default=data.get("default"),
            items=data.get("items"),
            properties=data.get("properties"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation.

        Returns:
            Dictionary with all non-None fields.
        """
        result: Dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "required": self.required,
        }

        if self.enum:
            result["enum"] = self.enum
        if self.default is not None:
            result["default"] = self.default
        if self.items:
            result["items"] = self.items
        if self.properties:
            result["properties"] = self.properties

        return result


@dataclass
class SkillDefinition:
    """Definition of a skill that can be executed by an agent.

    A skill represents a callable function/tool that the LLM can invoke.
    Skills can be defined in Markdown files with YAML frontmatter and
    converted to various LLM tool formats (Claude, OpenAI, etc.).

    Attributes:
        name: Unique skill identifier.
        description: Human-readable description of what the skill does.
        parameters: List of parameter definitions.
        version: Semantic version string.
        author: Optional author name/email.
        tags: List of categorization tags.
        implementation: Python code string or path to implementation file.
        source_path: Path to the source .md file (if loaded from file).
    """

    name: str
    description: str
    parameters: List[SkillParameter] = field(default_factory=list)

    # Metadata
    version: str = "1.0"
    author: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    # Execution
    implementation: Optional[str] = None
    source_path: Optional[str] = None

    def get_required_parameters(self) -> List[SkillParameter]:
        """Get all required parameters.

        Returns:
            List of parameters marked as required.
        """
        return [p for p in self.parameters if p.required]

    def get_optional_parameters(self) -> List[SkillParameter]:
        """Get all optional parameters.

        Returns:
            List of parameters not marked as required.
        """
        return [p for p in self.parameters if not p.required]

    def to_tool_use_format(self) -> Dict[str, Any]:
        """Convert to Claude Tool Use API format.

        This format is compatible with Anthropic's Claude API for tool use.

        Returns:
            Dictionary in Claude Tool Use format:
            {
                "name": "skill_name",
                "description": "...",
                "input_schema": {
                    "type": "object",
                    "properties": {...},
                    "required": [...]
                }
            }
        """
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for param in self.parameters:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }

    def to_openai_format(self) -> Dict[str, Any]:
        """Convert to OpenAI Function Calling format.

        This format is compatible with OpenAI's function calling API.

        Returns:
            Dictionary in OpenAI Function format:
            {
                "type": "function",
                "function": {
                    "name": "...",
                    "description": "...",
                    "parameters": {
                        "type": "object",
                        "properties": {...},
                        "required": [...]
                    }
                }
            }
        """
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for param in self.parameters:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation.

        Returns:
            Dictionary with all skill data.
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [p.to_dict() for p in self.parameters],
            "version": self.version,
            "author": self.author,
            "tags": self.tags,
            "implementation": self.implementation,
            "source_path": self.source_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillDefinition":
        """Create a SkillDefinition from a dictionary.

        Args:
            data: Dictionary with skill data (typically from YAML frontmatter).

        Returns:
            SkillDefinition instance.
        """
        parameters = [
            SkillParameter.from_dict(p) for p in data.get("parameters", [])
        ]

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            parameters=parameters,
            version=data.get("version", "1.0"),
            author=data.get("author"),
            tags=data.get("tags", []),
            implementation=data.get("implementation"),
            source_path=data.get("source_path"),
        )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"SkillDefinition(name={self.name!r}, "
            f"parameters={len(self.parameters)}, "
            f"version={self.version!r})"
        )


@dataclass
class SkillRegistry:
    """Registry of loaded skills.

    Provides methods to register, lookup, and query skills.
    """

    skills: Dict[str, SkillDefinition] = field(default_factory=dict)

    def register(self, skill: SkillDefinition) -> None:
        """Register a skill.

        Args:
            skill: SkillDefinition to register.
        """
        self.skills[skill.name] = skill

    def unregister(self, name: str) -> Optional[SkillDefinition]:
        """Unregister a skill by name.

        Args:
            name: Skill name to unregister.

        Returns:
            The removed skill, or None if not found.
        """
        return self.skills.pop(name, None)

    def get(self, name: str) -> Optional[SkillDefinition]:
        """Get a skill by name.

        Args:
            name: Skill name to look up.

        Returns:
            SkillDefinition if found, None otherwise.
        """
        return self.skills.get(name)

    def list_skills(self) -> List[str]:
        """List all registered skill names.

        Returns:
            List of skill names.
        """
        return list(self.skills.keys())

    def list_by_tag(self, tag: str) -> List[SkillDefinition]:
        """List skills with a specific tag.

        Args:
            tag: Tag to filter by.

        Returns:
            List of matching skills.
        """
        return [s for s in self.skills.values() if tag in s.tags]

    def get_tool_definitions(self, format: str = "claude") -> List[Dict[str, Any]]:
        """Get all skill definitions in tool format.

        Args:
            format: Target format ("claude" or "openai").

        Returns:
            List of tool definitions.

        Raises:
            ValueError: If format is not recognized.
        """
        if format == "claude":
            return [s.to_tool_use_format() for s in self.skills.values()]
        elif format == "openai":
            return [s.to_openai_format() for s in self.skills.values()]
        else:
            raise ValueError(f"Unknown format: {format}. Use 'claude' or 'openai'.")

    def __len__(self) -> int:
        """Return number of registered skills."""
        return len(self.skills)

    def __contains__(self, name: str) -> bool:
        """Check if a skill is registered."""
        return name in self.skills
