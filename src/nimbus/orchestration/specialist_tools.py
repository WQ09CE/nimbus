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
    DEFAULT_TIMEOUT = 600.0  # Subclasses override with role-appropriate timeouts
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

        # Extract parent action ID for sub-agent event routing
        parent_action_id = kwargs.pop("_parent_action_id", None)

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

        # Resolve the actual model for display
        # If no model specified, will inherit from orchestrator — extract its model name
        if executor_llm:
            resolved_model = getattr(executor_llm, '_model', model_name)
        else:
            resolved_model = getattr(self._agent_os._llm, '_model', '')
        # Normalize to full provider/model_id format
        from nimbus.core.models.registry import ModelRegistry
        try:
            resolved_model = ModelRegistry.normalize(resolved_model)
        except Exception:
            pass

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
        # Store parent action ID and resolved model on process for SSE event routing
        proc = self._agent_os._processes.get(pid)
        if proc:
            if parent_action_id:
                proc.signals["parent_action_id"] = parent_action_id  # type: ignore[assignment]
            proc.signals["resolved_model"] = resolved_model  # type: ignore[assignment]
        logger.info(f"[{self.ROLE}] Spawned {pid} for: {task[:80]}...")

        # Wait for completion -- with retry on failure
        MAX_RETRIES = 1  # One retry attempt
        output = ""
        for attempt in range(1 + MAX_RETRIES):
            timed_out = False
            try:
                result = await self._agent_os.wait(pid, timeout=timeout)
                
                # result may be a string (e.g., if wait() catches an unexpected error inside AgentOS) 
                # or a structured ToolResult object from VCPU
                if isinstance(result, str):
                    output = result
                    has_fault = False
                else:
                    raw_output = getattr(result, "output", None)
                    if raw_output is None:
                        output = f"({self.ROLE} returned no output)"
                    elif isinstance(raw_output, str):
                        output = raw_output
                    else:
                        output = str(raw_output)
                    
                    has_fault = getattr(result, "fault", None) is not None
                    if has_fault:
                        output += f"\n\nFault: {result.fault.message}"
                
                if has_fault:
                    # If the fault is retryable and we have retries left, retry
                    if getattr(result.fault, "retryable", False) and attempt < MAX_RETRIES:
                        logger.warning(
                            f"[{self.ROLE}] {pid} failed (attempt {attempt+1}), retrying: {result.fault.message}"
                        )
                        # Re-spawn with same parameters
                        pid = self._agent_os.spawn(
                            goal=goal,
                            profile=profile,
                            llm_client=executor_llm,
                        )
                        proc = self._agent_os._processes.get(pid)
                        if proc:
                            if parent_action_id:
                                proc.signals["parent_action_id"] = parent_action_id
                            proc.signals["resolved_model"] = resolved_model
                        continue
                # Success or non-retryable fault -- break out
                break
            except (asyncio.TimeoutError, Exception) as e:
                if "timed out" in str(e).lower() or isinstance(e, asyncio.TimeoutError):
                    timed_out = True
                    elapsed_now = time.time() - start_time
                    output = (
                        f"[{self.ROLE} TIMEOUT after {elapsed_now:.0f}s (limit: {timeout:.0f}s)]\n\n"
                        f"The {self.ROLE} did not finish within the timeout.\n"
                        f"Possible causes:\n"
                        f"- Task too complex for single specialist call\n"
                        f"- LLM thinking time exceeded expectations\n"
                        f"- Network/API latency\n\n"
                        f"You can retry with a longer timeout by passing `timeout` parameter, "
                        f'e.g. {{"task": "...", "timeout": {int(timeout * 2)}}}.\n'
                        f"Or break the task into smaller sub-tasks."
                    )
                    logger.warning(f"[{self.ROLE}] {pid} timed out after {elapsed_now:.0f}s")
                else:
                    if attempt < MAX_RETRIES:
                        logger.warning(
                            f"[{self.ROLE}] {pid} error (attempt {attempt+1}), retrying: {e}"
                        )
                        pid = self._agent_os.spawn(
                            goal=goal,
                            profile=profile,
                            llm_client=executor_llm,
                        )
                        proc = self._agent_os._processes.get(pid)
                        if proc:
                            if parent_action_id:
                                proc.signals["parent_action_id"] = parent_action_id
                            proc.signals["resolved_model"] = resolved_model
                        continue
                    output = f"[{self.ROLE} error: {e}]"
                    logger.error(f"[{self.ROLE}] {pid} failed: {e}")
                break

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
    DEFAULT_TIMEOUT = 300.0  # 5 min -- exploration should be quick
    TRACK_DIFF = False

    def _create_profile(self, model_id: str = "default") -> AgentProfile:
        return AgentProfile.create_explorer(model_id)


class ImplementTool(SpecialistTool):
    """Code implementation with full tool access."""
    ROLE = "implementer"
    DEFAULT_TIMEOUT = 900.0  # 15 min -- implementation tasks are heaviest
    TRACK_DIFF = True

    def _create_profile(self, model_id: str = "default") -> AgentProfile:
        return AgentProfile.create_implementer(model_id)


class DesignTool(SpecialistTool):
    """Architecture and design document creation."""
    ROLE = "architect"
    DEFAULT_TIMEOUT = 600.0  # 10 min -- design docs are medium weight
    TRACK_DIFF = True  # Track .md file creation

    def _create_profile(self, model_id: str = "default") -> AgentProfile:
        return AgentProfile.create_architect(model_id)


class TestTool(SpecialistTool):
    """Test execution and verification."""
    ROLE = "tester"
    DEFAULT_TIMEOUT = 600.0  # 10 min -- test execution is medium weight
    TRACK_DIFF = False

    def _create_profile(self, model_id: str = "default") -> AgentProfile:
        return AgentProfile.create_tester(model_id)
