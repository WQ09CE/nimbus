"""Tests for SubagentConfig, SubagentConfigLoader, and SubagentRegistry."""

import pytest
import tempfile
from pathlib import Path

from nimbus.core.agent_config import (
    SubagentConfig,
    SubagentConfigLoader,
    SubagentRegistry,
    get_default_registry,
    reset_default_registry,
)


class TestSubagentConfig:
    """Tests for SubagentConfig dataclass."""

    def test_default_values(self):
        """Test default SubagentConfig values."""
        config = SubagentConfig(
            name="test",
            description="Test agent",
        )

        assert config.name == "test"
        assert config.description == "Test agent"
        assert config.mode == "subagent"
        assert config.allowed_tools == []
        assert config.model is None
        assert config.prompt == ""
        assert config.max_turns == 50
        assert config.metadata == {}

    def test_full_config(self):
        """Test creating SubagentConfig with all fields."""
        config = SubagentConfig(
            name="explorer",
            description="Code exploration expert",
            mode="subagent",
            allowed_tools=["Read", "Glob", "Grep"],
            model="claude-3-5-sonnet",
            prompt="You are a code explorer.",
            max_turns=30,
            metadata={"version": "1.0"},
        )

        assert config.name == "explorer"
        assert config.mode == "subagent"
        assert config.allowed_tools == ["Read", "Glob", "Grep"]
        assert config.model == "claude-3-5-sonnet"
        assert config.max_turns == 30
        assert config.metadata == {"version": "1.0"}

    def test_empty_name_raises(self):
        """Test that empty name raises ValueError."""
        with pytest.raises(ValueError, match="name cannot be empty"):
            SubagentConfig(name="", description="Test")

    def test_invalid_name_characters_raises(self):
        """Test that invalid name characters raise ValueError."""
        with pytest.raises(ValueError, match="invalid characters"):
            SubagentConfig(name="test@agent", description="Test")

    def test_valid_name_with_hyphens(self):
        """Test that names with hyphens are valid."""
        config = SubagentConfig(name="code-explorer", description="Test")
        assert config.name == "code-explorer"

    def test_valid_name_with_underscores(self):
        """Test that names with underscores are valid."""
        config = SubagentConfig(name="code_explorer", description="Test")
        assert config.name == "code_explorer"

    def test_invalid_mode_raises(self):
        """Test that invalid mode raises ValueError."""
        with pytest.raises(ValueError, match="Invalid mode"):
            SubagentConfig(name="test", description="Test", mode="invalid")

    def test_invalid_max_turns_raises(self):
        """Test that max_turns < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_turns must be at least 1"):
            SubagentConfig(name="test", description="Test", max_turns=0)

    def test_is_tool_allowed_with_restrictions(self):
        """Test is_tool_allowed when tools are restricted."""
        config = SubagentConfig(
            name="test",
            description="Test",
            allowed_tools=["Read", "Glob"],
        )

        assert config.is_tool_allowed("Read") is True
        assert config.is_tool_allowed("Glob") is True
        assert config.is_tool_allowed("Write") is False
        assert config.is_tool_allowed("Bash") is False

    def test_is_tool_allowed_no_restrictions(self):
        """Test is_tool_allowed when all tools are allowed."""
        config = SubagentConfig(
            name="test",
            description="Test",
            allowed_tools=[],  # Empty means all allowed
        )

        assert config.is_tool_allowed("Read") is True
        assert config.is_tool_allowed("Write") is True
        assert config.is_tool_allowed("Bash") is True
        assert config.is_tool_allowed("AnyTool") is True

    def test_get_allowed_tools_set(self):
        """Test getting allowed tools as a set."""
        config = SubagentConfig(
            name="test",
            description="Test",
            allowed_tools=["Read", "Glob", "Read"],  # Duplicate
        )

        tools = config.get_allowed_tools_set()
        assert tools == {"Read", "Glob"}

    def test_to_dict(self):
        """Test serializing to dictionary."""
        config = SubagentConfig(
            name="explorer",
            description="Code explorer",
            mode="subagent",
            allowed_tools=["Read"],
            model="gpt-4",
            prompt="Explore code.",
            max_turns=25,
            metadata={"key": "value"},
        )

        data = config.to_dict()

        assert data["name"] == "explorer"
        assert data["description"] == "Code explorer"
        assert data["mode"] == "subagent"
        assert data["allowed_tools"] == ["Read"]
        assert data["model"] == "gpt-4"
        assert data["prompt"] == "Explore code."
        assert data["max_turns"] == 25
        assert data["metadata"] == {"key": "value"}

    def test_from_dict_full(self):
        """Test creating from dictionary with all fields."""
        data = {
            "name": "implementer",
            "description": "Code implementer",
            "mode": "all",
            "allowed_tools": ["Read", "Write", "Bash"],
            "model": "claude-3-opus",
            "prompt": "Implement code.",
            "max_turns": 100,
            "metadata": {"cost": "high"},
        }

        config = SubagentConfig.from_dict(data)

        assert config.name == "implementer"
        assert config.description == "Code implementer"
        assert config.mode == "all"
        assert config.allowed_tools == ["Read", "Write", "Bash"]
        assert config.model == "claude-3-opus"
        assert config.prompt == "Implement code."
        assert config.max_turns == 100
        assert config.metadata == {"cost": "high"}

    def test_from_dict_minimal(self):
        """Test creating from dictionary with minimal fields."""
        data = {"name": "minimal"}

        config = SubagentConfig.from_dict(data)

        assert config.name == "minimal"
        assert config.description == ""
        assert config.mode == "subagent"
        assert config.allowed_tools == []

    def test_from_dict_missing_name_raises(self):
        """Test that missing name raises ValueError."""
        with pytest.raises(ValueError, match="must have a 'name' field"):
            SubagentConfig.from_dict({"description": "No name"})


class TestSubagentConfigLoader:
    """Tests for SubagentConfigLoader."""

    def test_load_from_yaml(self):
        """Test loading config from YAML file."""
        yaml_content = """
