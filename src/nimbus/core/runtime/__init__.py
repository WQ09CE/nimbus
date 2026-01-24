"""Runtime module for DAG execution and replanning coordination.

This module provides:

- AsyncRuntime: Parallel DAG execution with timeout/retry support
- ReplanCoordinator: Coordinates replanning with running tasks
- CancellationToken: Cooperative task cancellation
- CoordinatorConfig: Configuration for coordinator behavior

Example:
    ```python
    from nimbus.core.runtime import (
        AsyncRuntime,
        ReplanCoordinator,
        CancellationToken,
        CoordinatorConfig,
    )

    # Basic usage
    runtime = AsyncRuntime(skills={"search": search_skill})
    result = await runtime.execute_dag(dag)

    # With replanning support
    coordinator = ReplanCoordinator(
        config=CoordinatorConfig(cancel_timeout=10.0)
    )
    runtime = AsyncRuntime(
        skills={"search": search_skill},
        coordinator=coordinator,
    )
    ```
"""

from .executor import AsyncRuntime, SkillFunc
from .coordinator import ReplanCoordinator, CoordinatorConfig
from .cancellation import CancellationToken

__all__ = [
    # Main runtime
    "AsyncRuntime",
    "SkillFunc",
    # Coordination
    "ReplanCoordinator",
    "CoordinatorConfig",
    # Cancellation
    "CancellationToken",
]
