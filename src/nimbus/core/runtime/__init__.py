"""Runtime module for DAG execution, replanning coordination, and agentic loops.

This module provides:

- AsyncRuntime: Parallel DAG execution with timeout/retry support
- ReplanCoordinator: Coordinates replanning with running tasks
- CancellationToken: Cooperative task cancellation
- CoordinatorConfig: Configuration for coordinator behavior
- AgenticRunner: Agentic loop runtime for tool-using agents
- AgenticConfig: Configuration for agentic loop
- AgenticEvent: Events emitted during agentic execution
- ToolRegistryExecutor: Adapter to use ToolRegistry with AgenticRunner

Example:
    ```python
    from nimbus.core.runtime import (
        AsyncRuntime,
        ReplanCoordinator,
        CancellationToken,
        CoordinatorConfig,
        AgenticRunner,
        AgenticConfig,
        ToolRegistryExecutor,
    )

    # DAG-based execution
    runtime = AsyncRuntime(skills={"search": search_skill})
    result = await runtime.execute_dag(dag)

    # Agentic loop execution
    executor = ToolRegistryExecutor(registry, workspace=Path.cwd())
    runner = AgenticRunner(llm_client, executor, AgenticConfig(max_iterations=20))
    async for event in runner.run("Fix the bug in auth.py"):
        print(event.type, event.data)
    ```
"""

from .executor import AsyncRuntime, SkillFunc
from .coordinator import ReplanCoordinator, CoordinatorConfig
from .cancellation import CancellationToken
from .agentic import AgenticRunner, AgenticConfig, AgenticEvent, ToolRegistryExecutor

__all__ = [
    # Main runtime
    "AsyncRuntime",
    "SkillFunc",
    # Coordination
    "ReplanCoordinator",
    "CoordinatorConfig",
    # Cancellation
    "CancellationToken",
    # Agentic loop
    "AgenticRunner",
    "AgenticConfig",
    "AgenticEvent",
    "ToolRegistryExecutor",
]
