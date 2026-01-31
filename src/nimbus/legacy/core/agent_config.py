"""Agent Configuration System for Subagents.

This module provides configuration management for subagent definitions:
- SubagentConfig: Configuration dataclass for subagent definitions
- SubagentConfigLoader: Load configurations from YAML files and directories
- SubagentRegistry: Registry for managing and querying subagent configurations

Subagents are specialized agents that can be spawned by the primary agent
to handle specific tasks (e.g., code exploration, implementation, testing).

Example YAML configuration:
    ```yaml
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
    ```
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, Set, Union

import yaml

from .logging import get_logger

logger = get_logger("agent_config")


# Type alias for agent mode
AgentMode = Literal["primary", "subagent", "all"]


@dataclass
class SubagentConfig:
    """Configuration for a subagent definition.

    Subagents are specialized agents that can be spawned by the primary agent
    to handle specific types of tasks. Each subagent has a defined set of
    allowed tools and a specialized system prompt.

    Attributes:
        name: Unique identifier for the subagent (e.g., "explorer", "implementer").
        description: Human-readable description of what the subagent does.
        mode: Run mode - "primary" (main agent only), "subagent" (spawnable only),
              or "all" (can run as either).
        allowed_tools: List of tool names this subagent can use. Empty list means
                      all tools are allowed. Tool names should match registered
                      tool names (e.g., "Read", "Write", "Bash").
        model: Optional model override for this subagent. If None, uses the
               primary agent's model.
        prompt: System prompt that defines the subagent's behavior, constraints,
               and output format.
        max_turns: Maximum number of conversation turns before the subagent
                   must return a result.
        metadata: Optional additional metadata for custom extensions.
    """
    name: str
    description: str
    mode: AgentMode = "subagent"
    allowed_tools: List[str] = field(default_factory=list)
    model: Optional[str] = None
    prompt: str = ""
    max_turns: int = 50
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.name:
            raise ValueError("Subagent name cannot be empty")
        if not self.name.isidentifier():
            # Allow names like "code-explorer" with hyphens
            if not all(c.isalnum() or c in "-_" for c in self.name):
                raise ValueError(
                    f"Subagent name '{self.name}' contains invalid characters. "
                    "Use only alphanumeric characters, hyphens, and underscores."
                )
        if self.mode not in ("primary", "subagent", "all"):
            raise ValueError(
                f"Invalid mode '{self.mode}'. Must be 'primary', 'subagent', or 'all'."
            )
        if self.max_turns < 1:
            raise ValueError("max_turns must be at least 1")

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a specific tool is allowed for this subagent.

        Args:
            tool_name: Name of the tool to check.

        Returns:
            True if the tool is allowed, False otherwise.
            Returns True if allowed_tools is empty (all tools allowed).
        """
        if not self.allowed_tools:
            return True  # Empty list means all tools allowed
        return tool_name in self.allowed_tools

    def get_allowed_tools_set(self) -> Set[str]:
        """Get the set of allowed tool names.

        Returns:
            Set of tool names. Empty set if all tools are allowed.
        """
        return set(self.allowed_tools)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for YAML export or API response.

        Returns:
            Dictionary representation of the configuration.
        """
        return {
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "allowed_tools": self.allowed_tools,
            "model": self.model,
            "prompt": self.prompt,
            "max_turns": self.max_turns,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubagentConfig":
        """Create SubagentConfig from dictionary.

        Args:
            data: Dictionary with subagent configuration.

        Returns:
            SubagentConfig instance.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        if "name" not in data:
            raise ValueError("Subagent configuration must have a 'name' field")

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            mode=data.get("mode", "subagent"),
            allowed_tools=data.get("allowed_tools", []),
            model=data.get("model"),
            prompt=data.get("prompt", ""),
            max_turns=data.get("max_turns", 50),
            metadata=data.get("metadata", {}),
        )


