"""SubagentRuntime for parallel subagent execution.

This module provides the SubagentRuntime class which executes SubagentDAGs
with parallel subagent execution, context injection, and failure handling.

Features:
- Parallel execution of independent subagents
- Context injection from dependency results
- AgenticRunner integration for each subagent
- Retry and fallback failure handling
- Replan coordination

Example:
    >>> from nimbus.core.task.runtime import SubagentRuntime
    >>> from nimbus.core.task.types import SubagentDAG
    >>>
    >>> runtime = SubagentRuntime(
    ...     llm_client=llm_client,
    ...     tool_registry=registry,
    ...     workspace=Path.cwd(),
    ... )
    >>> result = await runtime.execute(dag, parent_context="")
    >>> print(result.status)
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Set, TYPE_CHECKING

from .types import (
    SubagentType,
    SubagentNode,
    SubagentDAG,
    SubagentResult,
    SubagentStatus,
    SubagentExecutionResult,
    SubagentExecutionStats,
    SUBAGENT_TOOLS,
)
from .coordinator import SubagentReplanCoordinator

if TYPE_CHECKING:
    from nimbus.tools import ToolRegistry
    from nimbus.core.runtime.agentic import AgenticRunner, AgenticConfig


@dataclass
class SubagentRuntimeConfig:
    """Configuration for SubagentRuntime.

    Attributes:
        max_concurrent: Maximum concurrent subagent executions.
        default_timeout: Default timeout per subagent in seconds.
        default_max_turns: Default max turns per subagent.
        enable_replan: Enable replan on failure.
        max_replan_attempts: Maximum replan attempts per DAG.
    """
    max_concurrent: int = 5
    default_timeout: float = 300.0
    default_max_turns: int = 50
    enable_replan: bool = True
    max_replan_attempts: int = 2


class SubagentRuntime:
    """Executes SubagentDAG with parallel subagent execution.

    Each subagent runs in its own AgenticRunner with isolated context
    and restricted tool permissions.

    Attributes:
        llm_client: LLM client for subagent completions.
        tool_registry: Tool registry for tool execution.
        workspace: Workspace directory for file operations.
        config: Runtime configuration.
    """

    def __init__(
        self,
        llm_client: Any,
        tool_registry: "ToolRegistry",
        workspace: Optional[Path] = None,
        config: Optional[SubagentRuntimeConfig] = None,
    ):
        """Initialize SubagentRuntime.

        Args:
            llm_client: LLM client for subagent completions.
            tool_registry: Tool registry for tool execution.
            workspace: Workspace directory for sandbox.
            config: Optional runtime configuration.
        """
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.workspace = workspace or Path.cwd()
        self.config = config or SubagentRuntimeConfig()
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._coordinator = SubagentReplanCoordinator()

    async def execute(
        self,
        dag: SubagentDAG,
        parent_context: str = "",
    ) -> SubagentExecutionResult:
        """Execute SubagentDAG with parallel subagent execution.

        Args:
            dag: SubagentDAG to execute.
            parent_context: Context from parent agent.

        Returns:
            SubagentExecutionResult with all results and statistics.
        """
        start_time = datetime.now()
        total_turns = 0

        while not dag.is_completed():
            # Check for replan pause
            if self._coordinator.is_paused():
                await asyncio.sleep(0.1)
                continue

            ready_nodes = dag.get_ready_nodes()

            if ready_nodes:
                # Execute ready nodes in parallel
                tasks = [
                    asyncio.create_task(self._execute_subagent(node, dag, parent_context))
                    for node in ready_nodes
                ]
                # Wait for all tasks to complete
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                # Check if deadlock
                running_count = sum(
                    1 for n in dag.nodes.values()
                    if n.status == SubagentStatus.RUNNING
                )
                if running_count == 0 and not dag.is_completed():
                    # Deadlock detected
                    for node in dag.nodes.values():
                        if node.status == SubagentStatus.PENDING:
                            node.status = SubagentStatus.FAILED
                            node.error = "Deadlock: unreachable due to missing dependencies"
                    break

                await asyncio.sleep(0.05)

        # Collect statistics
        end_time = datetime.now()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        completed = sum(1 for n in dag.nodes.values() if n.status == SubagentStatus.COMPLETED)
        failed = sum(1 for n in dag.nodes.values() if n.status == SubagentStatus.FAILED)
        skipped = sum(1 for n in dag.nodes.values() if n.status == SubagentStatus.SKIPPED)

        # Collect total turns
        for node in dag.nodes.values():
            if node.result:
                total_turns += node.result.turn_count

        # Calculate efficiency
        serial_time = sum(node.duration_ms or 0 for node in dag.nodes.values())
        efficiency = serial_time / duration_ms if duration_ms > 0 else 0.0

        stats = SubagentExecutionStats(
            total_nodes=len(dag.nodes),
            completed=completed,
            failed=failed,
            skipped=skipped,
            total_duration_ms=duration_ms,
            total_turns=total_turns,
            parallel_efficiency=efficiency,
        )

        # Determine status
        if failed == 0 and skipped == 0:
            status = "success"
        elif completed > 0:
            status = "partial"
        else:
            status = "failed"

        # Build final summary
        final_summary = self._build_final_summary(dag)

        return SubagentExecutionResult(
            dag_id=dag.id,
            status=status,
            results=dag.get_results(),
            errors=dag.get_errors(),
            duration_ms=duration_ms,
            stats=stats,
            final_summary=final_summary,
        )

    async def execute_stream(
        self,
        dag: SubagentDAG,
        parent_context: str = "",
    ) -> AsyncIterator[Dict[str, Any]]:
        """Execute SubagentDAG with streaming events.

        Args:
            dag: SubagentDAG to execute.
            parent_context: Context from parent agent.

        Yields:
            Event dicts with type and data fields:
            - {"type": "task_start", "dag_id": "...", "nodes": N}
            - {"type": "subagent_start", "node_id": "...", "subagent_type": "...", "goal": "..."}
            - {"type": "subagent_progress", "node_id": "...", "event_type": "...", ...}
            - {"type": "subagent_complete", "node_id": "...", "status": "...", "summary": "..."}
            - {"type": "task_complete", "dag_id": "...", "status": "...", ...}
        """
        start_time = datetime.now()
        total_turns = 0

        yield {
            "type": "task_start",
            "dag_id": dag.id,
            "nodes": len(dag.nodes),
            "goal": dag.user_goal,
            "complexity": dag.complexity,
        }

        # Track active tasks for concurrent execution
        active_tasks: Dict[str, asyncio.Task] = {}
        event_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

        async def execute_node_with_events(node: SubagentNode) -> None:
            """Execute a single node and put events into queue."""
            # Emit start event
            await event_queue.put({
                "type": "subagent_start",
                "node_id": node.id,
                "subagent_type": node.subagent_type.value,
                "goal": node.goal,
            })

            try:
                await self._execute_subagent_with_events(node, dag, parent_context, event_queue)
            except Exception as e:
                node.status = SubagentStatus.FAILED
                node.error = str(e)
                await event_queue.put({
                    "type": "subagent_complete",
                    "node_id": node.id,
                    "status": "failed",
                    "error": str(e),
                })

        while not dag.is_completed():
            # Check for replan pause
            if self._coordinator.is_paused():
                await asyncio.sleep(0.1)
                continue

            # Start new ready nodes
            ready_nodes = dag.get_ready_nodes()
            for node in ready_nodes:
                if node.id not in active_tasks:
                    task = asyncio.create_task(execute_node_with_events(node))
                    active_tasks[node.id] = task

            # Drain event queue
            while not event_queue.empty():
                event = await event_queue.get()
                yield event

                # Clean up completed tasks
                if event.get("type") == "subagent_complete":
                    node_id = event.get("node_id")
                    if node_id in active_tasks:
                        del active_tasks[node_id]

            # Check for deadlock or completion
            if not active_tasks and not dag.is_completed():
                running_count = sum(
                    1 for n in dag.nodes.values()
                    if n.status == SubagentStatus.RUNNING
                )
                if running_count == 0:
                    # Deadlock detected
                    for node in dag.nodes.values():
                        if node.status == SubagentStatus.PENDING:
                            node.status = SubagentStatus.FAILED
                            node.error = "Deadlock: unreachable due to missing dependencies"
                            yield {
                                "type": "subagent_complete",
                                "node_id": node.id,
                                "status": "failed",
                                "error": node.error,
                            }
                    break

            await asyncio.sleep(0.05)

        # Wait for remaining active tasks
        if active_tasks:
            await asyncio.gather(*active_tasks.values(), return_exceptions=True)
            # Drain remaining events
            while not event_queue.empty():
                event = await event_queue.get()
                yield event

        # Collect statistics
        end_time = datetime.now()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        completed = sum(1 for n in dag.nodes.values() if n.status == SubagentStatus.COMPLETED)
        failed = sum(1 for n in dag.nodes.values() if n.status == SubagentStatus.FAILED)
        skipped = sum(1 for n in dag.nodes.values() if n.status == SubagentStatus.SKIPPED)

        # Collect total turns
        for node in dag.nodes.values():
            if node.result:
                total_turns += node.result.turn_count

        # Calculate efficiency
        serial_time = sum(node.duration_ms or 0 for node in dag.nodes.values())
        efficiency = serial_time / duration_ms if duration_ms > 0 else 0.0

        # Determine status
        if failed == 0 and skipped == 0:
            status = "success"
        elif completed > 0:
            status = "partial"
        else:
            status = "failed"

        # Build final summary
        final_summary = self._build_final_summary(dag)

        yield {
            "type": "task_complete",
            "dag_id": dag.id,
            "status": status,
            "stats": {
                "total_nodes": len(dag.nodes),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "total_duration_ms": duration_ms,
                "total_turns": total_turns,
                "parallel_efficiency": efficiency,
            },
            "final_summary": final_summary,
            "errors": dag.get_errors(),
        }

    async def _execute_subagent_with_events(
        self,
        node: SubagentNode,
        dag: SubagentDAG,
        parent_context: str,
        event_queue: asyncio.Queue,
    ) -> None:
        """Execute a single subagent node with events sent to queue.

        Args:
            node: SubagentNode to execute.
            dag: Parent DAG for context access.
            parent_context: Context from parent agent.
            event_queue: Queue to send events to.
        """
        async with self._semaphore:
            node.status = SubagentStatus.RUNNING
            node.started_at = datetime.now()

            try:
                # Build context from dependencies
                dep_context = dag.get_context_for_node(node.id)
                full_context = f"{parent_context}\n\n{dep_context}".strip()

                # Create restricted tool registry
                child_registry = self._create_restricted_registry(node)

                # Create AgenticRunner for this subagent
                from nimbus.core.runtime.agentic import (
                    AgenticRunner,
                    AgenticConfig,
                    ToolRegistryExecutor,
                )

                executor = ToolRegistryExecutor(child_registry, workspace=self.workspace)

                runner = AgenticRunner(
                    llm_client=self.llm_client,
                    tool_executor=executor,
                    config=AgenticConfig(
                        max_iterations=node.max_turns,
                        allowed_tools=node.get_allowed_tools(),
                        workspace=self.workspace,
                        system_instruction=self._build_system_instruction(node),
                    ),
                )

                # Execute agentic loop with timeout
                result_text = ""
                turn_count = 0
                files_accessed: List[str] = []
                files_modified: List[str] = []
                start_time = datetime.now()

                async for event in runner.run(goal=node.goal, context=full_context):
                    # Check timeout
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if elapsed > node.timeout:
                        raise RuntimeError(f"Timeout after {node.timeout}s")

                    if event.type == "tool_call":
                        turn_count += 1
                        # Track file access
                        tool_name = event.data.get("name", "")
                        arguments = event.data.get("arguments", {})
                        if tool_name == "Read":
                            file_path = arguments.get("file_path", "")
                            if file_path and file_path not in files_accessed:
                                files_accessed.append(file_path)
                        elif tool_name in ("Write", "Edit"):
                            file_path = arguments.get("file_path", "")
                            if file_path and file_path not in files_modified:
                                files_modified.append(file_path)

                        # Emit progress event
                        await event_queue.put({
                            "type": "subagent_progress",
                            "node_id": node.id,
                            "event_type": "tool_call",
                            "tool_name": tool_name,
                            "arguments": arguments,
                            "call_id": event.data.get("call_id", ""),
                        })
                    elif event.type == "tool_result":
                        # Emit progress event for tool result
                        result = event.data.get("result", "")
                        # Truncate large results for the event
                        if isinstance(result, str) and len(result) > 500:
                            result = result[:500] + "...[truncated]"
                        await event_queue.put({
                            "type": "subagent_progress",
                            "node_id": node.id,
                            "event_type": "tool_result",
                            "tool_name": event.data.get("name", ""),
                            "result_preview": result,
                            "call_id": event.data.get("call_id", ""),
                            "is_error": event.data.get("is_error", False),
                        })
                    elif event.type == "response":
                        result_text = event.data.get("content", "")
                    elif event.type == "error":
                        raise RuntimeError(event.data.get("message", "Unknown error"))

                # Create result
                node.status = SubagentStatus.COMPLETED
                node.result = SubagentResult(
                    agent_id=f"subagent_{node.id}",
                    summary=self._generate_summary(result_text, node.goal),
                    result=result_text,
                    files_accessed=files_accessed,
                    files_modified=files_modified,
                    turn_count=turn_count,
                    duration_ms=node.duration_ms or 0,
                )

                # Emit completion event
                await event_queue.put({
                    "type": "subagent_complete",
                    "node_id": node.id,
                    "status": "completed",
                    "summary": node.result.summary,
                    "files_accessed": files_accessed,
                    "files_modified": files_modified,
                    "turn_count": turn_count,
                })

            except Exception as e:
                node.status = SubagentStatus.FAILED
                node.error = str(e)
                await self._handle_failure(node, dag)

                # Emit failure event
                await event_queue.put({
                    "type": "subagent_complete",
                    "node_id": node.id,
                    "status": "failed",
                    "error": str(e),
                })

            finally:
                node.finished_at = datetime.now()

    async def _execute_subagent(
        self,
        node: SubagentNode,
        dag: SubagentDAG,
        parent_context: str,
    ) -> None:
        """Execute a single subagent node.

        Args:
            node: SubagentNode to execute.
            dag: Parent DAG for context access.
            parent_context: Context from parent agent.
        """
        async with self._semaphore:
            node.status = SubagentStatus.RUNNING
            node.started_at = datetime.now()

            try:
                # Build context from dependencies
                dep_context = dag.get_context_for_node(node.id)
                full_context = f"{parent_context}\n\n{dep_context}".strip()

                # Create restricted tool registry
                child_registry = self._create_restricted_registry(node)

                # Create AgenticRunner for this subagent
                from nimbus.core.runtime.agentic import (
                    AgenticRunner,
                    AgenticConfig,
                    ToolRegistryExecutor,
                )

                executor = ToolRegistryExecutor(child_registry, workspace=self.workspace)

                runner = AgenticRunner(
                    llm_client=self.llm_client,
                    tool_executor=executor,
                    config=AgenticConfig(
                        max_iterations=node.max_turns,
                        allowed_tools=node.get_allowed_tools(),
                        workspace=self.workspace,
                        system_instruction=self._build_system_instruction(node),
                    ),
                )

                # Execute agentic loop with timeout
                result_text = ""
                turn_count = 0
                files_accessed: List[str] = []
                files_modified: List[str] = []
                start_time = datetime.now()

                async for event in runner.run(goal=node.goal, context=full_context):
                    # Check timeout
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if elapsed > node.timeout:
                        raise RuntimeError(f"Timeout after {node.timeout}s")

                    if event.type == "tool_call":
                        turn_count += 1
                        # Track file access
                        tool_name = event.data.get("name", "")
                        arguments = event.data.get("arguments", {})
                        if tool_name == "Read":
                            file_path = arguments.get("file_path", "")
                            if file_path and file_path not in files_accessed:
                                files_accessed.append(file_path)
                        elif tool_name in ("Write", "Edit"):
                            file_path = arguments.get("file_path", "")
                            if file_path and file_path not in files_modified:
                                files_modified.append(file_path)
                    elif event.type == "response":
                        result_text = event.data.get("content", "")
                    elif event.type == "error":
                        raise RuntimeError(event.data.get("message", "Unknown error"))

                # Create result
                node.status = SubagentStatus.COMPLETED
                node.result = SubagentResult(
                    agent_id=f"subagent_{node.id}",
                    summary=self._generate_summary(result_text, node.goal),
                    result=result_text,
                    files_accessed=files_accessed,
                    files_modified=files_modified,
                    turn_count=turn_count,
                    duration_ms=node.duration_ms or 0,
                )

            except Exception as e:
                node.status = SubagentStatus.FAILED
                node.error = str(e)
                await self._handle_failure(node, dag)

            finally:
                node.finished_at = datetime.now()

    def _create_restricted_registry(self, node: SubagentNode) -> "ToolRegistry":
        """Create restricted tool registry for a subagent.

        Args:
            node: SubagentNode with tool permissions.

        Returns:
            ToolRegistry with only allowed tools.
        """
        from nimbus.tools import ToolRegistry

        allowed_tools = node.get_allowed_tools()
        child_registry = ToolRegistry()

        for tool_name in allowed_tools:
            result = self.tool_registry.get(tool_name)
            if result:
                tool_def, tool_func = result
                child_registry.register(tool_def, tool_func)

        return child_registry

    def _build_system_instruction(self, node: SubagentNode) -> str:
        """Build system instruction for a subagent.

        Args:
            node: SubagentNode being executed.

        Returns:
            System instruction string.
        """
        tools_str = ", ".join(sorted(node.get_allowed_tools()))

        descriptions = {
            SubagentType.EYE: "You are a code exploration agent. Your job is to read, search, and understand code.",
            SubagentType.BODY: "You are a code implementation agent. Your job is to write, edit, and execute code.",
            SubagentType.MIND: "You are a design agent. Your job is to analyze requirements and create designs.",
            SubagentType.TONGUE: "You are a testing agent. Your job is to run tests and verify correctness.",
            SubagentType.NOSE: "You are a code review agent. Your job is to review code and provide feedback.",
            SubagentType.EAR: "You are a requirements agent. Your job is to clarify and analyze requirements.",
        }

        return f"""{descriptions.get(node.subagent_type, "You are a helpful agent.")}

