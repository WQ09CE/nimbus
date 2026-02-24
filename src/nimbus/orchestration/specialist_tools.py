"""
Specialist Tools -- Typed Meta-Tools for Multi-Agent Orchestration.

Replaces the single Dispatch tool with typed specialist tools:
- Explore: Read-only codebase investigation
- Implement: Code writing and execution
- Design: Architecture and design documents
- Test: Test execution and verification

Each tool spawns a specialist agent via AgentOS.spawn() with
appropriate AgentProfile and structured GoalDocument.
"""

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from nimbus.adapters.llm_factory import create_llm_client
from nimbus.agentos import AgentOS
from nimbus.core.profile import AgentProfile
from nimbus.core.protocol import NIMFS_OFFLOAD_THRESHOLD
from nimbus.orchestration.context_protocol import GoalDocument
from nimbus.orchestration.workspace_diff import (
    diff_snapshots,
    take_snapshot,
)


class SpecialistTool:
    """Base class for specialist meta-tools."""

    # Subclasses override these
    ROLE = "specialist"
    DEFAULT_TIMEOUT = 600.0  # 10 min default for all specialists
    TRACK_DIFF = False  # Only Implementer tracks workspace diffs

    def __init__(
        self,
        agent_os: AgentOS,
        workspace: Optional[Path] = None,
    ):
        self._agent_os = agent_os
        self._workspace = workspace or Path.cwd()

    def _create_profile(self, model_id: str = "default") -> AgentProfile:
        """Create the appropriate AgentProfile for this specialist."""
        raise NotImplementedError

    async def execute(self, task: str, context: str = "", **kwargs) -> str:
        """
        Execute a specialist task.

        1. Build structured GoalDocument
        2. Create specialist profile
        3. Take workspace snapshot (if tracking diffs)
        4. Spawn specialist process
        5. Wait for completion
        6. Format and return result
        """
        start_time = time.time()

        # Resolve timeout: explicit kwarg > class default
        timeout = kwargs.get("timeout")
        if timeout is not None:
            try:
                timeout = float(timeout)
            except (TypeError, ValueError):
                timeout = self.DEFAULT_TIMEOUT
        else:
            timeout = self.DEFAULT_TIMEOUT

        # Resolve model
        model_name = kwargs.get("model", "")
        executor_llm = None
        if model_name:
            try:
                executor_llm = await create_llm_client(model_name)
                logger.info(f"  [{self.ROLE}] Using model: {model_name}")
            except Exception as e:
                logger.warning(f"  [{self.ROLE}] Failed to create LLM for {model_name}: {e}")

        # Resolve instructions
        instructions = kwargs.get("instructions", "")

        # Build goal document
        profile = self._create_profile(model_name or "default")
        constraints = [
            f"Tool budget: {profile.max_iterations} iterations",
            f"Role: {self.ROLE}",
        ]
        if instructions:
            constraints.append(f"Additional instructions: {instructions}")
        goal_doc = GoalDocument(
            mission=task,
            context=context,
            workspace=str(self._workspace),
            constraints=constraints,
        )
        goal = goal_doc.render()

        # Take before snapshot (for diff tracking)
        snapshot_before = None
        if self.TRACK_DIFF:
            snapshot_before = take_snapshot(self._workspace)

        # Spawn specialist process
        pid = self._agent_os.spawn(
            goal=goal,
            profile=profile,
            llm_client=executor_llm,
        )
        logger.info(f"[{self.ROLE}] Spawned {pid} for: {task[:80]}...")

        # Wait for completion -- with actionable timeout hint
        timed_out = False
        try:
            result = await self._agent_os.wait(pid, timeout=timeout)
            output = result.output or f"({self.ROLE} returned no output)"
            if result.fault:
                output += f"\n\nFault: {result.fault.message}"
        except (asyncio.TimeoutError, Exception) as e:
            if "timed out" in str(e).lower() or isinstance(e, asyncio.TimeoutError):
                timed_out = True
                elapsed = time.time() - start_time
                output = (
                    f"[{self.ROLE} TIMEOUT after {elapsed:.0f}s (limit: {timeout:.0f}s)]\n\n"
                    f"The {self.ROLE} did not finish within the timeout.\n"
                    f"Possible causes:\n"
                    f"- Task too complex for single specialist call\n"
                    f"- LLM thinking time exceeded expectations\n"
                    f"- Network/API latency\n\n"
                    f"You can retry with a longer timeout by passing `timeout` parameter, "
                    f'e.g. {{"task": "...", "timeout": {int(timeout * 2)}}}.\n'
                    f"Or break the task into smaller sub-tasks."
                )
                logger.warning(f"[{self.ROLE}] {pid} timed out after {elapsed:.0f}s")
            else:
                output = f"[{self.ROLE} error: {e}]"
                logger.error(f"[{self.ROLE}] {pid} failed: {e}")

        # Offload large output to NimFS to save orchestrator context tokens
        if len(output) > NIMFS_OFFLOAD_THRESHOLD and self._workspace:
            try:
                from nimbus.core.nimfs.manager import NimFSManager
                from nimbus.core.nimfs.models import ArtifactTTL

                nimfs = NimFSManager(self._workspace)
                artifact_ref = nimfs.write_artifact(
                    content=output,
                    task_id=f"{self.ROLE}-{pid[:8]}",
                    producer=self.ROLE,
                    artifact_type="text",
                    ttl=ArtifactTTL.TASK,
                    summary=f"{self.ROLE.title()} result ({len(output)} chars)",
                )
                original_size = len(output)
                preview = output[:500]
                output = (
                    f"[Result stored as artifact]\n"
                    f"Reference: {artifact_ref}\n"
                    f"Size: {original_size} chars\n\n"
                    f"Preview:\n{preview}...\n\n"
                    f"Use NimFSReadArtifact to read the full result."
                )
                logger.info(f"[{self.ROLE}] Offloaded large output to NimFS: {artifact_ref}")
            except Exception as e:
                # Offload failure must not block normal flow -- keep original output
                logger.debug(f"[{self.ROLE}] NimFS offload skipped: {e}")

        # Compute diff (for Implementer)
        diff_summary = ""
        if snapshot_before is not None:
            snapshot_after = take_snapshot(self._workspace)
            diff = diff_snapshots(snapshot_before, snapshot_after)
            if diff.has_changes:
                diff_summary = f"\n\n### Files Changed\n{diff.summary()}"

        elapsed = time.time() - start_time
        logger.info(f"[{self.ROLE}] {pid} done in {elapsed:.1f}s")

        return f"## {self.ROLE.title()} Result\n\n{output}{diff_summary}"


