"""
Nimbus v2 Kernel Gate - The Customs

This module is the unified entry point for all side-effects.
All tool executions, IPC messages, and subprocess spawns must go through the Gate.

Key Responsibilities:
- Permission checking (via CapabilityToken)
- Timeout enforcement
- Event emission (observability)
- Result packaging (ToolResult)

Design Principle: "One Gate, All Effects"
"""

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional, Protocol

from nimbus.v2.core.protocol import (
    ActionIR,
    ToolResult,
    Fault,
    Event,
    IPCMessage,
    ArtifactRef,
)


class PermissionManager(Protocol):
    """Protocol for permission management."""

    @property
    def tools(self) -> Dict[str, Any]:
        """Tool permission configuration."""
        ...

    def check_tool(self, tool_name: str) -> bool:
        """Check if tool is allowed."""
        ...


class EventStream(Protocol):
    """Protocol for event emission."""

    def emit(self, event: Event) -> None:
        """Emit an event to the stream."""
        ...


class ToolExecutor(Protocol):
    """Protocol for tool execution."""

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Execute a tool and return the result."""
        ...


class IPCBus(Protocol):
    """Protocol for IPC message bus."""

    def publish(self, message: IPCMessage) -> None:
        """Publish a message to the bus."""
        ...


class KernelGate:
    """
    System Call Gate.

    Enforces permissions, handles timeouts, and ensures observability.
    All side-effects must pass through this gate.

    Example:
        gate = KernelGate(
            pid="proc-001",
            permission_mgr=perm_mgr,
            event_stream=events,
            tool_executor=executor,
            ipc_bus=ipc
        )

        result = await gate.syscall_tool(action, timeout_sec=30.0)
    """

    def __init__(
        self,
        pid: str,
        permission_mgr: Optional[PermissionManager] = None,
        event_stream: Optional[EventStream] = None,
        tool_executor: Optional[ToolExecutor] = None,
        ipc_bus: Optional[IPCBus] = None,
        default_timeout: float = 60.0,
    ):
        """
        Initialize the Kernel Gate.

        Args:
            pid: Process ID for event emission
            permission_mgr: Permission manager for access control
            event_stream: Event stream for observability
            tool_executor: Tool executor for running tools
            ipc_bus: IPC bus for inter-process communication
            default_timeout: Default execution timeout in seconds
        """
        self.pid = pid
        self.perm = permission_mgr
        self.events = event_stream
        self.executor = tool_executor
        self.ipc = ipc_bus
        self.default_timeout = default_timeout

    async def syscall_tool(
        self,
        action: ActionIR,
        timeout_sec: Optional[float] = None,
    ) -> ToolResult:
        """
        Execute a TOOL_CALL action with safety checks.

        This is the main entry point for tool execution. It:
        1. Checks permissions
        2. Emits start event
        3. Executes with timeout
        4. Packages result
        5. Emits finish event

        Args:
            action: The TOOL_CALL ActionIR to execute
            timeout_sec: Execution timeout (uses default if not specified)

        Returns:
            ToolResult with status, output, and optional fault
        """
        tool_name = action.name
        timeout = timeout_sec or self.default_timeout

        # 1. Permission Check
        if not self._check_tool_permission(tool_name):
            fault = Fault(
                domain="PERMISSION",
                code="PERMISSION_DENIED",
                message=f"Tool '{tool_name}' not allowed for process '{self.pid}'",
                retryable=False,
                context={"tool": tool_name, "pid": self.pid}
            )
            self._emit_event("FAULT_RAISED", {
                "action_id": action.id,
                "fault": str(fault)
            })
            return ToolResult(status="ERROR", fault=fault)

        # 2. Emit Start Event
        self._emit_event("TOOL_STARTED", {
            "action_id": action.id,
            "tool": tool_name,
            "args_keys": list(action.args.keys())
        })
        start_time = time.time_ns()

        # 3. Execution (with Timeout)
        output = None
        fault = None
        status: str = "OK"
        artifacts: List[ArtifactRef] = []

        try:
            if self.executor is None:
                raise Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message="No tool executor configured",
                    retryable=False
                )

            # Wrap execution in timeout
            output = await asyncio.wait_for(
                self.executor.execute(tool_name, action.args),
                timeout=timeout
            )
            status = "OK"

        except asyncio.TimeoutError:
            status = "TIMEOUT"
            fault = Fault(
                domain="RESOURCE",
                code="TIMEOUT",
                message=f"Tool '{tool_name}' execution exceeded {timeout}s",
                retryable=True,
                context={"tool": tool_name, "timeout_sec": timeout}
            )

        except Fault as f:
            status = "ERROR"
            fault = f

        except asyncio.CancelledError:
            status = "CANCELLED"
            fault = Fault(
                domain="KERNEL",
                code="SYSTEM_ERROR",
                message=f"Tool '{tool_name}' execution was cancelled",
                retryable=True
            )

        except Exception as e:
            status = "ERROR"
            output = str(e)
            fault = Fault(
                domain="TOOL",
                code="TOOL_FAILURE",
                message=str(e),
                retryable=True,
                context={"tool": tool_name, "exception_type": type(e).__name__}
            )

        # 4. Result Packaging
        duration_ms = (time.time_ns() - start_time) // 1_000_000
        result = ToolResult(
            status=status,
            output=output,
            fault=fault,
            artifacts=artifacts,
            timing_ms={"total": duration_ms}
        )

        # 5. Emit Finish Event
        self._emit_event("TOOL_FINISHED", {
            "action_id": action.id,
            "tool": tool_name,
            "status": status,
            "duration_ms": duration_ms,
            "has_fault": fault is not None
        })

        return result

    def post_ipc(
        self,
        channel: str,
        key: str,
        value_ref: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Publish data reference to IPC bus.

        Args:
            channel: Message channel (e.g., "task_result")
            key: Unique key within channel (e.g., "t1.output")
            value_ref: Reference to actual data (artifact ID / store key)
            meta: Additional metadata
        """
        msg = IPCMessage(
            channel=channel,
            key=key,
            value_ref=value_ref,
            meta=meta or {}
        )

        if self.ipc:
            self.ipc.publish(msg)

        self._emit_event("ACTION_EMITTED", {
            "type": "IPC",
            "channel": channel,
            "key": key
        })

    def request_replan(self, reason: Dict[str, Any]) -> None:
        """
        Signal the kernel scheduler to replan.

        Args:
            reason: Reason for replan request
        """
        self._emit_event("REPLAN_REQUESTED", {
            "pid": self.pid,
            "reason": reason
        })

    def _check_tool_permission(self, tool_name: str) -> bool:
        """
        Check if a tool is allowed for this process.

        Args:
            tool_name: Name of the tool to check

        Returns:
            True if allowed, False otherwise
        """
        if self.perm is None:
            # No permission manager = allow all
            return True

        # Check via permission manager protocol
        if hasattr(self.perm, 'check_tool'):
            return self.perm.check_tool(tool_name)

        # Fallback: simple whitelist check
        tools_config = getattr(self.perm, 'tools', {})
        allowed = tools_config.get("allow", [])
        denied = tools_config.get("deny", [])

        # Deny takes precedence
        if tool_name in denied:
            return False

        # Allow wildcard or explicit allow
        return "*" in allowed or tool_name in allowed

    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Emit an event to the event stream.

        Args:
            event_type: Type of event (see EventType)
            data: Event data
        """
        if self.events:
            self.events.emit(Event(
                type=event_type,  # type: ignore
                pid=self.pid,
                data=data
            ))


# =============================================================================
# Simple implementations for testing
# =============================================================================

class SimplePermissionManager:
    """Simple permission manager for testing."""

    def __init__(self, allowed_tools: List[str]):
        self.tools = {"allow": allowed_tools, "deny": []}

    def check_tool(self, tool_name: str) -> bool:
        return "*" in self.tools["allow"] or tool_name in self.tools["allow"]


class SimpleEventStream:
    """Simple event stream that collects events in a list."""

    def __init__(self):
        self.events: List[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events.clear()


class SimpleIPCBus:
    """Simple IPC bus that stores messages in a dict."""

    def __init__(self):
        self.messages: Dict[str, IPCMessage] = {}

    def publish(self, message: IPCMessage) -> None:
        key = f"{message.channel}:{message.key}"
        self.messages[key] = message

    def get(self, channel: str, key: str) -> Optional[IPCMessage]:
        return self.messages.get(f"{channel}:{key}")
