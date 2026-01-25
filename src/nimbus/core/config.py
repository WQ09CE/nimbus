"""Configuration dataclasses for Agent Framework.

This module provides type-safe configuration classes for:
- LLM settings (model, temperature, tokens)
- Memory settings (budgets, compression)
- Runtime settings (timeout, retries, concurrency)
- Skill definitions (builtin, markdown, wukong)
- Agent configuration (combines all above)
- Core agent configuration (from YAML)
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from .logging import get_logger

logger = get_logger("config")


class SkillType(Enum):
    """Types of skills that can be loaded."""
    BUILTIN = "builtin"      # Built-in Python skills
    MARKDOWN = "markdown"    # Markdown-defined skills
    WUKONG = "wukong"        # Wukong framework skills


@dataclass
class LLMConfig:
    """Configuration for LLM client.

    Attributes:
        model: Model identifier (e.g., "claude-3-5-sonnet", "gpt-4").
        temperature: Sampling temperature (0.0-1.0).
        max_tokens: Maximum tokens in response.
        api_key_env: Environment variable name for API key.
        base_url: Optional base URL for API endpoint.
    """
    model: str = "claude-3-5-sonnet"
    temperature: float = 0.7
    max_tokens: int = 4096
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMConfig":
        """Create LLMConfig from dictionary.

        Args:
            data: Dictionary with LLM configuration.

        Returns:
            LLMConfig instance.
        """
        return cls(
            model=data.get("model", "claude-3-5-sonnet"),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 4096),
            api_key_env=data.get("api_key_env", "ANTHROPIC_API_KEY"),
            base_url=data.get("base_url"),
        )


@dataclass
class MemoryConfigSpec:
    """Configuration for memory manager.

    This is separate from core.memory.MemoryConfig to support YAML loading
    with additional fields like 'type'.

    Attributes:
        type: Memory implementation ("simple" or "tiered").
        pinned_budget: Token budget for pinned items.
        working_budget: Token budget for working memory (aka working_memory_budget).
        episodic_budget: Token budget for conversation history.
        semantic_budget: Token budget for RAG cache.
        compression_threshold: Turns before compression.
        checkpoint_interval: Turns between checkpoints.
        checkpoint_path: Path for checkpoint storage.
    """
    type: str = "simple"
    pinned_budget: int = 1000
    working_budget: int = 4000
    episodic_budget: int = 8000
    semantic_budget: int = 4000
    compression_threshold: int = 6
    checkpoint_interval: int = 5
    checkpoint_path: str = "./.checkpoints"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryConfigSpec":
        """Create MemoryConfigSpec from dictionary.

        Args:
            data: Dictionary with memory configuration.

        Returns:
            MemoryConfigSpec instance.
        """
        return cls(
            type=data.get("type", "simple"),
            pinned_budget=data.get("pinned_budget", 1000),
            # Support both working_budget and working_memory_budget
            working_budget=data.get("working_budget", data.get("working_memory_budget", 4000)),
            episodic_budget=data.get("episodic_budget", 8000),
            semantic_budget=data.get("semantic_budget", 4000),
            compression_threshold=data.get("compression_threshold", 6),
            checkpoint_interval=data.get("checkpoint_interval", 5),
            checkpoint_path=data.get("checkpoint_path", "./.checkpoints"),
        )

    def to_memory_config(self) -> "MemoryConfig":
        """Convert to core.memory.MemoryConfig.

        Returns:
            MemoryConfig instance for TieredMemoryManager.
        """
        from .memory import MemoryConfig
        return MemoryConfig(
            pinned_budget=self.pinned_budget,
            working_budget=self.working_budget,
            episodic_budget=self.episodic_budget,
            semantic_budget=self.semantic_budget,
            compression_threshold=self.compression_threshold,
            checkpoint_interval=self.checkpoint_interval,
            checkpoint_path=self.checkpoint_path,
        )


@dataclass
class RuntimeConfigSpec:
    """Configuration for async runtime.

    Attributes:
        default_timeout: Task timeout in seconds.
        max_retries: Maximum retry attempts.
        retry_delay: Delay between retries in seconds.
        max_concurrent: Maximum concurrent tasks.
    """
    default_timeout: float = 30.0
    max_retries: int = 2
    retry_delay: float = 1.0
    max_concurrent: int = 10

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeConfigSpec":
        """Create RuntimeConfigSpec from dictionary.

        Args:
            data: Dictionary with runtime configuration.

        Returns:
            RuntimeConfigSpec instance.
        """
        return cls(
            default_timeout=data.get("default_timeout", 30.0),
            max_retries=data.get("max_retries", 2),
            retry_delay=data.get("retry_delay", 1.0),
            max_concurrent=data.get("max_concurrent", 10),
        )

    def to_runtime_config(self) -> "RuntimeConfig":
        """Convert to core.types.RuntimeConfig.

        Returns:
            RuntimeConfig instance for AsyncRuntime.
        """
        from .types import RuntimeConfig
        return RuntimeConfig(
            default_timeout=self.default_timeout,
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
            max_concurrent=self.max_concurrent,
        )


@dataclass
class PlannerConfigSpec:
    """Configuration for planner pipeline.

    Attributes:
        enable_router: Use TaskRouter for complexity-based routing.
        use_tool_planner: Use ToolDAGPlanner for MODERATE tasks.
        enable_context_analyzer: Enable context dependency analysis.
        enable_rule_planner: Enable rule-based fast path.
        enable_llm_enhancer: Enable LLM-based planning.
    """
    enable_router: bool = False
    use_tool_planner: bool = False
    enable_context_analyzer: bool = True
    enable_rule_planner: bool = True
    enable_llm_enhancer: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlannerConfigSpec":
        """Create PlannerConfigSpec from dictionary.

        Args:
            data: Dictionary with planner configuration.

        Returns:
            PlannerConfigSpec instance.
        """
        return cls(
            enable_router=data.get("enable_router", False),
            use_tool_planner=data.get("use_tool_planner", False),
            enable_context_analyzer=data.get("enable_context_analyzer", True),
            enable_rule_planner=data.get("enable_rule_planner", True),
            enable_llm_enhancer=data.get("enable_llm_enhancer", True),
        )

    def to_pipeline_config(self) -> "PipelineConfig":
        """Convert to PipelineConfig.

        Returns:
            PipelineConfig instance for PlannerPipeline.
        """
        from .planner import PipelineConfig, PlanningMode
        return PipelineConfig(
            enable_router=self.enable_router,
            use_tool_planner=self.use_tool_planner,
            enable_context_analyzer=self.enable_context_analyzer,
            enable_rule_planner=self.enable_rule_planner,
            enable_llm_enhancer=self.enable_llm_enhancer,
            planning_mode=PlanningMode.HYBRID,
        )


@dataclass
class SkillConfig:
    """Configuration for a single skill.

    Attributes:
        name: Skill name for routing.
        type: Skill type (builtin, markdown, wukong).
        path: Optional path to skill file (for markdown/wukong).
        params: Optional default parameters for the skill.
        enabled: Whether the skill is enabled.
    """
    name: str
    type: str = "builtin"
    path: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillConfig":
        """Create SkillConfig from dictionary.

        Args:
            data: Dictionary with skill configuration.

        Returns:
            SkillConfig instance.
        """
        return cls(
            name=data.get("name", ""),
            type=data.get("type", "builtin"),
            path=data.get("path"),
            params=data.get("params", {}),
            enabled=data.get("enabled", True),
        )


@dataclass
class AgentConfig:
    """Complete agent configuration.

    Combines all configuration aspects for creating an agent:
    - Basic metadata (name, version)
    - LLM settings
    - Memory settings
    - Runtime settings
    - Skill definitions
    - System prompt

    Attributes:
        name: Agent name.
        version: Agent version string.
        llm: LLM configuration.
        memory: Memory configuration.
        runtime: Runtime configuration.
        skills: List of skill configurations.
        system_prompt: System prompt for the agent.
        planner_type: Planner type ("simple" or "dag").
        enable_logging: Whether to enable logging.
    """
    name: str
    version: str = "1.0.0"
    llm: LLMConfig = field(default_factory=LLMConfig)
    memory: MemoryConfigSpec = field(default_factory=MemoryConfigSpec)
    runtime: RuntimeConfigSpec = field(default_factory=RuntimeConfigSpec)
    skills: List[SkillConfig] = field(default_factory=list)
    system_prompt: str = ""
    planner_type: str = "dag"
    enable_logging: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentConfig":
        """Create AgentConfig from dictionary.

        Args:
            data: Dictionary with agent configuration.

        Returns:
            AgentConfig instance.
        """
        # Parse nested configurations
        llm_data = data.get("llm", {})
        memory_data = data.get("memory", {})
        runtime_data = data.get("runtime", {})
        skills_data = data.get("skills", [])

        return cls(
            name=data.get("name", "Unnamed Agent"),
            version=data.get("version", "1.0.0"),
            llm=LLMConfig.from_dict(llm_data),
            memory=MemoryConfigSpec.from_dict(memory_data),
            runtime=RuntimeConfigSpec.from_dict(runtime_data),
            skills=[SkillConfig.from_dict(s) for s in skills_data],
            system_prompt=data.get("system_prompt", ""),
            planner_type=data.get("planner_type", "dag"),
            enable_logging=data.get("enable_logging", True),
        )

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> "AgentConfig":
        """Load AgentConfig from YAML file.

        Args:
            path: Path to YAML configuration file.

        Returns:
            AgentConfig instance.

        Raises:
            FileNotFoundError: If config file doesn't exist.
            yaml.YAMLError: If YAML parsing fails.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return cls.from_dict(data or {})

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation.
        """
        return {
            "name": self.name,
            "version": self.version,
            "llm": {
                "model": self.llm.model,
                "temperature": self.llm.temperature,
                "max_tokens": self.llm.max_tokens,
                "api_key_env": self.llm.api_key_env,
                "base_url": self.llm.base_url,
            },
            "memory": {
                "type": self.memory.type,
                "pinned_budget": self.memory.pinned_budget,
                "working_budget": self.memory.working_budget,
                "episodic_budget": self.memory.episodic_budget,
                "semantic_budget": self.memory.semantic_budget,
                "compression_threshold": self.memory.compression_threshold,
                "checkpoint_interval": self.memory.checkpoint_interval,
                "checkpoint_path": self.memory.checkpoint_path,
            },
            "runtime": {
                "default_timeout": self.runtime.default_timeout,
                "max_retries": self.runtime.max_retries,
                "retry_delay": self.runtime.retry_delay,
                "max_concurrent": self.runtime.max_concurrent,
            },
            "skills": [
                {
                    "name": s.name,
                    "type": s.type,
                    "path": s.path,
                    "params": s.params,
                    "enabled": s.enabled,
                }
                for s in self.skills
            ],
            "system_prompt": self.system_prompt,
            "planner_type": self.planner_type,
            "enable_logging": self.enable_logging,
        }

    def to_yaml(self, path: Union[str, Path]) -> None:
        """Save configuration to YAML file.

        Args:
            path: Path to save YAML file.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)