class ExploreTool(SpecialistTool):
    """Read-only codebase exploration."""
    ROLE = "explorer"
    DEFAULT_TIMEOUT = 600.0  # 10 min default for all specialists
    TRACK_DIFF = False

    def _create_profile(self, model_id: str = "default") -> AgentProfile:
        return AgentProfile.create_explorer(model_id)


class ImplementTool(SpecialistTool):
    """Code implementation with full tool access."""
    ROLE = "implementer"
    DEFAULT_TIMEOUT = 600.0  # 10 min default for all specialists
    TRACK_DIFF = True

    def _create_profile(self, model_id: str = "default") -> AgentProfile:
        return AgentProfile.create_implementer(model_id)


class DesignTool(SpecialistTool):
    """Architecture and design document creation."""
    ROLE = "architect"
    DEFAULT_TIMEOUT = 600.0  # 10 min default for all specialists
    TRACK_DIFF = True  # Track .md file creation

    def _create_profile(self, model_id: str = "default") -> AgentProfile:
        return AgentProfile.create_architect(model_id)


class TestTool(SpecialistTool):
    """Test execution and verification."""
    ROLE = "tester"
    DEFAULT_TIMEOUT = 600.0  # 10 min default for all specialists
    TRACK_DIFF = False

    def _create_profile(self, model_id: str = "default") -> AgentProfile:
        return AgentProfile.create_tester(model_id)