class SubagentConfigLoader:
    """Loader for subagent configuration files.

    Supports loading configurations from:
    - Single YAML files
    - Dictionaries
    - Multiple directories with auto-discovery

    Default search directories (in priority order):
    1. ~/.nimbus/agents/ (user-level configurations)
    2. src/nimbus/data/agents/ (built-in configurations)
    3. .nimbus/agents/ (project-level configurations)

    Example:
        ```python
        loader = SubagentConfigLoader()

        # Load single file
        config = loader.load_from_yaml("~/.nimbus/agents/explorer.yaml")

        # Discover all agents in default directories
        configs = loader.discover_agents()

        # Discover in custom directories
        configs = loader.discover_agents([
            Path("./custom-agents"),
            Path("~/.my-agents"),
        ])
        ```
    """

    # Default search directories (relative paths resolved at runtime)
    DEFAULT_DIRS = [
        "~/.nimbus/agents",
        ".nimbus/agents",
    ]

    def __init__(self, base_path: Optional[Path] = None):
        """Initialize the loader.

        Args:
            base_path: Base path for resolving relative directories.
                      Defaults to current working directory.
        """
        self.base_path = base_path or Path.cwd()

    def load_from_yaml(self, path: Union[str, Path]) -> SubagentConfig:
        """Load a single subagent configuration from YAML file.

        Args:
            path: Path to the YAML configuration file.

        Returns:
            SubagentConfig instance.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            yaml.YAMLError: If YAML parsing fails.
            ValueError: If configuration is invalid.
        """
        path = Path(path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        logger.debug(f"Loading subagent config from: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError(f"Empty configuration file: {path}")

        # Use filename as name if not specified
        if "name" not in data:
            data["name"] = path.stem

        config = SubagentConfig.from_dict(data)
        logger.debug(f"Loaded subagent '{config.name}' from {path}")
        return config

    def load_from_dict(self, data: Dict[str, Any]) -> SubagentConfig:
        """Load subagent configuration from dictionary.

        Args:
            data: Dictionary with subagent configuration.

        Returns:
            SubagentConfig instance.

        Raises:
            ValueError: If configuration is invalid.
        """
        return SubagentConfig.from_dict(data)

    def discover_agents(
        self,
        dirs: Optional[List[Union[str, Path]]] = None,
        include_builtin: bool = True,
    ) -> Dict[str, SubagentConfig]:
        """Discover and load all subagent configurations from directories.

        Scans directories for YAML files (*.yaml, *.yml) and loads each
        as a subagent configuration. Later directories override earlier ones
        if there are name conflicts.

        Args:
            dirs: List of directories to search. If None, uses default
                  directories. Paths can be absolute or relative to base_path.
            include_builtin: Whether to include built-in agents from the
                            package's data directory.

        Returns:
            Dictionary mapping subagent names to their configurations.
        """
        configs: Dict[str, SubagentConfig] = {}

        # Build search path
        search_dirs: List[Path] = []

        # Add built-in directory if requested
        if include_builtin:
            builtin_dir = Path(__file__).parent.parent / "data" / "agents"
            if builtin_dir.exists():
                search_dirs.append(builtin_dir)
                logger.debug(f"Including built-in agents from: {builtin_dir}")

        # Add user-specified or default directories
        if dirs is None:
            for dir_str in self.DEFAULT_DIRS:
                dir_path = Path(dir_str).expanduser()
                if not dir_path.is_absolute():
                    dir_path = self.base_path / dir_path
                search_dirs.append(dir_path)
        else:
            for dir_entry in dirs:
                path = Path(dir_entry).expanduser()
                if not path.is_absolute():
                    path = self.base_path / path
                search_dirs.append(path)

        # Scan directories
        for search_dir in search_dirs:
            if not search_dir.exists():
                logger.debug(f"Skipping non-existent directory: {search_dir}")
                continue

            if not search_dir.is_dir():
                logger.warning(f"Path is not a directory: {search_dir}")
                continue

            logger.debug(f"Scanning for agents in: {search_dir}")

            # Find all YAML files
            for pattern in ["*.yaml", "*.yml"]:
                for yaml_file in search_dir.glob(pattern):
                    try:
                        config = self.load_from_yaml(yaml_file)
                        if config.name in configs:
                            logger.debug(
                                f"Overriding agent '{config.name}' from {yaml_file}"
                            )
                        configs[config.name] = config
                    except Exception as e:
                        logger.warning(
                            f"Failed to load agent config from {yaml_file}: {e}"
                        )

        logger.info(f"Discovered {len(configs)} subagent configurations")
        return configs


class SubagentRegistry:
    """Registry for managing subagent configurations.

    Provides centralized access to subagent definitions with:
    - Registration of new configurations
    - Lookup by name
    - Filtering by mode
    - Tool permission validation

    Example:
        ```python
        registry = SubagentRegistry()

        # Register configurations
        registry.register(explorer_config)
        registry.register(implementer_config)

        # Load from directories
        registry.load_from_directories(["~/.nimbus/agents"])

        # Query
        config = registry.get("explorer")
        subagents = registry.list_agents(mode="subagent")

        # Validate tools
        issues = registry.validate_tools(config, ["Read", "Write", "Bash"])
        ```
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._configs: Dict[str, SubagentConfig] = {}
        self._loader = SubagentConfigLoader()

    def register(self, config: SubagentConfig) -> None:
        """Register a subagent configuration.

        Args:
            config: SubagentConfig to register.

        Raises:
            ValueError: If config.name is already registered.
        """
        if config.name in self._configs:
            logger.warning(
                f"Overwriting existing subagent configuration: {config.name}"
            )
        self._configs[config.name] = config
        logger.debug(f"Registered subagent: {config.name}")

    def register_from_dict(self, data: Dict[str, Any]) -> SubagentConfig:
        """Create and register a subagent from dictionary.

        Args:
            data: Dictionary with subagent configuration.

        Returns:
            The registered SubagentConfig.
        """
        config = SubagentConfig.from_dict(data)
        self.register(config)
        return config

    def get(self, name: str) -> Optional[SubagentConfig]:
        """Get a subagent configuration by name.

        Args:
            name: Subagent name to look up.

        Returns:
            SubagentConfig if found, None otherwise.
        """
        return self._configs.get(name)

    def get_or_raise(self, name: str) -> SubagentConfig:
        """Get a subagent configuration by name, raising if not found.

        Args:
            name: Subagent name to look up.

        Returns:
            SubagentConfig.

        Raises:
            KeyError: If subagent is not registered.
        """
        config = self.get(name)
        if config is None:
            available = ", ".join(self._configs.keys()) or "(none)"
            raise KeyError(
                f"Subagent '{name}' not found. Available: {available}"
            )
        return config

    def unregister(self, name: str) -> bool:
        """Remove a subagent configuration from the registry.

        Args:
            name: Subagent name to remove.

        Returns:
            True if removed, False if not found.
        """
        if name in self._configs:
            del self._configs[name]
            logger.debug(f"Unregistered subagent: {name}")
            return True
        return False

    def list_agents(
        self,
        mode: Optional[AgentMode] = None,
    ) -> List[SubagentConfig]:
        """List subagent configurations, optionally filtered by mode.

        Args:
            mode: Filter by agent mode. If None, returns all agents.
                 - "primary": Only agents that can run as primary
                 - "subagent": Only agents that can be spawned as subagents
                 - "all": Only agents that can run in any mode

        Returns:
            List of matching SubagentConfig instances.
        """
        if mode is None:
            return list(self._configs.values())

        result = []
        for config in self._configs.values():
            if config.mode == mode:
                result.append(config)
            elif config.mode == "all":
                # "all" mode matches any filter
                result.append(config)
        return result

    def list_names(self, mode: Optional[AgentMode] = None) -> List[str]:
        """List subagent names, optionally filtered by mode.

        Args:
            mode: Filter by agent mode. If None, returns all names.

        Returns:
            List of subagent names.
        """
        return [config.name for config in self.list_agents(mode)]

    def validate_tools(
        self,
        config: SubagentConfig,
        available_tools: List[str],
    ) -> List[str]:
        """Validate that a subagent's allowed tools are available.

        Args:
            config: SubagentConfig to validate.
            available_tools: List of tool names that are available in the system.

        Returns:
            List of tool names that are in allowed_tools but not available.
            Empty list means all tools are valid.
        """
        if not config.allowed_tools:
            return []  # All tools allowed, no validation needed

        available_set = set(available_tools)
        invalid_tools = []

        for tool_name in config.allowed_tools:
            if tool_name not in available_set:
                invalid_tools.append(tool_name)

        if invalid_tools:
            logger.warning(
                f"Subagent '{config.name}' references unavailable tools: "
                f"{invalid_tools}"
            )

        return invalid_tools

    def validate_all(
        self,
        available_tools: List[str],
    ) -> Dict[str, List[str]]:
        """Validate all registered subagents' tool permissions.

        Args:
            available_tools: List of tool names that are available in the system.

        Returns:
            Dictionary mapping subagent names to their invalid tool lists.
            Only includes subagents with validation issues.
        """
        issues: Dict[str, List[str]] = {}
        for name, config in self._configs.items():
            invalid = self.validate_tools(config, available_tools)
            if invalid:
                issues[name] = invalid
        return issues

    def load_from_directories(
        self,
        dirs: Optional[List[Union[str, Path]]] = None,
        include_builtin: bool = True,
    ) -> int:
        """Load and register subagents from directories.

        Args:
            dirs: List of directories to search. If None, uses defaults.
            include_builtin: Whether to include built-in agents.

        Returns:
            Number of configurations loaded.
        """
        configs = self._loader.discover_agents(dirs, include_builtin)
        for config in configs.values():
            self.register(config)
        return len(configs)

    def clear(self) -> None:
        """Remove all registered subagent configurations."""
        self._configs.clear()
        logger.debug("Cleared all subagent configurations")

    def __len__(self) -> int:
        """Return the number of registered subagents."""
        return len(self._configs)

    def __contains__(self, name: str) -> bool:
        """Check if a subagent is registered."""
        return name in self._configs

    def __iter__(self) -> Iterator[SubagentConfig]:
        """Iterate over registered subagent configurations."""
        return iter(self._configs.values())

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """Export all configurations as a dictionary.

        Returns:
            Dictionary mapping names to serialized configurations.
        """
        return {name: config.to_dict() for name, config in self._configs.items()}


# Global default registry instance
_default_registry: Optional[SubagentRegistry] = None


def get_default_registry() -> SubagentRegistry:
    """Get the default global subagent registry.

    Creates and initializes the registry on first call by loading
    configurations from default directories.

    Returns:
        The default SubagentRegistry instance.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = SubagentRegistry()
        try:
            _default_registry.load_from_directories()
        except Exception as e:
            logger.warning(f"Failed to load default subagent configs: {e}")
    return _default_registry


def reset_default_registry() -> None:
    """Reset the default registry (mainly for testing)."""
    global _default_registry
    _default_registry = None
