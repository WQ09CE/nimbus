"""Context Stack for focused LLM calls.

Architecture Layer: 1 (Agent OS - Kernel)
Von Neumann Role: Context Manager (CPU Context Switching)

In the Agent OS architecture, ContextStack serves as the context
switching mechanism, similar to how CPUs save/restore register state:
- ContextFrame -> CPU register snapshot (IP, SP, flags)
- Push/pop operations -> Context switch on interrupt/syscall
- Frame inheritance -> Process context inheritance from parent
- Token budgets -> Register file size limits

Each phase (planner, tool, subagent) gets an isolated "register set",
preventing interference and enabling efficient execution.

This module provides:
- ContextFrame: Single context stack frame with isolated data
- ContextStack: Stack manager for push/pop operations with async context manager
- FrameFactory: Factory for common frame types (planner, tool, synthesize)

The context stack enables different phases (Planner, Tool execution, Subagent)
to get focused, phase-specific context views instead of flat full context.

Example:
    >>> memory = TieredMemoryManager()
    >>> context = ContextStack(memory=memory)
    >>>
    >>> # Planner phase - minimal context (500 tokens)
    >>> planner_frame = FrameFactory.planner(goal="Read main.py", available_skills={"Read"})
    >>> async with context.frame(planner_frame):
    ...     view = context.get_view()  # ~500 tokens
    ...     dag = await planner.plan(view)
    >>>
    >>> # Tool phase - workspace focused (1000 tokens)
    >>> tool_frame = FrameFactory.tool_execution("Read", {"path": "main.py"}, workspace)
    >>> async with context.frame(tool_frame):
    ...     view = context.get_view()  # ~1000 tokens
    ...     result = await execute_tool(view)
"""

__layer__ = 1  # Agent OS Layer
__role__ = "Context_Manager"  # CPU context switching

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from ..utils.tokens import estimate_tokens, truncate_to_tokens

# =============================================================================
# Exceptions
# =============================================================================


class ContextStackOverflow(Exception):
    """Raised when stack depth exceeds maximum allowed."""

    pass


class ContextStackUnderflow(Exception):
    """Raised when attempting to pop the root frame."""

    pass


# =============================================================================
# ContextFrame
# =============================================================================