name: explorer
description: "Code exploration expert"
mode: subagent
allowed_tools:
  - Read
  - Glob
  - Grep
prompt: |
  You are a code exploration expert.
  Only read files, never modify them.
max_turns: 30
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()

            loader = SubagentConfigLoader()
            config = loader.load_from_yaml(f.name)

            assert config.name == "explorer"
            assert config.description == "Code exploration expert"
            assert config.mode == "subagent"
            assert config.allowed_tools == ["Read", "Glob", "Grep"]
            assert "code exploration expert" in config.prompt
            assert config.max_turns == 30

    def test_load_from_yaml_uses_filename_as_name(self):
        """Test that filename is used as name if not specified."""
        yaml_content = """
description: "Test agent"
mode: subagent
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, prefix="myagent_"
        ) as f:
            f.write(yaml_content)
            f.flush()

            loader = SubagentConfigLoader()
            config = loader.load_from_yaml(f.name)

            # Name should be derived from filename (without extension)
            assert config.name.startswith("myagent_")

    def test_load_from_yaml_file_not_found(self):
        """Test loading non-existent file raises error."""
        loader = SubagentConfigLoader()

        with pytest.raises(FileNotFoundError):
            loader.load_from_yaml("/nonexistent/path.yaml")

    def test_load_from_yaml_empty_file(self):
        """Test loading empty file raises error."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("")
            f.flush()

            loader = SubagentConfigLoader()

            with pytest.raises(ValueError, match="Empty configuration"):
                loader.load_from_yaml(f.name)

    def test_load_from_dict(self):
        """Test loading config from dictionary."""
        data = {
            "name": "tester",
            "description": "Test runner",
            "allowed_tools": ["Bash"],
        }

        loader = SubagentConfigLoader()
        config = loader.load_from_dict(data)

        assert config.name == "tester"
        assert config.description == "Test runner"
        assert config.allowed_tools == ["Bash"]

    def test_discover_agents_empty_directory(self):
        """Test discovering agents in empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = SubagentConfigLoader()
            configs = loader.discover_agents([tmpdir], include_builtin=False)

            assert len(configs) == 0

    def test_discover_agents_with_yaml_files(self):
        """Test discovering agents from YAML files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create YAML files
            (Path(tmpdir) / "explorer.yaml").write_text("""
name: explorer
description: Explorer agent
allowed_tools: [Read, Glob]
""")
            (Path(tmpdir) / "implementer.yml").write_text("""
name: implementer
description: Implementer agent
allowed_tools: [Read, Write, Bash]
""")

            loader = SubagentConfigLoader()
            configs = loader.discover_agents([tmpdir], include_builtin=False)

            assert len(configs) == 2
            assert "explorer" in configs
            assert "implementer" in configs
            assert configs["explorer"].allowed_tools == ["Read", "Glob"]
            assert configs["implementer"].allowed_tools == ["Read", "Write", "Bash"]

    def test_discover_agents_later_directories_override(self):
        """Test that later directories override earlier ones."""
        with tempfile.TemporaryDirectory() as dir1:
            with tempfile.TemporaryDirectory() as dir2:
                # Create same-named agent in both directories
                (Path(dir1) / "agent.yaml").write_text("""
name: agent
description: First version
allowed_tools: [Read]
""")
                (Path(dir2) / "agent.yaml").write_text("""
name: agent
description: Second version
allowed_tools: [Read, Write]
""")

                loader = SubagentConfigLoader()
                configs = loader.discover_agents([dir1, dir2], include_builtin=False)

                assert len(configs) == 1
                # Second directory should override
                assert configs["agent"].description == "Second version"
                assert configs["agent"].allowed_tools == ["Read", "Write"]

    def test_discover_agents_skips_invalid_files(self):
        """Test that invalid YAML files are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Valid file
            (Path(tmpdir) / "valid.yaml").write_text("""
