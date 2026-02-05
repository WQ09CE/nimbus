"""
DualAgentOrchestrator — Core/Executor dual-agent orchestration layer.

Sits on top of AgentOS without modifying the kernel (VCPU/MMU/Gate).

Architecture:
    User Task → Core Agent (read-only, orchestration)
                    ↕ Dispatch / Verify
                Executor Agent (full permissions, implementation)
                    ↓
                Final Result
"""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.protocol import ToolResult
from nimbus.core.runtime.vcpu import LLMClient, VCPUConfig
from nimbus.tools import register_default_tools

from .prompts import CORE_SYSTEM_PROMPT, EXECUTOR_SYSTEM_PROMPT
from .tools import (
    DISPATCH_TOOL_DEF,
    VERIFY_TOOL_DEF,
    is_command_readonly,
    run_verify_checks,
)
from .workspace_diff import (
    WorkspaceDiff,
    diff_snapshots,
    read_changed_files,
    take_snapshot,
)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class OrchestratorConfig:
    """Configuration for DualAgentOrchestrator."""

    # Core agent
    core_max_iterations: int = 20
    core_system_prompt: str = CORE_SYSTEM_PROMPT

    # Executor agent
    executor_max_iterations: int = 25
    executor_system_prompt: str = EXECUTOR_SYSTEM_PROMPT

    # Dispatch limits
    max_dispatch_count: int = 5
    dispatch_timeout: float = 300.0  # seconds per dispatch

    # Total time budget
    total_timeout: float = 600.0  # seconds

    # Bash whitelist for Core
    enforce_bash_whitelist: bool = True

    # Context injection: auto-attach changed file contents on re-dispatch
    auto_inject_context: bool = True
    context_max_file_size: int = 8000
    context_max_files: int = 8


# =============================================================================
# DualAgentOrchestrator
# =============================================================================


