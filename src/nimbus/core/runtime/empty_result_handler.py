"""
Empty Result Handler - Handles "success but no result" situations.

Extracted from VCPU to follow single responsibility principle.
Handles cases like Glob/Grep returning no matches.
"""

from typing import Optional

from nimbus.core.logging import get_logger
from nimbus.core.protocol import ActionIR, Fault, ToolResult
from nimbus.core.runtime.error_handler import ErrorHandlerRegistry
from nimbus.core.runtime.execution_state import ExecutionState

logger = get_logger("kernel.vcpu.empty_result")


class EmptyResultHandler:
    """
    Handles "success but no result" situations (e.g., Glob/Grep no match).

    These cases have status=OK but LLM may fall into repeated attempts.
    This handler provides intelligent recovery hints and tracks failures.
    """

    # Tools that can return "no match" results
    NO_MATCH_TOOLS = {"Glob", "Grep"}

    def __init__(
        self,
        state: ExecutionState,
        error_registry: ErrorHandlerRegistry,
        max_tool_failures: int = 6,
    ):
        """
        Initialize handler.

        Args:
            state: Execution state for tracking failures
            error_registry: Registry for getting recovery actions
            max_tool_failures: Maximum failures before forcing termination
        """
        self._state = state
        self._error_registry = error_registry
        self._max_tool_failures = max_tool_failures

    def is_no_match(self, action: ActionIR, result: ToolResult) -> bool:
        """
        Check if result is a "no match" situation.

        Args:
            action: The executed action
            result: The tool result

        Returns:
            True if this is a no-match situation
        """
        if action.name not in self.NO_MATCH_TOOLS:
            return False

        output = str(result.output).lower() if result.output else ""
        return "no match" in output or "matched nothing" in output

    def handle(self, action: ActionIR, result: ToolResult) -> Optional[ToolResult]:
        """
        Handle a "no match" result.

        Args:
            action: The executed action
            result: The tool result

        Returns:
            Override ToolResult if needed, None to use original
        """
        if not self.is_no_match(action, result):
            # Has results, clear failure count
            self._error_registry.clear_failure(action.name, action.args)
            self._state.tool_failure_counts[action.name] = 0
            return None

        # Record tool-level failure
        self._state.tool_failure_counts[action.name] = (
            self._state.tool_failure_counts.get(action.name, 0) + 1
        )
        tool_failures = self._state.tool_failure_counts[action.name]

        logger.debug(
            f"🔧 {action.name} no-match count: {tool_failures}/{self._max_tool_failures}"
        )

        # Force termination if too many failures
        if tool_failures >= self._max_tool_failures:
            return self._create_hard_stop(action, tool_failures)

        return None

    def _create_hard_stop(self, action: ActionIR, failures: int) -> ToolResult:
        """Create a hard stop result to force LLM to change strategy."""
        logger.warning(
            f"🛑 {action.name} failed {failures} times, forcing termination"
        )

        return ToolResult(
            status="ERROR",
            output=(
                f"[HARD STOP] {action.name} has returned no matches {failures} times.\n\n"
                f"The files you're searching for DO NOT EXIST in this workspace.\n"
                f"Stop searching and work with what's available.\n\n"
                f"REQUIRED ACTION: Stop now and report:\n"
                f"1. What you were trying to find\n"
                f"2. What you actually found/accomplished\n"
                f"3. Any obstacles encountered\n\n"
                f"DO NOT call {action.name} again."
            ),
            is_final=False,
            fault=Fault(
                domain="RUNTIME",
                code="EXCESSIVE_FAILURES",
                message=f"{action.name} returned no matches {failures} consecutive times",
                retryable=False,
            ),
        )
