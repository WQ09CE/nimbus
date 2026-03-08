"""
Recovery Executor - Executes error recovery actions.

Separates strategy (ErrorHandlerRegistry) from execution (RecoveryExecutor).
This follows the Single Responsibility Principle:
- ErrorHandlerRegistry: Decides WHAT to do
- RecoveryExecutor: Executes HOW to do it
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from nimbus.core.logging import get_logger
from nimbus.core.protocol import ActionIR, ToolResult
from nimbus.core.runtime.error_handler import ErrorHandlerRegistry, RecoveryAction

logger = get_logger("kernel.vcpu.recovery")


@dataclass
class RecoveryContext:
    """
    Context for recovery execution.

    Contains everything needed to execute a recovery action.
    """

    original_action: ActionIR
    original_result: ToolResult
    default_timeout: float = 30.0


# Type alias for tool executor function
ToolExecutor = Callable[[ActionIR, float], Awaitable[ToolResult]]


class RecoveryExecutor:
    """
    Executes error recovery actions.

    Handles:
    - skip: No intervention, let original error propagate
    - inject_hint: Enhance error message with helpful hint
    - auto_tool: Execute recovery tool and combine results
    - modify_args: Retry with modified arguments
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        error_registry: Optional[ErrorHandlerRegistry] = None,
    ):
        """
        Initialize RecoveryExecutor.

        Args:
            tool_executor: Function to execute tools (e.g., gate.syscall_tool)
            error_registry: Registry for clearing failure counts on success
        """
        self._execute_tool = tool_executor
        self._error_registry = error_registry

    async def execute(
        self, recovery: RecoveryAction, ctx: RecoveryContext
    ) -> Optional[ToolResult]:
        """
        Execute a recovery action.

        Args:
            recovery: The recovery action to execute
            ctx: Context containing original action and result

        Returns:
            Modified ToolResult if recovery produces output, None to use original
        """
        action_type = recovery.action_type

        if action_type == "skip":
            return self._handle_skip()

        if action_type == "inject_hint":
            return self._handle_inject_hint(recovery, ctx)

        if action_type == "auto_tool":
            return await self._handle_auto_tool(recovery, ctx)

        if action_type == "modify_args":
            return await self._handle_modify_args(recovery, ctx)

        logger.warning(f"Unknown recovery action type: {action_type}")
        return None

    def _handle_skip(self) -> None:
        """Handle skip action - no intervention."""
        return None

    def _handle_inject_hint(
        self, recovery: RecoveryAction, ctx: RecoveryContext
    ) -> Optional[ToolResult]:
        """
        Handle inject_hint action.

        Enhances error output with a helpful hint while preserving the error status.
        """
        if not recovery.hint:
            return None

        # Build enhanced output: original error + hint
        error_msg = self._get_error_message(ctx.original_result)
        enhanced_output = f"{error_msg}\n\n{recovery.hint}"

        logger.info(f"🔧 Enhancing error with hint: {recovery.hint[:50]}...")

        return ToolResult(
            status="ERROR",
            output=enhanced_output,
            fault=ctx.original_result.fault,
        )

    async def _handle_auto_tool(
        self, recovery: RecoveryAction, ctx: RecoveryContext
    ) -> Optional[ToolResult]:
        """
        Handle auto_tool action.

        Automatically executes a recovery tool and combines its output with
        the original error message.
        """
        if not recovery.auto_tool or not recovery.auto_args:
            return None

        logger.info(f"🔧 Auto-executing recovery tool: {recovery.auto_tool}")

        # Create recovery action
        recovery_action = ActionIR(
            kind="TOOL_CALL",
            name=recovery.auto_tool,
            id=f"recovery_{ctx.original_action.id}",
            args=recovery.auto_args,
            meta={"recovery_for": ctx.original_action.name},
        )

        # Execute recovery tool
        recovery_result = await self._execute_tool(
            recovery_action, ctx.default_timeout
        )

        # Combine error message, hint, and recovery result
        # Use [Recovery] prefix to avoid triggering _auto_detect_tool_failure's [Error] check
        error_msg = self._get_error_message(ctx.original_result)
        parts = [f"[Recovery] Original tool '{ctx.original_action.name}' failed: {error_msg}"]

        if recovery.hint:
            parts.append(f"(Hint: {recovery.hint})")

        if recovery_result.output:
            parts.append(f"\n[Auto-Recovery Output]:\n{recovery_result.output}")

        combined_message = "\n".join(parts)
        logger.info("🔧 Enhanced error with auto-recovery result")

        return ToolResult(
            status="ERROR",
            output=combined_message,
            fault=ctx.original_result.fault,
        )

    async def _handle_modify_args(
        self, recovery: RecoveryAction, ctx: RecoveryContext
    ) -> Optional[ToolResult]:
        """
        Handle modify_args action.

        Retries the original action with modified arguments.
        """
        if not recovery.modified_args:
            return None

        logger.info(f"🔧 Retrying {ctx.original_action.name} with modified args")

        # Create modified action
        new_action = ActionIR(
            kind=ctx.original_action.kind,
            name=ctx.original_action.name,
            id=ctx.original_action.id,
            args={**ctx.original_action.args, **recovery.modified_args},
            meta={
                **(ctx.original_action.meta or {}),
                "modified_by_recovery": True,
            },
        )

        # Execute with modified args
        new_result = await self._execute_tool(new_action, ctx.default_timeout)

        if new_result.status == "OK":
            # Success! Clear failure count
            if self._error_registry:
                self._error_registry.clear_failure(
                    ctx.original_action.name, ctx.original_action.args
                )

            # Add recovery note
            if new_result.output:
                new_result = ToolResult(
                    status="OK",
                    output=f"[Recovered with modified args]\n{new_result.output}",
                )
            return new_result

        # Modified args also failed
        return None

    def _get_error_message(self, result: ToolResult) -> str:
        """Extract error message from a ToolResult."""
        if result.output:
            return str(result.output)
        if result.fault:
            return f"[Error] {result.fault.message}"
        return "[Error] Unknown error"
