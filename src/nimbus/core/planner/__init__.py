"""Planner package for DAG-based task planning.

This package provides a modular planning pipeline with:
- Legacy planners (SimplePlanner, DAGPlanner, AdaptivePlanner) for backward compatibility
- Rule-based planning for fast, deterministic patterns
- LLM-based planning for flexible, context-aware planning
- DAG validation and repair utilities
- Pipeline orchestration for multi-stage planning

Example (Legacy):
    ```python
    from nimbus.core.planner import DAGPlanner, AdaptivePlanner

    # Use legacy DAGPlanner
    planner = DAGPlanner(llm_client)
    dag = await planner.create_plan(goal, context, skills)
    ```

Example (New Pipeline):
    ```python
    from nimbus.core.planner import (
        PlannerPipeline,
        PipelineConfig,
        PlanningMode,
        DAGValidator,
    )

    # Create a default pipeline
    pipeline = PlannerPipeline.default(llm_client)

    # Or customize the pipeline
    config = PipelineConfig(
        planning_mode=PlanningMode.HYBRID,
        skill_whitelist={"search", "summarize", "chat"},
        max_llm_tasks=15,
    )
    pipeline = PlannerPipeline.default(llm_client, config)

    # Execute planning
    dag = await pipeline.plan(
        goal="搜索 Python 教程并总结",
        context="用户是初学者",
        available_skills={"search", "summarize", "chat"},
    )
    ```
"""

# Legacy planners (backward compatibility)
from .legacy import (
    SimplePlanner,
    DAGPlanner,
    AdaptivePlanner,
    ReplanRequest,
    ReplanningStrategy,
    LLMClient,
)

# New pipeline components
from .protocol import (
    PlanningMode,
    PlanningContext,
    PlannerStage,
)

from .validator import (
    ValidationResult,
    DAGValidator,
)

from .rule_planner import (
    RulePlanner,
    PLANNING_RULES,
)

from .llm_enhancer import (
    LLMEnhancer,
)

from .pipeline import (
    PipelineConfig,
    PlannerPipeline,
)

__all__ = [
    # Legacy (backward compatibility)
    "SimplePlanner",
    "DAGPlanner",
    "AdaptivePlanner",
    "ReplanRequest",
    "ReplanningStrategy",
    "LLMClient",
    # Protocol
    "PlanningMode",
    "PlanningContext",
    "PlannerStage",
    # Validator
    "ValidationResult",
    "DAGValidator",
    # Rule Planner
    "RulePlanner",
    "PLANNING_RULES",
    # LLM Enhancer
    "LLMEnhancer",
    # Pipeline
    "PipelineConfig",
    "PlannerPipeline",
]