## Available Tools
{tools_str}

## Guidelines
1. Stay focused on your assigned task
2. Use the available tools effectively
3. Provide a clear summary when complete
4. If you encounter errors, explain what went wrong

## Workspace
{self.workspace}"""

    async def _handle_failure(
        self,
        node: SubagentNode,
        dag: SubagentDAG,
    ) -> None:
        """Handle subagent failure with retry and replan support.

        Args:
            node: Failed SubagentNode.
            dag: Parent DAG.
        """
        # Check for retry
        if node.retry_count < node.max_retries:
            node.retry_count += 1
            node.status = SubagentStatus.PENDING
            node.error = None
            return

        # Check for fallback node
        if node.on_failure:
            fallback = dag.nodes.get(node.on_failure)
            if fallback:
                # Inject error context into fallback
                fallback.goal = f"{fallback.goal}\n\nPrevious error: {node.error}"
                fallback.status = SubagentStatus.PENDING
                return

        # Check for replan
        if self.config.enable_replan and self._should_replan(node, dag):
            await self._coordinator.request_replan(node, dag, node.error or "Unknown error")
            return

        # Mark downstream as skipped
        dag.mark_downstream_skipped(node.id)

    def _should_replan(self, node: SubagentNode, dag: SubagentDAG) -> bool:
        """Determine if replan is appropriate.

        Args:
            node: Failed node.
            dag: Parent DAG.

        Returns:
            True if replan should be attempted.
        """
        # Don't replan if we've already replanned too many times
        if len(dag.replan_history) >= self.config.max_replan_attempts:
            return False

        # Don't replan for simple failures
        if "timeout" in (node.error or "").lower():
            return False

        # Don't replan if most nodes completed
        if dag.completed_count > len(dag.nodes) * 0.7:
            return False

        return True

    def _generate_summary(self, result: str, goal: str) -> str:
        """Generate a concise summary from result.

        Args:
            result: Full result string.
            goal: Original goal.

        Returns:
            Summary string (max 500 chars).
        """
        if not result:
            return f"Completed: {goal[:100]}"

        if len(result) <= 500:
            return result

        return result[:450] + f"... [truncated, {len(result)} chars total]"

    def _build_final_summary(self, dag: SubagentDAG) -> str:
        """Build final summary from all completed nodes.

        Args:
            dag: Executed DAG.

        Returns:
            Combined summary string.
        """
        summaries = []

        for node in dag.nodes.values():
            if node.status == SubagentStatus.COMPLETED and node.result:
                summaries.append(
                    f"## {node.subagent_type.value.upper()} ({node.id})\n{node.result.summary}"
                )
            elif node.status == SubagentStatus.FAILED:
                summaries.append(
                    f"## {node.subagent_type.value.upper()} ({node.id}) - FAILED\n{node.error}"
                )

        return "\n\n".join(summaries) if summaries else "No results"