@dataclass
class ContextFrame:
    """Single context stack frame.

    Similar to CPU call stack frames, each frame contains context information
    specific to a particular call phase.

    Attributes:
        id: Unique frame identifier.
        name: Frame name (e.g., "planner", "tool:Read", "subagent:eye").
        purpose: Description of frame purpose.
        system_prompt: System prompt for this phase.
        tools: List of tools available in this phase.
        max_tokens: Maximum token budget for this frame.
        data: Custom data dictionary for the frame.
        parent_id: Parent frame ID (for inheritance chain).
        inherit_from: List of fields to inherit from parent frame.
        created_at: Frame creation timestamp.
    """

    id: str
    name: str
    purpose: str = ""
    system_prompt: str = ""
    tools: List[str] = field(default_factory=list)
    max_tokens: int = 2000
    data: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None
    inherit_from: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def get(self, key: str, default: Any = None) -> Any:
        """Get value from frame data.

        Args:
            key: Data key to retrieve.
            default: Default value if key not found.

        Returns:
            Value from data dict or default.
        """
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set value in frame data.

        Args:
            key: Data key to set.
            value: Value to store.
        """
        self.data[key] = value

    def derive(
        self,
        name: str,
        override: Optional[Dict[str, Any]] = None,
        inherit: Optional[List[str]] = None,
    ) -> "ContextFrame":
        """Derive a child frame from this frame.

        Creates a new frame that inherits specified fields from the parent.

        Args:
            name: Name for the child frame.
            override: Dict of fields to override in child frame.
                     Supports: purpose, system_prompt, tools, max_tokens, data.
            inherit: List of data keys to inherit from parent frame.

        Returns:
            New ContextFrame instance with parent_id set to this frame's id.

        Example:
            >>> parent = ContextFrame(id="root", name="agent", data={"workspace": "/app"})
            >>> child = parent.derive("subagent", inherit=["workspace"])
            >>> child.get("workspace")  # "/app"
        """
        override = override or {}
        inherit = inherit or []

        # Inherit specified data fields from parent
        inherited_data = {k: v for k, v in self.data.items() if k in inherit}

        # Merge inherited data with override data
        child_data = {**inherited_data, **override.get("data", {})}

        return ContextFrame(
            id=f"{self.id}:{uuid.uuid4().hex[:6]}",
            name=name,
            purpose=override.get("purpose", ""),
            system_prompt=override.get("system_prompt", self.system_prompt),
            tools=override.get("tools", list(self.tools)),
            max_tokens=override.get("max_tokens", self.max_tokens),
            data=child_data,
            parent_id=self.id,
            inherit_from=inherit,
        )


# =============================================================================
# ContextStack
# =============================================================================


class ContextStack:
    """Context stack manager.

    Manages ContextFrame stack structure with push/pop operations
    and async context manager support.

    Features:
    - Stack-based frame management (push/pop)
    - Async context manager (async with syntax)
    - Integration with TieredMemory
    - Frame inheritance and derivation
    - View generation (context string for current frame)

    Example:
        >>> context = ContextStack(memory=memory)
        >>> async with context.frame(planner_frame):
        ...     view = context.get_view()  # Get focused context
        ...     # Do work with focused context
    """

    def __init__(
        self,
        memory: Optional[Any] = None,
        max_depth: int = 10,
    ):
        """Initialize context stack.

        Args:
            memory: Associated memory manager (SimpleMemory or TieredMemoryManager).
                   Provides historical context for view generation.
            max_depth: Maximum stack depth (default 10).
        """
        self._stack: List[ContextFrame] = []
        self._memory = memory
        self._max_depth = max_depth

        # Create and push root frame
        self._root = self._create_root_frame()
        self._stack.append(self._root)

    def _create_root_frame(self) -> ContextFrame:
        """Create the root frame.

        Returns:
            Root ContextFrame with default settings.
        """
        return ContextFrame(
            id="root",
            name="agent",
            purpose="Agent main execution context",
            max_tokens=8000,  # Root frame has larger budget
        )

    @property
    def current(self) -> ContextFrame:
        """Get the current (top) frame.

        Returns:
            The frame at the top of the stack.
        """
        return self._stack[-1]

    @property
    def depth(self) -> int:
        """Get current stack depth.

        Returns:
            Number of frames in the stack (including root).
        """
        return len(self._stack)

    @property
    def root(self) -> ContextFrame:
        """Get the root frame.

        Returns:
            The root frame at the bottom of the stack.
        """
        return self._root

    def push(self, frame: ContextFrame) -> None:
        """Push a new frame onto the stack.

        Args:
            frame: Frame to push.

        Raises:
            ContextStackOverflow: If max depth would be exceeded.
        """
        if self.depth >= self._max_depth:
            raise ContextStackOverflow(
                f"Stack overflow: max depth {self._max_depth} exceeded"
            )

        # Set parent_id if not already set
        if frame.parent_id is None:
            frame.parent_id = self.current.id

        self._stack.append(frame)

    def pop(self) -> ContextFrame:
        """Pop the top frame from the stack.

        Returns:
            The popped frame.

        Raises:
            ContextStackUnderflow: If attempting to pop root frame.
        """
        if self.depth <= 1:
            raise ContextStackUnderflow("Cannot pop root frame")

        return self._stack.pop()

    @asynccontextmanager
    async def frame(self, frame: ContextFrame) -> AsyncIterator[ContextFrame]:
        """Async context manager for frame lifecycle.

        Pushes frame on entry, pops on exit (even on exception).

        Args:
            frame: Frame to push.

        Yields:
            The pushed frame.

        Example:
            >>> async with context.frame(planner_frame) as f:
            ...     view = context.get_view()
            ...     # Frame is automatically popped on exit
        """
        self.push(frame)
        try:
            yield frame
        finally:
            self.pop()

    def get_view(self, include_memory: bool = True) -> str:
        """Generate context view for current frame.

        Assembles a context string based on the current frame's configuration.

        Args:
            include_memory: Whether to include context from Memory.

        Returns:
            Formatted context string, truncated to frame's max_tokens.
        """
        parts = []
        frame = self.current

        # 1. System prompt
        if frame.system_prompt:
            parts.append(frame.system_prompt)

        # 2. Frame-specific data
        if frame.data:
            frame_data = "\n".join(
                f"- {k}: {v}"
                for k, v in frame.data.items()
                if not k.startswith("_")  # Skip private data
            )
            if frame_data:
                parts.append(f"## Context\n{frame_data}")

        # 3. Optional: Context from Memory
        if include_memory and self._memory:
            memory_context = self._get_memory_context(frame)
            if memory_context:
                parts.append(memory_context)

        # 4. Available tools
        if frame.tools:
            tools_list = ", ".join(frame.tools)
            parts.append(f"## Available Tools\n{tools_list}")

        # Assemble and truncate
        full_context = "\n\n".join(parts)
        return self._truncate_to_tokens(full_context, frame.max_tokens)

    def _get_memory_context(self, frame: ContextFrame) -> str:
        """Get context from Memory appropriate for frame type.

        Filters and formats Memory content based on frame type.

        Args:
            frame: Current frame to get context for.

        Returns:
            Formatted context string from memory.
        """
        if self._memory is None:
            return ""

        # Determine frame type from name prefix
        frame_type = frame.name.split(":")[0]

        # Import TieredMemoryManager for type checking
        from .memory import TieredMemoryManager

        if frame_type == "planner":
            # Planner only needs brief history (summaries)
            if isinstance(self._memory, TieredMemoryManager):
                summaries = self._memory.episodic_summaries[-2:]
                if summaries:
                    return "## Recent Context\n" + "\n".join(summaries)
            return ""

        elif frame_type == "tool":
            # Tool needs workspace
            if isinstance(self._memory, TieredMemoryManager):
                workspace_item = self._memory.memory_get("workspace")
                if workspace_item:
                    return f"## Workspace\n{workspace_item}"
            return ""

        elif frame_type == "synthesize":
            # Synthesize needs full conversation history
            result: str = self._memory.get_context()
            return result

        else:
            # Default: get standard context
            default_result: str = self._memory.get_context()
            return default_result

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate text to fit within token limit.

        Args:
            text: Text to truncate.
            max_tokens: Maximum allowed tokens.

        Returns:
            Truncated text.
        """
        current_tokens = estimate_tokens(text)
        if current_tokens <= max_tokens:
            return text

        return truncate_to_tokens(text, max_tokens)

    def create_subagent_frame(
        self,
        subagent_type: str,
        task_prompt: str,
        allowed_tools: List[str],
    ) -> ContextFrame:
        """Create an isolated frame for subagent execution.

        Derives a restricted child frame from current frame for subagent use.

        Args:
            subagent_type: Subagent type (eye, body, mind, tongue, nose, ear).
            task_prompt: Task description for the subagent.
            allowed_tools: List of tools the subagent can use.

        Returns:
            ContextFrame configured for the subagent type.
        """
        # Data fields to inherit from parent
        inherit_keys = ["workspace", "session_id"]

        # Subagent type specific configurations
        subagent_configs = {
            "eye": {
                "purpose": "Code exploration and discovery",
                "max_tokens": 1500,
                "system_prompt": "You are an exploration agent. Focus on reading and understanding code.",
            },
            "body": {
                "purpose": "Code implementation",
                "max_tokens": 2000,
                "system_prompt": "You are a coding agent. Implement the requested changes.",
            },
            "mind": {
                "purpose": "Architecture design",
                "max_tokens": 2000,
                "system_prompt": "You are a design agent. Think through architecture decisions.",
            },
            "tongue": {
                "purpose": "Testing and verification",
                "max_tokens": 1500,
                "system_prompt": "You are a testing agent. Verify code correctness.",
            },
            "nose": {
                "purpose": "Code review",
                "max_tokens": 1500,
                "system_prompt": "You are a review agent. Analyze code quality and issues.",
            },
            "ear": {
                "purpose": "Requirements analysis",
                "max_tokens": 1000,
                "system_prompt": "You are a requirements agent. Clarify user needs.",
            },
        }

        config = subagent_configs.get(subagent_type, subagent_configs["eye"])

        return self.current.derive(
            name=f"subagent:{subagent_type}",
            override={
                "purpose": config["purpose"],
                "system_prompt": config["system_prompt"],
                "tools": allowed_tools,
                "max_tokens": config["max_tokens"],
                "data": {"task": task_prompt},
            },
            inherit=inherit_keys,
        )

    def get_stack_trace(self) -> List[Dict[str, Any]]:
        """Get stack trace for debugging.

        Returns:
            List of dicts with frame info (id, name, depth, etc.).
        """
        return [
            {
                "id": frame.id,
                "name": frame.name,
                "depth": i,
                "max_tokens": frame.max_tokens,
                "tools_count": len(frame.tools),
                "data_keys": list(frame.data.keys()),
                "parent_id": frame.parent_id,
            }
            for i, frame in enumerate(self._stack)
        ]


