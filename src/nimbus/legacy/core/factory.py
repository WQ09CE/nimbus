"""Agent Factory for creating agents from configuration.

This module provides the AgentFactory class for:
- Creating Agent instances from YAML configuration files
- Creating Agent instances from dictionary configuration
- Loading and registering skills dynamically
"""

import os
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional, Union

from .agent import CodeAgent
from .config import AgentConfig, LLMConfig, SkillConfig
from .logging import get_logger

logger = get_logger("factory")

# Type alias for skill functions
SkillFunc = Callable[..., Coroutine[Any, Any, Any]]


class MockLLMClient:
    """Mock LLM client for testing without actual API calls."""

    def __init__(self, default_response: str = "Mock response"):
        self.default_response = default_response

    async def complete(self, prompt: str) -> str:
        return self.default_response


class AgentFactory:
    """Factory for creating CodeAgent instances from configuration.

    The factory supports:
    - Loading configuration from YAML files
    - Loading configuration from dictionaries
    - Dynamically loading and registering skills
    - Creating custom LLM clients

    Example:
        ```python
        # From YAML file
        agent = AgentFactory.create(Path("agents/default.yaml"))

        # From dictionary
        config = {
            "name": "My Agent",
            "llm": {"model": "claude-3-5-sonnet"},
            "skills": [{"name": "synthesize", "type": "builtin"}]
        }
        agent = AgentFactory.create_from_dict(config)
        ```
    """

    # Registry of custom LLM client factories
    _llm_factories: Dict[str, Callable[[LLMConfig], Any]] = {}

    # Registry of custom skill loaders
    _skill_loaders: Dict[str, Callable[[SkillConfig], SkillFunc]] = {}

    @classmethod
    def register_llm_factory(
        cls,
        name: str,
        factory: Callable[[LLMConfig], Any]
    ) -> None:
        """Register a custom LLM client factory.

        Args:
            name: Factory name (usually model prefix like "claude", "gpt").
            factory: Function that creates LLM client from LLMConfig.

        Example:
            ```python
            def create_openai_client(config: LLMConfig):
                import openai
                return openai.AsyncOpenAI(api_key=os.getenv(config.api_key_env))

            AgentFactory.register_llm_factory("gpt", create_openai_client)
            ```
        """
        cls._llm_factories[name] = factory
        logger.debug(f"Registered LLM factory: {name}")

    @classmethod
    def register_skill_loader(
        cls,
        skill_type: str,
        loader: Callable[[SkillConfig], SkillFunc]
    ) -> None:
        """Register a custom skill loader.

        Args:
            skill_type: Skill type to handle (e.g., "wukong", "langchain").
            loader: Function that loads skill from SkillConfig.

        Example:
            ```python
            def load_wukong_skill(config: SkillConfig) -> SkillFunc:
                from wukong import load_skill
                return load_skill(config.path)

            AgentFactory.register_skill_loader("wukong", load_wukong_skill)
            ```
        """
        cls._skill_loaders[skill_type] = loader
        logger.debug(f"Registered skill loader: {skill_type}")

    @classmethod
    def create(
        cls,
        config_path: Union[str, Path],
        llm_client: Optional[Any] = None,
    ) -> CodeAgent:
        """Create Agent from YAML configuration file.

        Args:
            config_path: Path to YAML configuration file.
            llm_client: Optional pre-configured LLM client.
                       If not provided, will be created from config.

        Returns:
            Configured CodeAgent instance.

        Raises:
            FileNotFoundError: If config file doesn't exist.
            ValueError: If configuration is invalid.
        """
        config = AgentConfig.from_yaml(config_path)
        logger.info(f"Creating agent '{config.name}' from {config_path}")
        return cls.create_from_config(config, llm_client)

    @classmethod
    def create_from_dict(
        cls,
        config: Dict[str, Any],
        llm_client: Optional[Any] = None,
    ) -> CodeAgent:
        """Create Agent from dictionary configuration.

        Args:
            config: Dictionary with agent configuration.
            llm_client: Optional pre-configured LLM client.

        Returns:
            Configured CodeAgent instance.
        """
        agent_config = AgentConfig.from_dict(config)
        logger.info(f"Creating agent '{agent_config.name}' from dict")
        return cls.create_from_config(agent_config, llm_client)

    @classmethod
    def create_from_config(
        cls,
        config: AgentConfig,
        llm_client: Optional[Any] = None,
    ) -> CodeAgent:
        """Create Agent from AgentConfig instance.

        Args:
            config: AgentConfig instance.
            llm_client: Optional pre-configured LLM client.

        Returns:
            Configured CodeAgent instance.
        """
        # Create LLM client if not provided
        if llm_client is None:
            llm_client = cls._create_llm_client(config.llm)

        # Convert config specs to core types
        memory_config = config.memory.to_memory_config()
        runtime_config = config.runtime.to_runtime_config()

        # Create agent
        agent = CodeAgent(
            llm_client=llm_client,
            system_prompt=config.system_prompt,
            memory_type=config.memory.type,
            memory_config=memory_config,
            planner_type=config.planner_type,
            runtime_config=runtime_config,
            enable_logging=config.enable_logging,
        )

        # Load and register skills
        cls._register_skills(agent, config.skills, llm_client)

        logger.info(
            f"Agent '{config.name}' created with "
            f"{len(config.skills)} skills, "
            f"planner={config.planner_type}, "
            f"memory={config.memory.type}"
        )

        return agent

    @classmethod
    def _create_llm_client(cls, config: LLMConfig) -> Any:
        """Create LLM client from configuration.

        Args:
            config: LLM configuration.

        Returns:
            LLM client instance.
        """
        # Check for registered factory based on model prefix
        model_prefix = config.model.split("-")[0].lower()

        if model_prefix in cls._llm_factories:
            return cls._llm_factories[model_prefix](config)

        # Default: try to create Anthropic client
        if "claude" in config.model.lower():
            return cls._create_anthropic_client(config)

        # Fallback to mock client with warning
        logger.warning(
            f"No LLM factory for model '{config.model}', "
            "using mock client. Register a factory with "
            "AgentFactory.register_llm_factory()"
        )
        return MockLLMClient()

    @classmethod
    def _create_anthropic_client(cls, config: LLMConfig) -> Any:
        """Create Anthropic client wrapper.

        Args:
            config: LLM configuration.

        Returns:
            Anthropic client wrapper with complete() method.
        """
        try:
            import anthropic
        except ImportError:
            logger.warning(
                "anthropic package not installed, using mock client. "
                "Install with: pip install anthropic"
            )
            return MockLLMClient()

        api_key = os.getenv(config.api_key_env)
        if not api_key:
            logger.warning(
                f"API key not found in environment variable '{config.api_key_env}', "
                "using mock client"
            )
            return MockLLMClient()

        # Create wrapper class with complete() method
        class AnthropicWrapper:
            def __init__(self, client, model, max_tokens, temperature):
                self.client = client
                self.model = model
                self.max_tokens = max_tokens
                self.temperature = temperature

            async def complete(self, prompt: str) -> str:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.content[0].text

        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=config.base_url,
        )

        return AnthropicWrapper(
            client=client,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

    @classmethod
    def _register_skills(
        cls,
        agent: CodeAgent,
        skills: list,
        llm_client: Any,
    ) -> None:
        """Load and register skills for the agent.

        Args:
            agent: CodeAgent to register skills on.
            skills: List of SkillConfig.
            llm_client: LLM client for skills that need it.
        """
        for skill_config in skills:
            if not skill_config.enabled:
                logger.debug(f"Skipping disabled skill: {skill_config.name}")
                continue

            try:
                skill_func = cls._load_skill(skill_config, llm_client)
                if skill_func:
                    agent.register_skill(skill_config.name, skill_func)
                    logger.debug(f"Registered skill: {skill_config.name}")
            except Exception as e:
                logger.warning(f"Failed to load skill '{skill_config.name}': {e}")

    @classmethod
    def _load_skill(
        cls,
        config: SkillConfig,
        llm_client: Any,
    ) -> Optional[SkillFunc]:
        """Load a skill from configuration.

        Args:
            config: Skill configuration.
            llm_client: LLM client for skills that need it.

        Returns:
            Skill function or None if loading fails.
        """
        skill_type = config.type.lower()

        # Check for custom skill loader
        if skill_type in cls._skill_loaders:
            return cls._skill_loaders[skill_type](config)

        # Handle builtin skills
        if skill_type == "builtin":
            return cls._load_builtin_skill(config, llm_client)

        # Handle markdown skills
        if skill_type == "markdown":
            return cls._load_markdown_skill(config, llm_client)

        logger.warning(f"Unknown skill type: {skill_type}")
        return None

    @classmethod
    def _load_builtin_skill(
        cls,
        config: SkillConfig,
        llm_client: Any,
    ) -> Optional[SkillFunc]:
        """Load a builtin skill.

        Args:
            config: Skill configuration.
            llm_client: LLM client for skills that need it.

        Returns:
            Skill function or None.
        """
        skill_name = config.name.lower()

        # Import builtin skills
        try:
            if skill_name == "synthesize":
                from ..skills.synthesize import create_synthesize_skill
                return create_synthesize_skill(llm_client)

            elif skill_name == "search":
                from ..skills.search import web_search
                return web_search

            elif skill_name == "summarize":
                from ..skills.summarize import summarize_text
                return summarize_text

            elif skill_name == "keywords":
                from ..skills.summarize import extract_keywords
                return extract_keywords

            else:
                logger.warning(f"Unknown builtin skill: {skill_name}")
                return None

        except ImportError as e:
            logger.warning(f"Failed to import builtin skill '{skill_name}': {e}")
            return None

    @classmethod
    def _load_markdown_skill(
        cls,
        config: SkillConfig,
        llm_client: Any,
    ) -> Optional[SkillFunc]:
        """Load a markdown-defined skill.

        Args:
            config: Skill configuration with path to markdown file.
            llm_client: LLM client for skill execution.

        Returns:
            Skill function or None.
        """
        if not config.path:
            logger.warning(f"Markdown skill '{config.name}' has no path")
            return None

        path = Path(config.path).expanduser()
        if not path.exists():
            logger.warning(f"Markdown skill file not found: {path}")
            return None

        # Read markdown content
        content = path.read_text(encoding="utf-8")

        # Create a simple skill that uses the markdown as prompt template
        async def markdown_skill(**kwargs) -> str:
            # Replace placeholders in markdown with kwargs
            prompt = content
            for key, value in kwargs.items():
                prompt = prompt.replace(f"{{{{{key}}}}}", str(value))

            # Execute with LLM
            return await llm_client.complete(prompt)

        return markdown_skill


# Convenience function for quick agent creation
def create_agent(
    config: Union[str, Path, Dict[str, Any]],
    llm_client: Optional[Any] = None,
) -> CodeAgent:
    """Convenience function to create an agent from config.

    Args:
        config: Path to YAML file or configuration dictionary.
        llm_client: Optional pre-configured LLM client.

    Returns:
        Configured CodeAgent instance.

    Example:
        ```python
        # From YAML
        agent = create_agent("agents/default.yaml")

        # From dict
        agent = create_agent({"name": "My Agent", "llm": {"model": "gpt-4"}})
        ```
    """
    if isinstance(config, (str, Path)):
        return AgentFactory.create(config, llm_client)
    else:
        return AgentFactory.create_from_dict(config, llm_client)
