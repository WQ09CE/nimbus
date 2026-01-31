"""CodeAgent - Main orchestrator for code exploration and analysis.

Architecture Layer: 2 (Application)
Von Neumann Role: Process Definition (init/systemd)

In the Agent OS architecture, CodeAgent is the "init process" or "PID 1",
the first user-space process that bootstraps the entire agent system:
- Creates and manages the tool registry (loads device drivers)
- Initializes memory subsystem (mounts filesystems)
- Starts subagent executor (forks child processes)
- Orchestrates the execution lifecycle

CodeAgent is not part of the "kernel" itself but the primary application
that uses kernel services to accomplish user goals.

Execution model: Uses v2 AgentOS exclusively for all task execution.
The v1 SubagentDAG/SubagentRuntime has been removed in favor of the unified
AgentOS architecture which provides process-based orchestration with VCPU execution.
"""

__layer__ = 2  # Application Layer
__role__ = "Process_Definition"  # Main application process

import json
import uuid
from pathlib import Path
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
    TYPE_CHECKING,
    Union,
)

from .memory import SimpleMemory, TieredMemoryManager, MemoryConfig, PinnedItem, SubagentContext
from .agent_config import SubagentRegistry, SubagentConfig
from .permission import create_permission_manager, CODER_PERMISSIONS
from .config import CoreAgentConfig, load_core_agent_config
from .planner import LLMClient
from .types import AgentResponse
from .logging import get_logger, setup_logging
from .tracing import get_tracer, Tracer

if TYPE_CHECKING:
    from nimbus.tools import ToolRegistry
    from nimbus.tools.subagent import SubagentExecutor
    from nimbus.agentos import AgentOS

SkillFunc = Callable[..., Coroutine[Any, Any, Any]]