# =============================================================================
# FrameFactory
# =============================================================================


class FrameFactory:
    """Factory for creating common frame types.

    Provides pre-configured frames for different execution phases.
    """

    @staticmethod
    def planner(
        goal: str,
        available_skills: Set[str],
    ) -> ContextFrame:
        """Create a Planner phase frame.

        Minimal context frame for task planning and DAG generation.

        Args:
            goal: User goal to plan for.
            available_skills: Set of available skill names.

        Returns:
            ContextFrame with 500 token budget.
        """
        return ContextFrame(
            id=f"planner:{uuid.uuid4().hex[:6]}",
            name="planner",
            purpose="Task planning and DAG generation",
            system_prompt="""You are a task planner. Analyze user goals and generate execution plans.
Focus only on task decomposition, do not execute operations.""",
            tools=list(available_skills),
            max_tokens=500,  # Planner frame is minimal
            data={"goal": goal},
        )

    @staticmethod
    def tool_execution(
        tool_name: str,
        params: Dict[str, Any],
        workspace: Path,
    ) -> ContextFrame:
        """Create a Tool execution frame.

        Workspace-focused frame for tool execution.

        Args:
            tool_name: Name of the tool to execute.
            params: Tool parameters.
            workspace: Current workspace path.

        Returns:
            ContextFrame with 1000 token budget.
        """
        return ContextFrame(
            id=f"tool:{tool_name}:{uuid.uuid4().hex[:6]}",
            name=f"tool:{tool_name}",
            purpose=f"Execute {tool_name} tool",
            tools=[tool_name],
            max_tokens=1000,
            data={
                "workspace": str(workspace),
                "params": params,
            },
        )

    @staticmethod
    def synthesize(
        message: str,
        upstream_results: Dict[str, Any],
    ) -> ContextFrame:
        """Create a Synthesize phase frame.

        Larger context frame for generating final response.

        Args:
            message: Original user message.
            upstream_results: Results from upstream tasks.

        Returns:
            ContextFrame with 4000 token budget.
        """
        return ContextFrame(
            id=f"synthesize:{uuid.uuid4().hex[:6]}",
            name="synthesize",
            purpose="Generate final response to user",
            system_prompt="Based on the collected information, generate a complete response to the user's question.",
            tools=[],  # Synthesize doesn't need tools
            max_tokens=4000,  # Synthesize needs larger context
            data={
                "message": message,
                "results": upstream_results,
            },
        )

    @staticmethod
    def context_analyzer() -> ContextFrame:
        """Create a Context Analyzer frame.

        Minimal frame for analyzing context dependencies.

        Returns:
            ContextFrame with 300 token budget.
        """
        return ContextFrame(
            id=f"analyzer:{uuid.uuid4().hex[:6]}",
            name="analyzer",
            purpose="Analyze context dependencies",
            max_tokens=300,  # Analyzer frame is most minimal
            data={},
        )

    @staticmethod
    def router(goal: str) -> ContextFrame:
        """Create a Router frame.

        Minimal frame for routing decisions.

        Args:
            goal: User goal to route.

        Returns:
            ContextFrame with 400 token budget.
        """
        return ContextFrame(
            id=f"router:{uuid.uuid4().hex[:6]}",
            name="router",
            purpose="Route request to appropriate handler",
            max_tokens=400,
            data={"goal": goal},
        )
