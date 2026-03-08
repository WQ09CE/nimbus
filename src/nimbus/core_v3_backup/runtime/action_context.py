"""
Action Context - Encapsulates execution context for action handlers.

Provides a clean interface for passing dependencies to action handlers,
improving testability and reducing coupling between VCPU and its components.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from nimbus.core.memory.mmu import MMU
from nimbus.core.protocol import ActionIR, ToolResult
from nimbus.core.runtime.recovery_executor import RecoveryContext, RecoveryExecutor


@dataclass
class ActionContext:
    """
    Encapsulates the execution context for action handlers.

    This reduces direct coupling between handlers and VCPU internals,
    making it easier to test handlers in isolation.

    Attributes:
        gate: The kernel gate for tool execution
        mmu: Memory management unit
        recovery_executor: Executor for error recovery actions
        default_timeout: Default timeout for tool execution
        emit_event: Callback for emitting events
    """

    # Core dependencies
    gate: Any  # KernelGate, using Any to avoid circular import
    mmu: MMU
    recovery_executor: RecoveryExecutor

    # Configuration
    default_timeout: float = 30.0

    # Callbacks
    emit_event: Optional[Callable[[str, Dict[str, Any]], None]] = None

    async def execute_tool(self, action: ActionIR) -> ToolResult:
        """
        Execute a tool through the gate.

        Args:
            action: The action to execute

        Returns:
            ToolResult from execution
        """
        return await self.gate.syscall_tool(action, timeout_sec=self.default_timeout)

    async def try_recover(
        self, action: ActionIR, result: ToolResult
    ) -> Optional[ToolResult]:
        """
        Attempt to recover from a tool error.

        Args:
            action: The failed action
            result: The error result

        Returns:
            Recovered ToolResult or None if recovery failed
        """
        if not result.fault:
            return None

        ctx = RecoveryContext(
            original_action=action,
            original_result=result,
            default_timeout=self.default_timeout,
        )


        # Get recovery strategy from registry
        registry = self.recovery_executor._error_registry
        if registry is None:
            return None

        recovery = await registry.handle_error(
            fault_message=result.fault.message,
            tool_name=action.name,
            args=action.args,
            workspace=None,
        )

        if recovery is None:
            return None

        return await self.recovery_executor.execute(recovery, ctx)

    def add_to_memory(
        self,
        action: ActionIR,
        result: ToolResult,
        thinking: Optional[str] = None,
    ) -> None:
        """
        Add action result to memory.

        Args:
            action: The executed action
            result: The result to record
            thinking: Optional thinking content
        """
        self.mmu.add_tool_result(action, result, thinking)

    def fire_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Emit an event if callback is registered.

        Args:
            event_type: Type of event
            data: Event data
        """
        if self.emit_event:
            self.emit_event(event_type, data)
