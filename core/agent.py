"""NotebookAgent - Main orchestrator for OpenNotebook."""

import uuid
from typing import Any, AsyncIterator, Callable, Coroutine, Dict, List, Optional, Union

from .memory import SimpleMemory, TieredMemoryManager, MemoryConfig, PinnedItem
from .planner import SimplePlanner, DAGPlanner, LLMClient
from .executor import SimpleExecutor
from .runtime import AsyncRuntime
from .types import (
    NotebookResponse, Plan, TaskDAG, RuntimeConfig, ExecutionResult,
    Artifact, ArtifactType, TaskStatus,
)
from .logging import get_logger, setup_logging
from .tracing import get_tracer, Tracer


SkillFunc = Callable[..., Coroutine[Any, Any, Any]]


class NotebookAgent:
    """Main agent that orchestrates memory, planning, and execution.

    Supports two memory implementations:
    - "simple": Basic memory with conversation history (default, backward compatible)
    - "tiered": Advanced multi-tier memory with compression and checkpointing

    Supports two planner/executor modes:
    - "simple": SimplePlanner + SimpleExecutor (serial execution)
    - "dag": DAGPlanner + AsyncRuntime (parallel execution with dependencies)
    """

    def __init__(
        self,
        llm_client: LLMClient,
        system_prompt: str = "",
        memory_type: str = "simple",
        memory_config: Optional[MemoryConfig] = None,
        planner_type: str = "simple",
        runtime_config: Optional[RuntimeConfig] = None,
        enable_logging: bool = True,
        session_id: Optional[str] = None,
    ):
        """Initialize the notebook agent.

        Args:
            llm_client: LLM client with async complete(prompt) method.
            system_prompt: Optional system prompt for the agent.
            memory_type: Memory implementation ("simple" or "tiered").
            memory_config: Configuration for TieredMemoryManager.
            planner_type: Planner implementation ("simple" or "dag").
            runtime_config: Configuration for AsyncRuntime (DAG mode only).
            enable_logging: Enable structured logging and tracing.
            session_id: Session identifier for checkpointing.
        """
        self.llm_client = llm_client
        self.system_prompt = system_prompt
        self.session_id = session_id or str(uuid.uuid4())[:8]

        # Initialize memory based on type
        self._memory_type = memory_type
        if memory_type == "tiered":
            config = memory_config or MemoryConfig()
            self.memory = TieredMemoryManager(
                config=config,
                llm_client=llm_client,
                session_id=self.session_id
            )
        else:
            self.memory = SimpleMemory()

        # Initialize planner and executor/runtime based on type
        self._planner_type = planner_type
        if planner_type == "dag":
            self.planner = DAGPlanner(llm_client)
            self.runtime = AsyncRuntime(config=runtime_config or RuntimeConfig())
            self.executor = None  # Not used in DAG mode
        else:
            self.planner = SimplePlanner(llm_client)
            self.executor = SimpleExecutor()
            self.runtime = None  # Not used in simple mode

        # Logging and tracing
        self._enable_logging = enable_logging
        if enable_logging:
            setup_logging()
            self.logger = get_logger("agent")
            self.tracer: Optional[Tracer] = get_tracer("agent")
        else:
            self.logger = None
            self.tracer = None

        self._register_default_skills()

    def _register_default_skills(self) -> None:
        """Register built-in skills."""
        from ..skills.chat import create_chat_skill
        from ..skills.search import web_search
        from ..skills.summarize import summarize_text, extract_keywords

        chat_skill = create_chat_skill(self.llm_client)
        self.register_skill("chat", chat_skill)
        self.register_skill("search", web_search)
        self.register_skill("summarize", summarize_text)
        self.register_skill("keywords", extract_keywords)

    def register_skill(self, name: str, func: SkillFunc) -> None:
        """Register a custom skill.

        Args:
            name: Skill name for routing.
            func: Async function implementing the skill.
        """
        if self._planner_type == "dag":
            self.runtime.register_skill(name, func)
        else:
            self.executor.register_skill(name, func)

    def get_skill_names(self) -> set:
        """Get set of registered skill names."""
        if self._planner_type == "dag":
            return self.runtime.get_skill_names()
        else:
            return set(self.executor.get_skill_names())

    def on_file_upload(
        self, filename: str, file_type: str, summary: str
    ) -> None:
        """Handle file upload event.

        Args:
            filename: Name of the uploaded file.
            file_type: Type of file (e.g., "pdf", "csv").
            summary: Brief summary of file contents.
        """
        if self._memory_type == "tiered":
            item = PinnedItem(
                id=f"file:{filename}",
                type="file_meta",
                content=f"[{file_type}] {filename}: {summary}",
                priority=10
            )
            self.memory.pin(item)
        else:
            metadata = f"[{file_type}] {summary}"
            self.memory.pin(filename, metadata)

    def on_file_remove(self, filename: str) -> None:
        """Handle file removal event.

        Args:
            filename: Name of the file being removed.
        """
        if self._memory_type == "tiered":
            self.memory.unpin(f"file:{filename}")
        else:
            self.memory.unpin(filename)

    async def run(self, user_input: str) -> NotebookResponse:
        """Process user input and generate response.

        Args:
            user_input: User's message or command.

        Returns:
            NotebookResponse with text and optional artifacts.
        """
        # Start tracing if enabled
        if self.tracer:
            with self.tracer.start_span("agent.run", {"input_length": len(user_input)}) as span:
                return await self._run_internal(user_input, span)
        else:
            return await self._run_internal(user_input, None)

    async def _run_internal(self, user_input: str, span=None) -> NotebookResponse:
        """Internal run implementation with tracing support."""
        dag: Optional[TaskDAG] = None
        execution_result: Optional[ExecutionResult] = None

        try:
            if self.logger:
                self.logger.info("agent_run_start", user_input=user_input[:100])

            # Add user input to memory
            if self._memory_type == "tiered":
                await self.memory.add_turn("user", user_input)
            else:
                self.memory.add_turn("user", user_input)

            # Get context for planning
            if self.tracer and span:
                with self.tracer.start_span("memory.get_context") as ctx_span:
                    context = self.memory.get_context()
                    ctx_span.set_attribute("context_length", len(context))
            else:
                context = self.memory.get_context()

            available_skills = self.get_skill_names()

            # Create execution plan (DAG or simple Plan)
            if self.tracer and span:
                with self.tracer.start_span("planner.create_plan") as plan_span:
                    plan = await self.planner.create_plan(
                        goal=user_input,
                        context=context,
                        available_skills=available_skills,
                    )
                    if isinstance(plan, TaskDAG):
                        plan_span.set_attribute("plan_mode", "dag")
                        plan_span.set_attribute("task_count", len(plan.nodes))
                        dag = plan
                    else:
                        plan_span.set_attribute("plan_mode", plan.mode if hasattr(plan, 'mode') else "unknown")
            else:
                plan = await self.planner.create_plan(
                    goal=user_input,
                    context=context,
                    available_skills=available_skills,
                )
                if isinstance(plan, TaskDAG):
                    dag = plan

            # Handle empty DAG edge case
            if isinstance(plan, TaskDAG) and len(plan.nodes) == 0:
                if self.logger:
                    self.logger.warning("empty_dag_created", goal=user_input[:50])
                return NotebookResponse(
                    text="I understand your request but couldn't determine specific actions to take. Could you please provide more details?",
                    dag=dag,
                    memory_stats=self.get_memory_stats(),
                )

            # Execute plan (DAG or simple)
            if self.tracer and span:
                with self.tracer.start_span("runtime.execute") as exec_span:
                    response_text, execution_result = await self._execute_plan_with_result(
                        plan, user_input, context
                    )
                    exec_span.set_attribute("response_length", len(response_text))
            else:
                response_text, execution_result = await self._execute_plan_with_result(
                    plan, user_input, context
                )

            # Truncate overly long responses
            max_response_length = 50000  # ~12k tokens
            if len(response_text) > max_response_length:
                if self.logger:
                    self.logger.warning(
                        "response_truncated",
                        original_length=len(response_text),
                        truncated_length=max_response_length,
                    )
                response_text = response_text[:max_response_length] + "\n\n[Response truncated due to length]"

            # Add response to memory
            if self._memory_type == "tiered":
                await self.memory.add_turn("assistant", response_text)
            else:
                self.memory.add_turn("assistant", response_text)

            # Collect artifacts from execution
            artifacts = self._collect_artifacts(dag, execution_result)

            # Generate suggestions for follow-up
            suggestions = self._generate_suggestions(user_input, response_text, dag)

            if self.logger:
                duration_ms = span.duration_ms if span else 0
                self.logger.info(
                    "agent_run_complete",
                    duration_ms=duration_ms,
                    artifact_count=len(artifacts),
                    suggestion_count=len(suggestions),
                )

            return NotebookResponse(
                text=response_text,
                artifacts=artifacts,
                suggestions=suggestions,
                dag=dag,
                memory_stats=self.get_memory_stats(),
            )

        except Exception as e:
            if self.logger:
                self.logger.error("agent_run_failed", error=str(e))
            error_msg = f"An error occurred: {str(e)}"
            return NotebookResponse(
                text=error_msg,
                error=str(e),
                dag=dag,
                memory_stats=self.get_memory_stats(),
            )

    async def _execute_plan(
        self, plan: Union[Plan, TaskDAG], user_input: str, context: str
    ) -> str:
        """Execute plan and return response text.

        Args:
            plan: Execution plan (Plan for simple mode, TaskDAG for DAG mode).
            user_input: Original user input.
            context: Conversation context.

        Returns:
            Response text.
        """
        response_text, _ = await self._execute_plan_with_result(plan, user_input, context)
        return response_text

    async def _execute_plan_with_result(
        self, plan: Union[Plan, TaskDAG], user_input: str, context: str
    ) -> tuple[str, Optional[ExecutionResult]]:
        """Execute plan and return response text with execution result.

        Args:
            plan: Execution plan (Plan for simple mode, TaskDAG for DAG mode).
            user_input: Original user input.
            context: Conversation context.

        Returns:
            Tuple of (response_text, ExecutionResult or None).
        """
        # Handle TaskDAG (DAG mode)
        if isinstance(plan, TaskDAG):
            result: ExecutionResult = await self.runtime.execute_dag(plan)
            return self._extract_response_from_dag_result(result), result

        # Handle Plan (simple mode)
        if plan.is_direct():
            return plan.direct_response or "", None

        # Execute multi-step plan
        results = await self.executor.execute(plan)

        # Combine results into response
        if results:
            # For now, return the last result as the response
            last_result = results[-1]
            if isinstance(last_result, str):
                return last_result, None
            return (str(last_result) if last_result else "Task completed."), None

        return "I completed the task but have no output to show.", None

    def _extract_response_from_dag_result(self, result: ExecutionResult) -> str:
        """Extract response text from DAG execution result.

        Args:
            result: ExecutionResult from DAG execution.

        Returns:
            Response text to return to user.
        """
        if result.status == "failed":
            # All tasks failed - provide graceful degradation message
            error_msgs = [f"- {tid}: {err}" for tid, err in result.errors.items()]
            if self.logger:
                self.logger.error(
                    "all_tasks_failed",
                    error_count=len(result.errors),
                    errors=result.errors,
                )
            return (
                "I encountered errors while processing your request:\n"
                + "\n".join(error_msgs)
                + "\n\nPlease try rephrasing your request or breaking it into smaller parts."
            )

        if result.status == "partial":
            # Some tasks succeeded, some failed - graceful degradation
            response_parts = []
            for task_id, task_result in result.results.items():
                if task_result:
                    response_parts.append(str(task_result))

            if result.errors:
                error_summary = ", ".join(result.errors.keys())
                response_parts.append(
                    f"\n(Note: Some operations could not be completed: {error_summary})"
                )
                if self.logger:
                    self.logger.warning(
                        "partial_execution",
                        completed=result.stats.completed,
                        failed=result.stats.failed,
                        errors=result.errors,
                    )

            return "\n\n".join(response_parts) if response_parts else "Partial completion."

        # Success - combine all results
        if result.results:
            # Return the last non-None result, or combine them
            values = [v for v in result.results.values() if v is not None]
            if values:
                last_result = values[-1]
                if isinstance(last_result, str):
                    return last_result
                return str(last_result)

        return "Task completed."

    def _collect_artifacts(
        self,
        dag: Optional[TaskDAG],
        execution_result: Optional[ExecutionResult],
    ) -> List[Artifact]:
        """Collect artifacts from DAG execution results.

        Artifacts are extracted from task results that contain structured data.

        Args:
            dag: The executed TaskDAG (if any).
            execution_result: The execution result (if any).

        Returns:
            List of Artifact objects.
        """
        artifacts: List[Artifact] = []

        if dag is None or execution_result is None:
            return artifacts

        for task_id, node in dag.nodes.items():
            if node.status != TaskStatus.COMPLETED or node.result is None:
                continue

            result = node.result

            # Check if result contains artifact data
            if isinstance(result, dict):
                # Look for artifact markers in the result
                if "artifact_type" in result:
                    artifact = self._create_artifact_from_result(task_id, node.skill, result)
                    if artifact:
                        artifacts.append(artifact)
                        if self.logger:
                            self.logger.debug(
                                "artifact_collected",
                                artifact_id=artifact.id,
                                artifact_type=artifact.type.value,
                            )

                # Check for nested artifacts
                elif "artifacts" in result and isinstance(result["artifacts"], list):
                    for i, artifact_data in enumerate(result["artifacts"]):
                        artifact = self._create_artifact_from_result(
                            f"{task_id}_artifact_{i}",
                            node.skill,
                            artifact_data,
                        )
                        if artifact:
                            artifacts.append(artifact)

        return artifacts

    def _create_artifact_from_result(
        self,
        task_id: str,
        skill: str,
        result: Dict[str, Any],
    ) -> Optional[Artifact]:
        """Create an Artifact from a task result dictionary.

        Args:
            task_id: ID of the source task.
            skill: Name of the skill that produced the result.
            result: Result dictionary with artifact data.

        Returns:
            Artifact instance or None if not valid artifact data.
        """
        try:
            artifact_type_str = result.get("artifact_type", "")
            if not artifact_type_str:
                return None

            # Map string to ArtifactType
            try:
                artifact_type = ArtifactType(artifact_type_str)
            except ValueError:
                if self.logger:
                    self.logger.warning(
                        "unknown_artifact_type",
                        type=artifact_type_str,
                        task_id=task_id,
                    )
                return None

            artifact_id = result.get("id", f"artifact_{task_id}")
            title = result.get("title", f"Output from {skill}")
            data = result.get("data")

            return Artifact(
                id=artifact_id,
                type=artifact_type,
                title=title,
                data=data,
                mime_type=result.get("mime_type"),
                url=result.get("url"),
                metadata={
                    "source_task": task_id,
                    "source_skill": skill,
                    **result.get("metadata", {}),
                },
            )
        except Exception as e:
            if self.logger:
                self.logger.warning(
                    "artifact_creation_failed",
                    task_id=task_id,
                    error=str(e),
                )
            return None

    def _generate_suggestions(
        self,
        user_input: str,
        response_text: str,
        dag: Optional[TaskDAG],
    ) -> List[str]:
        """Generate follow-up action suggestions.

        Based on the user input, response, and executed tasks, suggest
        potential next steps the user might want to take.

        Args:
            user_input: Original user input.
            response_text: Generated response text.
            dag: Executed TaskDAG (if any).

        Returns:
            List of suggestion strings.
        """
        suggestions: List[str] = []

        # Analyze executed skills to suggest complementary actions
        if dag:
            executed_skills = {
                node.skill for node in dag.nodes.values()
                if node.status == TaskStatus.COMPLETED
            }

            # Suggest based on what was executed
            if "search" in executed_skills:
                suggestions.append("Summarize the search results")
                suggestions.append("Search for related topics")

            if "summarize" in executed_skills:
                suggestions.append("Generate a detailed outline")
                suggestions.append("Extract key action items")

            if "chat" in executed_skills:
                suggestions.append("Ask a follow-up question")

            # If there were failures, suggest retry
            failed_count = sum(
                1 for node in dag.nodes.values()
                if node.status == TaskStatus.FAILED
            )
            if failed_count > 0:
                suggestions.append("Retry the failed operations")

        # Limit suggestions
        return suggestions[:3]

    def clear_memory(self) -> None:
        """Clear conversation history."""
        self.memory.clear_history()

    def reset(self) -> None:
        """Fully reset the agent state."""
        self.memory.clear()

    async def run_stream(
        self, user_input: str
    ) -> AsyncIterator[Dict[str, Any]]:
        """Process user input with streaming status updates.

        Args:
            user_input: User's message or command.

        Yields:
            Status dicts with type and content fields:
            - {"type": "status", "content": "..."}
            - {"type": "planning", "content": "..."}
            - {"type": "task_start", "task_id": "...", "skill": "..."}
            - {"type": "task_done", "task_id": "...", "result": "..."}
            - {"type": "error", "content": "..."}
            - {"type": "complete", "content": "..."}
            DAG mode additional events:
            - {"type": "dag_start", "dag_id": "...", "goal": "...", "total_tasks": N}
            - {"type": "dag_complete", "dag_id": "...", "completed": N, ...}
        """
        try:
            yield {"type": "status", "content": "Analyzing input..."}

            if self.logger:
                self.logger.info("agent_run_stream_start", user_input=user_input[:100])

            # Add user input to memory
            if self._memory_type == "tiered":
                await self.memory.add_turn("user", user_input)
            else:
                self.memory.add_turn("user", user_input)

            # Get context for planning
            context = self.memory.get_context()
            available_skills = self.get_skill_names()

            yield {"type": "planning", "content": "Creating execution plan..."}

            # Create execution plan
            plan = await self.planner.create_plan(
                goal=user_input,
                context=context,
                available_skills=available_skills,
            )

            # Stream execution based on planner type
            if self._planner_type == "dag" and isinstance(plan, TaskDAG):
                # DAG mode - use runtime streaming
                response_text = "Task completed."
                async for status in self.runtime.execute_stream(plan):
                    yield status
                    if status.get("type") == "dag_complete":
                        # Extract final response from dag_complete event
                        results = status.get("results", {})
                        response_text = self._extract_response_from_results(results)
            else:
                # Simple mode - use executor streaming
                if plan.is_direct():
                    response_text = plan.direct_response or ""
                    yield {"type": "direct", "content": response_text}
                else:
                    yield {"type": "status", "content": f"Executing {len(plan.tasks)} task(s)..."}

                    results = []
                    async for status in self.executor.execute_stream(plan):
                        yield status
                        if status.get("type") == "task_done":
                            results.append(status.get("result"))

                    # Generate final response
                    if results:
                        last_result = results[-1]
                        response_text = str(last_result) if last_result else "Task completed."
                    else:
                        response_text = "I completed the task but have no output to show."

            # Add response to memory
            if self._memory_type == "tiered":
                await self.memory.add_turn("assistant", response_text)
            else:
                self.memory.add_turn("assistant", response_text)

            if self.logger:
                self.logger.info("agent_run_stream_complete")

            yield {"type": "complete", "content": response_text}

        except Exception as e:
            if self.logger:
                self.logger.error("agent_run_stream_failed", error=str(e))
            yield {"type": "error", "content": f"An error occurred: {str(e)}"}

    def _extract_response_from_results(self, results: Dict[str, Any]) -> str:
        """Extract response text from DAG results dict.

        Args:
            results: Dictionary of task_id -> result.

        Returns:
            Response text.
        """
        if not results:
            return "Task completed."

        values = [v for v in results.values() if v is not None]
        if values:
            last_result = values[-1]
            if isinstance(last_result, str):
                return last_result
            return str(last_result)

        return "Task completed."

    # =========================================================================
    # Notebook-specific methods (AI Notebook layer)
    # =========================================================================

    def set_notebook_context(self, context: "NotebookContext") -> None:
        """Set the notebook context.

        Args:
            context: NotebookContext instance.
        """
        self.notebook_context = context
        self._update_system_prompt()

    def get_notebook_context(self) -> Optional["NotebookContext"]:
        """Get the current notebook context.

        Returns:
            NotebookContext if set, None otherwise.
        """
        return getattr(self, 'notebook_context', None)

    def _update_system_prompt(self) -> None:
        """Update system prompt based on notebook context."""
        if not hasattr(self, 'notebook_context'):
            return

        ctx = self.notebook_context

        # Build sources info
        sources_info = "\n".join([
            f"- {s.title} (ID: {s.id}, {len(s.chunks)} chunks)"
            for s in ctx.sources
            if s.id in ctx.active_source_ids
        ])

        self.system_prompt = f"""You are an AI Notebook assistant.

The user is currently working with the following documents:
{sources_info if sources_info else "(No documents loaded)"}

Available actions:
- When answering questions, use 'rag_search' skill to find answers from documents
- Use 'draft_outline' skill to generate outlines
- Use 'draft_summary' skill to generate summaries
- Use 'draft_notes' skill to extract key notes

Please answer in Chinese and cite document sources when referencing content."""

    def add_source(self, source: "Source") -> None:
        """Add a knowledge source to the notebook.

        Args:
            source: Source object to add.
        """
        from ..domain.models import NotebookContext

        # Initialize context if not exists
        if not hasattr(self, 'notebook_context') or self.notebook_context is None:
            self.notebook_context = NotebookContext(notebook_id="default")

        self.notebook_context.sources.append(source)
        self.notebook_context.active_source_ids.append(source.id)

        # Sync to retrieval service if available
        if hasattr(self, 'retrieval') and self.retrieval is not None:
            self.retrieval.add_source(source)

        # Update memory pin for quick reference
        if self._memory_type == "tiered":
            item = PinnedItem(
                id=f"source:{source.id}",
                type="file_meta",
                content=f"{source.title} ({len(source.chunks)} chunks)",
                priority=10
            )
            self.memory.pin(item)
        else:
            self.memory.pin(
                f"source:{source.id}",
                f"{source.title} ({len(source.chunks)} chunks)"
            )
        self._update_system_prompt()

    def remove_source(self, source_id: str) -> bool:
        """Remove a knowledge source from the notebook.

        Args:
            source_id: ID of source to remove.

        Returns:
            True if removed, False if not found.
        """
        if not hasattr(self, 'notebook_context') or self.notebook_context is None:
            return False

        # Find and remove source
        for i, source in enumerate(self.notebook_context.sources):
            if source.id == source_id:
                self.notebook_context.sources.pop(i)
                if source_id in self.notebook_context.active_source_ids:
                    self.notebook_context.active_source_ids.remove(source_id)

                # Remove from retrieval service
                if hasattr(self, 'retrieval') and self.retrieval is not None:
                    self.retrieval.remove_source(source_id)

                # Remove from memory
                self.memory.unpin(f"source:{source_id}")
                self._update_system_prompt()
                return True

        return False

    def set_active_sources(self, source_ids: list) -> None:
        """Set which sources are active for queries.

        Args:
            source_ids: List of source IDs to activate.
        """
        if not hasattr(self, 'notebook_context') or self.notebook_context is None:
            return

        # Validate source IDs
        valid_ids = {s.id for s in self.notebook_context.sources}
        self.notebook_context.active_source_ids = [
            sid for sid in source_ids if sid in valid_ids
        ]
        self._update_system_prompt()

    def setup_retrieval(self, retrieval_service: "RetrievalService") -> None:
        """Set up retrieval service for RAG.

        Args:
            retrieval_service: RetrievalService instance.
        """
        self.retrieval = retrieval_service

        # Index existing sources
        if hasattr(self, 'notebook_context') and self.notebook_context is not None:
            for source in self.notebook_context.sources:
                self.retrieval.add_source(source)

    def register_notebook_skills(self) -> None:
        """Register notebook-specific skills (RAG, draft)."""
        if not hasattr(self, 'retrieval') or self.retrieval is None:
            raise RuntimeError("Retrieval service must be set up first")

        from ..skills.rag import create_rag_skill
        from ..skills.draft import create_draft_skill

        # Register RAG skill
        rag_skill = create_rag_skill(self.retrieval, self.llm_client)
        self.register_skill("rag_search", rag_skill)

        # Register draft skills
        draft_skills = create_draft_skill(self.llm_client)
        for name, skill_func in draft_skills.items():
            self.register_skill(f"draft_{name}", skill_func)

    # =========================================================================
    # Memory and Tracing utilities
    # =========================================================================

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get memory usage statistics.

        Returns:
            Dictionary with memory stats.
        """
        if self._memory_type == "tiered":
            stats = self.memory.get_stats()
            return {
                "type": "tiered",
                "pinned_tokens": stats.pinned_tokens,
                "working_tokens": stats.working_tokens,
                "episodic_tokens": stats.episodic_tokens,
                "semantic_tokens": stats.semantic_tokens,
                "total_tokens": stats.total_tokens,
                "compression_count": stats.compression_count,
                "turn_count": stats.turn_count,
            }
        else:
            return {
                "type": "simple",
                "turn_count": self.memory.get_turn_count(),
                "pinned_count": self.memory.get_pinned_count(),
            }

    def get_trace_summary(self) -> Optional[Dict[str, Any]]:
        """Get execution trace summary.

        Returns:
            Trace summary or None if tracing disabled.
        """
        if self.tracer:
            return self.tracer.get_trace_summary()
        return None

    async def checkpoint(self) -> Optional[str]:
        """Manually trigger a checkpoint.

        Returns:
            Checkpoint file path or None if not using tiered memory.
        """
        if self._memory_type == "tiered":
            return await self.memory.checkpoint()
        return None

    async def restore_checkpoint(self) -> bool:
        """Restore from latest checkpoint.

        Returns:
            True if restored, False otherwise.
        """
        if self._memory_type == "tiered":
            return await self.memory.restore()
        return False

    def set_working_context(self, key: str, value: Any) -> None:
        """Set working memory context (tiered memory only).

        Args:
            key: Context key.
            value: Context value.
        """
        if self._memory_type == "tiered":
            self.memory.set_working(key, value)

    def get_working_context(self, key: str, default: Any = None) -> Any:
        """Get working memory context (tiered memory only).

        Args:
            key: Context key.
            default: Default value.

        Returns:
            Context value or default.
        """
        if self._memory_type == "tiered":
            return self.memory.get_working(key, default)
        return default