name: valid
description: Valid agent
""")
            # Invalid file (missing required field after from_dict is called)
            (Path(tmpdir) / "invalid.yaml").write_text("""
# This should still work since name comes from filename
description: Invalid agent
""")

            loader = SubagentConfigLoader()
            configs = loader.discover_agents([tmpdir], include_builtin=False)

            # Both should load (invalid uses filename as name)
            assert len(configs) == 2


class TestSubagentRegistry:
    """Tests for SubagentRegistry."""

    def test_register_and_get(self):
        """Test registering and retrieving a config."""
        registry = SubagentRegistry()

        config = SubagentConfig(
            name="explorer",
            description="Explorer agent",
        )
        registry.register(config)

        retrieved = registry.get("explorer")
        assert retrieved is not None
        assert retrieved.name == "explorer"
        assert retrieved.description == "Explorer agent"

    def test_get_nonexistent_returns_none(self):
        """Test that getting nonexistent config returns None."""
        registry = SubagentRegistry()
        assert registry.get("nonexistent") is None

    def test_get_or_raise(self):
        """Test get_or_raise raises for nonexistent config."""
        registry = SubagentRegistry()

        config = SubagentConfig(name="exists", description="Exists")
        registry.register(config)

        # Should work for existing
        assert registry.get_or_raise("exists") is config

        # Should raise for nonexistent
        with pytest.raises(KeyError, match="not found"):
            registry.get_or_raise("nonexistent")

    def test_register_from_dict(self):
        """Test registering from dictionary."""
        registry = SubagentRegistry()

        config = registry.register_from_dict({
            "name": "tester",
            "description": "Test runner",
        })

        assert config.name == "tester"
        assert registry.get("tester") is config

    def test_unregister(self):
        """Test unregistering a config."""
        registry = SubagentRegistry()

        config = SubagentConfig(name="temp", description="Temporary")
        registry.register(config)

        assert "temp" in registry
        assert registry.unregister("temp") is True
        assert "temp" not in registry
        assert registry.unregister("temp") is False  # Already removed

    def test_list_agents_all(self):
        """Test listing all agents."""
        registry = SubagentRegistry()

        registry.register(SubagentConfig(name="a", description="A", mode="primary"))
        registry.register(SubagentConfig(name="b", description="B", mode="subagent"))
        registry.register(SubagentConfig(name="c", description="C", mode="all"))

        agents = registry.list_agents()
        assert len(agents) == 3

    def test_list_agents_by_mode_primary(self):
        """Test listing agents filtered by primary mode."""
        registry = SubagentRegistry()

        registry.register(SubagentConfig(name="a", description="A", mode="primary"))
        registry.register(SubagentConfig(name="b", description="B", mode="subagent"))
        registry.register(SubagentConfig(name="c", description="C", mode="all"))

        agents = registry.list_agents(mode="primary")
        names = [a.name for a in agents]

        assert "a" in names  # primary mode
        assert "c" in names  # all mode matches any filter
        assert "b" not in names  # subagent mode

    def test_list_agents_by_mode_subagent(self):
        """Test listing agents filtered by subagent mode."""
        registry = SubagentRegistry()

        registry.register(SubagentConfig(name="a", description="A", mode="primary"))
        registry.register(SubagentConfig(name="b", description="B", mode="subagent"))
        registry.register(SubagentConfig(name="c", description="C", mode="all"))

        agents = registry.list_agents(mode="subagent")
        names = [a.name for a in agents]

        assert "b" in names  # subagent mode
        assert "c" in names  # all mode matches any filter
        assert "a" not in names  # primary mode

    def test_list_names(self):
        """Test listing agent names."""
        registry = SubagentRegistry()

        registry.register(SubagentConfig(name="explorer", description="E"))
        registry.register(SubagentConfig(name="implementer", description="I"))

        names = registry.list_names()
        assert set(names) == {"explorer", "implementer"}

    def test_validate_tools(self):
        """Test validating tool permissions."""
        registry = SubagentRegistry()

        config = SubagentConfig(
            name="test",
            description="Test",
            allowed_tools=["Read", "Write", "UnknownTool"],
        )

        available = ["Read", "Write", "Bash", "Glob"]
        invalid = registry.validate_tools(config, available)

        assert invalid == ["UnknownTool"]

    def test_validate_tools_empty_allowed(self):
        """Test validating when all tools are allowed."""
        registry = SubagentRegistry()

        config = SubagentConfig(
            name="test",
            description="Test",
            allowed_tools=[],  # All allowed
        )

        available = ["Read", "Write"]
        invalid = registry.validate_tools(config, available)

        assert invalid == []

    def test_validate_all(self):
        """Test validating all registered agents."""
        registry = SubagentRegistry()

        registry.register(SubagentConfig(
            name="valid",
            description="Valid agent",
            allowed_tools=["Read", "Write"],
        ))
        registry.register(SubagentConfig(
            name="invalid",
            description="Invalid agent",
            allowed_tools=["Read", "BadTool"],
        ))

        available = ["Read", "Write", "Bash"]
        issues = registry.validate_all(available)

        assert "valid" not in issues
        assert "invalid" in issues
        assert issues["invalid"] == ["BadTool"]

    def test_load_from_directories(self):
        """Test loading from directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agent1.yaml").write_text("""
