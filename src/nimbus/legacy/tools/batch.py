"""Batch tool for parallel execution of multiple tool calls.

This module provides a tool for executing multiple tool calls in parallel,
with concurrency limits, timeout control, and error isolation.

Example:
    >>> from nimbus.tools import batch_tool
    >>> result = await batch_tool(
    ...     tool_calls=[
    ...         {"name": "Read", "params": {"file_path": "/project/main.py"}},
    ...         {"name": "Grep", "params": {"pattern": "def main", "path": "src"}},
    ...     ],
    ...     tool_registry=registry,
    ... )
    >>> print(result)
    {"results": [...], "summary": "Completed 2/2 tool calls"}
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

from .base import ToolExecutionError, ToolParameter, ToolRegistry, tool

# Maximum concurrent tool calls allowed
MAX_CONCURRENT_CALLS = 25

# Default timeout in seconds for each tool call
DEFAULT_TIMEOUT = 30.0


class BatchExecutionError(Exception):
    """Exception raised when batch execution encounters a critical error.

    Attributes:
        message: Error description.
        partial_results: Results collected before the error occurred.
    """

    def __init__(
        self,
        message: str,
        partial_results: Optional[List[Dict[str, Any]]] = None,
    ):
        self.message = message
        self.partial_results = partial_results or []
        super().__init__(message)


async def _execute_single_tool(
    index: int,
    tool_call: Dict[str, Any],
    registry: ToolRegistry,
    timeout: float,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a single tool call with timeout and error handling.

    Args:
        index: Index of the tool call in the batch.
        tool_call: Tool call specification with 'name' and 'params'.
        registry: Tool registry for executing tools.
        timeout: Maximum execution time in seconds.
        context: Additional context to pass to the tool.

    Returns:
        Result dictionary with index, name, status, and result/error.
    """
    # Validate tool_call structure first
    if not isinstance(tool_call, dict):
        return {
            "index": index,
            "name": "unknown",
            "status": "error",
            "error": f"Invalid tool_call format: expected dict, got {type(tool_call).__name__}",
        }

    name = tool_call.get("name", "unknown")

    if "name" not in tool_call:
        return {
            "index": index,
            "name": "unknown",
            "status": "error",
            "error": "Missing required field 'name' in tool_call",
        }

    params = tool_call.get("params", {})
    if not isinstance(params, dict):
        return {
            "index": index,
            "name": name,
            "status": "error",
            "error": f"Invalid params format: expected dict, got {type(params).__name__}",
        }

    # Check if tool exists
    if name not in registry:
        return {
            "index": index,
            "name": name,
            "status": "error",
            "error": f"Tool '{name}' not found in registry",
        }

    try:
        # Execute with timeout
        result = await asyncio.wait_for(
            registry.execute(name, params, **context),
            timeout=timeout,
        )

        # Convert result to string if needed for consistent output
        if isinstance(result, (dict, list)):
            result_str = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            result_str = str(result)

        return {
            "index": index,
            "name": name,
            "status": "success",
            "result": result_str,
        }

    except asyncio.TimeoutError:
        return {
            "index": index,
            "name": name,
            "status": "error",
            "error": f"Tool execution timed out after {timeout}s",
        }
    except ToolExecutionError as e:
        return {
            "index": index,
            "name": name,
            "status": "error",
            "error": str(e),
        }
    except Exception as e:
        return {
            "index": index,
            "name": name,
            "status": "error",
            "error": f"{type(e).__name__}: {str(e)}",
        }


