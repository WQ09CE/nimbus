"""Subagent tool for spawning isolated child agents.

This module provides SubagentTool for creating and managing child agents with:
- Isolated context (read-only snapshot from parent)
- Tool permission restrictions (must be subset of parent)
- Recursive depth limiting (max_depth=3)
- Concurrent execution limiting (max_concurrent=5)
- Support for foreground (blocking) and background execution

Inspired by Claude Code's Task tool design.

Example:
    >>> result = await subagent_task(
    ...     prompt="Explore the src/nimbus/tools directory",
    ...     subagent_type="explorer",
    ...     description="Explore tools dir",
    ... )
    >>> print(result["summary"])
    Found 12 Python files in the tools directory...
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, TYPE_CHECKING

from .base import ToolParameter, tool

if TYPE_CHECKING:
    from nimbus.tools import ToolRegistry
    from nimbus.core.agent_config import SubagentRegistry


# =============================================================================
# Constants
# =============================================================================

# Maximum recursion depth for nested subagents
MAX_DEPTH = 3

# Maximum concurrent background subagents
MAX_CONCURRENT = 5

# Default max turns for subagent execution
DEFAULT_MAX_TURNS = 50

# Subagent type to tool permission mapping
SUBAGENT_TOOL_PERMISSIONS: Dict[str, Set[str]] = {
    "explorer": {"Read", "Glob", "Grep"},
    "researcher": {"Read", "Glob", "Grep", "WebSearch", "WebFetch"},
    "coder": {"Read", "Write", "Edit", "Bash", "Glob", "Grep"},
    "reviewer": {"Read", "Glob", "Grep"},
}


# =============================================================================
# Subagent Types
# =============================================================================


class SubagentType(str, Enum):
    """Types of subagents with predefined capabilities.

    Attributes:
        EXPLORER: Code exploration with read-only access.
        RESEARCHER: Research with web search capabilities.
        CODER: Implementation with full file operations.
        REVIEWER: Code review with read-only access.
    """

    EXPLORER = "explorer"
    RESEARCHER = "researcher"
    CODER = "coder"
    REVIEWER = "reviewer"


class SubagentStatus(str, Enum):
    """Status of a subagent execution.

    Attributes:
        PENDING: Subagent created but not started.
        RUNNING: Subagent is currently executing.
        COMPLETED: Subagent finished successfully.
        FAILED: Subagent encountered an error.
        CANCELLED: Subagent was cancelled by parent.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# =============================================================================
# Context Management
# =============================================================================