name: agent1
description: Agent 1
""")
            (Path(tmpdir) / "agent2.yaml").write_text("""
name: agent2
description: Agent 2
""")

            registry = SubagentRegistry()
            count = registry.load_from_directories([tmpdir], include_builtin=False)

            assert count == 2
            assert "agent1" in registry
            assert "agent2" in registry

    def test_clear(self):
        """Test clearing all configs."""
        registry = SubagentRegistry()

        registry.register(SubagentConfig(name="a", description="A"))
        registry.register(SubagentConfig(name="b", description="B"))

        assert len(registry) == 2

        registry.clear()

        assert len(registry) == 0

    def test_len(self):
        """Test __len__ method."""
        registry = SubagentRegistry()
        assert len(registry) == 0

        registry.register(SubagentConfig(name="a", description="A"))
        assert len(registry) == 1

        registry.register(SubagentConfig(name="b", description="B"))
        assert len(registry) == 2

    def test_contains(self):
        """Test __contains__ method."""
        registry = SubagentRegistry()

        registry.register(SubagentConfig(name="exists", description="E"))

        assert "exists" in registry
        assert "nonexistent" not in registry

    def test_iter(self):
        """Test __iter__ method."""
        registry = SubagentRegistry()

        registry.register(SubagentConfig(name="a", description="A"))
        registry.register(SubagentConfig(name="b", description="B"))

        names = [config.name for config in registry]
        assert set(names) == {"a", "b"}

    def test_to_dict(self):
        """Test exporting all configs as dictionary."""
        registry = SubagentRegistry()

        registry.register(SubagentConfig(
            name="explorer",
            description="Explorer",
            allowed_tools=["Read"],
        ))

        data = registry.to_dict()

        assert "explorer" in data
        assert data["explorer"]["name"] == "explorer"
        assert data["explorer"]["description"] == "Explorer"
        assert data["explorer"]["allowed_tools"] == ["Read"]


class TestGlobalRegistry:
    """Tests for global registry functions."""

    def teardown_method(self):
        """Reset global registry after each test."""
        reset_default_registry()

    def test_get_default_registry(self):
        """Test getting the default registry."""
        registry = get_default_registry()

        assert isinstance(registry, SubagentRegistry)

    def test_get_default_registry_singleton(self):
        """Test that default registry is a singleton."""
        registry1 = get_default_registry()
        registry2 = get_default_registry()

        assert registry1 is registry2

    def test_reset_default_registry(self):
        """Test resetting the default registry."""
        registry1 = get_default_registry()
        registry1.register(SubagentConfig(name="test", description="Test"))

        reset_default_registry()

        registry2 = get_default_registry()
        assert registry1 is not registry2
        # New registry should not have the old config (unless loaded from files)
