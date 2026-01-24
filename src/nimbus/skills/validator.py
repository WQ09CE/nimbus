"""Skill validation for the Nimbus Agent Framework.

This module provides validation for skill definitions and skill Markdown files.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

from .schema import SkillDefinition, SkillParameter


class ValidationError:
    """Represents a single validation error.

    Attributes:
        message: Error description.
        field: Field that caused the error (optional).
        severity: Error severity ("error" or "warning").
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        severity: str = "error",
    ):
        self.message = message
        self.field = field
        self.severity = severity

    def __str__(self) -> str:
        if self.field:
            return f"[{self.severity.upper()}] {self.field}: {self.message}"
        return f"[{self.severity.upper()}] {self.message}"

    def __repr__(self) -> str:
        return f"ValidationError({self.message!r}, field={self.field!r})"


class SkillValidator:
    """Validator for skill definitions and files.

    Validates:
    - Required fields (name, description)
    - Parameter definitions
    - Type validity
    - Naming conventions
    - Implementation syntax (if present)
    """

    # Valid parameter types
    VALID_TYPES: Set[str] = {"string", "number", "integer", "boolean", "array", "object"}

    # Pattern for valid skill/parameter names (snake_case or camelCase)
    NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

    # Pattern for semantic version
    VERSION_PATTERN = re.compile(r"^\d+\.\d+(\.\d+)?$")

    # YAML frontmatter pattern
    FRONTMATTER_PATTERN = re.compile(
        r"^---\s*\n(.*?)\n---\s*\n?",
        re.DOTALL,
    )

    def __init__(self, strict: bool = False):
        """Initialize the validator.

        Args:
            strict: If True, treat warnings as errors.
        """
        self.strict = strict

    def validate(self, skill: SkillDefinition) -> List[ValidationError]:
        """Validate a skill definition.

        Args:
            skill: SkillDefinition to validate.

        Returns:
            List of validation errors (empty if valid).
        """
        errors: List[ValidationError] = []

        # Validate name
        errors.extend(self._validate_name(skill.name, "name"))

        # Validate description
        if not skill.description:
            errors.append(ValidationError("Description is required", "description"))
        elif len(skill.description) < 10:
            errors.append(ValidationError(
                "Description should be at least 10 characters",
                "description",
                "warning",
            ))

        # Validate version
        if skill.version and not self.VERSION_PATTERN.match(skill.version):
            errors.append(ValidationError(
                f"Invalid version format: {skill.version}. Use semantic versioning (e.g., 1.0 or 1.0.0)",
                "version",
                "warning",
            ))

        # Validate parameters
        errors.extend(self._validate_parameters(skill.parameters))

        # Validate tags
        for tag in skill.tags:
            if not isinstance(tag, str) or not tag.strip():
                errors.append(ValidationError(
                    f"Invalid tag: {tag}",
                    "tags",
                    "warning",
                ))

        # Filter warnings if strict mode
        if self.strict:
            return errors
        return [e for e in errors if e.severity == "error"]

    def _validate_name(
        self,
        name: str,
        field: str,
    ) -> List[ValidationError]:
        """Validate a name field.

        Args:
            name: Name to validate.
            field: Field name for error reporting.

        Returns:
            List of validation errors.
        """
        errors: List[ValidationError] = []

        if not name:
            errors.append(ValidationError(f"{field} is required", field))
        elif not self.NAME_PATTERN.match(name):
            errors.append(ValidationError(
                f"Invalid {field}: '{name}'. Use snake_case (e.g., get_weather)",
                field,
            ))

        return errors

    def _validate_parameters(
        self,
        parameters: List[SkillParameter],
    ) -> List[ValidationError]:
        """Validate parameter definitions.

        Args:
            parameters: List of parameters to validate.

        Returns:
            List of validation errors.
        """
        errors: List[ValidationError] = []
        seen_names: Set[str] = set()

        for i, param in enumerate(parameters):
            field_prefix = f"parameters[{i}]"

            # Check for duplicate names
            if param.name in seen_names:
                errors.append(ValidationError(
                    f"Duplicate parameter name: {param.name}",
                    field_prefix,
                ))
            seen_names.add(param.name)

            # Validate parameter name
            errors.extend(self._validate_name(param.name, f"{field_prefix}.name"))

            # Validate type
            if param.type not in self.VALID_TYPES:
                errors.append(ValidationError(
                    f"Invalid type: {param.type}. Valid types: {', '.join(sorted(self.VALID_TYPES))}",
                    f"{field_prefix}.type",
                ))

            # Validate description
            if not param.description:
                errors.append(ValidationError(
                    "Parameter description is recommended",
                    f"{field_prefix}.description",
                    "warning",
                ))

            # Validate enum values
            if param.enum:
                if not isinstance(param.enum, list) or len(param.enum) == 0:
                    errors.append(ValidationError(
                        "Enum must be a non-empty list",
                        f"{field_prefix}.enum",
                    ))
                elif param.default is not None and param.default not in param.enum:
                    errors.append(ValidationError(
                        f"Default value '{param.default}' not in enum: {param.enum}",
                        f"{field_prefix}.default",
                    ))

            # Validate array items
            if param.type == "array" and param.items:
                if not isinstance(param.items, dict):
                    errors.append(ValidationError(
                        "Array items must be an object with type definition",
                        f"{field_prefix}.items",
                    ))

            # Validate object properties
            if param.type == "object" and param.properties:
                if not isinstance(param.properties, dict):
                    errors.append(ValidationError(
                        "Object properties must be a dictionary",
                        f"{field_prefix}.properties",
                    ))

        return errors

    def validate_file(self, path: Path) -> List[ValidationError]:
        """Validate a skill Markdown file.

        Validates:
        - File exists and is readable
        - Has valid YAML frontmatter
        - Frontmatter contains required fields
        - Skill definition is valid

        Args:
            path: Path to the .md skill file.

        Returns:
            List of validation errors.
        """
        errors: List[ValidationError] = []
        path = Path(path)

        # Check file exists
        if not path.exists():
            errors.append(ValidationError(f"File not found: {path}"))
            return errors

        # Check extension
        if path.suffix.lower() != ".md":
            errors.append(ValidationError(
                f"Expected .md file, got: {path.suffix}",
                severity="warning",
            ))

        # Read file
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            errors.append(ValidationError(f"Failed to read file: {e}"))
            return errors

        # Check for frontmatter
        match = self.FRONTMATTER_PATTERN.match(content)
        if not match:
            errors.append(ValidationError("Missing or invalid YAML frontmatter"))
            return errors

        # Parse YAML
        frontmatter_str = match.group(1)
        try:
            frontmatter = yaml.safe_load(frontmatter_str)
        except yaml.YAMLError as e:
            errors.append(ValidationError(f"Invalid YAML: {e}"))
            return errors

        if not isinstance(frontmatter, dict):
            errors.append(ValidationError("Frontmatter must be a YAML dictionary"))
            return errors

        # Validate required fields
        if "name" not in frontmatter:
            errors.append(ValidationError("Missing required field: name"))

        if "description" not in frontmatter:
            errors.append(ValidationError(
                "Missing field: description",
                severity="warning",
            ))

        # Create and validate skill definition
        if "name" in frontmatter:
            try:
                skill = SkillDefinition.from_dict(frontmatter)
                errors.extend(self.validate(skill))
            except Exception as e:
                errors.append(ValidationError(f"Failed to parse skill: {e}"))

        return errors

    def validate_frontmatter(
        self,
        frontmatter: Dict[str, Any],
    ) -> List[ValidationError]:
        """Validate YAML frontmatter dictionary.

        Args:
            frontmatter: Parsed YAML frontmatter.

        Returns:
            List of validation errors.
        """
        errors: List[ValidationError] = []

        if not isinstance(frontmatter, dict):
            errors.append(ValidationError("Frontmatter must be a dictionary"))
            return errors

        # Required fields
        if "name" not in frontmatter:
            errors.append(ValidationError("Missing required field: name"))

        # Recommended fields
        if "description" not in frontmatter:
            errors.append(ValidationError(
                "Missing recommended field: description",
                severity="warning",
            ))

        # Validate parameters if present
        if "parameters" in frontmatter:
            params = frontmatter["parameters"]
            if not isinstance(params, list):
                errors.append(ValidationError(
                    "Parameters must be a list",
                    "parameters",
                ))
            else:
                for i, param in enumerate(params):
                    if not isinstance(param, dict):
                        errors.append(ValidationError(
                            f"Parameter {i} must be an object",
                            f"parameters[{i}]",
                        ))
                    elif "name" not in param:
                        errors.append(ValidationError(
                            f"Parameter {i} missing name",
                            f"parameters[{i}]",
                        ))

        return errors

    def get_all_errors(
        self,
        skill: SkillDefinition,
    ) -> List[ValidationError]:
        """Get all validation errors including warnings.

        Args:
            skill: SkillDefinition to validate.

        Returns:
            List of all errors and warnings.
        """
        old_strict = self.strict
        self.strict = True
        try:
            return self.validate(skill)
        finally:
            self.strict = old_strict

    def is_valid(self, skill: SkillDefinition) -> bool:
        """Check if a skill definition is valid.

        Args:
            skill: SkillDefinition to validate.

        Returns:
            True if valid (no errors), False otherwise.
        """
        return len(self.validate(skill)) == 0

    def is_file_valid(self, path: Path) -> bool:
        """Check if a skill file is valid.

        Args:
            path: Path to skill file.

        Returns:
            True if valid, False otherwise.
        """
        errors = self.validate_file(path)
        return all(e.severity != "error" for e in errors)


def validate_skill(skill: SkillDefinition, strict: bool = False) -> List[str]:
    """Convenience function to validate a skill definition.

    Args:
        skill: SkillDefinition to validate.
        strict: If True, include warnings.

    Returns:
        List of error messages.
    """
    validator = SkillValidator(strict=strict)
    errors = validator.validate(skill)
    return [str(e) for e in errors]


def validate_skill_file(path: Path, strict: bool = False) -> List[str]:
    """Convenience function to validate a skill file.

    Args:
        path: Path to skill file.
        strict: If True, include warnings.

    Returns:
        List of error messages.
    """
    validator = SkillValidator(strict=strict)
    errors = validator.validate_file(path)
    if not strict:
        errors = [e for e in errors if e.severity == "error"]
    return [str(e) for e in errors]