@dataclass
class SubagentContext:
    """Isolated context for subagent execution.

    Provides a read-only snapshot of the parent context with:
    - Immutable conversation history snapshot
    - Independent working memory
    - Restricted tool permissions

    Attributes:
        agent_id: Unique identifier for this subagent.
        parent_id: ID of the parent agent (None for root).
        depth: Recursion depth (0 for root, max MAX_DEPTH).
        context_snapshot: Read-only snapshot of parent context.
        working_memory: Subagent's own working memory.
        allowed_tools: Set of tools this subagent can use.
        created_at: Timestamp when context was created.
    """

    agent_id: str
    parent_id: Optional[str]
    depth: int
    context_snapshot: str
    working_memory: Dict[str, Any] = field(default_factory=dict)
    allowed_tools: Set[str] = field(default_factory=set)
    created_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def create(
        cls,
        parent_context: str,
        parent_id: Optional[str],
        depth: int,
        allowed_tools: Set[str],
    ) -> "SubagentContext":
        """Create a new subagent context from parent.

        Args:
            parent_context: Context string from parent agent.
            parent_id: ID of the parent agent.
            depth: Current recursion depth.
            allowed_tools: Set of tools allowed for this subagent.

        Returns:
            New SubagentContext instance.

        Raises:
            ValueError: If depth exceeds MAX_DEPTH.
        """
        if depth > MAX_DEPTH:
            raise ValueError(
                f"Maximum subagent depth ({MAX_DEPTH}) exceeded. "
                "Cannot create nested subagent."
            )

        return cls(
            agent_id=f"subagent_{uuid.uuid4().hex[:8]}",
            parent_id=parent_id,
            depth=depth,
            context_snapshot=parent_context,
            allowed_tools=allowed_tools,
        )

    def set_working(self, key: str, value: Any) -> None:
        """Set a value in working memory.

        Args:
            key: Memory key.
            value: Value to store.
        """
        self.working_memory[key] = value

    def get_working(self, key: str, default: Any = None) -> Any:
        """Get a value from working memory.

        Args:
            key: Memory key.
            default: Default value if not found.

        Returns:
            Stored value or default.
        """
        return self.working_memory.get(key, default)

    def can_use_tool(self, tool_name: str) -> bool:
        """Check if this subagent can use a specific tool.

        Args:
            tool_name: Name of the tool to check.

        Returns:
            True if tool is allowed.
        """
        return tool_name in self.allowed_tools

    def get_full_context(self) -> str:
        """Get full context including snapshot and working memory.

        Returns:
            Formatted context string.
        """
        parts = []

        # Add context snapshot
        if self.context_snapshot:
            parts.append(f"## Parent Context (read-only)\n{self.context_snapshot}")

        # Add working memory
        if self.working_memory:
            working_lines = [f"- {k}: {v}" for k, v in self.working_memory.items()]
            parts.append(f"## Working Memory\n" + "\n".join(working_lines))

        # Add metadata
        parts.append(
            f"## Subagent Info\n"
            f"- ID: {self.agent_id}\n"
            f"- Depth: {self.depth}/{MAX_DEPTH}\n"
            f"- Allowed Tools: {', '.join(sorted(self.allowed_tools))}"
        )

        return "\n\n".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize context to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "agent_id": self.agent_id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "context_snapshot": self.context_snapshot,
            "working_memory": self.working_memory,
            "allowed_tools": list(self.allowed_tools),
            "created_at": self.created_at.isoformat(),
        }


# =============================================================================
# Subagent Result
# =============================================================================


@dataclass
class SubagentResult:
    """Result from subagent execution.

    Attributes:
        agent_id: ID of the subagent.
        status: Final execution status.
        summary: Concise summary of what was accomplished.
        result: Full result data (may be truncated for return).
        error: Error message if failed.
        turn_count: Number of turns executed.
        duration_ms: Total execution time in milliseconds.
        files_accessed: List of files read during execution.
        files_modified: List of files written/edited during execution.
    """

    agent_id: str
    status: SubagentStatus
    summary: str
    result: Optional[Any] = None
    error: Optional[str] = None
    turn_count: int = 0
    duration_ms: int = 0
    files_accessed: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize result to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "agent_id": self.agent_id,
            "status": self.status.value,
            "summary": self.summary,
            "result": self.result,
            "error": self.error,
            "turn_count": self.turn_count,
            "duration_ms": self.duration_ms,
            "files_accessed": self.files_accessed,
            "files_modified": self.files_modified,
        }


# =============================================================================
# Subagent Executor
# =============================================================================


