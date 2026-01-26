"""Planning pipeline orchestration.

This module provides the PlannerPipeline class that orchestrates
multi-stage planning through a series of PlannerStage instances.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Set

if TYPE_CHECKING:
    from ..context import ContextStack

from ..logging import get_logger
from ..types import TaskDAG
from .protocol import FailedTaskInfo, PlannerStage, PlanningContext, PlanningMode
from .validator import DAGValidator

logger = get_logger("planner.pipeline")


@dataclass
class PipelineConfig:
    """Configuration for the planning pipeline.

    Attributes:
        enable_context_analyzer: Whether to include context analysis.
        enable_rule_planner: Whether to include rule-based planning.
        enable_llm_enhancer: Whether to include LLM-based planning.
        enable_validator: Whether to validate the final DAG.
        early_exit: Whether to exit early when a stage sets the flag.
        planning_mode: Default planning mode.
        skill_whitelist: Set of allowed skills.
        max_llm_tasks: Maximum tasks the LLM can generate.
        max_depth: Maximum DAG depth.
        enable_router: Whether to enable TaskRouter for complexity-based routing.
        use_tool_planner: Whether to use ToolDAGPlanner for MODERATE tasks.
    """
    enable_context_analyzer: bool = True
    enable_rule_planner: bool = True
    enable_llm_enhancer: bool = True
    enable_validator: bool = True
    early_exit: bool = True
    planning_mode: PlanningMode = PlanningMode.HYBRID
    skill_whitelist: Set[str] = field(default_factory=set)
    max_llm_tasks: int = 20
    max_depth: int = 10
    # New router mode options
    enable_router: bool = False
    use_tool_planner: bool = False


class LLMClient(Protocol):
    """Protocol for LLM client interface."""
    async def complete(self, prompt: str) -> str: ...


class PlannerPipeline:
    """Orchestrates multi-stage planning.

    The pipeline executes a series of PlannerStage instances in order,
    passing a PlanningContext through each stage. Stages can modify
    the context, add intermediate results, or set the final DAG.

    Example:
        ```python
        # Create pipeline with default stages
        pipeline = PlannerPipeline.default(llm_client)

        # Execute planning
        dag = await pipeline.plan(
            goal="搜索 AI 趋势，然后总结",
            context="用户是技术从业者",
            available_skills={"search", "summarize", "synthesize"},
        )
        ```
    """

    def __init__(
        self,
        stages: List[PlannerStage],
        config: Optional[PipelineConfig] = None,
    ):
        """Initialize the planning pipeline.

        Args:
            stages: List of PlannerStage instances to execute.
            config: Pipeline configuration.
        """
        self.stages = stages
        self.config = config or PipelineConfig()
        self._validator = DAGValidator(
            skill_whitelist=self.config.skill_whitelist,
            max_tasks=self.config.max_llm_tasks,
            max_depth=self.config.max_depth,
        )

    async def plan(
        self,
        goal: str,
        context: str,
        available_skills: Set[str],
    ) -> TaskDAG:
        """Execute the full planning pipeline.

        Args:
            goal: User's goal/request.
            context: Conversation context.
            available_skills: Set of available skill names.

        Returns:
            The final TaskDAG to execute.
        """
        # Create initial context
        ctx = PlanningContext(
            goal=goal,
            conversation_context=context,
            available_skills=available_skills,
            skill_whitelist=self.config.skill_whitelist or available_skills,
            planning_mode=self.config.planning_mode,
        )

        logger.info(f"Starting planning pipeline for: {goal[:50]}...")

        # Execute each stage
        for stage in self.stages:
            try:
                logger.debug(f"Executing stage: {stage.name}")
                ctx = await stage.process(ctx)

                # Check for early exit
                if self.config.early_exit and ctx.early_exit:
                    logger.debug(f"Early exit after stage: {stage.name}")
                    break

                # Check if we have a final DAG
                if ctx.final_dag:
                    logger.debug(f"Final DAG set by stage: {stage.name}")
                    if not self.config.enable_validator:
                        break

            except Exception as e:
                logger.error(f"Stage {stage.name} failed: {e}")
                ctx.add_error(f"Stage {stage.name} failed: {str(e)}")
                # Continue to next stage or fallback

        # Final validation if enabled
        if self.config.enable_validator and ctx.final_dag:
            self._validator.skill_whitelist = ctx.skill_whitelist
            result = self._validator.validate(ctx.final_dag)
            if not result.valid:
                if result.repaired_dag:
                    ctx.final_dag = result.repaired_dag
                    ctx.warnings.extend(result.warnings)
                    logger.info("Final DAG was repaired")
                else:
                    ctx.errors.extend(result.errors)
                    logger.warning(f"Final DAG validation failed: {result.errors}")
                    # Create fallback
                    ctx.final_dag = self._create_fallback(ctx)

        # Ensure we have a DAG
        if not ctx.final_dag:
            ctx.final_dag = ctx.get_dag() or self._create_fallback(ctx)

        # Log summary
        logger.info(
            f"Pipeline complete: {len(ctx.final_dag.nodes)} tasks, "
            f"{len(ctx.errors)} errors, {len(ctx.warnings)} warnings"
        )

        return ctx.final_dag

    def _create_fallback(self, ctx: PlanningContext) -> TaskDAG:
        """Create a fallback DAG when planning fails.

        Args:
            ctx: Planning context.

        Returns:
            Simple chat DAG as fallback.
        """
        logger.warning("Creating fallback DAG")
        return TaskDAG.create_simple("synthesize", {"message": ctx.goal})

    async def plan_with_context(
        self,
        ctx: PlanningContext,
    ) -> PlanningContext:
        """Execute pipeline with an existing context.

        This method allows for more control over the planning process
        by providing a pre-configured context.

        Args:
            ctx: Pre-configured planning context.

        Returns:
            Updated planning context.
        """
        logger.info(f"Starting planning with context for: {ctx.goal[:50]}...")

        for stage in self.stages:
            try:
                logger.debug(f"Executing stage: {stage.name}")
                ctx = await stage.process(ctx)

                if self.config.early_exit and ctx.early_exit:
                    break

            except Exception as e:
                logger.error(f"Stage {stage.name} failed: {e}")
                ctx.add_error(f"Stage {stage.name} failed: {str(e)}")

        return ctx

    @classmethod
    def default(
        cls,
        llm_client: LLMClient,
        config: Optional[PipelineConfig] = None,
        context_stack: Optional["ContextStack"] = None,
    ) -> "PlannerPipeline":
        """Create a default pipeline with all stages.

        Creates a pipeline with:
        1. ContextAnalyzer - analyze context dependencies
        2. RulePlanner - fast rule-based matching
        3. LLMEnhancer - LLM-based planning/enhancement
        4. Validation via pipeline config

        If config.enable_router is True, uses the router-based architecture:
        1. TaskRouter - classify task complexity (SIMPLE/MODERATE/COMPLEX)
        2. ToolDAGPlanner - generate DAG for MODERATE tasks (read-only tools)

        Args:
            llm_client: LLM client for LLMEnhancer or router.
            config: Optional pipeline configuration.
            context_stack: Optional ContextStack for focused context views.

        Returns:
            Configured PlannerPipeline instance.
        """
        config = config or PipelineConfig()

        # If router mode is enabled, delegate to with_router
        if config.enable_router:
            return cls.with_router(llm_client, config, context_stack)

        from .context_analyzer import ContextAnalyzer
        from .llm_enhancer import LLMEnhancer
        from .rule_planner import RulePlanner

        stages: List[PlannerStage] = []

        if config.enable_context_analyzer:
            stages.append(ContextAnalyzer())

        if config.enable_rule_planner:
            stages.append(RulePlanner())

        if config.enable_llm_enhancer:
            validator = DAGValidator(
                skill_whitelist=config.skill_whitelist,
                max_tasks=config.max_llm_tasks,
                max_depth=config.max_depth,
            )
            stages.append(LLMEnhancer(llm_client, validator))

        return cls(stages, config)

    @classmethod
    def rule_only(cls, config: Optional[PipelineConfig] = None) -> "PlannerPipeline":
        """Create a rule-only pipeline (no LLM).

        Args:
            config: Optional pipeline configuration.

        Returns:
            Pipeline with only rule-based planning.
        """
        from .rule_planner import RulePlanner

        config = config or PipelineConfig()
        config.planning_mode = PlanningMode.RULE_ONLY
        config.enable_llm_enhancer = False

        return cls([RulePlanner()], config)

    @classmethod
    def llm_only(
        cls,
        llm_client: LLMClient,
        config: Optional[PipelineConfig] = None,
    ) -> "PlannerPipeline":
        """Create an LLM-only pipeline (no rules).

        Args:
            llm_client: LLM client.
            config: Optional pipeline configuration.

        Returns:
            Pipeline with only LLM-based planning.
        """
        from .llm_enhancer import LLMEnhancer

        config = config or PipelineConfig()
        config.planning_mode = PlanningMode.LLM_FULL
        config.enable_rule_planner = False

        validator = DAGValidator(
            skill_whitelist=config.skill_whitelist,
            max_tasks=config.max_llm_tasks,
            max_depth=config.max_depth,
        )

        return cls([LLMEnhancer(llm_client, validator)], config)

    @classmethod
    def with_router(
        cls,
        llm_client: LLMClient,
        config: Optional[PipelineConfig] = None,
        context_stack: Optional["ContextStack"] = None,
    ) -> "PlannerPipeline":
        """Create a pipeline with TaskRouter for complexity-based routing.

        This pipeline uses a lightweight router (400 char prompt) to classify
        task complexity:
        - SIMPLE: Direct synthesize response (early exit)
        - MODERATE: ToolDAGPlanner with Read/Glob/Grep only
        - COMPLEX: Subagent delegation (early exit)

        Architecture:
            ```
            User Goal
                |
                v
            [try_rule_match] ---- Fast path (skip all if matched)
                | (miss)
                v
            [TaskRouter] ---- 400 char prompt
                |
                +-- SIMPLE --> Direct synthesize DAG (early_exit)
                |
                +-- MODERATE --> [ToolDAGPlanner] (compact prompt, read-only tools)
                |
                +-- COMPLEX --> Subagent DAG (early_exit)
            ```

        Args:
            llm_client: LLM client for router and tool planner.
            config: Optional pipeline configuration.
            context_stack: Optional ContextStack for focused context views.

        Returns:
            Pipeline configured for router mode.

        Example:
            ```python
            pipeline = PlannerPipeline.with_router(llm_client)
            dag = await pipeline.plan("Read main.py", "", {"Read", "synthesize"})
            ```
        """
        from .router import TaskRouter, TaskRouterStage
        from .tool_planner import ToolPlannerStage

        config = config or PipelineConfig()
        config.enable_router = True
        config.use_tool_planner = True
        config.planning_mode = PlanningMode.HYBRID

        stages: List[PlannerStage] = []

        # 1. TaskRouter stage (routes SIMPLE/COMPLEX to early exit)
        router = TaskRouter(llm_client, enable_llm=True)
        stages.append(TaskRouterStage(router))

        # 2. ToolPlannerStage (only for MODERATE tasks)
        stages.append(ToolPlannerStage(llm_client, context_stack))

        return cls(stages, config)

    def add_stage(self, stage: PlannerStage, index: Optional[int] = None) -> None:
        """Add a stage to the pipeline.

        Args:
            stage: The stage to add.
            index: Position to insert at. If None, appends to end.
        """
        if index is None:
            self.stages.append(stage)
        else:
            self.stages.insert(index, stage)

    def remove_stage(self, name: str) -> bool:
        """Remove a stage by name.

        Args:
            name: Name of the stage to remove.

        Returns:
            True if stage was found and removed.
        """
        original_count = len(self.stages)
        self.stages = [s for s in self.stages if s.name != name]
        return len(self.stages) < original_count

    async def try_rule_match(
        self,
        goal: str,
        available_skills: Set[str],
    ) -> Optional[TaskDAG]:
        """Try fast rule matching without context construction.

        This method only executes the RulePlanner stage to check if
        the goal matches any predefined patterns. If matched, returns
        the DAG immediately without needing conversation context.

        This is an optimization to skip expensive context construction
        for simple, rule-matched requests.

        Args:
            goal: User's goal/request.
            available_skills: Set of available skill names.

        Returns:
            TaskDAG if rule matched and early_exit, None otherwise.
        """
        # Find rule planner stage
        rule_planner = None
        for stage in self.stages:
            if stage.name == "rule_planner":
                rule_planner = stage
                break

        if rule_planner is None:
            return None

        # Create minimal context (no conversation context)
        ctx = PlanningContext(
            goal=goal,
            conversation_context="",  # Skip context
            available_skills=available_skills,
            skill_whitelist=self.config.skill_whitelist or available_skills,
            planning_mode=self.config.planning_mode,
        )

        # Execute only rule planner
        try:
            ctx = await rule_planner.process(ctx)

            # If rule matched with early exit, return the DAG
            if ctx.early_exit and ctx.final_dag:
                logger.debug(f"Fast rule match: {ctx.metadata.get('matched_rule', 'unknown')}")
                return ctx.final_dag

        except Exception as e:
            logger.debug(f"Rule match failed: {e}")

        return None

    async def replan(
        self,
        goal: str,
        context: str,
        available_skills: Set[str],
        failed_tasks: List[FailedTaskInfo],
        completed_task_ids: Optional[Set[str]] = None,
        completed_task_results: Optional[Dict[str, Any]] = None,
        replan_attempt: int = 1,
    ) -> TaskDAG:
        """Replan based on failed task information.

        This method creates a new plan that accounts for previous failures.
        It skips rule-based planning and goes directly to LLM enhancement
        with additional context about what went wrong.

        Args:
            goal: User's original goal/request.
            context: Conversation context.
            available_skills: Set of available skill names.
            failed_tasks: List of FailedTaskInfo from previous execution.
            completed_task_ids: Set of task IDs that completed successfully.
            completed_task_results: Dict mapping task IDs to their results.
            replan_attempt: Current replan attempt number (1-based).

        Returns:
            New TaskDAG to execute.
        """
        logger.info(
            f"Replanning (attempt {replan_attempt}) for: {goal[:50]}... "
            f"with {len(failed_tasks)} failed tasks"
        )

        # Create replanning context
        ctx = PlanningContext(
            goal=goal,
            conversation_context=context,
            available_skills=available_skills,
            skill_whitelist=self.config.skill_whitelist or available_skills,
            planning_mode=PlanningMode.LLM_FULL,  # Force LLM for replanning
            is_replan=True,
            failed_tasks=failed_tasks,
            replan_attempt=replan_attempt,
            completed_task_ids=completed_task_ids or set(),
            completed_task_results=completed_task_results or {},
        )

        # Skip rule planner for replanning - go directly to LLM
        for stage in self.stages:
            # Only run LLM enhancer for replanning
            if stage.name != "llm_enhancer":
                continue

            try:
                logger.debug(f"Replan: executing stage {stage.name}")
                ctx = await stage.process(ctx)

                if ctx.final_dag:
                    logger.debug(f"Replan: DAG generated with {len(ctx.final_dag.nodes)} tasks")
                    break

            except Exception as e:
                logger.error(f"Replan stage {stage.name} failed: {e}")
                ctx.add_error(f"Replan stage {stage.name} failed: {str(e)}")

        # Validation
        if self.config.enable_validator and ctx.final_dag:
            self._validator.skill_whitelist = ctx.skill_whitelist
            result = self._validator.validate(ctx.final_dag)
            if not result.valid:
                if result.repaired_dag:
                    ctx.final_dag = result.repaired_dag
                    ctx.warnings.extend(result.warnings)
                    logger.info("Replan DAG was repaired")
                else:
                    ctx.errors.extend(result.errors)
                    logger.warning(f"Replan DAG validation failed: {result.errors}")
                    ctx.final_dag = self._create_fallback(ctx)

        # Ensure we have a DAG
        if not ctx.final_dag:
            ctx.final_dag = ctx.get_dag() or self._create_fallback(ctx)

        logger.info(
            f"Replan complete: {len(ctx.final_dag.nodes)} tasks, "
            f"{len(ctx.errors)} errors"
        )

        return ctx.final_dag
