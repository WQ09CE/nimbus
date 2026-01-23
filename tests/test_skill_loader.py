"""Tests for the skill loading system.

Tests cover:
- SkillParameter and SkillDefinition data models
- YAML frontmatter parsing
- Tool format conversions (Claude and OpenAI)
- SkillValidator
- SkillLoader
"""

import pytest
import tempfile
from pathlib import Path

from nimbus.skills.schema import (
    SkillParameter,
    SkillDefinition,
    SkillRegistry,
)
from nimbus.skills.loader import (
    SkillLoader,
    SkillLoadError,
    create_skill_loader,
)
from nimbus.skills.validator import (
    SkillValidator,
    ValidationError,
    validate_skill,
    validate_skill_file,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_skill_md():
    """Sample skill Markdown content."""
    return '''---
name: get_weather
version: "1.0"
description: Get current weather information for a specified location
author: test_author
tags:
  - weather
  - api

parameters:
  - name: location
    type: string
    description: City name, e.g. "San Francisco, CA"
    required: true
  - name: unit
    type: string
    description: Temperature unit
    enum: ["celsius", "fahrenheit"]
    default: "celsius"
---

## Description

This skill retrieves current weather information for a specified location.

## Usage

Call with a location string to get weather data.

## Implementation

```python
async def get_weather(location: str, unit: str = "celsius") -> str:
    # Implementation here
    return f"Weather in {location}: 20 degrees {unit}"
```
'''


@pytest.fixture
def minimal_skill_md():
    """Minimal valid skill Markdown content."""
    return '''---
name: simple_skill
description: A simple test skill
---

Just a simple skill.
'''


@pytest.fixture
def skill_with_complex_params_md():
    """Skill with complex parameter types."""
    return '''---
name: complex_skill
description: A skill with complex parameter types
parameters:
  - name: items
    type: array
    description: List of items to process
    required: true
    items:
      type: string
  - name: config
    type: object
    description: Configuration object
    properties:
      timeout:
        type: number
      retries:
        type: integer
---

Complex skill documentation.
'''


@pytest.fixture
def temp_skill_dir():
    """Create a temporary directory for skill files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# =============================================================================
# SkillParameter Tests
# =============================================================================


class TestSkillParameter:
    """Tests for SkillParameter dataclass."""

    def test_basic_creation(self):
        """Test creating a basic parameter."""
        param = SkillParameter(
            name="location",
            type="string",
            description="The city name",
            required=True,
        )

        assert param.name == "location"
        assert param.type == "string"
        assert param.description == "The city name"
        assert param.required is True
        assert param.enum is None
        assert param.default is None

    def test_parameter_with_enum(self):
        """Test parameter with enum values."""
        param = SkillParameter(
            name="unit",
            type="string",
            description="Temperature unit",
            enum=["celsius", "fahrenheit"],
            default="celsius",
        )

        assert param.enum == ["celsius", "fahrenheit"]
        assert param.default == "celsius"

    def test_to_json_schema_basic(self):
        """Test JSON Schema conversion for basic parameter."""
        param = SkillParameter(
            name="query",
            type="string",
            description="Search query",
            required=True,
        )

        schema = param.to_json_schema()

        assert schema["type"] == "string"
        assert schema["description"] == "Search query"
        assert "enum" not in schema
        assert "default" not in schema

    def test_to_json_schema_with_enum(self):
        """Test JSON Schema conversion with enum."""
        param = SkillParameter(
            name="format",
            type="string",
            description="Output format",
            enum=["json", "xml", "csv"],
            default="json",
        )

        schema = param.to_json_schema()

        assert schema["type"] == "string"
        assert schema["enum"] == ["json", "xml", "csv"]
        assert schema["default"] == "json"

    def test_to_json_schema_array(self):
        """Test JSON Schema conversion for array type."""
        param = SkillParameter(
            name="tags",
            type="array",
            description="List of tags",
            items={"type": "string"},
        )

        schema = param.to_json_schema()

        assert schema["type"] == "array"
        assert schema["items"] == {"type": "string"}

    def test_from_dict(self):
        """Test creating parameter from dictionary."""
        data = {
            "name": "count",
            "type": "integer",
            "description": "Number of results",
            "required": False,
            "default": 10,
        }

        param = SkillParameter.from_dict(data)

        assert param.name == "count"
        assert param.type == "integer"
        assert param.description == "Number of results"
        assert param.required is False
        assert param.default == 10

    def test_to_dict(self):
        """Test converting parameter to dictionary."""
        param = SkillParameter(
            name="query",
            type="string",
            description="Search query",
            required=True,
            enum=["a", "b"],
        )

        data = param.to_dict()

        assert data["name"] == "query"
        assert data["type"] == "string"
        assert data["enum"] == ["a", "b"]


# =============================================================================
# SkillDefinition Tests
# =============================================================================


class TestSkillDefinition:
    """Tests for SkillDefinition dataclass."""

    def test_basic_creation(self):
        """Test creating a basic skill definition."""
        skill = SkillDefinition(
            name="greet",
            description="Greet the user",
        )

        assert skill.name == "greet"
        assert skill.description == "Greet the user"
        assert skill.parameters == []
        assert skill.version == "1.0"
        assert skill.author is None
        assert skill.tags == []

    def test_skill_with_parameters(self):
        """Test skill with parameters."""
        params = [
            SkillParameter(
                name="name",
                type="string",
                description="User name",
                required=True,
            ),
            SkillParameter(
                name="formal",
                type="boolean",
                description="Use formal greeting",
                default=False,
            ),
        ]

        skill = SkillDefinition(
            name="greet",
            description="Greet the user",
            parameters=params,
        )

        assert len(skill.parameters) == 2
        assert skill.get_required_parameters() == [params[0]]
        assert skill.get_optional_parameters() == [params[1]]

    def test_to_tool_use_format(self):
        """Test conversion to Claude Tool Use format."""
        skill = SkillDefinition(
            name="get_weather",
            description="Get weather information",
            parameters=[
                SkillParameter(
                    name="location",
                    type="string",
                    description="City name",
                    required=True,
                ),
                SkillParameter(
                    name="unit",
                    type="string",
                    description="Temperature unit",
                    enum=["celsius", "fahrenheit"],
                    default="celsius",
                ),
            ],
        )

        tool = skill.to_tool_use_format()

        assert tool["name"] == "get_weather"
        assert tool["description"] == "Get weather information"
        assert tool["input_schema"]["type"] == "object"
        assert "location" in tool["input_schema"]["properties"]
        assert "unit" in tool["input_schema"]["properties"]
        assert tool["input_schema"]["required"] == ["location"]

        # Check property details
        location_prop = tool["input_schema"]["properties"]["location"]
        assert location_prop["type"] == "string"
        assert location_prop["description"] == "City name"

        unit_prop = tool["input_schema"]["properties"]["unit"]
        assert unit_prop["enum"] == ["celsius", "fahrenheit"]
        assert unit_prop["default"] == "celsius"

    def test_to_openai_format(self):
        """Test conversion to OpenAI Function format."""
        skill = SkillDefinition(
            name="search",
            description="Search the web",
            parameters=[
                SkillParameter(
                    name="query",
                    type="string",
                    description="Search query",
                    required=True,
                ),
            ],
        )

        func = skill.to_openai_format()

        assert func["type"] == "function"
        assert func["function"]["name"] == "search"
        assert func["function"]["description"] == "Search the web"
        assert func["function"]["parameters"]["type"] == "object"
        assert "query" in func["function"]["parameters"]["properties"]
        assert func["function"]["parameters"]["required"] == ["query"]

    def test_from_dict(self):
        """Test creating skill from dictionary."""
        data = {
            "name": "calculate",
            "description": "Perform calculations",
            "version": "2.0",
            "author": "test",
            "tags": ["math", "utility"],
            "parameters": [
                {
                    "name": "expression",
                    "type": "string",
                    "description": "Math expression",
                    "required": True,
                },
            ],
        }

        skill = SkillDefinition.from_dict(data)

        assert skill.name == "calculate"
        assert skill.description == "Perform calculations"
        assert skill.version == "2.0"
        assert skill.author == "test"
        assert skill.tags == ["math", "utility"]
        assert len(skill.parameters) == 1
        assert skill.parameters[0].name == "expression"

    def test_to_dict(self):
        """Test converting skill to dictionary."""
        skill = SkillDefinition(
            name="test",
            description="Test skill",
            version="1.5",
            tags=["test"],
        )

        data = skill.to_dict()

        assert data["name"] == "test"
        assert data["description"] == "Test skill"
        assert data["version"] == "1.5"
        assert data["tags"] == ["test"]


# =============================================================================
# SkillRegistry Tests
# =============================================================================


class TestSkillRegistry:
    """Tests for SkillRegistry."""

    def test_register_and_get(self):
        """Test registering and retrieving skills."""
        registry = SkillRegistry()

        skill = SkillDefinition(name="test", description="Test skill")
        registry.register(skill)

        assert "test" in registry
        assert registry.get("test") == skill
        assert len(registry) == 1

    def test_unregister(self):
        """Test unregistering a skill."""
        registry = SkillRegistry()
        skill = SkillDefinition(name="test", description="Test")
        registry.register(skill)

        removed = registry.unregister("test")

        assert removed == skill
        assert "test" not in registry
        assert registry.unregister("nonexistent") is None

    def test_list_skills(self):
        """Test listing all skill names."""
        registry = SkillRegistry()
        registry.register(SkillDefinition(name="a", description="A"))
        registry.register(SkillDefinition(name="b", description="B"))

        names = registry.list_skills()

        assert sorted(names) == ["a", "b"]

    def test_list_by_tag(self):
        """Test filtering skills by tag."""
        registry = SkillRegistry()
        registry.register(SkillDefinition(name="a", description="A", tags=["api"]))
        registry.register(SkillDefinition(name="b", description="B", tags=["api", "web"]))
        registry.register(SkillDefinition(name="c", description="C", tags=["local"]))

        api_skills = registry.list_by_tag("api")

        assert len(api_skills) == 2
        assert {s.name for s in api_skills} == {"a", "b"}

    def test_get_tool_definitions_claude(self):
        """Test getting all tools in Claude format."""
        registry = SkillRegistry()
        registry.register(SkillDefinition(name="a", description="Skill A"))
        registry.register(SkillDefinition(name="b", description="Skill B"))

        tools = registry.get_tool_definitions("claude")

        assert len(tools) == 2
        assert all("input_schema" in t for t in tools)

    def test_get_tool_definitions_openai(self):
        """Test getting all tools in OpenAI format."""
        registry = SkillRegistry()
        registry.register(SkillDefinition(name="a", description="Skill A"))

        tools = registry.get_tool_definitions("openai")

        assert len(tools) == 1
        assert tools[0]["type"] == "function"

    def test_get_tool_definitions_invalid_format(self):
        """Test that invalid format raises error."""
        registry = SkillRegistry()

        with pytest.raises(ValueError, match="Unknown format"):
            registry.get_tool_definitions("invalid")


# =============================================================================
# SkillLoader Tests
# =============================================================================


class TestSkillLoader:
    """Tests for SkillLoader."""

    def test_load_skill_from_file(self, temp_skill_dir, sample_skill_md):
        """Test loading a skill from a Markdown file."""
        skill_path = temp_skill_dir / "weather.md"
        skill_path.write_text(sample_skill_md)

        loader = SkillLoader(skill_dirs=[temp_skill_dir])
        skill = loader.load_skill(skill_path)

        assert skill.name == "get_weather"
        assert skill.version == "1.0"
        assert skill.author == "test_author"
        assert "weather" in skill.tags
        assert len(skill.parameters) == 2
        assert skill.parameters[0].name == "location"
        assert skill.parameters[0].required is True
        assert skill.parameters[1].name == "unit"
        assert skill.parameters[1].enum == ["celsius", "fahrenheit"]
        assert skill.implementation is not None
        assert "get_weather" in skill.implementation

    def test_load_minimal_skill(self, temp_skill_dir, minimal_skill_md):
        """Test loading a minimal skill."""
        skill_path = temp_skill_dir / "simple.md"
        skill_path.write_text(minimal_skill_md)

        loader = SkillLoader(skill_dirs=[temp_skill_dir])
        skill = loader.load_skill(skill_path)

        assert skill.name == "simple_skill"
        assert skill.description == "A simple test skill"
        assert skill.parameters == []

    def test_load_skill_file_not_found(self, temp_skill_dir):
        """Test loading non-existent file."""
        loader = SkillLoader(skill_dirs=[temp_skill_dir])

        with pytest.raises(FileNotFoundError):
            loader.load_skill(temp_skill_dir / "nonexistent.md")

    def test_load_skill_invalid_extension(self, temp_skill_dir):
        """Test loading file with wrong extension."""
        txt_file = temp_skill_dir / "skill.txt"
        txt_file.write_text("some content")

        loader = SkillLoader(skill_dirs=[temp_skill_dir])

        with pytest.raises(SkillLoadError, match="must have .md extension"):
            loader.load_skill(txt_file)

    def test_load_skill_missing_frontmatter(self, temp_skill_dir):
        """Test loading file without frontmatter."""
        skill_path = temp_skill_dir / "no_front.md"
        skill_path.write_text("# No frontmatter\nJust markdown.")

        loader = SkillLoader(skill_dirs=[temp_skill_dir])

        with pytest.raises(SkillLoadError, match="frontmatter"):
            loader.load_skill(skill_path)

    def test_load_skill_missing_name(self, temp_skill_dir):
        """Test loading file without name field."""
        skill_path = temp_skill_dir / "no_name.md"
        skill_path.write_text("""---
description: Missing name
---
Content
""")

        loader = SkillLoader(skill_dirs=[temp_skill_dir])

        with pytest.raises(SkillLoadError, match="name"):
            loader.load_skill(skill_path)

    def test_load_skill_invalid_yaml(self, temp_skill_dir):
        """Test loading file with invalid YAML."""
        skill_path = temp_skill_dir / "bad_yaml.md"
        skill_path.write_text("""---
name: test
  bad: indentation
---
Content
""")

        loader = SkillLoader(skill_dirs=[temp_skill_dir])

        with pytest.raises(SkillLoadError, match="YAML"):
            loader.load_skill(skill_path)

    def test_load_from_directory(self, temp_skill_dir, sample_skill_md, minimal_skill_md):
        """Test loading all skills from a directory."""
        (temp_skill_dir / "weather.md").write_text(sample_skill_md)
        (temp_skill_dir / "simple.md").write_text(minimal_skill_md)

        loader = SkillLoader(skill_dirs=[temp_skill_dir])
        skills = loader.load_from_directory(temp_skill_dir)

        assert len(skills) == 2
        assert "get_weather" in skills
        assert "simple_skill" in skills

    def test_load_from_directory_with_subdirs(self, temp_skill_dir, sample_skill_md):
        """Test loading SKILL.md from subdirectories."""
        subdir = temp_skill_dir / "weather_skill"
        subdir.mkdir()
        (subdir / "SKILL.md").write_text(sample_skill_md)

        loader = SkillLoader(skill_dirs=[temp_skill_dir])
        skills = loader.load_from_directory(temp_skill_dir)

        assert "get_weather" in skills

    def test_load_all(self, temp_skill_dir, sample_skill_md, minimal_skill_md):
        """Test loading all skills."""
        (temp_skill_dir / "weather.md").write_text(sample_skill_md)
        (temp_skill_dir / "simple.md").write_text(minimal_skill_md)

        loader = SkillLoader(skill_dirs=[temp_skill_dir])
        skills = loader.load_all()

        assert len(skills) == 2
        assert len(loader.registry) == 2
        assert "get_weather" in loader
        assert "simple_skill" in loader

    def test_reload(self, temp_skill_dir, sample_skill_md):
        """Test reloading skills."""
        (temp_skill_dir / "weather.md").write_text(sample_skill_md)

        loader = SkillLoader(skill_dirs=[temp_skill_dir])
        loader.load_all()
        assert len(loader) == 1

        # Remove the file and reload
        (temp_skill_dir / "weather.md").unlink()
        skills = loader.reload()
        assert len(skills) == 0
        assert len(loader) == 0

    def test_get_tool_definitions(self, temp_skill_dir, sample_skill_md):
        """Test getting tool definitions from loader."""
        (temp_skill_dir / "weather.md").write_text(sample_skill_md)

        loader = SkillLoader(skill_dirs=[temp_skill_dir])
        loader.load_all()

        claude_tools = loader.get_tool_definitions("claude")
        openai_tools = loader.get_tool_definitions("openai")

        assert len(claude_tools) == 1
        assert claude_tools[0]["name"] == "get_weather"
        assert "input_schema" in claude_tools[0]

        assert len(openai_tools) == 1
        assert openai_tools[0]["type"] == "function"

    def test_add_skill_dir(self, temp_skill_dir):
        """Test adding skill directory."""
        loader = SkillLoader(skill_dirs=[])

        loader.add_skill_dir(temp_skill_dir)

        assert temp_skill_dir in loader.skill_dirs

    def test_create_skill_loader_factory(self, temp_skill_dir):
        """Test factory function."""
        loader = create_skill_loader(
            additional_dirs=[temp_skill_dir],
            include_defaults=False,
        )

        assert temp_skill_dir in loader.skill_dirs


# =============================================================================
# SkillValidator Tests
# =============================================================================


class TestSkillValidator:
    """Tests for SkillValidator."""

    def test_validate_valid_skill(self):
        """Test validating a valid skill."""
        skill = SkillDefinition(
            name="test_skill",
            description="A test skill for validation",
            parameters=[
                SkillParameter(
                    name="query",
                    type="string",
                    description="Search query",
                    required=True,
                ),
            ],
        )

        validator = SkillValidator()
        errors = validator.validate(skill)

        assert len(errors) == 0
        assert validator.is_valid(skill)

    def test_validate_missing_name(self):
        """Test validation catches missing name."""
        skill = SkillDefinition(
            name="",
            description="Test description",
        )

        validator = SkillValidator()
        errors = validator.validate(skill)

        assert any("name" in str(e).lower() for e in errors)

    def test_validate_invalid_name(self):
        """Test validation catches invalid names."""
        skill = SkillDefinition(
            name="Invalid-Name",
            description="Test description",
        )

        validator = SkillValidator()
        errors = validator.validate(skill)

        assert any("name" in str(e).lower() for e in errors)

    def test_validate_missing_description(self):
        """Test validation catches missing description."""
        skill = SkillDefinition(
            name="test",
            description="",
        )

        validator = SkillValidator()
        errors = validator.validate(skill)

        assert any("description" in str(e).lower() for e in errors)

    def test_validate_invalid_param_type(self):
        """Test validation catches invalid parameter types."""
        skill = SkillDefinition(
            name="test",
            description="Test description here",
            parameters=[
                SkillParameter(
                    name="param",
                    type="invalid_type",
                    description="Test param",
                ),
            ],
        )

        validator = SkillValidator()
        errors = validator.validate(skill)

        assert any("type" in str(e).lower() for e in errors)

    def test_validate_duplicate_param_names(self):
        """Test validation catches duplicate parameter names."""
        skill = SkillDefinition(
            name="test",
            description="Test description here",
            parameters=[
                SkillParameter(name="query", type="string", description="Query"),
                SkillParameter(name="query", type="string", description="Another"),
            ],
        )

        validator = SkillValidator()
        errors = validator.validate(skill)

        assert any("duplicate" in str(e).lower() for e in errors)

    def test_validate_enum_default_mismatch(self):
        """Test validation catches default not in enum."""
        skill = SkillDefinition(
            name="test",
            description="Test description here",
            parameters=[
                SkillParameter(
                    name="format",
                    type="string",
                    description="Output format",
                    enum=["json", "xml"],
                    default="csv",
                ),
            ],
        )

        validator = SkillValidator()
        errors = validator.validate(skill)

        assert any("default" in str(e).lower() for e in errors)

    def test_validate_strict_mode(self):
        """Test strict mode includes warnings."""
        skill = SkillDefinition(
            name="test",
            description="Short",  # Too short - warning
        )

        validator_normal = SkillValidator(strict=False)
        validator_strict = SkillValidator(strict=True)

        normal_errors = validator_normal.validate(skill)
        strict_errors = validator_strict.validate(skill)

        # Strict should have more "errors" (includes warnings)
        assert len(strict_errors) >= len(normal_errors)

    def test_validate_file(self, temp_skill_dir, sample_skill_md):
        """Test validating a skill file."""
        skill_path = temp_skill_dir / "weather.md"
        skill_path.write_text(sample_skill_md)

        validator = SkillValidator()
        errors = validator.validate_file(skill_path)

        assert len(errors) == 0
        assert validator.is_file_valid(skill_path)

    def test_validate_file_not_found(self, temp_skill_dir):
        """Test validating non-existent file."""
        validator = SkillValidator()
        errors = validator.validate_file(temp_skill_dir / "nonexistent.md")

        assert len(errors) > 0
        assert any("not found" in str(e).lower() for e in errors)

    def test_validate_file_invalid_frontmatter(self, temp_skill_dir):
        """Test validating file with invalid frontmatter."""
        skill_path = temp_skill_dir / "bad.md"
        skill_path.write_text("No frontmatter here")

        validator = SkillValidator()
        errors = validator.validate_file(skill_path)

        assert len(errors) > 0

    def test_validate_frontmatter(self):
        """Test validating frontmatter dictionary."""
        validator = SkillValidator()

        valid_fm = {"name": "test", "description": "Test skill"}
        invalid_fm = {"description": "Missing name"}

        valid_errors = validator.validate_frontmatter(valid_fm)
        invalid_errors = validator.validate_frontmatter(invalid_fm)

        assert len([e for e in valid_errors if e.severity == "error"]) == 0
        assert len([e for e in invalid_errors if e.severity == "error"]) > 0

    def test_convenience_functions(self, temp_skill_dir, sample_skill_md):
        """Test convenience validation functions."""
        skill_path = temp_skill_dir / "weather.md"
        skill_path.write_text(sample_skill_md)

        skill = SkillDefinition(
            name="test",
            description="A valid test skill here",
        )

        skill_errors = validate_skill(skill)
        file_errors = validate_skill_file(skill_path)

        assert len(skill_errors) == 0
        assert len(file_errors) == 0


# =============================================================================
# Integration Tests
# =============================================================================


class TestSkillLoadingIntegration:
    """Integration tests for the skill loading system."""

    def test_full_workflow(self, temp_skill_dir, sample_skill_md):
        """Test complete workflow: load, validate, convert."""
        # Write skill file
        skill_path = temp_skill_dir / "weather.md"
        skill_path.write_text(sample_skill_md)

        # Create loader and load
        loader = SkillLoader(skill_dirs=[temp_skill_dir])
        skills = loader.load_all()

        assert len(skills) == 1
        skill = skills["get_weather"]

        # Validate
        validator = SkillValidator()
        errors = validator.validate(skill)
        assert len(errors) == 0

        # Convert to Claude format
        claude_tool = skill.to_tool_use_format()
        assert claude_tool["name"] == "get_weather"
        assert "location" in claude_tool["input_schema"]["properties"]
        assert claude_tool["input_schema"]["required"] == ["location"]

        # Convert to OpenAI format
        openai_func = skill.to_openai_format()
        assert openai_func["type"] == "function"
        assert openai_func["function"]["name"] == "get_weather"

    def test_complex_parameters(self, temp_skill_dir, skill_with_complex_params_md):
        """Test loading skill with complex parameter types."""
        skill_path = temp_skill_dir / "complex.md"
        skill_path.write_text(skill_with_complex_params_md)

        loader = SkillLoader(skill_dirs=[temp_skill_dir])
        skill = loader.load_skill(skill_path)

        # Check array parameter
        items_param = next(p for p in skill.parameters if p.name == "items")
        assert items_param.type == "array"
        assert items_param.items == {"type": "string"}

        # Check object parameter
        config_param = next(p for p in skill.parameters if p.name == "config")
        assert config_param.type == "object"
        assert "timeout" in config_param.properties

        # Verify conversion
        tool = skill.to_tool_use_format()
        props = tool["input_schema"]["properties"]

        assert props["items"]["type"] == "array"
        assert props["items"]["items"] == {"type": "string"}
        assert props["config"]["type"] == "object"

    def test_multiple_directories(self, temp_skill_dir, sample_skill_md, minimal_skill_md):
        """Test loading from multiple directories."""
        dir1 = temp_skill_dir / "dir1"
        dir2 = temp_skill_dir / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        (dir1 / "weather.md").write_text(sample_skill_md)
        (dir2 / "simple.md").write_text(minimal_skill_md)

        loader = SkillLoader(skill_dirs=[dir1, dir2])
        skills = loader.load_all()

        assert len(skills) == 2
        assert "get_weather" in skills
        assert "simple_skill" in skills
