"""Planning stage protocol and types.

This module defines the protocol for planning stages and the context
passed through the planning pipeline.
"""

from typing import Protocol, Optional, Set, List, Any
from dataclasses import dataclass, field
from enum import Enum

from ..types import TaskDAG


class PlanningMode(str, Enum):
    """Mode of planning to use.

    Attributes:
        RULE_ONLY: Use only rule-based planning (fast, deterministic).
        LLM_FULL: Use only LLM-based planning (flexible, slower).
        HYBRID: Use rules first, then LLM enhancement if needed.
    """
    RULE_ONLY = "rule_only"
    LLM_FULL = "llm_full"
    HYBRID = "hybrid"


@dataclass
class PlanningContext:
    """Context passed through the planning pipeline.

    This dataclass carries all the information needed for planning,
    as well as intermediate results from each planning stage.

    Attributes:
        goal: The user's original goal/request.
        conversation_context: Conversation history or additional context.
        available_skills: Set of skill names that can be used.
        skill_whitelist: Set of skills that must be validated against.
        rule_dag: DAG produced by rule-based planning (if any).
        llm_dag: DAG produced by LLM-based planning (if any).
        final_dag: The final DAG to be executed.
        planning_mode: The planning mode being used.
        errors: List of errors encountered during planning.
        warnings: List of warnings generated during planning.
        metadata: Additional metadata from planning stages.
        early_exit: Whether to exit early (skip remaining stages).
    """
    goal: str
    conversation_context: str
    available_skills: Set[str]
    skill_whitelist: Set[str] = field(default_factory=set)

    # Intermediate results
    rule_dag: Optional[TaskDAG] = None
    llm_dag: Optional[TaskDAG] = None
    final_dag: Optional[TaskDAG] = None

    # Metadata
    planning_mode: PlanningMode = PlanningMode.LLM_FULL
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    # Control flow
    early_exit: bool = False

    def add_error(self, error: str) -> None:
        """Add an error message."""
        self.errors.append(error)

    def add_warning(self, warning: str) -> None:
        """Add a warning message."""
        self.warnings.append(warning)

    def has_errors(self) -> bool:
        """Check if there are any errors."""
        return len(self.errors) > 0

    def is_complete(self) -> bool:
        """Check if planning produced a final DAG."""
        return self.final_dag is not None

    def get_dag(self) -> Optional[TaskDAG]:
        """Get the best available DAG (final > llm > rule)."""
        return self.final_dag or self.llm_dag or self.rule_dag


class PlannerStage(Protocol):
    """Protocol for a stage in the planning pipeline.

    Each stage processes the PlanningContext and can modify it,
    adding intermediate results or the final DAG.

    Example:
        ```python
        class MyStage:
            @property
            def name(self) -> str:
                return "my_stage"

            async def process(self, ctx: PlanningContext) -> PlanningContext:
                # Process and modify ctx
                ctx.metadata["my_stage_ran"] = True
                return ctx
        ```
    """

    @property
    def name(self) -> str:
        """Stage name for logging/tracing."""
        ...

    async def process(self, ctx: PlanningContext) -> PlanningContext:
        """Process context and return updated context.

        Args:
            ctx: The current planning context.

        Returns:
            Updated planning context (can be the same object).
        """
        ...
