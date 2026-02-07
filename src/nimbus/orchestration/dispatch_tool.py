"""
DispatchTool -- Meta-Tool for Dual-Agent orchestration.

Encapsulates Executor AgentOS lifecycle, event forwarding, and dispatch/verify logic.
Designed to be registered as a tool on any AgentOS (typically the Core agent).

Usage:
    dispatch_tool = DispatchTool(workspace, llm_client, config, parent_events)

    # Register on an AgentOS
    agent_os.register_tool("Dispatch", dispatch_tool.dispatch, ...)
    agent_os.register_tool("Verify", dispatch_tool.verify, ...)
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.protocol import Event
from nimbus.core.runtime.vcpu import LLMClient, VCPUConfig
from nimbus.tools import register_default_tools

from .prompts import EXECUTOR_SYSTEM_PROMPT
from .tools import run_verify_checks
from .workspace_diff import (
    WorkspaceDiff,
    diff_snapshots,
    read_changed_files,
    take_snapshot,
)


@dataclass
class DispatchToolConfig:
    """Configuration for DispatchTool."""

    # Executor agent
    executor_max_iterations: int = 25
    executor_system_prompt: str = EXECUTOR_SYSTEM_PROMPT

    # Dispatch limits
    max_dispatch_count: int = 5
    dispatch_timeout: float = 300.0  # seconds per dispatch

    # Total time budget
    total_timeout: float = 600.0  # seconds

    # Context injection: auto-attach changed file contents on re-dispatch
    auto_inject_context: bool = True
    context_max_file_size: int = 8000
    context_max_files: int = 8


class DispatchTool:
    """
    Dispatch Meta-Tool: creates and manages an internal Executor AgentOS.

    Designed to be registered on a Core AgentOS as a regular tool.
    Internally creates a separate Executor AgentOS with full permissions
    (Read/Write/Edit/Bash) to carry out implementation tasks.

    Features:
    - Lazy Executor OS creation (session-level singleton, reused across dispatches)
    - Event forwarding: Executor events bubble up to parent event_stream
    - Workspace snapshot/diff tracking
    - Dispatch count and time budget enforcement
    """

    def __init__(
        self,
        workspace: Path,
        llm_client: LLMClient,
        config: Optional[DispatchToolConfig] = None,
        parent_events: Optional[Any] = None,  # SimpleEventStream
    ):
        self._workspace = workspace
        self._llm = llm_client
        self._config = config or DispatchToolConfig()
        self._parent_events = parent_events

        # State
        self._executor_os: Optional[AgentOS] = None
        self._event_forwarder: Optional[Callable] = None
        self._dispatch_count = 0
        self._last_dispatch_diff: Optional[WorkspaceDiff] = None
        self._start_time: Optional[float] = None

    # =========================================================================
    # Executor OS Lifecycle
    # =========================================================================

    def _get_or_create_executor(self) -> AgentOS:
        """Lazy-create Executor AgentOS, reuse across dispatches."""
        if self._executor_os is not None:
            return self._executor_os

        executor_config = AgentOSConfig(
            kernel_tools=False,  # Don't auto-register; we do it manually with workspace binding
            system_rules=self._config.executor_system_prompt,
            vcpu_config=VCPUConfig(max_iterations=self._config.executor_max_iterations),
            workspace_info=f"Workspace: {self._workspace}",
            enable_session=False,
        )
        self._executor_os = AgentOS(llm_client=self._llm, config=executor_config)

        # Register all tools WITH workspace binding
        register_default_tools(self._executor_os, workspace=self._workspace)

        # Register event forwarder
        if self._parent_events is not None:
            self._event_forwarder = lambda event: self._forward_event(event)
            self._executor_os.add_event_listener(self._event_forwarder)

        logger.info(
            f"Executor AgentOS created: tools={self._executor_os.list_tools()}"
        )
        return self._executor_os

    def _forward_event(self, event: Event) -> None:
        """Forward Executor events to parent event_stream with metadata."""
        # Add source metadata so parent can distinguish executor events
        event.data["_source"] = "executor"
        event.data["_dispatch_id"] = f"dispatch_{self._dispatch_count}"
        if self._parent_events is not None:
            self._parent_events.emit(event)

    # =========================================================================
    # Dispatch Tool Handler
    # =========================================================================

    async def dispatch(self, task: str, context: str = "", **kwargs) -> str:
        """
        Handle Dispatch tool calls from Core Agent.

        1. Check dispatch limits (count and time budget)
        2. Auto-inject context from previous dispatch if enabled
        3. Take workspace snapshot
        4. Run Executor on the task
        5. Diff workspace and return results
        """
        # Initialize start time on first dispatch
        if self._start_time is None:
            self._start_time = time.time()

        # --- Guard: dispatch count limit ---
        if self._dispatch_count >= self._config.max_dispatch_count:
            return (
                f"[Error] Maximum dispatch count ({self._config.max_dispatch_count}) reached.\n"
                f"Please work with the current results. "
                f"Use Read and Verify to check what has been done."
            )

        # --- Guard: time budget ---
        elapsed = time.time() - self._start_time
        remaining = self._config.total_timeout - elapsed
        if remaining < 30:
            return (
                f"[Error] Insufficient time budget. "
                f"Elapsed: {elapsed:.0f}s, remaining: {remaining:.0f}s.\n"
                f"Please finalize with current results."
            )

        self._dispatch_count += 1
        dispatch_num = self._dispatch_count

        logger.info(
            f"Dispatch #{dispatch_num}: {task[:80]}... "
            f"(remaining: {remaining:.0f}s)"
        )

        # --- Auto-inject context from previous dispatch ---
        if (
            self._config.auto_inject_context
            and self._last_dispatch_diff
            and self._last_dispatch_diff.has_changes
        ):
            injected = read_changed_files(
                self._workspace,
                self._last_dispatch_diff,
                max_file_size=self._config.context_max_file_size,
                max_files=self._config.context_max_files,
            )
            if injected:
                context = injected + "\n" + context if context else injected

        # --- Compose executor goal ---
        executor_goal = task
        if context:
            executor_goal = f"{task}\n\n## Context\n{context}"

        # Add time hint
        dispatch_timeout = min(self._config.dispatch_timeout, remaining - 20)
        executor_goal += (
            f"\n\nTime budget for this task: {dispatch_timeout:.0f} seconds. Be efficient."
        )

        # --- Get or create Executor ---
        executor_os = self._get_or_create_executor()

        # --- Take before snapshot ---
        snapshot_before = take_snapshot(self._workspace)

        # --- Run Executor ---
        try:
            result = await asyncio.wait_for(
                executor_os.run(executor_goal, role="executor"),
                timeout=dispatch_timeout,
            )
            executor_output = result.output or "(Executor returned no output)"
            if result.fault:
                executor_output += f"\n\nExecutor fault: {result.fault}"
        except asyncio.TimeoutError:
            executor_output = (
                f"[Executor timed out after {dispatch_timeout:.0f}s]\n"
                f"Some work may have been partially completed."
            )
            logger.warning(f"Dispatch #{dispatch_num} timed out")
        except Exception as e:
            executor_output = f"[Executor error: {e}]"
            logger.error(f"Dispatch #{dispatch_num} failed: {e}")

        # --- Take after snapshot and diff ---
        snapshot_after = take_snapshot(self._workspace)
        diff = diff_snapshots(snapshot_before, snapshot_after)
        self._last_dispatch_diff = diff

        # --- Format result ---
        output = f"## Dispatch #{dispatch_num} Result\n\n"
        output += f"### Executor Report\n{executor_output}\n\n"
        output += f"### Files Changed\n{diff.summary()}\n"

        if diff.has_changes:
            output += (
                "\nUse `Read` to inspect specific files and "
                "`Verify` to run checks before accepting."
            )

        budget_info = (
            f"\nDispatches used: {self._dispatch_count}/{self._config.max_dispatch_count}"
        )
        remaining_now = self._config.total_timeout - (time.time() - self._start_time)
        budget_info += f", Time remaining: {remaining_now:.0f}s"
        output += budget_info

        logger.info(
            f"Dispatch #{dispatch_num} done: "
            f"{len(diff.created)} created, {len(diff.modified)} modified, "
            f"{len(diff.deleted)} deleted"
        )

        return output

    # =========================================================================
    # Verify Tool Handler
    # =========================================================================

    async def verify(self, checks: Any = None, **kwargs) -> str:
        """
        Handle Verify tool calls from Core Agent.

        Delegates to run_verify_checks with the workspace path.
        """
        if checks is None:
            checks = kwargs.get("checks", [])

        if isinstance(checks, str):
            try:
                checks = json.loads(checks)
            except json.JSONDecodeError:
                return "[Error] Invalid checks format. Expected a JSON array."

        if not isinstance(checks, list) or not checks:
            return "[Error] Verify requires a non-empty 'checks' array."

        logger.info(f"Verify: running {len(checks)} checks")
        return await run_verify_checks(checks, self._workspace)

    # =========================================================================
    # Status & Cleanup
    # =========================================================================

    def get_status(self) -> Dict[str, Any]:
        """Get current dispatch tool status."""
        elapsed = time.time() - self._start_time if self._start_time else 0
        return {
            "dispatch_count": self._dispatch_count,
            "max_dispatches": self._config.max_dispatch_count,
            "elapsed_seconds": round(elapsed, 1),
            "total_timeout": self._config.total_timeout,
            "executor_created": self._executor_os is not None,
            "executor_tools": self._executor_os.list_tools() if self._executor_os else [],
        }

    def reset(self) -> None:
        """Reset dispatch state for a new task sequence."""
        self._dispatch_count = 0
        self._last_dispatch_diff = None
        self._start_time = None

    def cleanup(self) -> None:
        """Clean up resources (remove event forwarder)."""
        if self._executor_os and self._event_forwarder:
            self._executor_os.remove_event_listener(self._event_forwarder)
            self._event_forwarder = None