@dataclass
class CoreAgentConfig:
    """Configuration for the core CodeAgent loaded from YAML.

    This configuration uses the same format as subagent configs (coder, reviewer, etc.)
    with additional fields specific to the core agent.

    Attributes:
        name: Agent name.
        description: Agent description.
        mode: Agent mode ("primary" for core, "subagent" for others).
        model: LLM model identifier.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens in response.
        allowed_tools: List of allowed tool names.
        prompt: System prompt for the agent (unified with subagent format).
        max_turns: Maximum conversation turns.
        memory: Memory configuration spec (core agent only).
        runtime: Runtime configuration spec (core agent only).
        planner_type: Planner type ("simple" or "dag", core agent only).
        planner: Planner configuration spec (router, tool planner, etc.).
        enable_logging: Whether to enable logging.
    """
    name: str = "core"
    description: str = "核心 Agent，负责任务规划、分发和协调"
    mode: str = "primary"
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.7
    max_tokens: int = 8192
    allowed_tools: List[str] = field(default_factory=lambda: [
        "Read", "Write", "Edit", "Bash", "Glob", "Grep", "Subagent"
    ])
    prompt: str = ""  # 统一使用 prompt 字段
    max_turns: int = 100
    memory: MemoryConfigSpec = field(default_factory=MemoryConfigSpec)
    runtime: RuntimeConfigSpec = field(default_factory=RuntimeConfigSpec)
    planner_type: str = "dag"
    planner: PlannerConfigSpec = field(default_factory=PlannerConfigSpec)
    enable_logging: bool = True

    # Alias for backward compatibility
    @property
    def system_prompt(self) -> str:
        """Alias for prompt (backward compatibility)."""
        return self.prompt

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CoreAgentConfig":
        """Create CoreAgentConfig from dictionary.

        Supports both 'prompt' (new format) and 'system_prompt' (legacy format).

        Args:
            data: Dictionary with core agent configuration.

        Returns:
            CoreAgentConfig instance.
        """
        memory_data = data.get("memory", {})
        runtime_data = data.get("runtime", {})
        planner_data = data.get("planner", {})

        # Support both 'prompt' and 'system_prompt' for backward compatibility
        prompt = data.get("prompt", data.get("system_prompt", ""))

        return cls(
            name=data.get("name", "core"),
            description=data.get("description", "核心 Agent，负责任务规划、分发和协调"),
            mode=data.get("mode", "primary"),
            model=data.get("model", "claude-sonnet-4-20250514"),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 8192),
            allowed_tools=data.get("allowed_tools", [
                "Read", "Write", "Edit", "Bash", "Glob", "Grep", "Subagent"
            ]),
            prompt=prompt,
            max_turns=data.get("max_turns", 100),
            memory=MemoryConfigSpec.from_dict(memory_data),
            runtime=RuntimeConfigSpec.from_dict(runtime_data),
            planner_type=data.get("planner_type", "dag"),
            planner=PlannerConfigSpec.from_dict(planner_data),
            enable_logging=data.get("enable_logging", True),
        )

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> "CoreAgentConfig":
        """Load CoreAgentConfig from YAML file.

        Args:
            path: Path to YAML configuration file.

        Returns:
            CoreAgentConfig instance.

        Raises:
            FileNotFoundError: If config file doesn't exist.
            yaml.YAMLError: If YAML parsing fails.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return cls.from_dict(data or {})

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization.

        Uses the unified format compatible with subagent configs.

        Returns:
            Dictionary representation.
        """
        return {
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "allowed_tools": self.allowed_tools,
            "prompt": self.prompt,
            "max_turns": self.max_turns,
            "memory": {
                "type": self.memory.type,
                "pinned_budget": self.memory.pinned_budget,
                "working_budget": self.memory.working_budget,
                "episodic_budget": self.memory.episodic_budget,
                "semantic_budget": self.memory.semantic_budget,
            },
            "runtime": {
                "default_timeout": self.runtime.default_timeout,
                "max_retries": self.runtime.max_retries,
                "max_concurrent": self.runtime.max_concurrent,
            },
            "planner_type": self.planner_type,
            "planner": {
                "enable_router": self.planner.enable_router,
                "use_tool_planner": self.planner.use_tool_planner,
                "enable_context_analyzer": self.planner.enable_context_analyzer,
                "enable_rule_planner": self.planner.enable_rule_planner,
                "enable_llm_enhancer": self.planner.enable_llm_enhancer,
            },
            "enable_logging": self.enable_logging,
        }