class DualAgentOrchestrator:
    """
    Dual-Agent orchestrator: Core (read-only verifier) + Executor (implementer).

    Creates two separate AgentOS instances with different tool sets:
    - Core: Read + Bash(whitelist) + Memo + Dispatch + Verify
    - Executor: Read + Write + Edit + Bash(full) + Memo

    Usage:
        orchestrator = DualAgentOrchestrator(llm_client=llm, workspace=Path("/app"))
        result = await orchestrator.run("Build a gRPC server...")
    """

    def __init__(
        self,
        llm_client: LLMClient,
        workspace: Optional[Path] = None,
        config: Optional[OrchestratorConfig] = None,
    ):
        self._llm = llm_client
        self.workspace = workspace or Path.cwd()
        self.config = config or OrchestratorConfig()

        # Dispatch state
        self._dispatch_count = 0
        self._last_dispatch_diff: Optional[WorkspaceDiff] = None
        self._start_time: Optional[float] = None

        # Create the two AgentOS instances
        self._core_os = self._create_core_os()
        self._executor_os = self._create_executor_os()

        logger.info(
            f"DualAgentOrchestrator initialized: "
            f"workspace={self.workspace}, "
            f"core_iter={self.config.core_max_iterations}, "
            f"executor_iter={self.config.executor_max_iterations}"
        )

    # =========================================================================
    # AgentOS Factory
    # =========================================================================

    def _create_core_os(self) -> AgentOS:
        """Create Core Agent's AgentOS — read-only tools + Dispatch + Verify."""
        core_config = AgentOSConfig(
            kernel_tools=False,  # Do NOT auto-register Read/Write/Edit/Bash
            system_rules=self.config.core_system_prompt,
            vcpu_config=VCPUConfig(max_iterations=self.config.core_max_iterations),
            workspace_info=f"Workspace: {self.workspace}",
            enable_session=False,  # Lightweight, no persistence needed
        )
        os = AgentOS(llm_client=self._llm, config=core_config)

        # Register only read-only kernel tools
        register_default_tools(os, workspace=self.workspace, tools=["Read", "Bash"])

        # If whitelist enforcement is on, wrap the Bash tool
        if self.config.enforce_bash_whitelist:
            self._wrap_core_bash(os)

        # Register Dispatch tool (implemented by this orchestrator)
        os.register_tool(
            name="Dispatch",
            func=self._handle_dispatch,
            description=DISPATCH_TOOL_DEF["description"],
            parameters=DISPATCH_TOOL_DEF["parameters"],
        )

        # Register Verify tool
        os.register_tool(
            name="Verify",
            func=self._handle_verify,
            description=VERIFY_TOOL_DEF["description"],
            parameters=VERIFY_TOOL_DEF["parameters"],
        )

        logger.info(f"Core AgentOS created: tools={os.list_tools()}")
        return os

    def _create_executor_os(self) -> AgentOS:
        """Create Executor Agent's AgentOS — full tool permissions."""
        executor_config = AgentOSConfig(
            kernel_tools=False,  # Don't auto-register — we do it manually with workspace binding
            system_rules=self.config.executor_system_prompt,
            vcpu_config=VCPUConfig(max_iterations=self.config.executor_max_iterations),
            workspace_info=f"Workspace: {self.workspace}",
            enable_session=False,
        )
        os = AgentOS(llm_client=self._llm, config=executor_config)

        # Register all tools WITH workspace binding so files go to the right place
        register_default_tools(os, workspace=self.workspace)

        logger.info(f"Executor AgentOS created: tools={os.list_tools()}")
        return os

    def _wrap_core_bash(self, os: AgentOS) -> None:
        """
        Replace Core's Bash tool with a whitelist-filtered version.

        If the command doesn't match the whitelist, returns an error message
        instead of executing.
        """
        # Get the original Bash function from the registry
        original_entry = os._tools.get("Bash")
        if not original_entry:
            return

        original_def, original_func = original_entry

        async def filtered_bash(**kwargs):
            command = kwargs.get("command", "")
            if not is_command_readonly(command):
                return (
                    f"[Error] Core Agent cannot execute write commands.\n"
                    f"Blocked command: {command[:100]}\n"
                    f"Use Dispatch to delegate write operations to the Executor."
                )
            return await original_func(**kwargs)

        # Re-register with the filtered version
        os._tools.unregister("Bash")
        os._tools.register(original_def, filtered_bash)

    # =========================================================================
    # Main Entry Point
    # =========================================================================

    async def run(self, goal: str) -> ToolResult:
        """
        Execute a task using the dual-agent architecture.

        The Core Agent receives the goal, plans, dispatches to Executor,
        verifies, and returns the final result.

        Args:
            goal: The user's task description

        Returns:
            ToolResult from the Core Agent's execution
        """
        self._start_time = time.time()
        self._dispatch_count = 0
        self._last_dispatch_diff = None

        logger.info(f"🚀 DualAgent run started: {goal[:100]}...")

        result = await self._core_os.run(goal, role="core")

        elapsed = time.time() - self._start_time
        logger.info(
            f"🏁 DualAgent run completed: "
            f"status={result.status}, "
            f"dispatches={self._dispatch_count}, "
            f"elapsed={elapsed:.1f}s"
        )

        return result

    # =========================================================================
    # Dispatch Tool Handler
    # =========================================================================

    async def _handle_dispatch(self, task: str, context: str = "", **kwargs) -> str:
        """
        Handle Dispatch tool calls from Core Agent.

        1. Check dispatch limits (count and time budget)
        2. Auto-inject context from previous dispatch if enabled
        3. Take workspace snapshot
        4. Run Executor on the task
        5. Diff workspace and return results
        """
        # --- Guard: dispatch count limit ---
        if self._dispatch_count >= self.config.max_dispatch_count:
            return (
                f"[Error] Maximum dispatch count ({self.config.max_dispatch_count}) reached.\n"
                f"Please work with the current results. "
                f"Use Read and Verify to check what has been done."
            )

        # --- Guard: time budget ---
        elapsed = time.time() - (self._start_time or time.time())
        remaining = self.config.total_timeout - elapsed
        if remaining < 30:
            return (
                f"[Error] Insufficient time budget. "
                f"Elapsed: {elapsed:.0f}s, remaining: {remaining:.0f}s.\n"
                f"Please finalize with current results."
            )

        self._dispatch_count += 1
        dispatch_num = self._dispatch_count

        logger.info(
            f"📤 Dispatch #{dispatch_num}: {task[:80]}... "
            f"(remaining: {remaining:.0f}s)"
        )

        # --- Auto-inject context from previous dispatch ---
        if self.config.auto_inject_context and self._last_dispatch_diff and self._last_dispatch_diff.has_changes:
            injected = read_changed_files(
                self.workspace,
                self._last_dispatch_diff,
                max_file_size=self.config.context_max_file_size,
                max_files=self.config.context_max_files,
            )
            if injected:
                context = injected + "\n" + context if context else injected

        # --- Compose executor goal ---
        executor_goal = task
        if context:
            executor_goal = f"{task}\n\n## Context\n{context}"

        # Add time hint
        dispatch_timeout = min(self.config.dispatch_timeout, remaining - 20)
        executor_goal += f"\n\n⏱ Time budget for this task: {dispatch_timeout:.0f} seconds. Be efficient."

        # --- Take before snapshot ---
        snapshot_before = take_snapshot(self.workspace)

        # --- Run Executor ---
        try:
            result = await asyncio.wait_for(
                self._executor_os.run(executor_goal, role="executor"),
                timeout=dispatch_timeout,
            )
            executor_output = result.output or "(Executor returned no output)"
            if result.fault:
                executor_output += f"\n\n⚠️ Executor fault: {result.fault}"
        except asyncio.TimeoutError:
            executor_output = (
                f"[Executor timed out after {dispatch_timeout:.0f}s]\n"
                f"Some work may have been partially completed."
            )
            logger.warning(f"⏱ Dispatch #{dispatch_num} timed out")
        except Exception as e:
            executor_output = f"[Executor error: {e}]"
            logger.error(f"💥 Dispatch #{dispatch_num} failed: {e}")

        # --- Take after snapshot and diff ---
        snapshot_after = take_snapshot(self.workspace)
        diff = diff_snapshots(snapshot_before, snapshot_after)
        self._last_dispatch_diff = diff

        # --- Format result ---
        output = f"## Dispatch #{dispatch_num} Result\n\n"
        output += f"### Executor Report\n{executor_output}\n\n"
        output += f"### Files Changed\n{diff.summary()}\n"

        if diff.has_changes:
            output += (
                f"\n💡 Use `Read` to inspect specific files and "
                f"`Verify` to run checks before accepting."
            )

        budget_info = f"\n📊 Dispatches used: {self._dispatch_count}/{self.config.max_dispatch_count}"
        remaining_now = self.config.total_timeout - (time.time() - (self._start_time or time.time()))
        budget_info += f", Time remaining: {remaining_now:.0f}s"
        output += budget_info

        logger.info(
            f"📥 Dispatch #{dispatch_num} done: "
            f"{len(diff.created)} created, {len(diff.modified)} modified, "
            f"{len(diff.deleted)} deleted"
        )

        return output

    # =========================================================================
    # Verify Tool Handler
    # =========================================================================

    async def _handle_verify(self, checks: Any = None, **kwargs) -> str:
        """
        Handle Verify tool calls from Core Agent.

        Delegates to run_verify_checks with the workspace path.
        """
        if checks is None:
            checks = kwargs.get("checks", [])

        if isinstance(checks, str):
            # LLM sometimes sends JSON string
            import json
            try:
                checks = json.loads(checks)
            except json.JSONDecodeError:
                return "[Error] Invalid checks format. Expected a JSON array."

        if not isinstance(checks, list) or not checks:
            return "[Error] Verify requires a non-empty 'checks' array."

        logger.info(f"🔍 Verify: running {len(checks)} checks")
        return await run_verify_checks(checks, self.workspace)

    # =========================================================================
    # Status
    # =========================================================================

    def get_status(self) -> Dict[str, Any]:
        """Get current orchestrator status."""
        elapsed = time.time() - self._start_time if self._start_time else 0
        return {
            "dispatch_count": self._dispatch_count,
            "max_dispatches": self.config.max_dispatch_count,
            "elapsed_seconds": round(elapsed, 1),
            "total_timeout": self.config.total_timeout,
            "core_tools": self._core_os.list_tools(),
            "executor_tools": self._executor_os.list_tools(),
        }
