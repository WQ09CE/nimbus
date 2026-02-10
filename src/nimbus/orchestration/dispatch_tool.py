"""
DispatchTool -- Meta-Tool for Dual-Agent orchestration.

Spawns Executor processes via the parent AgentOS kernel using role-based
tool filtering.  No nested AgentOS instances.

Usage:
    dispatch_tool = DispatchTool(agent_os=core_os, config=config)

    # Register on the same AgentOS
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

from nimbus.adapters.llm_factory import create_llm_client
from nimbus.agentos import AgentOS

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
    executor_max_iterations: int = 15  # Right-sized dispatch should need 1-10 tool calls
    executor_system_prompt: str = EXECUTOR_SYSTEM_PROMPT

    # Dispatch limits
    max_dispatch_count: int = 8
    dispatch_timeout: float = 120.0  # seconds per dispatch (right-sized task ≈ 30-90s)

    # Total time budget
    total_timeout: float = 900.0  # seconds (8 dispatches × ~120s max, with overhead)

    # Context injection: auto-attach changed file contents on re-dispatch
    auto_inject_context: bool = True
    context_max_file_size: int = 8000
    context_max_files: int = 8

    # Model alias mapping for Executor
    model_aliases: Dict[str, str] = field(default_factory=lambda: {
        # Short aliases
        "claude": "anthropic/claude-opus-4-6",
        "opus": "anthropic/claude-opus-4-6",
        "sonnet": "anthropic/claude-sonnet-4-20250514",
        "gpt": "openai-codex/gpt-5.3-codex",
        "gpt5": "openai-codex/gpt-5.3-codex",
        "gpt-5.3": "openai-codex/gpt-5.3-codex",
        "codex": "openai-codex/gpt-5.3-codex",
        "gemini": "google-antigravity/gemini-3-pro-high",
        "gemini3": "google-antigravity/gemini-3-pro-high",
        "gemini-pro": "google-antigravity/gemini-3-pro-high",
    })


class DispatchTool:
    """
    Dispatch Meta-Tool: spawns Executor processes on the parent AgentOS kernel.

    Registered on the Core AgentOS as a regular tool.  Uses
    ``agent_os.spawn(role="executor")`` to create child processes that
    inherit the kernel's tool registry (filtered by role).

    Features:
    - Native child-process model (no nested AgentOS)
    - Workspace snapshot/diff tracking
    - Dispatch count and time budget enforcement
    """

    def __init__(
        self,
        agent_os: AgentOS,
        config: Optional[DispatchToolConfig] = None,
        workspace: Optional[Path] = None,
    ):
        self._agent_os = agent_os
        self._workspace = workspace or Path.cwd()
        self._config = config or DispatchToolConfig()
        
        # State
        self._dispatch_count = 0
        self._last_dispatch_diff: Optional[WorkspaceDiff] = None
        self._start_time: Optional[float] = None
        self._executor_pid: Optional[str] = None

    # =========================================================================
    # Dispatch Tool Handler
    # =========================================================================

    async def dispatch(self, task: str, context: str = "", **kwargs) -> str:
        """
        Handle Dispatch tool calls from Core Agent.

        1. Check dispatch limits (count and time budget)
        2. Auto-inject context from previous dispatch if enabled
        3. Take workspace snapshot
        4. Spawn Executor process via AgentOS
        5. Diff workspace and return results
        """
        # Auto-reset if previous run's time budget is exhausted
        if self._start_time is not None:
            elapsed_since_start = time.time() - self._start_time
            if elapsed_since_start > self._config.total_timeout:
                logger.info("Auto-resetting DispatchTool (previous time budget exhausted)")
                self.reset()

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

        # Executor is controlled by max_iterations, not wall-clock timeout.
        # Only add a conciseness hint, no time pressure.
        executor_goal += (
            f"\n\nYou have up to {self._config.executor_max_iterations} tool calls. Be efficient."
        )

        # --- Take before snapshot ---
        snapshot_before = take_snapshot(self._workspace)

        # --- Spawn Executor Process ---
        # We reuse the same PID if possible? No, each dispatch is a new task usually.
        # But if we want to share memory (Memo), we might want to attach to same session?
        # For now, let's spawn a fresh process for each dispatch, 
        # but we could implement state persistence later.
        
        # --- Resolve model for Executor ---
        executor_llm = None
        model_name = kwargs.get("model", "")
        if model_name:
            # Resolve alias
            resolved = self._config.model_aliases.get(model_name.lower().strip(), model_name)
            # Ensure it has provider prefix
            if "/" not in resolved:
                resolved = self._config.model_aliases.get(resolved.lower(), resolved)
            try:
                executor_llm = await create_llm_client(resolved)
                logger.info(f"  🤖 Executor using model: {resolved}")
            except Exception as e:
                logger.warning(f"  ⚠️ Failed to create LLM for {resolved}: {e}, using default")

        # NOTE: Native spawn with role="executor"
        pid = self._agent_os.spawn(
            goal=executor_goal, 
            role="executor", 
            system_rules=self._config.executor_system_prompt,
            max_iterations=self._config.executor_max_iterations,
            llm_client=executor_llm,
        )
        self._executor_pid = pid
        
        # --- Event Forwarding Hook ---
        # We need to bridge Executor events to the parent stream so UI can see them.
        # The parent stream is usually available via the Core process mechanism 
        # or we just rely on Global Event Bus if AgentOS supported it.
        # Since DispatchTool.dispatch is just a function, we can't easily yield events.
        # 
        # Solution: The AgentOS global event stream should already be emitting these events.
        # The UI needs to listen to ALL events or we need to chain them.
        # 
        # For now, we assume the UI (or client) listens to the OS event stream 
        # and filters/displays all PIDs.
        
        logger.info(f"Spawned Executor process {pid}")

        # --- Wait for completion ---
        # Executor stops naturally when it hits max_iterations or returns.
        # We use total_timeout as a safety net only (not per-dispatch).
        safety_timeout = max(remaining, 60.0)  # At least 60s safety margin
        executor_output = ""
        try:
            result = await self._agent_os.wait(pid, timeout=safety_timeout)
            executor_output = result.output or "(Executor returned no output)"
            if result.fault:
                executor_output += f"\n\nExecutor fault: {result.fault}"
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
            "last_executor_pid": self._executor_pid,
        }

    def reset(self) -> None:
        """Reset dispatch state for a new task sequence."""
        self._dispatch_count = 0
        self._last_dispatch_diff = None
        self._start_time = None
        self._executor_pid = None

    def cleanup(self) -> None:
        """Clean up resources."""
        self.reset()