# Default core agent config path
_DEFAULT_CORE_CONFIG_PATH = Path(__file__).parent.parent / "data" / "agents" / "core.yaml"

# Cached core agent config
_cached_core_config: Optional[CoreAgentConfig] = None


def load_core_agent_config(
    config_path: Optional[Union[str, Path]] = None,
    use_cache: bool = True,
) -> CoreAgentConfig:
    """Load core agent configuration from YAML file.

    This function provides a convenient way to load the core agent configuration
    with caching support. If no config_path is provided, it loads from the
    default location (src/nimbus/data/agents/core.yaml).

    Args:
        config_path: Optional path to YAML config file. If None, uses default.
        use_cache: Whether to use cached config (default: True).
                  Set to False to force reload.

    Returns:
        CoreAgentConfig instance.

    Example:
        ```python
        # Load default config
        config = load_core_agent_config()

        # Load custom config
        config = load_core_agent_config("~/.nimbus/agents/my_core.yaml")

        # Force reload (bypass cache)
        config = load_core_agent_config(use_cache=False)
        ```
    """
    global _cached_core_config

    # Use cache if available and requested
    if use_cache and _cached_core_config is not None and config_path is None:
        return _cached_core_config

    # Determine config path
    if config_path is None:
        path = _DEFAULT_CORE_CONFIG_PATH
    else:
        path = Path(config_path).expanduser()

    # Load config
    try:
        config = CoreAgentConfig.from_yaml(path)
        logger.debug(f"Loaded core agent config from: {path}")

        # Cache if using default path
        if config_path is None:
            _cached_core_config = config

        return config
    except FileNotFoundError:
        logger.warning(f"Core agent config not found at {path}, using defaults")
        return CoreAgentConfig()
    except Exception as e:
        logger.warning(f"Failed to load core agent config from {path}: {e}, using defaults")
        return CoreAgentConfig()


def reset_core_config_cache() -> None:
    """Reset the cached core agent configuration.

    Useful for testing or when configuration files have changed.
    """
    global _cached_core_config
    _cached_core_config = None
