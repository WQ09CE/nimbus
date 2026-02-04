"""
Nimbus v2 Kernel Gate - The Customs

This module is the unified entry point for all tool executions.
All tool calls must go through the Gate for timeout enforcement and observability.

Key Responsibilities:
- Timeout enforcement (asyncio.wait_for)
- Error packaging (Exception → Fault → ToolResult)
- Event emission (TOOL_STARTED/TOOL_FINISHED for observability)

Design Principle: "One Gate, All Effects"

Note: Permission checking and IPC were removed as YAGNI (You Aren't Gonna Need It).
They can be added back when actually needed (~15 lines each).
"""

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional, Protocol

from nimbus.core.protocol import (
    ActionIR,
    ArtifactRef,
    Event,
    Fault,
    ToolResult,
)


class EventStream(Protocol):
    """Protocol for event emission (used by web-ui SSE)."""

    def emit(self, event: Event) -> None:
        """Emit an event to the stream."""
        ...


class ToolExecutor(Protocol):
    """Protocol for tool execution."""

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Execute a tool and return the result."""
        ...


class KernelGate:
    """
    System Call Gate - Simplified.

    Handles timeout enforcement, error packaging, and event emission.
    All tool executions must pass through this gate.

    Example:
        gate = KernelGate(
            pid="proc-001",
            tool_executor=executor,
            event_stream=events,  # optional
        )

        result = await gate.syscall_tool(action, timeout_sec=30.0)
    """

    def __init__(
        self,
        pid: str,
        tool_executor: ToolExecutor,
        event_stream: Optional[EventStream] = None,
        default_timeout: float = 60.0,
        local_tools: Optional[Dict[str, Callable]] = None,
    ):
        """
        Initialize the Kernel Gate.

        Args:
            pid: Process ID for event emission
            tool_executor: Tool executor for running tools
            event_stream: Event stream for observability (optional)
            default_timeout: Default execution timeout in seconds
            local_tools: Process-specific tools (in-memory)
        """
        self.pid = pid
        self.executor = tool_executor
        self.events = event_stream
        self.default_timeout = default_timeout
        self.local_tools = local_tools or {}

    async def syscall_tool(
        self,
        action: ActionIR,
        timeout_sec: Optional[float] = None,
    ) -> ToolResult:
        """
        Execute a TOOL_CALL action with timeout and error handling.

        This is the main entry point for tool execution. It:
        1. Emits TOOL_STARTED event
        2. Executes with timeout (asyncio.wait_for)
        3. Packages result/error into ToolResult
        4. Emits TOOL_FINISHED event

        Args:
            action: The TOOL_CALL ActionIR to execute
            timeout_sec: Execution timeout (uses default if not specified)

        Returns:
            ToolResult with status, output, and optional fault
        """
        tool_name = action.name
        timeout = timeout_sec or self.default_timeout

        # 1. Emit Start Event
        self._emit_event(
            "TOOL_STARTED",
            {
                "action_id": action.id,
                "tool": tool_name,
                "args": action.args,
                "args_keys": list(action.args.keys()),
            },
        )

        from nimbus.core.logging import get_logger

        logger = get_logger("kernel.gate")
        logger.info(f"Executing tool '{tool_name}'...")

        start_time = time.time_ns()

        # 2. Execution with Timeout
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
                    retryable=False,
                )

            # Check local tools first (Process-specific)
            if tool_name in self.local_tools:
                func = self.local_tools[tool_name]
                if asyncio.iscoroutinefunction(func):
                    output = await asyncio.wait_for(func(**action.args), timeout=timeout)
                else:
                    # Run sync function in thread pool to avoid blocking loop
                    output = await asyncio.to_thread(func, **action.args)
            else:
                # Fallback to global registry
                output = await asyncio.wait_for(
                    self.executor.execute(tool_name, action.args), timeout=timeout
                )
            status = "OK"

        except asyncio.TimeoutError:
            status = "TIMEOUT"
            error_msg = f"Tool '{tool_name}' execution exceeded {timeout}s"
            output = f"[Error] {error_msg}"
            fault = Fault(
                domain="RESOURCE",
                code="TIMEOUT",
                message=error_msg,
                retryable=True,
                context={"tool": tool_name, "timeout_sec": timeout},
            )

        except Fault as f:
            status = "ERROR"
            output = f"[Error] {f.message}"
            fault = f

        except asyncio.CancelledError:
            # Log the cancellation
            duration_ms = (time.time_ns() - start_time) // 1_000_000
            logger.info(f"🛑 Tool '{tool_name}' cancelled after {duration_ms}ms")
            # Re-raise to propagate cancellation to the caller
            # This ensures the entire task is cancelled, not just the tool
            raise

        except Exception as e:
            status = "ERROR"
            # Standardize error output
            output = f"[Error] {str(e)}"
            fault = Fault(
                domain="TOOL",
                code="TOOL_FAILURE",
                message=str(e),
                retryable=True,
                context={"tool": tool_name, "exception_type": type(e).__name__},
            )

        # 3. Result Packaging
        duration_ms = (time.time_ns() - start_time) // 1_000_000

        status_emoji = "✅" if status == "OK" else "❌"
        if status == "OK":
            output_preview = str(output)
            if len(output_preview) > 200:
                output_preview = output_preview[:200] + "..."
            logger.info(
                f"{status_emoji} Tool '{tool_name}' finished in {duration_ms}ms | Output: {output_preview}"
            )
        else:
            logger.error(
                f"{status_emoji} Tool '{tool_name}' failed in {duration_ms}ms | Status: {status} | Error: {fault}"
            )

        result = ToolResult(
            status=status,
            output=output,
            fault=fault,
            artifacts=artifacts,
            timing_ms={"total": duration_ms},
        )

        # 4. Emit Finish Event
        fault_data = None
        if fault is not None:
            fault_data = {
                "domain": fault.domain,
                "code": fault.code,
                "message": fault.message,
                "retryable": fault.retryable,
            }

        self._emit_event(
            "TOOL_FINISHED",
            {
                "action_id": action.id,
                "tool": tool_name,
                "status": status,
                "output": output,
                "duration_ms": duration_ms,
                "fault": fault_data,
            },
        )

        return result

    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Emit an event to the event stream.

        Args:
            event_type: Type of event (TOOL_STARTED, TOOL_FINISHED, etc.)
            data: Event data
        """
        if self.events:
            self.events.emit(
                Event(
                    type=event_type,  # type: ignore
                    pid=self.pid,
                    data=data,
                )
            )


# =============================================================================
# Simple implementation for testing
# =============================================================================


class SimpleEventStream:
    """Simple event stream that collects events and supports listeners."""

    def __init__(self):
        self.events: List[Event] = []
        self._listeners: List[Callable[[Event], Any]] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass  # Ignore listener errors

    def add_listener(self, listener: Callable[[Event], Any]) -> None:
        """Add an event listener."""
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[Event], Any]) -> None:
        """Remove an event listener."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def clear(self) -> None:
        """Clear all collected events."""
        self.events.clear()
