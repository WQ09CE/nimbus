"""
TUI Dashboard State Management

Provides StateManager and DashboardState for aggregating and managing
all dashboard state from AgentOS events.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional

if TYPE_CHECKING:
    from nimbus.agentos import AgentOS
    from nimbus.core.protocol import Event


# =============================================================================
# Chat Message
# =============================================================================


@dataclass
class ChatMessage:
    """
    A message in the chat history.

    Attributes:
        role: Message sender role (user, agent, tool, system)
        content: Message content
        timestamp: When the message was created
        tool_name: Tool name (for tool messages)
        tool_status: Tool execution status (running, success, error)
    """

    role: Literal["user", "agent", "tool", "system"]
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_name: Optional[str] = None
    tool_status: Optional[str] = None  # "running", "success", "error"


# =============================================================================
# Dashboard State
# =============================================================================


@dataclass
class DashboardState:
    """
    Aggregated state for dashboard rendering.

    This dataclass holds all state needed to render the dashboard.
    It is updated by StateManager in response to AgentOS events.
    """

    # AgentOS state
    processes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tools: List[str] = field(default_factory=list)
    event_count: int = 0

    # Active DAG state (if any)
    current_dag_id: Optional[str] = None
    dag_status: Optional[Dict[str, int]] = None
    dag_tasks: Optional[Dict[str, str]] = None

    # Active VCPU state (from current process)
    vcpu_iteration: int = 0
    vcpu_max_iterations: int = 50
    vcpu_is_running: bool = False
    vcpu_timing: Dict[str, int] = field(default_factory=dict)

    # Memory state
    mmu_tokens: int = 0
    mmu_max_tokens: int = 128000
    mmu_stack_depth: int = 0

    # Chat state
    messages: List[ChatMessage] = field(default_factory=list)
    is_processing: bool = False

    # Status bar state
    status_text: str = ""


# =============================================================================
# State Manager
# =============================================================================


class StateManager:
    """
    Manages dashboard state by subscribing to AgentOS events.

    Key Design:
    - Subscribe to EventStream for real-time updates
    - Poll component states for detailed info
    - Debounce updates to prevent UI flicker
    """

    def __init__(
        self,
        agent_os: Optional["AgentOS"] = None,
        max_messages: int = 100,
        debounce_ms: int = 100,
    ):
        """
        Initialize StateManager.

        Args:
            agent_os: AgentOS instance to subscribe to (optional for testing)
            max_messages: Maximum chat messages to keep
            debounce_ms: Minimum milliseconds between updates
        """
        self._os = agent_os
        self._state = DashboardState()
        self._last_update_ms: float = 0.0
        self._debounce_ms = debounce_ms
        self._max_messages = max_messages
        self._subscribers: List[Callable[[DashboardState], None]] = []
        self._active_pid: Optional[str] = None

        # Subscribe to AgentOS events if provided
        if agent_os is not None:
            # Check if event stream has subscribe method (scheduler EventStream has it)
            if hasattr(agent_os._events, "subscribe"):
                agent_os._events.subscribe(self._handle_event)
            # Initialize state from AgentOS
            self._sync_from_agentos()

    def _sync_from_agentos(self) -> None:
        """Synchronize state from AgentOS."""
        if self._os is None:
            return

        os_state = self._os.get_state()
        self._state.processes = os_state.get("processes", {})
        self._state.tools = os_state.get("tools", [])
        self._state.event_count = os_state.get("event_count", 0)

    def _handle_event(self, event: "Event") -> None:
        """Handle incoming event and update state."""
        # Debounce: skip if too soon after last update
        now_ms = time.time() * 1000
        if now_ms - self._last_update_ms < self._debounce_ms:
            return

        event_type = event.type
        event_data = event.data or {}
        pid = event.pid

        if event_type == "PROC_SPAWNED":
            self._update_processes()
            self._active_pid = pid
        elif event_type == "PROC_FINISHED":
            self._update_processes()
            if pid == self._active_pid:
                self._active_pid = None
        elif event_type in ("TASK_ASSIGNED", "TASK_FINISHED"):
            dag_id = event_data.get("dag_id")
            if dag_id:
                self._update_dag(dag_id)
        elif event_type == "STEP_STARTED":
            self._update_vcpu(pid)
        elif event_type in ("TOOL_STARTED", "TOOL_FINISHED"):
            self._update_vcpu(pid)
            # Add tool message to chat
            tool_name = event_data.get("tool_name", "Unknown")
            if event_type == "TOOL_STARTED":
                self.add_tool_message(tool_name, "Executing...", "running")
            else:
                status = "success" if event_data.get("status") == "OK" else "error"
                output = event_data.get("output", "")
                if isinstance(output, str) and len(output) > 100:
                    output = output[:100] + "..."
                self.add_tool_message(tool_name, str(output), status)

        self._state.event_count = len(self._os.get_events()) if self._os else 0
        self._last_update_ms = now_ms
        self._notify_subscribers()

    def _update_processes(self) -> None:
        """Refresh process list from AgentOS."""
        if self._os is None:
            return
        state = self._os.get_state()
        self._state.processes = state.get("processes", {})

    def _update_dag(self, dag_id: str) -> None:
        """Refresh DAG status from Scheduler."""
        if self._os is None:
            return

        self._state.current_dag_id = dag_id
        self._state.dag_status = self._os._scheduler.get_dag_status(dag_id)

        dag = self._os._scheduler.get_dag(dag_id)
        if dag:
            self._state.dag_tasks = {tid: task.state for tid, task in dag.tasks.items()}

    def _update_vcpu(self, pid: str) -> None:
        """Refresh VCPU state from active process."""
        if self._os is None:
            return

        process = self._os.get_process(pid)
        if process and process.vcpu:
            vcpu_state = process.vcpu.get_state()
            self._state.vcpu_iteration = vcpu_state.get("iteration", 0)
            self._state.vcpu_is_running = vcpu_state.get("is_running", False)
            self._state.vcpu_max_iterations = 50  # Default from config

            if process.mmu:
                mmu_state = process.mmu.get_state()
                self._state.mmu_tokens = mmu_state.get("estimated_tokens", 0)
                self._state.mmu_stack_depth = mmu_state.get("stack_depth", 0)

    def _notify_subscribers(self) -> None:
        """Notify all subscribers of state change."""
        for callback in self._subscribers:
            try:
                callback(self._state)
            except Exception:
                pass  # Don't let subscriber errors crash the state manager

    # =========================================================================
    # Public API
    # =========================================================================

    def get_state(self) -> DashboardState:
        """Get current dashboard state."""
        return self._state

    def subscribe(self, callback: Callable[[DashboardState], None]) -> None:
        """Subscribe to state changes."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[DashboardState], None]) -> None:
        """Unsubscribe from state changes."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    # =========================================================================
    # Message Management
    # =========================================================================

    def add_message(self, message: ChatMessage) -> None:
        """Add a message to chat history."""
        self._state.messages.append(message)
        if len(self._state.messages) > self._max_messages:
            self._state.messages.pop(0)
        self._notify_subscribers()

    def add_user_message(self, content: str) -> None:
        """Add a user message."""
        self.add_message(ChatMessage(role="user", content=content))

    def add_agent_message(self, content: str) -> None:
        """Add an agent message."""
        self.add_message(ChatMessage(role="agent", content=content))

    def add_system_message(self, content: str) -> None:
        """Add a system message."""
        self.add_message(ChatMessage(role="system", content=content))

    def add_tool_message(self, tool_name: str, content: str, status: str = "success") -> None:
        """Add a tool message."""
        self.add_message(
            ChatMessage(
                role="tool",
                content=content,
                tool_name=tool_name,
                tool_status=status,
            )
        )

    def set_processing(self, is_processing: bool, status_text: str = "") -> None:
        """Set processing state."""
        self._state.is_processing = is_processing
        self._state.status_text = status_text
        self._notify_subscribers()

    def clear_messages(self) -> None:
        """Clear all chat messages."""
        self._state.messages.clear()
        self._notify_subscribers()
