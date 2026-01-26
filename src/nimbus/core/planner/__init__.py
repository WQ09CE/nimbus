"""Planner package for DAG-based task planning.

This package provides a modular planning pipeline with:
- Context analysis for detecting context-dependent questions
- Rule-based planning for fast, deterministic patterns
- LLM-based planning for flexible, context-aware planning
- DAG validation and repair utilities
- Pipeline orchestration for multi-stage planning

Legacy planners (SimplePlanner, DAGPlanner) are deprecated.
Use PlannerPipeline instead. AdaptivePlanner is still used by coordinators.

Example (Recommended - Pipeline):
    ```python
    from nimbus.core.planner import (
        PlannerPipeline,
        PipelineConfig,
        PlanningMode,
    )

    # Create a default pipeline with context analysis
    pipeline = PlannerPipeline.default(llm_client)

    # Or customize the pipeline
    config = PipelineConfig(
        enable_context_analyzer=True,
        planning_mode=PlanningMode.HYBRID,
        skill_whitelist={"search", "summarize", "synthesize"},
        max_llm_tasks=15,
    )
    pipeline = PlannerPipeline.default(llm_client, config)

    # Execute planning
    dag = await pipeline.plan(
        goal="What is this project's name?",
        context="Previous: Read pyproject.toml... [project] name='nimbus'",
        available_skills={"search", "summarize", "synthesize"},
    )
    ```

Example (Legacy - Deprecated):
    ```python
    # DEPRECATED: Use PlannerPipeline instead
    from nimbus.core.planner import DAGPlanner

    planner = DAGPlanner(llm_client)
    dag = await planner.create_plan(goal, context, skills)
    ```
"""

import warnings

# Legacy planners (backward compatibility - deprecated except AdaptivePlanner)
from .legacy import (
    SimplePlanner,  # DEPRECATED
    DAGPlanner,     # DEPRECATED
    AdaptivePlanner,  # Still used by coordinators
    ReplanRequest,
    ReplanningStrategy,
    LLMClient,
)

# New pipeline components
from .protocol import (
    PlanningMode,
    PlanningContext,
    PlannerStage,
    FailedTaskInfo,
)

from .validator import (
    ValidationResult,
    DAGValidator,
)

from .context_analyzer import (
    ContextAnalyzer,
    CONTEXT_REFERENCE_PATTERNS,
)

from .rule_planner import (
    RulePlanner,
    PLANNING_RULES,
)

from .llm_enhancer import (
    LLMEnhancer,
)

from .router import (
    TaskComplexity,
    RoutingResult,
    TaskRouter,
    TaskRouterStage,
)

from .tool_planner import (
    ToolDAGPlanner,
    ToolPlannerStage,
    READONLY_TOOLS,
    TOOL_DAG_PROMPT,
    get_prompt_size,
    validate_prompt_size,
)

from .pipeline import (
    PipelineConfig,
    PlannerPipeline,
)


# Deprecation warnings for legacy planners
def _warn_deprecated(name: str):
    """Issue deprecation warning for legacy planners."""
    warnings.warn(
        f"{name} is deprecated. Use PlannerPipeline instead. "
        "See nimbus.core.planner module documentation for migration guide.",
        DeprecationWarning,
        stacklevel=3,
    )


# Wrapper to warn on SimplePlanner usage
_OriginalSimplePlanner = SimplePlanner


class SimplePlanner(_OriginalSimplePlanner):
    """DEPRECATED: Use PlannerPipeline instead."""

    def __init__(self, *args, **kwargs):
        _warn_deprecated("SimplePlanner")
        super().__init__(*args, **kwargs)


# Wrapper to warn on DAGPlanner usage
_OriginalDAGPlanner = DAGPlanner


class DAGPlanner(_OriginalDAGPlanner):
    """DEPRECATED: Use PlannerPipeline instead."""

    def __init__(self, *args, **kwargs):
        _warn_deprecated("DAGPlanner")
        super().__init__(*args, **kwargs)


__all__ = [
    # Legacy (backward compatibility - deprecated)
    "SimplePlanner",  # DEPRECATED
    "DAGPlanner",     # DEPRECATED
    "AdaptivePlanner",  # Still used for re-planning
    "ReplanRequest",
    "ReplanningStrategy",
    "LLMClient",
    # Protocol
    "PlanningMode",
    "PlanningContext",
    "PlannerStage",
    "FailedTaskInfo",
    # Validator
    "ValidationResult",
    "DAGValidator",
    # Context Analyzer
    "ContextAnalyzer",
    "CONTEXT_REFERENCE_PATTERNS",
    # Rule Planner
    "RulePlanner",
    "PLANNING_RULES",
    # LLM Enhancer
    "LLMEnhancer",
    # Task Router (ADR-010)
    "TaskComplexity",
    "RoutingResult",
    "TaskRouter",
    "TaskRouterStage",
    # Tool Planner (lightweight read-only planner)
    "ToolDAGPlanner",
    "ToolPlannerStage",
    "READONLY_TOOLS",
    "TOOL_DAG_PROMPT",
    "get_prompt_size",
    "validate_prompt_size",
    # Pipeline
    "PipelineConfig",
    "PlannerPipeline",
]
