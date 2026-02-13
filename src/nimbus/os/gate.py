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
        timeout = timeout_sec or _META_TOOL_TIMEOUTS.get(tool_name, self.default_timeout)

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

        # Normalize common parameter name aliases (LLM hallucination tolerance)
        action.args = _normalize_tool_args(tool_name, action.args)

        # Validate required parameters (after normalization)
        validation_error = _validate_required_args(tool_name, action.args)
        if validation_error:
            error_output = f"[Error] {validation_error}"
            self._emit_event(
                "TOOL_FINISHED",
                {
                    "action_id": action.id,
                    "tool": tool_name,
                    "status": "ERROR",
                    "output": error_output,
                    "duration_ms": 0,
                    "fault": {
                        "domain": "TOOL",
                        "code": "MISSING_REQUIRED_PARAM",
                        "message": validation_error,
                        "retryable": True,
                    },
                },
            )
            return ToolResult(
                status="ERROR",
                output=error_output,
                fault=Fault(
                    domain="TOOL",
                    code="MISSING_REQUIRED_PARAM",
                    message=validation_error,
                    retryable=True,
                ),
            )

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

        # Apply memory protection (truncation)
        # We truncate here to ensure neither the log file nor the MMU sees
        # the massive output string.
        output = _truncate_output(output)

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


# =============================================================================
# Tool Argument Normalization (LLM hallucination tolerance)
# =============================================================================

# Meta-tools that spawn sub-agents need longer timeouts than regular tools.
# These override the Gate's default_timeout (typically 60s).
_META_TOOL_TIMEOUTS: Dict[str, float] = {
    "Dispatch": 600.0,  # Executor agent runs inside; controlled by max_iterations not timeout
    "Verify": 120.0,    # Runs multiple checks sequentially
}

# Maps tool_name -> {alias: canonical_name}
_ARG_ALIASES: Dict[str, Dict[str, str]] = {
    "Read": {
        "path": "file_path",
        "filename": "file_path",
        "file": "file_path",
    },
    "Write": {
        "path": "file_path",
        "filename": "file_path",
        "file": "file_path",
    },
    "Edit": {
        "path": "file_path",
        "filename": "file_path",
        "file": "file_path",
        "old": "old_text",
        "oldText": "old_text",
        "search": "old_text",
        "new": "new_text",
        "newText": "new_text",
        "replace": "new_text",
    },
    "Bash": {
        "cmd": "command",
        "script": "command",
    },
    "CoreBash": {
        "cmd": "command",
        "script": "command",
    },
}

# Required parameters for each tool (used for pre-flight validation)
_REQUIRED_PARAMS: Dict[str, List[str]] = {
    "Read": ["file_path"],
    "Write": ["file_path", "content"],
    "Edit": ["file_path", "old_text", "new_text"],
    "Bash": ["command"],
}


# =============================================================================
# Tool Output Truncation (Memory Protection)
# =============================================================================

# Hard limit for tool output (~50k tokens).
# Prevents a single massive tool output (e.g. `cat huge.log`) from blowing up
# the context window and crashing the MMU.
# OpenClaw uses ~400k chars (100k tokens), but we are more conservative here
# to ensure room for the rest of the context stack.
MAX_TOOL_OUTPUT_CHARS = 200_000

# When truncating, keep this many characters from the start.
# This allows the Agent to verify file headers or see the error format.
TRUNCATION_KEEP_CHARS = 2_000


def _truncate_output(text: Any) -> Any:
    """
    Truncate large string outputs to protect memory.

    Args:
        text: The tool output (usually string, but could be any type)

    Returns:
        The truncated string if it was a large string, or the original object.
    """
    if not isinstance(text, str):
        return text

    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text

    original_len = len(text)
    cut_point = TRUNCATION_KEEP_CHARS

    # Attempt to cut at a newline for cleaner output
    try:
        # Look for the last newline within the keep limit
        last_newline = text.rfind('\n', 0, TRUNCATION_KEEP_CHARS)
        if last_newline > TRUNCATION_KEEP_CHARS * 0.8:  # Only if it's not too close to start
            cut_point = last_newline
    except Exception:
        pass  # Fallback to strict length

    head = text[:cut_point]

    warning = (
        f"\n\n⚠️ [Output Truncated] The tool output was too large ({original_len:,} chars) "
        f"and has been truncated to the first {cut_point:,} chars.\n"
        f"To see more, please use specific tools (e.g. `Read` with line numbers) "
        f"or commands (e.g. `head`/`tail`/`grep`) to process the data."
    )

    return head + warning


def _normalize_tool_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize tool argument names to handle common LLM hallucinations.

    LLMs frequently use 'path' instead of 'file_path', 'filename' instead of
    'file_path', etc. Rather than wasting a turn on an error, we silently fix it.

    Only remaps if the canonical name is NOT already present (avoid overwriting).
    """
    aliases = _ARG_ALIASES.get(tool_name)
    if not aliases:
        return args

    normalized = dict(args)
    for alias, canonical in aliases.items():
        if alias in normalized and canonical not in normalized:
            normalized[canonical] = normalized.pop(alias)

    return normalized


def _validate_required_args(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """Validate that all required parameters are present after normalization.
    
    Returns an error message string if validation fails, None if OK.
    This catches cases where LLM omits required params entirely
    (not just uses wrong names, which _normalize_tool_args handles).
    """
    required = _REQUIRED_PARAMS.get(tool_name)
    if not required:
        return None

    missing = [p for p in required if p not in args or args[p] is None or args[p] == ""]
    if not missing:
        return None

    # Build a loud, clear error with concrete examples
    missing_str = ", ".join(f"'{p}'" for p in missing)

    # Tool-specific usage examples
    examples = {
        "Bash": 'Bash(command="ls -la")',
        "Read": 'Read(file_path="src/main.py")',
        "Write": 'Write(file_path="output.txt", content="hello")',
        "Edit": 'Edit(file_path="main.py", old_text="old", new_text="new")',
    }
    example = examples.get(tool_name, f'{tool_name}({", ".join(f"{p}=..." for p in required)})')

    return (
        f"PARAMETER ERROR: You must provide {missing_str} in your tool call. "
        f"You sent: {tool_name}({', '.join(f'{k}={v!r}' for k, v in args.items()) if args else ''}). "
        f"Correct example: {example}"
    )
