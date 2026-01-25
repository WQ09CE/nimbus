"""Subagent DAG Orchestration Module.

This module provides task-level DAG orchestration using subagents,
as opposed to the tool-level DAG in nimbus.core.runtime.

Key components:
- SubagentType: Enum of available subagent types (eye, body, mind, etc.)
- SubagentNode: A node representing a subagent task in the DAG
- SubagentDAG: DAG of subagent tasks with dependencies
- TaskPlanner: Plans subagent decomposition from user goals
- SubagentRuntime: Executes SubagentDAG with parallel execution
- SubagentReplanCoordinator: Coordinates replanning on failure

Example:
    >>> from nimbus.core.task import (
    ...     TaskPlanner,
    ...     SubagentRuntime,
    ...     SubagentType,
    ...     SubagentDAG,
    ... )
    >>>
    >>> # Plan subagent tasks
    >>> planner = TaskPlanner(llm_client)
    >>> dag = await planner.plan("Implement a caching layer")
    >>>
    >>> # Execute with runtime
    >>> runtime = SubagentRuntime(llm_client, tool_registry, workspace)
    >>> result = await runtime.execute(dag)
    >>> print(result.final_summary)
"""

from .types import (
    SubagentType,
    SubagentStatus,
    SubagentNode,
    SubagentDAG,
    SubagentResult,
    SubagentExecutionResult,
    SubagentExecutionStats,
    SubagentReplanRecord,
    SUBAGENT_TOOLS,
)
from .planner import TaskPlanner, TASK_PATTERNS
from .runtime import SubagentRuntime, SubagentRuntimeConfig
from .coordinator import SubagentReplanCoordinator

__all__ = [
    # Types
    "SubagentType",
    "SubagentStatus",
    "SubagentNode",
    "SubagentDAG",
    "SubagentResult",
    "SubagentExecutionResult",
    "SubagentExecutionStats",
    "SubagentReplanRecord",
    "SUBAGENT_TOOLS",
    # Planner
    "TaskPlanner",
    "TASK_PATTERNS",
    # Runtime
    "SubagentRuntime",
    "SubagentRuntimeConfig",
    # Coordinator
    "SubagentReplanCoordinator",
]