class CodeAgent:
    """Agent for code exploration and analysis.

    Features:
    - Read/Glob/Grep tools for code exploration
    - SubagentDAG orchestration for task decomposition
    - Subagents use AgenticRunner for LLM-driven tool selection
    - Memory for conversation context
    - Extensible skill system

    Supports two memory implementations:
    - "simple": Basic memory with conversation history (default, backward compatible)
    - "tiered": Advanced multi-tier memory with compression and checkpointing

    Execution Model:
    - Uses task mode (SubagentDAG) exclusively
    - User goals are decomposed into subagent tasks (eye, body, mind, tongue, nose)
    - Each subagent runs independently with isolated context and permissions
    - Subagents execute in parallel when dependencies allow

    Configuration:
    - The agent can be configured via YAML file (core.yaml)
    - Default config location: src/nimbus/data/agents/core.yaml
    - Constructor parameters override YAML configuration
    """

    # Class-level default config (loaded once)
    _default_config: Optional[CoreAgentConfig] = None

    @classmethod
    def get_default_config(cls) -> CoreAgentConfig:
        """Get the default core agent configuration.

        Loads from YAML file on first call and caches the result.

        Returns:
            CoreAgentConfig instance with default settings.
        """
        if cls._default_config is None:
            cls._default_config = load_core_agent_config()
        return cls._default_config

    @classmethod
    def reset_default_config(cls) -> None:
        """Reset the cached default configuration.

        Useful for testing or when configuration files have changed.
        """
        cls._default_config = None

    @classmethod
    def from_config(
        cls,
        llm_client: LLMClient,
        config: Optional[CoreAgentConfig] = None,
        session_id: Optional[str] = None,
        workspace: Optional[Path] = None,
        tool_registry: Optional["ToolRegistry"] = None,
        **overrides: Any,
    ) -> "CodeAgent":
        """Create a CodeAgent from configuration.

        This is the recommended way to create a CodeAgent with YAML configuration.
        The config parameter provides base settings which can be overridden by
        explicit keyword arguments.

        Args:
            llm_client: LLM client with async complete(prompt) method.
            config: CoreAgentConfig instance. If None, loads default config.
            session_id: Session identifier for checkpointing.
            workspace: Workspace directory for tool sandbox validation.
            tool_registry: Optional tool registry for code tools.
            **overrides: Override specific config values:
                - system_prompt: Override system prompt
                - memory_type: Override memory type
                - enable_logging: Override logging setting

        Returns:
            Configured CodeAgent instance.

        Example:
            ```python
            # Use default config
            agent = CodeAgent.from_config(llm_client)

            # Use custom config
            config = CoreAgentConfig.from_yaml("my_config.yaml")
            agent = CodeAgent.from_config(llm_client, config=config)

            # Override specific settings
            agent = CodeAgent.from_config(
                llm_client,
                system_prompt="Custom prompt",
                memory_type="tiered",
            )
            ```
        """
        if config is None:
            config = cls.get_default_config()

        # Apply overrides
        system_prompt = overrides.get("system_prompt", config.system_prompt)
        memory_type = overrides.get("memory_type", config.memory.type)
        enable_logging = overrides.get("enable_logging", config.enable_logging)

        # Convert config specs to core types
        memory_config = config.memory.to_memory_config()

        return cls(
            llm_client=llm_client,
            system_prompt=system_prompt,
            memory_type=memory_type,
            memory_config=memory_config,
            enable_logging=enable_logging,
            session_id=session_id,
            workspace=workspace,
            tool_registry=tool_registry,
        )

    def __init__(
        self,
        llm_client: LLMClient,
        system_prompt: Optional[str] = None,
        memory_type: Optional[str] = None,
        memory_config: Optional[MemoryConfig] = None,
        enable_logging: Optional[bool] = None,
        session_id: Optional[str] = None,
        workspace: Optional[Path] = None,
        tool_registry: Optional["ToolRegistry"] = None,
        load_yaml_config: bool = True,
        # Deprecated parameters (kept for backward compatibility, ignored)
        planner_type: Optional[str] = None,
        runtime_config: Any = None,
        execution_mode: Optional[str] = None,
        runtime_version: Optional[str] = None,  # Deprecated, always uses v2
    ):
        """Initialize the code agent.

        Args:
            llm_client: LLM client with async complete(prompt) method.
            system_prompt: Optional system prompt for the agent. If None and
                          load_yaml_config is True, uses value from YAML config.
            memory_type: Memory implementation ("simple" or "tiered"). If None and
                        load_yaml_config is True, uses value from YAML config.
            memory_config: Configuration for TieredMemoryManager.
            enable_logging: Enable structured logging and tracing. If None and
                           load_yaml_config is True, uses value from YAML config.
            session_id: Session identifier for checkpointing.
            workspace: Workspace directory for tool sandbox validation.
            tool_registry: Optional tool registry for code tools. If not provided,
                          default tools (Read, Glob, Grep) are registered automatically.
            load_yaml_config: Whether to load defaults from YAML config file.
                             Set to False for backward compatibility or testing.
            planner_type: Deprecated, ignored. Kept for backward compatibility.
            runtime_config: Deprecated, ignored. Kept for backward compatibility.
            execution_mode: Deprecated, ignored. Task mode is now the only mode.
            runtime_version: Deprecated, ignored. Always uses v2 AgentOS.
        """
        # Load YAML config for default values if requested
        if load_yaml_config:
            yaml_config = self.get_default_config()
        else:
            yaml_config = None

        # Apply YAML defaults for None values
        if system_prompt is None:
            system_prompt = yaml_config.system_prompt if yaml_config else ""
        if memory_type is None:
            memory_type = yaml_config.memory.type if yaml_config else "simple"
        if enable_logging is None:
            enable_logging = yaml_config.enable_logging if yaml_config else True

        self.llm_client = llm_client
        self.system_prompt = system_prompt
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.workspace = workspace or Path.cwd()

        # Initialize tool registry
        self.tool_registry = tool_registry or self._create_default_tools()

        # Initialize memory based on type
        self._memory_type = memory_type
        if memory_type == "tiered":
            config = memory_config or MemoryConfig()
            self.memory = TieredMemoryManager(
                config=config, llm_client=llm_client, session_id=self.session_id
            )
            # Add workspace info to pinned memory
            if self.workspace:
                workspace_item = PinnedItem(
                    id="workspace",
                    type="system",
                    content=f"Current workspace: {self.workspace}",
                    priority=100,  # High priority
                    description="Agent workspace directory, all relative paths are based on this",
                    read_only=True,
                )
                self.memory.pin(workspace_item)
        else:
            self.memory = SimpleMemory()

        # Logging and tracing
        self._enable_logging = enable_logging
        if enable_logging:
            setup_logging()
            self.logger = get_logger("agent")
            self.tracer: Optional[Tracer] = get_tracer("agent")
        else:
            self.logger = None
            self.tracer = None

        # Initialize subagent system
        self._subagent_registry = SubagentRegistry()
        try:
            self._subagent_registry.load_from_directories(include_builtin=True)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to load subagent configs: {e}")
        self._permission_manager = create_permission_manager(CODER_PERMISSIONS)
        self._subagent_executor: Optional["SubagentExecutor"] = None

        # Initialize v2 AgentOS runtime
        self._v2_agentos: Optional["AgentOS"] = None
        self._init_v2_runtime()

        self._register_default_skills()

    def _create_default_tools(self) -> "ToolRegistry":
        """Create registry with default code exploration tools.

        Registers the Read, Glob, Grep, Bash, Subagent and Batch tools for
        file operations, command execution, and task delegation.

        Returns:
            ToolRegistry with default tools registered.
        """
        from nimbus.tools import (
            ToolRegistry,
            read_file,
            glob_files,
            grep_content,
            bash_command,
            subagent_task,
            get_subagent_result,
            cancel_subagent,
            list_subagents,
            batch_tool,
        )

        registry = ToolRegistry()
        # Core file operation tools
        registry.register_decorated(read_file)
        registry.register_decorated(glob_files)
        registry.register_decorated(grep_content)
        registry.register_decorated(bash_command)
        # Subagent tools
        registry.register_decorated(subagent_task)
        registry.register_decorated(get_subagent_result)
        registry.register_decorated(cancel_subagent)
        registry.register_decorated(list_subagents)
        # Batch tool
        registry.register_decorated(batch_tool)
        return registry

    def _init_v2_runtime(self) -> None:
        """Initialize V2 runtime (AgentOS) with native v2 tools.

        Creates AgentOS with:
        - LLM adapter wrapping the LLM client
        - Native v2 tools (Read, Glob, Grep, Bash, Write, Edit)

        This method is called during __init__.
        """
        from nimbus.agentos import create_agent_os
        from nimbus.tools import register_default_tools

        # Create LLM adapter for v2
        v2_llm = self._create_v2_llm_adapter()

        # Create AgentOS with native v2 tools
        self._v2_agentos = create_agent_os(
            llm_client=v2_llm,
            system_rules=self.system_prompt,
            workspace=self.workspace,
            register_defaults=True,  # Register native v2 tools
        )

        if self.logger:
            self.logger.info(
                "v2_runtime_initialized",
                tools=self._v2_agentos.list_tools(),
            )

    def _create_v2_llm_adapter(self) -> Any:
        """Create LLM adapter for v2 AgentOS.

        Wraps the existing LLM client to implement v2 LLMClient protocol.

        Returns:
            LLM client compatible with v2 VCPU.
        """
        # Check if client already has chat method (v2 compatible)
        if hasattr(self.llm_client, 'chat'):
            return self.llm_client

        # Create adapter for clients with complete_with_tools method
        class V2LLMAdapter:
            """Adapter for v2 LLMClient protocol."""

            def __init__(self, client: Any):
                self.client = client

            async def chat(
                self,
                messages: List[Dict[str, Any]],
                tools: Optional[List[Dict[str, Any]]] = None,
            ) -> Any:
                """Send messages to LLM with tools."""
                response = await self.client.complete_with_tools(
                    messages=messages,
                    tools=tools,
                )
                return V2LLMResponse(response)

        class V2LLMResponse:
            """Response wrapper for v2 protocol."""

            def __init__(self, completion_response: Any):
                self._response = completion_response

            @property
            def content(self) -> Optional[str]:
                return self._response.content

            @property
            def tool_calls(self) -> Optional[List[Any]]:
                if not self._response.tool_calls:
                    return None
                v2_calls = []
                for tc in self._response.tool_calls:
                    # Convert arguments to JSON string if it's a dict
                    # v1 LLM clients return Dict[str, Any], v2 protocol expects JSON string
                    args = tc.arguments
                    if isinstance(args, dict):
                        args = json.dumps(args)
                    v2_calls.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": args,
                        },
                    })
                return v2_calls

        return V2LLMAdapter(self.llm_client)

    def _register_default_skills(self) -> None:
        """Register built-in skills."""
        from ..skills.synthesize import create_synthesize_skill
        from ..skills.search import web_search
        from ..skills.summarize import summarize_text, extract_keywords

        synthesize_skill = create_synthesize_skill(self.llm_client)
        self.register_skill("synthesize", synthesize_skill)
        self.register_skill("search", web_search)
        self.register_skill("summarize", summarize_text)
        self.register_skill("keywords", extract_keywords)

    def register_skill(self, name: str, func: SkillFunc) -> None:
        """Register a custom skill.

        Skills are stored in an internal registry for use by subagents.

        Args:
            name: Skill name for routing.
            func: Async function implementing the skill.
        """
        if not hasattr(self, "_skills"):
            self._skills: Dict[str, SkillFunc] = {}
        self._skills[name] = func

    def get_skill_names(self) -> set:
        """Get set of registered skill and tool names.

        Returns:
            Set of all skill names plus tool names from the tool registry.
        """
        names: set = set()
        if hasattr(self, "_skills"):
            names.update(self._skills.keys())
        if self.tool_registry:
            names.update(self.tool_registry.list_tools())
        return names

    def on_file_upload(self, filename: str, file_type: str, summary: str) -> None:
        """Handle file upload event.

        Args:
            filename: Name of the uploaded file.
            file_type: Type of file (e.g., "pdf", "csv").
            summary: Brief summary of file contents.
        """
        if self._memory_type == "tiered":
            item = PinnedItem(
                id=f"file:{filename}",
                type="file_meta",
                content=f"[{file_type}] {filename}: {summary}",
                priority=10,
            )
            self.memory.pin(item)
        else:
            metadata = f"[{file_type}] {summary}"
            self.memory.pin(filename, metadata)

    def on_file_remove(self, filename: str) -> None:
        """Handle file removal event.

        Args:
            filename: Name of the file being removed.
        """
        if self._memory_type == "tiered":
            self.memory.unpin(f"file:{filename}")
        else:
            self.memory.unpin(filename)

    async def run(self, user_input: str) -> AgentResponse:
        """Process user input and generate response.

        Uses V2 AgentOS runtime for task execution.

        Args:
            user_input: User's message or command.

        Returns:
            AgentResponse with text and optional artifacts.
        """
        try:
            if self.logger:
                self.logger.info(
                    "agent_run_start",
                    user_input=user_input[:100],
                )

            # Add user input to memory
            if self._memory_type == "tiered":
                self.memory.add_turn_sync("user", user_input)
            else:
                self.memory.add_turn("user", user_input)

            # Get context for planning
            context = self.memory.get_context()

            # Execute using V2 runtime (AgentOS)
            if self._v2_agentos is None:
                raise RuntimeError("AgentOS not initialized")

            result = await self._v2_agentos.run(f"Context:\n{context}\n\nGoal: {user_input}")
            if result.status == "OK":
                response_text = str(result.output) if result.output else "Task completed successfully."
            else:
                error_msg = str(result.fault) if result.fault else "Unknown error"
                response_text = f"Task failed: {error_msg}"

            # Truncate overly long responses
            max_response_length = 50000  # ~12k tokens
            if len(response_text) > max_response_length:
                if self.logger:
                    self.logger.warning(
                        "response_truncated",
                        original_length=len(response_text),
                        truncated_length=max_response_length,
                    )
                response_text = (
                    response_text[:max_response_length] + "\n\n[Response truncated due to length]"
                )

            # Add response to memory
            if self._memory_type == "tiered":
                await self.memory.add_turn("assistant", response_text)
            else:
                self.memory.add_turn("assistant", response_text)

            if self.logger:
                self.logger.info("agent_run_complete")

            return AgentResponse(
                text=response_text,
                memory_stats=self.get_memory_stats(),
            )

        except Exception as e:
            if self.logger:
                self.logger.error("agent_run_failed", error=str(e))
            error_msg = f"An error occurred: {str(e)}"
            return AgentResponse(
                text=error_msg,
                error=str(e),
                memory_stats=self.get_memory_stats(),
            )

    def clear_memory(self) -> None:
        """Clear conversation history."""
        self.memory.clear_history()

    def reset(self) -> None:
        """Fully reset the agent state."""
        self.memory.clear()

    async def run_stream(
        self,
        user_input: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Process user input with streaming status updates.

        Uses V2 AgentOS runtime for task execution.

        Args:
            user_input: User's message or command.
            history: Optional conversation history from external cache.
                     List of dicts with 'role' and 'content' keys.
                     If provided, this history is injected into memory context
                     for planning, enabling multi-turn conversations.

        Yields:
            Status dicts with type and content fields:
            - {"type": "status", "content": "..."}
            - {"type": "task_start", "dag_id": "...", "nodes": N}
            - {"type": "subagent_complete", "node_id": "...", "status": "...", "summary": "..."}
            - {"type": "task_complete", "dag_id": "...", "status": "...", "stats": {...}}
            - {"type": "response", "content": "..."}
            - {"type": "error", "content": "..."}
            - {"type": "complete", "content": "..."}
        """
        try:
            yield {
                "type": "status",
                "content": "Starting v2 runtime...",
            }

            if self.logger:
                self.logger.info(
                    "agent_run_stream_start",
                    user_input=user_input[:100],
                )

            # Add user input to memory
            if self._memory_type == "tiered":
                self.memory.add_turn_sync("user", user_input)
            else:
                self.memory.add_turn("user", user_input)

            # Build context
            if history:
                context = self._build_context_from_history(history)
            else:
                context = self.memory.get_context()

            # Execute using V2 runtime (AgentOS)
            if self._v2_agentos is None:
                raise RuntimeError("AgentOS not initialized")

            yield {"type": "status", "content": "Spawning v2 process..."}

            full_goal = f"Context:\n{context}\n\nGoal: {user_input}"
            pid = self._v2_agentos.spawn(full_goal)

            yield {
                "type": "task_start",
                "dag_id": pid,
                "nodes": 1,
                "runtime": "v2",
            }

            result = await self._v2_agentos.wait(pid)

            if result.status == "OK":
                response_text = str(result.output) if result.output else "Task completed."
                yield {
                    "type": "subagent_complete",
                    "node_id": pid,
                    "status": "completed",
                    "summary": response_text[:500] if response_text else "Task completed",
                }
            else:
                error_msg = str(result.fault) if result.fault else "Unknown error"
                response_text = f"Task failed: {error_msg}"
                yield {
                    "type": "subagent_complete",
                    "node_id": pid,
                    "status": "failed",
                    "summary": error_msg,
                }

            yield {
                "type": "task_complete",
                "dag_id": pid,
                "status": "success" if result.status == "OK" else "failed",
                "final_summary": response_text,
            }

            # Add response to memory
            if response_text:
                if self._memory_type == "tiered":
                    await self.memory.add_turn("assistant", response_text)
                else:
                    self.memory.add_turn("assistant", response_text)

            if self.logger:
                self.logger.info("agent_run_stream_complete")

            yield {"type": "response", "content": response_text}
            yield {"type": "complete", "content": response_text or "Task completed."}

        except Exception as e:
            if self.logger:
                self.logger.error("agent_run_stream_failed", error=str(e))
            yield {"type": "error", "content": f"An error occurred: {str(e)}"}

    def _build_context_from_history(self, history: List[Dict[str, Any]]) -> str:
        """Build context string from external conversation history.

        Converts a list of message dicts into a formatted context string
        suitable for the planner and synthesize skill.

        Args:
            history: List of message dicts with 'role' and 'content' keys.

        Returns:
            Formatted context string with conversation history.
        """
        if not history:
            return ""

        parts = []

        # Add workspace info if available
        if self.workspace:
            parts.append(f"[Workspace: {self.workspace}]")

        # Format conversation history with clear role labels
        parts.append("=== Conversation History ===")
        parts.append(
            "(注意: 'User'=人类用户, 'Assistant'=AI助手)"
        )
        for msg in history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if role == "user":
                parts.append(f"User(人类): {content}")
            elif role == "assistant":
                parts.append(f"Assistant(AI): {content}")
            elif role == "system":
                parts.append(f"System: {content}")
        parts.append("=== End History ===")

        return "\n".join(parts)

    def _build_system_instruction(self) -> str:
        """Build system instruction for the agent.

        Combines the system_prompt with workspace information.

        Returns:
            System instruction string.
        """
        parts = []

        if self.system_prompt:
            parts.append(self.system_prompt)

        # Add workspace info
        if self.workspace:
            parts.append(f"\nWorkspace: {self.workspace}")

        return "\n".join(parts) if parts else ""

    # =========================================================================
    # Memory and Tracing utilities
    # =========================================================================

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get memory usage statistics.

        Returns:
            Dictionary with memory stats.
        """
        base_stats: Dict[str, Any] = {}

        if self._memory_type == "tiered":
            stats = self.memory.get_stats()
            base_stats.update({
                "type": "tiered",
                "pinned_tokens": stats.pinned_tokens,
                "working_tokens": stats.working_tokens,
                "episodic_tokens": stats.episodic_tokens,
                "semantic_tokens": stats.semantic_tokens,
                "total_tokens": stats.total_tokens,
                "compression_count": stats.compression_count,
                "turn_count": stats.turn_count,
            })
        else:
            base_stats.update({
                "type": "simple",
                "turn_count": self.memory.get_turn_count(),
                "pinned_count": self.memory.get_pinned_count(),
            })

        return base_stats

    def get_trace_summary(self) -> Optional[Dict[str, Any]]:
        """Get execution trace summary.

        Returns:
            Trace summary or None if tracing disabled.
        """
        if self.tracer:
            return self.tracer.get_trace_summary()
        return None

    async def checkpoint(self) -> Optional[str]:
        """Manually trigger a checkpoint.

        Returns:
            Checkpoint file path or None if not using tiered memory.
        """
        if self._memory_type == "tiered":
            return await self.memory.checkpoint()
        return None

    async def restore_checkpoint(self) -> bool:
        """Restore from latest checkpoint.

        Returns:
            True if restored, False otherwise.
        """
        if self._memory_type == "tiered":
            return await self.memory.restore()
        return False

    def set_working_context(self, key: str, value: Any) -> None:
        """Set working memory context (tiered memory only).

        Args:
            key: Context key.
            value: Context value.
        """
        if self._memory_type == "tiered":
            self.memory.set_working(key, value)

    def get_working_context(self, key: str, default: Any = None) -> Any:
        """Get working memory context (tiered memory only).

        Args:
            key: Context key.
            default: Default value.

        Returns:
            Context value or default.
        """
        if self._memory_type == "tiered":
            return self.memory.get_working(key, default)
        return default

    # =========================================================================
    # Subagent System (Phase 4)
    # =========================================================================

    async def spawn_subagent(
        self,
        prompt: str,
        subagent_type: str,
        run_in_background: bool = False,
        allowed_tools: Optional[List[str]] = None,
        max_turns: int = 50,
        **kwargs: Any,
    ) -> Union[str, Dict[str, Any]]:
        """Spawn a subagent to handle a subtask.

        Creates an isolated child agent with restricted permissions to handle
        specific tasks like exploration, research, implementation, or review.

        Args:
            prompt: Task description for the subagent.
            subagent_type: Type of subagent (explorer, researcher, coder, reviewer).
            run_in_background: If True, return immediately with agent_id.
            allowed_tools: Optional explicit tool permissions.
            max_turns: Maximum conversation turns.
            **kwargs: Additional arguments passed to the subagent.

        Returns:
            For foreground execution: Result dictionary with summary and details.
            For background execution: Agent ID string for later retrieval.

        Raises:
            ValueError: If subagent_type is unknown or tools are invalid.

        Example:
            >>> result = await agent.spawn_subagent(
            ...     prompt="Explore the src/nimbus directory",
            ...     subagent_type="explorer",
            ... )
            >>> print(result["summary"])
        """
        from nimbus.tools.subagent import get_executor

        # Get subagent configuration
        config = self._subagent_registry.get(subagent_type)
        if config is None:
            available = self._subagent_registry.list_names(mode="subagent")
            raise ValueError(
                f"Unknown subagent type: {subagent_type}. "
                f"Available types: {available}"
            )

        # Determine allowed tools
        if allowed_tools is None:
            allowed_tools = config.allowed_tools

        # Validate tools against available tools
        available_tools = set(self.tool_registry.list_tools())
        invalid_tools = self._subagent_registry.validate_tools(config, list(available_tools))
        if invalid_tools:
            if self.logger:
                self.logger.warning(
                    "subagent_invalid_tools",
                    subagent_type=subagent_type,
                    invalid_tools=invalid_tools,
                )
            # Filter out invalid tools
            allowed_tools = [t for t in allowed_tools if t not in invalid_tools]

        # Create isolated context from parent memory
        context = SubagentContext.from_parent_memory(
            parent_memory=self.memory,
            subagent_id="",  # Will be auto-generated
            subagent_type=subagent_type,
            max_history=5,
        )

        # Get or create executor
        if self._subagent_executor is None:
            self._subagent_executor = get_executor(
                workspace=self.workspace,
                tool_registry=self.tool_registry,
                llm_client=self.llm_client,
                parent_tools=available_tools,
            )

        # Get parent context string
        parent_context = context.get_context()

        # Spawn subagent
        result = await self._subagent_executor.spawn(
            prompt=prompt,
            subagent_type=subagent_type,
            description=f"{subagent_type}: {prompt[:30]}...",
            run_in_background=run_in_background,
            max_turns=max_turns,
            allowed_tools=set(allowed_tools) if allowed_tools else None,
            parent_context=parent_context,
        )

        if self.logger:
            self.logger.info(
                "subagent_spawned",
                subagent_type=subagent_type,
                background=run_in_background,
                agent_id=result.get("agent_id"),
            )

        # For background execution, return just the agent_id
        if run_in_background:
            return result.get("agent_id", "")

        return result

    async def spawn_subagent_and_verify(
        self,
        prompt: str,
        subagent_type: str,
        verify: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Spawn a subagent and optionally verify results.

        This method extends spawn_subagent with an additional verification step
        that checks if files reported as modified actually exist on disk.

        Args:
            prompt: Task description for the subagent.
            subagent_type: Type of subagent (explorer, researcher, coder, reviewer).
            verify: If True, verify that modified files exist after completion.
            **kwargs: Additional arguments passed to spawn_subagent.

        Returns:
            Result dictionary with additional 'verification' field:
            - "verification": "PASSED" if all checks pass
            - "verification": "FAILED: <reason>" if verification fails
            - "verification": "SKIPPED" if verify=False

        Raises:
            ValueError: If subagent_type is unknown or tools are invalid.

        Example:
            >>> result = await agent.spawn_subagent_and_verify(
            ...     prompt="Refactor auth.py to use new API",
            ...     subagent_type="coder",
            ...     verify=True,
            ... )
            >>> if result.get("verification") == "PASSED":
            ...     print("Changes verified successfully")
        """
        # Spawn the subagent (always foreground for verification)
        result = await self.spawn_subagent(
            prompt=prompt,
            subagent_type=subagent_type,
            run_in_background=False,  # Must be foreground for verification
            **kwargs,
        )

        # Handle case where spawn_subagent returns a string (background agent_id)
        if isinstance(result, str):
            return {
                "agent_id": result,
                "status": "running",
                "verification": "SKIPPED",
                "message": "Background execution - verification not applicable",
            }

        # Skip verification if not requested
        if not verify:
            result["verification"] = "SKIPPED"
            return result

        # Only verify completed tasks
        if result.get("status") != "completed":
            result["verification"] = "SKIPPED"
            return result

        # Verify that modified files exist
        modified_files = result.get("files_modified", [])
        for file_path in modified_files:
            if not Path(file_path).exists():
                result["verification"] = f"FAILED: File not found - {file_path}"
                if self.logger:
                    self.logger.warning(
                        "subagent_verification_failed",
                        file_path=file_path,
                        reason="file_not_found",
                    )
                return result

        result["verification"] = "PASSED"
        if self.logger:
            self.logger.info(
                "subagent_verification_passed",
                files_verified=len(modified_files),
            )

        return result

    async def get_subagent_result(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get the result of a background subagent.

        Args:
            agent_id: ID of the subagent.

        Returns:
            Result dictionary if available, None if not found.
        """
        if self._subagent_executor is None:
            return None
        return await self._subagent_executor.get_result(agent_id)

    async def cancel_subagent(self, agent_id: str) -> bool:
        """Cancel a running background subagent.

        Args:
            agent_id: ID of the subagent to cancel.

        Returns:
            True if cancelled, False if not found or already complete.
        """
        if self._subagent_executor is None:
            return False
        return await self._subagent_executor.cancel(agent_id)

    def get_subagent_types(self) -> List[str]:
        """Get all available subagent types.

        Returns:
            List of subagent type names.
        """
        return self._subagent_registry.list_names(mode="subagent")

    def get_subagent_config(self, subagent_type: str) -> Optional[SubagentConfig]:
        """Get configuration for a specific subagent type.

        Args:
            subagent_type: Name of the subagent type.

        Returns:
            SubagentConfig if found, None otherwise.
        """
        return self._subagent_registry.get(subagent_type)

    def list_running_subagents(self) -> List[str]:
        """List IDs of all running background subagents.

        Returns:
            List of agent IDs.
        """
        if self._subagent_executor is None:
            return []
        return self._subagent_executor.list_running()

    def list_completed_subagents(self) -> List[str]:
        """List IDs of all completed subagents.

        Returns:
            List of agent IDs.
        """
        if self._subagent_executor is None:
            return []
        return self._subagent_executor.list_completed()


# Backward compatibility alias
NotebookAgent = CodeAgent