class SubagentExecutor:
    """Manages subagent lifecycle and execution.

    Features:
    - Foreground (blocking) and background (async) execution
    - Concurrent subagent limiting
    - Recursive depth protection
    - Tool permission enforcement

    Attributes:
        parent_tools: Set of tools available to parent agent.
        workspace: Workspace directory for file operations.
        current_depth: Current recursion depth of parent.
        running_subagents: Dictionary of currently running subagents.
        completed_subagents: Dictionary of completed subagent results.
    """

    # Class-level semaphore for concurrent subagent limiting
    _semaphore: Optional[asyncio.Semaphore] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(
        self,
        parent_tools: Optional[Set[str]] = None,
        workspace: Optional[Path] = None,
        current_depth: int = 0,
        tool_registry: Optional["ToolRegistry"] = None,
        llm_client: Optional[Any] = None,
        registry: Optional["SubagentRegistry"] = None,
    ):
        """Initialize subagent executor.

        Args:
            parent_tools: Set of tools available to parent.
            workspace: Workspace directory for sandbox.
            current_depth: Current recursion depth.
            tool_registry: Tool registry for tool execution.
            llm_client: LLM client for subagent completions.
            registry: SubagentRegistry for loading custom subagent configurations.
                     If provided, tool permissions are read from registry first.
        """
        self.parent_tools = parent_tools or set()
        self.workspace = workspace or Path.cwd()
        self.current_depth = current_depth
        self.tool_registry = tool_registry
        self.llm_client = llm_client
        self._registry = registry

        # Track running and completed subagents
        self.running_subagents: Dict[str, asyncio.Task[SubagentResult]] = {}
        self.completed_subagents: Dict[str, SubagentResult] = {}

    @classmethod
    async def _get_semaphore(cls) -> asyncio.Semaphore:
        """Get or create the class-level semaphore.

        Returns:
            Semaphore for concurrent limiting.
        """
        async with cls._lock:
            if cls._semaphore is None:
                cls._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
            return cls._semaphore

    def _validate_tools(
        self,
        requested_tools: Set[str],
        subagent_type: str,
    ) -> Set[str]:
        """Validate and filter requested tools.

        Ensures requested tools are:
        1. A subset of parent's tools
        2. Within the subagent type's allowed tools

        Tool permissions are resolved in this order:
        1. From registry if available and subagent type is registered
        2. From hardcoded SUBAGENT_TOOL_PERMISSIONS as fallback

        Args:
            requested_tools: Tools requested for subagent.
            subagent_type: Type of subagent being created.

        Returns:
            Validated set of allowed tools.
        """
        # Get type-specific allowed tools - prefer registry over hardcoded
        type_allowed: Set[str]
        if self._registry:
            config = self._registry.get(subagent_type)
            if config and config.allowed_tools:
                type_allowed = set(config.allowed_tools)
            else:
                type_allowed = SUBAGENT_TOOL_PERMISSIONS.get(
                    subagent_type, SUBAGENT_TOOL_PERMISSIONS["explorer"]
                )
        else:
            type_allowed = SUBAGENT_TOOL_PERMISSIONS.get(
                subagent_type, SUBAGENT_TOOL_PERMISSIONS["explorer"]
            )

        # Intersect with parent tools and type-specific allowlist
        if self.parent_tools:
            allowed = requested_tools & self.parent_tools & type_allowed
        else:
            allowed = requested_tools & type_allowed

        return allowed

    def _resolve_llm_client(
        self,
        subagent_type: str,
        model_override: Optional[str] = None,
    ) -> Any:
        """Resolve LLM client for subagent.

        Determines which LLM client to use for the subagent based on:
        1. Explicit model override (highest priority)
        2. Unified config from ~/.nimbus/config.json agents section
        3. Parent's llm_client (fallback)

        Args:
            subagent_type: Type of subagent (e.g., "coder", "explorer").
            model_override: Explicit model override from spawn call.

        Returns:
            LLM client instance for the subagent.
        """
        from nimbus.core.agents_config import (
            get_agent_model,
            create_llm_client_for_agent,
        )
        from nimbus.core.logging import get_logger

        logger = get_logger("subagent")

        # Priority 1: Explicit model override
        if model_override:
            from nimbus.core.agents_config import detect_provider_from_model
            from nimbus.llm import create_llm_client

            provider = detect_provider_from_model(model_override)
            logger.info(f"Using model override for {subagent_type}: provider={provider}, model={model_override}")
            try:
                return create_llm_client(provider=provider, model=model_override)
            except Exception as e:
                logger.warning(f"Failed to create client for model override {model_override}: {e}")
                return self.llm_client

        # Priority 2: Unified config from config.json
        model = get_agent_model(subagent_type)
        if model:
            try:
                return create_llm_client_for_agent(subagent_type, fallback_client=self.llm_client)
            except Exception as e:
                logger.warning(f"Failed to create client for {subagent_type}: {e}, using parent client")
                return self.llm_client

        # Priority 3: Use parent's client
        logger.debug(f"No model configured for {subagent_type}, using parent's client")
        return self.llm_client

    def _create_context(
        self,
        parent_context: str,
        subagent_type: str,
        allowed_tools: Optional[Set[str]] = None,
    ) -> SubagentContext:
        """Create isolated context for subagent.

        Args:
            parent_context: Context string from parent.
            subagent_type: Type of subagent.
            allowed_tools: Optional explicit tool list.

        Returns:
            New SubagentContext.

        Raises:
            ValueError: If depth would exceed maximum.
        """
        new_depth = self.current_depth + 1

        # Determine tools
        if allowed_tools:
            tools = self._validate_tools(allowed_tools, subagent_type)
        else:
            tools = SUBAGENT_TOOL_PERMISSIONS.get(
                subagent_type, SUBAGENT_TOOL_PERMISSIONS["explorer"]
            )
            if self.parent_tools:
                tools = tools & self.parent_tools

        return SubagentContext.create(
            parent_context=parent_context,
            parent_id=None,  # Would be set by parent agent
            depth=new_depth,
            allowed_tools=tools,
        )

    async def spawn(
        self,
        prompt: str,
        subagent_type: str,
        description: str,
        run_in_background: bool = False,
        model: Optional[str] = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        allowed_tools: Optional[Set[str]] = None,
        parent_context: str = "",
    ) -> Dict[str, Any]:
        """Spawn a new subagent.

        Args:
            prompt: Task description for the subagent.
            subagent_type: Type of subagent (explorer, researcher, coder, reviewer).
            description: Short description (3-5 words) for status display.
            run_in_background: If True, return immediately with agent_id.
            model: Optional model override.
            max_turns: Maximum conversation turns.
            allowed_tools: Optional explicit tool permissions.
            parent_context: Context snapshot from parent.

        Returns:
            Dictionary with:
            - For foreground: Full SubagentResult as dict
            - For background: {"agent_id": str, "status": "running"}

        Raises:
            ValueError: If subagent_type is invalid or depth exceeded.
        """
        # Validate subagent type
        if subagent_type not in [t.value for t in SubagentType]:
            raise ValueError(
                f"Invalid subagent_type: {subagent_type}. "
                f"Must be one of: {[t.value for t in SubagentType]}"
            )

        # Check depth limit
        if self.current_depth >= MAX_DEPTH:
            raise ValueError(
                f"Maximum subagent depth ({MAX_DEPTH}) reached. "
                "Cannot spawn nested subagent."
            )

        # Create context
        context = self._create_context(
            parent_context=parent_context,
            subagent_type=subagent_type,
            allowed_tools=allowed_tools,
        )

        # For background execution
        if run_in_background:
            task = asyncio.create_task(
                self._execute_subagent(
                    context=context,
                    prompt=prompt,
                    description=description,
                    subagent_type=subagent_type,
                    model=model,
                    max_turns=max_turns,
                )
            )
            self.running_subagents[context.agent_id] = task

            # Set up completion callback
            task.add_done_callback(
                lambda t: self._on_subagent_complete(context.agent_id, t)
            )

            return {
                "agent_id": context.agent_id,
                "status": "running",
                "message": f"Subagent '{description}' started in background.",
            }

        # For foreground execution - block and wait
        result = await self._execute_subagent(
            context=context,
            prompt=prompt,
            description=description,
            subagent_type=subagent_type,
            model=model,
            max_turns=max_turns,
        )

        return result.to_dict()

    def _on_subagent_complete(
        self,
        agent_id: str,
        task: asyncio.Task[SubagentResult],
    ) -> None:
        """Handle subagent completion.

        Args:
            agent_id: ID of the completed subagent.
            task: The completed asyncio task.
        """
        # Remove from running
        self.running_subagents.pop(agent_id, None)

        # Store result
        try:
            result = task.result()
            self.completed_subagents[agent_id] = result
        except asyncio.CancelledError:
            self.completed_subagents[agent_id] = SubagentResult(
                agent_id=agent_id,
                status=SubagentStatus.CANCELLED,
                summary="Subagent was cancelled.",
            )
        except Exception as e:
            self.completed_subagents[agent_id] = SubagentResult(
                agent_id=agent_id,
                status=SubagentStatus.FAILED,
                summary=f"Subagent failed with error.",
                error=str(e),
            )

    async def _execute_subagent(
        self,
        context: SubagentContext,
        prompt: str,
        description: str,
        subagent_type: str = "explorer",
        model: Optional[str] = None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> SubagentResult:
        """Execute subagent task using a child CodeAgent.

        Creates a child CodeAgent with restricted tools and executes the task.
        This allows the subagent to iteratively read files and make edits.

        Args:
            context: Subagent context with permissions.
            prompt: Task prompt.
            description: Short description.
            subagent_type: Type of subagent (for loading config).
            model: Optional model override.
            max_turns: Maximum turns.

        Returns:
            SubagentResult with execution outcome.
        """
        start_time = datetime.now()
        files_accessed: List[str] = []
        files_modified: List[str] = []

        # Acquire semaphore for concurrent limiting
        semaphore = await self._get_semaphore()

        async with semaphore:
            try:
                # Create child CodeAgent with restricted tools
                # Use lazy import to avoid circular dependency
                from nimbus.core.agent import CodeAgent
                from nimbus.tools import ToolRegistry

                # Create restricted tool registry for subagent
                child_registry = ToolRegistry()
                if self.tool_registry:
                    for tool_name in context.allowed_tools:
                        result = self.tool_registry.get(tool_name)
                        if result:
                            tool_def, tool_func = result
                            child_registry.register(tool_def, tool_func)

                # Determine which llm_client to use
                # Priority: model parameter > subagent YAML config > parent's llm_client
                child_llm_client = self._resolve_llm_client(subagent_type, model)

                # Create child agent without router mode
                # load_yaml_config=False prevents subagent from using router mode
                # which would cause infinite recursion (router -> coder -> router -> coder...)
                child_agent = CodeAgent(
                    llm_client=child_llm_client,
                    tool_registry=child_registry,
                    workspace=self.workspace,
                    memory_type="simple",  # Simple memory for subagent
                    planner_type="dag",
                    enable_logging=False,  # Quiet subagent
                    load_yaml_config=False,  # Don't use router mode from core.yaml
                )

                # Build enhanced prompt with context
                enhanced_prompt = f"""## Task
{prompt}

## Context
{context.get_full_context()}

## Instructions
1. Use the available tools to complete the task
2. Read files first before editing to understand the content
3. Make appropriate modifications using Edit or Write tools
4. Return a summary of what was done
"""

                # Execute task with child agent
                response = await child_agent.run(enhanced_prompt)

                # Extract results
                accumulated_result = response.text if response.text else "Task completed"
                turn_count = 1  # TODO: track actual turns

                # Track file access from tool results
                for node in child_agent.runtime.dag.nodes.values() if child_agent.runtime.dag else []:
                    if node.skill == "Read":
                        file_path = node.params.get("file_path", "")
                        if file_path and file_path not in files_accessed:
                            files_accessed.append(file_path)
                    elif node.skill in ("Write", "Edit"):
                        file_path = node.params.get("file_path", "")
                        if file_path and file_path not in files_modified:
                            files_modified.append(file_path)

                # Calculate duration
                end_time = datetime.now()
                duration_ms = int((end_time - start_time).total_seconds() * 1000)

                # Generate summary (truncate if too long)
                summary = self._generate_summary(accumulated_result, description)

                return SubagentResult(
                    agent_id=context.agent_id,
                    status=SubagentStatus.COMPLETED,
                    summary=summary,
                    result=accumulated_result,
                    turn_count=turn_count,
                    duration_ms=duration_ms,
                    files_accessed=files_accessed,
                    files_modified=files_modified,
                )

            except asyncio.CancelledError:
                return SubagentResult(
                    agent_id=context.agent_id,
                    status=SubagentStatus.CANCELLED,
                    summary="Subagent was cancelled.",
                )
            except Exception as e:
                return SubagentResult(
                    agent_id=context.agent_id,
                    status=SubagentStatus.FAILED,
                    summary=f"Subagent failed: {str(e)[:100]}",
                    error=str(e),
                )

    def _build_system_prompt(
        self,
        context: SubagentContext,
        description: str,
    ) -> str:
        """Build system prompt for subagent.

        Args:
            context: Subagent context.
            description: Task description.

        Returns:
            System prompt string.
        """
        tools_str = ", ".join(sorted(context.allowed_tools))

        return f"""You are a subagent with the following constraints:

## Identity
- Agent ID: {context.agent_id}
- Type: {description}
- Depth: {context.depth}/{MAX_DEPTH}

## Capabilities
You have access to these tools only: {tools_str}

## Rules
1. Stay focused on your assigned task
2. Do not attempt to use tools not in your allowlist
3. Return a concise summary when complete
4. If you cannot complete the task, explain why

## Output Format
Provide your findings in a clear, structured format.
End with a brief summary of what was accomplished."""

    async def _execute_tool_calls(
        self,
        response: str,
        context: SubagentContext,
        files_accessed: List[str],
        files_modified: List[str],
    ) -> str:
        """Execute tool calls from LLM response.

        Args:
            response: LLM response containing tool calls.
            context: Subagent context for permission checking.
            files_accessed: List to track read files.
            files_modified: List to track modified files.

        Returns:
            Tool execution result.
        """
        # Simplified tool call parsing - real impl would use proper parsing
        # This is a placeholder for the actual tool execution logic
        return "[Tool execution placeholder - integrate with actual tool registry]"

    def _generate_summary(self, result: str, description: str) -> str:
        """Generate a concise summary from result.

        Args:
            result: Full result string.
            description: Task description.

        Returns:
            Summary string (max 500 chars).
        """
        if len(result) <= 500:
            return result

        # Truncate and add indicator
        return result[:450] + f"... [truncated, {len(result)} chars total]"

    async def get_result(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get result of a completed or running subagent.

        Args:
            agent_id: ID of the subagent.

        Returns:
            Result dict if available, None if not found.
        """
        # Check completed
        if agent_id in self.completed_subagents:
            return self.completed_subagents[agent_id].to_dict()

        # Check running
        if agent_id in self.running_subagents:
            return {
                "agent_id": agent_id,
                "status": "running",
                "message": "Subagent is still executing.",
            }

        return None

    async def cancel(self, agent_id: str) -> bool:
        """Cancel a running subagent.

        Args:
            agent_id: ID of the subagent to cancel.

        Returns:
            True if cancelled, False if not found or already complete.
        """
        task = self.running_subagents.get(agent_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def list_running(self) -> List[str]:
        """List IDs of all running subagents.

        Returns:
            List of agent IDs.
        """
        return list(self.running_subagents.keys())

    def list_completed(self) -> List[str]:
        """List IDs of all completed subagents.

        Returns:
            List of agent IDs.
        """
        return list(self.completed_subagents.keys())


# =============================================================================
# Tool Definition
# =============================================================================

# Module-level executor instance (initialized lazily)
_executor: Optional[SubagentExecutor] = None


def get_executor(
    workspace: Optional[Path] = None,
    tool_registry: Optional["ToolRegistry"] = None,
    llm_client: Optional[Any] = None,
    parent_tools: Optional[Set[str]] = None,
    registry: Optional["SubagentRegistry"] = None,
) -> SubagentExecutor:
    """Get or create the module-level executor.

    Note: If any parameters are provided, they will update the executor.
    This ensures the executor uses the latest configuration.

    Args:
        workspace: Workspace directory.
        tool_registry: Tool registry.
        llm_client: LLM client.
        parent_tools: Parent's allowed tools.
        registry: SubagentRegistry for custom subagent configurations.

    Returns:
        SubagentExecutor instance.
    """
    global _executor
    if _executor is None:
        _executor = SubagentExecutor(
            workspace=workspace,
            tool_registry=tool_registry,
            llm_client=llm_client,
            parent_tools=parent_tools,
            registry=registry,
        )
    else:
        # Update executor with new parameters if provided
        if workspace is not None:
            _executor.workspace = workspace
        if tool_registry is not None:
            _executor.tool_registry = tool_registry
        if llm_client is not None:
            _executor.llm_client = llm_client
        if parent_tools is not None:
            _executor.parent_tools = parent_tools
        if registry is not None:
            _executor._registry = registry
    return _executor


def reset_executor() -> None:
    """Reset the module-level executor.

    Useful for testing and cleanup.
    """
    global _executor
    _executor = None


@tool(
    name="Subagent",
    description=(
        "Spawn a child agent to handle a subtask. The subagent runs in an isolated "
        "context with restricted tool permissions. Use for delegating exploration, "
        "research, implementation, or review tasks."
    ),
    parameters=[
        ToolParameter(
            "prompt",
            "string",
            "Task description for the subagent. Be specific about what needs to be done.",
            required=True,
        ),
        ToolParameter(
            "subagent_type",
            "string",
            (
                "Type of subagent: 'explorer' (code exploration), 'researcher' (web research), "
                "'coder' (implementation), 'reviewer' (code review)"
            ),
            required=True,
            enum=["explorer", "researcher", "coder", "reviewer"],
        ),
        ToolParameter(
            "description",
            "string",
            "Short description (3-5 words) for status display",
            required=True,
        ),
        ToolParameter(
            "run_in_background",
            "boolean",
            "If true, run asynchronously and return agent_id for later retrieval",
            required=False,
            default=False,
        ),
        ToolParameter(
            "model",
            "string",
            "Optional model override (e.g., 'claude-3-opus', 'gpt-4')",
            required=False,
        ),
        ToolParameter(
            "max_turns",
            "integer",
            f"Maximum conversation turns. Defaults to {DEFAULT_MAX_TURNS}.",
            required=False,
            default=DEFAULT_MAX_TURNS,
        ),
        ToolParameter(
            "allowed_tools",
            "array",
            (
                "Optional explicit list of allowed tools (must be subset of parent's tools "
                "and subagent type's default tools)"
            ),
            required=False,
            items={"type": "string"},
        ),
    ],
    dangerous=False,
)
async def subagent_task(
    prompt: str,
    subagent_type: str,
    description: str,
    run_in_background: bool = False,
    model: Optional[str] = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    allowed_tools: Optional[List[str]] = None,
    workspace: Optional[Path] = None,
    parent_context: str = "",
    parent_tools: Optional[Set[str]] = None,
    tool_registry: Optional["ToolRegistry"] = None,
    llm_client: Optional[Any] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Spawn a subagent to handle a subtask.

    Creates an isolated child agent with restricted permissions to handle
    specific tasks like exploration, research, implementation, or review.

    Features:
        - Isolated context (read-only snapshot from parent)
        - Tool permission restrictions (subset of parent)
        - Recursive depth limiting (max 3 levels)
        - Concurrent execution limiting (max 5 background subagents)
        - Foreground (blocking) and background execution modes

    Args:
        prompt: Task description for the subagent.
        subagent_type: Type of subagent (explorer, researcher, coder, reviewer).
        description: Short description for status display.
        run_in_background: If True, return immediately with agent_id.
        model: Optional model override.
        max_turns: Maximum conversation turns.
        allowed_tools: Optional explicit tool permissions.
        workspace: Workspace directory for sandbox.
        parent_context: Context snapshot from parent agent.
        parent_tools: Set of tools available to parent.
        tool_registry: Tool registry for tool execution.
        llm_client: LLM client for subagent completions.

    Returns:
        For foreground execution:
            {
                "agent_id": str,
                "status": "completed" | "failed",
                "summary": str,
                "result": Any,
                "error": Optional[str],
                "turn_count": int,
                "duration_ms": int,
                "files_accessed": List[str],
                "files_modified": List[str],
            }

        For background execution:
            {
                "agent_id": str,
                "status": "running",
                "message": str,
            }

    Raises:
        ValueError: If subagent_type is invalid or max depth exceeded.

    Example:
        >>> # Foreground exploration
        >>> result = await subagent_task(
        ...     prompt="Find all Python files in src/nimbus/tools",
        ...     subagent_type="explorer",
        ...     description="Explore tools",
        ... )
        >>> print(result["summary"])

        >>> # Background research
        >>> result = await subagent_task(
        ...     prompt="Research best practices for async Python",
        ...     subagent_type="researcher",
        ...     description="Research async",
        ...     run_in_background=True,
        ... )
        >>> agent_id = result["agent_id"]
        >>> # Later: retrieve result
    """
    # Get or create executor
    executor = get_executor(
        workspace=workspace,
        tool_registry=tool_registry,
        llm_client=llm_client,
        parent_tools=parent_tools,
    )

    # Convert allowed_tools list to set if provided
    tools_set: Optional[Set[str]] = None
    if allowed_tools:
        tools_set = set(allowed_tools)

    # Spawn subagent
    return await executor.spawn(
        prompt=prompt,
        subagent_type=subagent_type,
        description=description,
        run_in_background=run_in_background,
        model=model,
        max_turns=max_turns,
        allowed_tools=tools_set,
        parent_context=parent_context,
    )


@tool(
    name="SubagentResult",
    description="Retrieve the result of a background subagent by its agent_id.",
    parameters=[
        ToolParameter(
            "agent_id",
            "string",
            "ID of the subagent to retrieve results for",
            required=True,
        ),
    ],
    dangerous=False,
)
async def get_subagent_result(
    agent_id: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieve the result of a background subagent.

    Args:
        agent_id: ID of the subagent.

    Returns:
        Result dictionary or status if still running.

    Raises:
        ValueError: If agent_id not found.
    """
    executor = get_executor()
    result = await executor.get_result(agent_id)

    if result is None:
        raise ValueError(f"Subagent with ID '{agent_id}' not found.")

    return result


@tool(
    name="SubagentCancel",
    description="Cancel a running background subagent.",
    parameters=[
        ToolParameter(
            "agent_id",
            "string",
            "ID of the subagent to cancel",
            required=True,
        ),
    ],
    dangerous=False,
)
async def cancel_subagent(
    agent_id: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Cancel a running background subagent.

    Args:
        agent_id: ID of the subagent to cancel.

    Returns:
        Status dictionary.
    """
    executor = get_executor()
    success = await executor.cancel(agent_id)

    if success:
        return {
            "agent_id": agent_id,
            "status": "cancelled",
            "message": "Subagent cancellation requested.",
        }
    else:
        return {
            "agent_id": agent_id,
            "status": "not_found",
            "message": "Subagent not found or already completed.",
        }


@tool(
    name="SubagentList",
    description="List all running and completed subagents.",
    parameters=[],
    dangerous=False,
)
async def list_subagents(**kwargs: Any) -> Dict[str, Any]:
    """List all running and completed subagents.

    Returns:
        Dictionary with running and completed agent lists.
    """
    executor = get_executor()

    return {
        "running": executor.list_running(),
        "completed": executor.list_completed(),
        "running_count": len(executor.list_running()),
        "completed_count": len(executor.list_completed()),
    }