@tool(
    name="Batch",
    description=(
        "Execute multiple tool calls in parallel. "
        "Supports up to 25 concurrent calls with timeout control and error isolation. "
        "Each tool call runs independently - a single failure does not affect others."
    ),
    parameters=[
        ToolParameter(
            name="tool_calls",
            type="array",
            description="List of tool calls to execute in parallel",
            required=True,
            items={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the tool to execute",
                    },
                    "params": {
                        "type": "object",
                        "description": "Parameters to pass to the tool",
                    },
                },
                "required": ["name"],
            },
        ),
        ToolParameter(
            name="timeout",
            type="number",
            description=f"Timeout in seconds for each tool call. Defaults to {DEFAULT_TIMEOUT}.",
            required=False,
            default=DEFAULT_TIMEOUT,
        ),
    ],
)
async def batch_tool(
    tool_calls: List[Dict[str, Any]],
    timeout: float = DEFAULT_TIMEOUT,
    tool_registry: Optional[ToolRegistry] = None,
    **context: Any,
) -> str:
    """Execute multiple tools in parallel.

    A batch execution tool that runs multiple tool calls concurrently,
    with configurable timeout, concurrency limits, and error isolation.

    Features:
        - Parallel execution with asyncio.gather
        - Maximum 25 concurrent calls
        - Configurable timeout per tool call
        - Error isolation: single failures don't affect others
        - Consistent JSON output format

    Args:
        tool_calls: List of tool calls, each with 'name' and optional 'params'.
        timeout: Maximum execution time per tool call in seconds.
        tool_registry: Registry containing the tools to execute.
                       If not provided, looks for it in context.
        **context: Additional context passed to each tool (workspace, etc.).

    Returns:
        JSON string with execution results:
        {
            "results": [
                {"index": 0, "name": "Read", "status": "success", "result": "..."},
                {"index": 1, "name": "Grep", "status": "error", "error": "..."}
            ],
            "summary": "Completed 1/2 tool calls"
        }

    Raises:
        ValueError: If tool_calls is empty or exceeds MAX_CONCURRENT_CALLS.
        BatchExecutionError: If registry is not available.

    Example:
        >>> result = await batch_tool(
        ...     tool_calls=[
        ...         {"name": "Read", "params": {"file_path": "/tmp/test.py"}},
        ...         {"name": "Glob", "params": {"pattern": "**/*.py"}},
        ...     ],
        ...     timeout=10.0,
        ...     tool_registry=registry,
        ... )
        >>> data = json.loads(result)
        >>> print(data["summary"])
        'Completed 2/2 tool calls'
    """
    # Validate input
    if not tool_calls:
        raise ValueError("tool_calls cannot be empty")

    if not isinstance(tool_calls, list):
        raise ValueError(
            f"tool_calls must be a list, got {type(tool_calls).__name__}"
        )

    if len(tool_calls) > MAX_CONCURRENT_CALLS:
        raise ValueError(
            f"Too many tool calls: {len(tool_calls)} exceeds limit of {MAX_CONCURRENT_CALLS}"
        )

    # Validate timeout
    if timeout <= 0:
        raise ValueError(f"timeout must be positive, got {timeout}")

    # Get registry from parameter or context
    registry = tool_registry
    if registry is None:
        registry = context.get("tool_registry")

    if registry is None:
        raise BatchExecutionError(
            "tool_registry not provided. Pass it as a parameter or in context."
        )

    if not isinstance(registry, ToolRegistry):
        raise BatchExecutionError(
            f"tool_registry must be a ToolRegistry instance, got {type(registry).__name__}"
        )

    # Create tasks for parallel execution
    tasks = [
        _execute_single_tool(
            index=i,
            tool_call=call,
            registry=registry,
            timeout=timeout,
            context=context,
        )
        for i, call in enumerate(tool_calls)
    ]

    # Execute all tasks in parallel with gather
    # return_exceptions=False because we handle exceptions in _execute_single_tool
    results = await asyncio.gather(*tasks, return_exceptions=False)

    # Sort results by index to maintain order
    results = sorted(results, key=lambda r: r["index"])

    # Calculate summary
    success_count = sum(1 for r in results if r["status"] == "success")
    total_count = len(results)
    summary = f"Completed {success_count}/{total_count} tool calls"

    # Build response
    response = {
        "results": results,
        "summary": summary,
    }

    return json.dumps(response, ensure_ascii=False, indent=2)