class ParallelDispatchTool:
    """Dispatch multiple specialist tasks in parallel via AgentOS.spawn_batch."""

    # Map specialist names to (role, profile factory)
    SPECIALIST_MAP = {
        "Explorer": ("explorer", AgentProfile.create_explorer),
        "Implementer": ("implementer", AgentProfile.create_implementer),
        "Architect": ("architect", AgentProfile.create_architect),
        "Tester": ("tester", AgentProfile.create_tester),
    }

    DEFAULT_TIMEOUT = 600.0

    def __init__(self, agent_os: AgentOS, workspace: Optional[Path] = None):
        self._agent_os = agent_os
        self._workspace = workspace or Path.cwd()

    async def execute(
        self,
        tasks: list = None,
        strategy: str = "wait_all",
        threshold: float = 0.6,
        timeout: float = None,
        **kwargs,
    ) -> str:
        """
        Execute multiple specialist tasks in parallel.

        Args:
            tasks: List of dicts with keys: specialist, task, model (optional), context (optional)
            strategy: "wait_all", "wait_any", or "wait_threshold"
            threshold: Completion fraction for wait_threshold
            timeout: Per-task timeout in seconds
        """
        import json as _json

        if tasks is None:
            tasks = kwargs.get("tasks", [])

        if not tasks:
            return "No tasks provided."

        # Parse tasks if string (LLM sometimes sends JSON string)
        if isinstance(tasks, str):
            try:
                tasks = _json.loads(tasks)
            except _json.JSONDecodeError:
                return "[Error] Invalid tasks format. Expected a JSON array."

        timeout = float(timeout) if timeout else self.DEFAULT_TIMEOUT

        # Build spawn_batch task specs
        batch_tasks = []
        valid_indices = []  # Track which original task indices are valid
        for i, t in enumerate(tasks):
            specialist_name = t.get("specialist", "")
            task_desc = t.get("task", "")
            model_name = t.get("model", "")
            context = t.get("context", "")

            if specialist_name not in self.SPECIALIST_MAP:
                logger.warning(
                    f"[ParallelDispatch] Unknown specialist '{specialist_name}', skipping task {i}"
                )
                continue

            role, profile_factory = self.SPECIALIST_MAP[specialist_name]
            profile = profile_factory(model_name or "default")

            # Build GoalDocument
            constraints = [
                f"Tool budget: {profile.max_iterations} iterations",
                f"Role: {role}",
            ]
            goal_doc = GoalDocument(
                mission=task_desc,
                context=context,
                workspace=str(self._workspace),
                constraints=constraints,
            )

            # Resolve LLM client if model specified
            llm_client = None
            if model_name:
                try:
                    llm_client = await create_llm_client(model_name)
                except Exception as e:
                    logger.warning(
                        f"[ParallelDispatch] Failed to create LLM for task {i} ({model_name}): {e}"
                    )

            batch_tasks.append({
                "goal": goal_doc.render(),
                "profile": profile,
                "llm_client": llm_client,
            })
            valid_indices.append(i)

        if not batch_tasks:
            return "[Error] No valid tasks to dispatch."

        # Execute via spawn_batch
        logger.info(
            f"[ParallelDispatch] Dispatching {len(batch_tasks)} tasks, strategy={strategy}"
        )
        results = await self._agent_os.spawn_batch(
            tasks=batch_tasks,
            timeout=timeout,
            strategy=strategy,
            threshold=threshold,
        )

        # Format aggregated results
        parts = []
        completed = 0
        partial = 0
        for j, result in enumerate(results):
            orig_idx = valid_indices[j] if j < len(valid_indices) else j
            specialist_name = tasks[orig_idx].get("specialist", "?") if orig_idx < len(tasks) else "?"
            task_desc = tasks[orig_idx].get("task", "")[:80] if orig_idx < len(tasks) else ""
            is_partial = result.status != "OK"
            if is_partial:
                partial += 1
            else:
                completed += 1

            output = result.output or "(no output)"
            # Truncate very long individual results for aggregated view
            output_str = str(output)
            if len(output_str) > 2000:
                output = output_str[:2000] + f"\n... [truncated, {len(output_str)} chars total]"

            status_label = "OK" if not is_partial else result.status
            parts.append(
                f"### Task {j+1}: {specialist_name} [{status_label}]\n"
                f"**Task:** {task_desc}\n"
                f"**Status:** {result.status}\n\n"
                f"{output}"
            )

        summary = f"{completed} completed, {partial} partial/failed out of {len(results)} total."
        header = f"## ParallelDispatch Results\n\n**Strategy:** {strategy} | **Summary:** {summary}\n\n"

        return header + "\n---\n".join(parts)
