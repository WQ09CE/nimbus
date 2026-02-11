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

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.protocol import ToolResult
from nimbus.core.runtime.vcpu import LLMClient, VCPUConfig
from nimbus.tools import register_default_tools

from .dispatch_tool import DispatchTool, DispatchToolConfig
from .prompts import PromptManager  # Use dynamic manager
from .tools import (
    DISPATCH_TOOL_DEF,
    VERIFY_TOOL_DEF,
    register_core_bash,
)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class OrchestratorConfig:
    """Configuration for DualAgentOrchestrator."""

    # Core agent
    core_max_iterations: int = 20
    # core_system_prompt removed: we use PromptManager

    # Executor agent
    executor_max_iterations: int = 15
    # executor_system_prompt removed: we use PromptManager

    # Dispatch limits
    max_dispatch_count: int = 8
    dispatch_timeout: float = 120.0
    total_timeout: float = 900.0

    # Bash whitelist for Core
    enforce_bash_whitelist: bool = True

    # Context injection
    auto_inject_context: bool = True
    context_max_file_size: int = 8000
    context_max_files: int = 8


# =============================================================================
# DualAgentOrchestrator
# =============================================================================


class DualAgentOrchestrator:
    """
    Dual-Agent orchestrator: Core (read-only verifier) + Executor (implementer).

    Uses a single AgentOS kernel with Role-Based Access Control (RBAC) for tools.
    - Core Process: Read + CoreBash + Dispatch + Verify
    - Executor Process: Read + Write + Edit + Bash (Full)
    """

    def __init__(
        self,
        llm_client: LLMClient,
        workspace: Optional[Path] = None,
        config: Optional[OrchestratorConfig] = None,
        model_id: str = "default",  # NEW: Pass model ID for Core prompt
    ):
        self._llm = llm_client
        self.workspace = workspace or Path.cwd()
        self.config = config or OrchestratorConfig()
        
        # Generate Core System Prompt
        core_prompt = PromptManager.get_system_prompt("core", model_id)

        # 1. Create Main AgentOS (Kernel)
        # Default system rules are for Core Agent (primary user of run())
        os_config = AgentOSConfig(
            kernel_tools=False,
            system_rules=core_prompt,
            vcpu_config=VCPUConfig(max_iterations=self.config.core_max_iterations),
            workspace_info=f"Workspace: {self.workspace}",
            enable_session=False,
        )
        self._core_os = AgentOS(llm_client=self._llm, config=os_config)

        # 2. Register Shared Tools (Available to ALL roles)
        register_default_tools(
            self._core_os, 
            workspace=self.workspace, 
            tools=["Read"]
        )

        # 3. Register Executor Tools (Restricted to 'executor' role)
        register_default_tools(
            self._core_os,
            workspace=self.workspace,
            tools=["Write", "Edit", "Bash"],
            roles=["executor"]
        )

        # 4. Register Core Specific Tools
        # CoreBash (Restricted Shell) -> 'core' role
        if self.config.enforce_bash_whitelist:
            register_core_bash(self._core_os)
        else:
            # Whitelist not enforced: give Core full Bash under "CoreBash" name
            # so the system prompt's tool references still work
            from nimbus.tools import get_tool, get_tool_function, create_workspace_wrapper
            bash_def = get_tool("Bash")
            bash_func = get_tool_function("Bash")
            if bash_def and bash_func:
                wrapped = create_workspace_wrapper(bash_func, self.workspace)
                self._core_os.register_tool(
                    name="CoreBash",
                    func=wrapped,
                    description=bash_def["description"],
                    parameters=bash_def.get("parameters"),
                    roles=["core"],
                )

        # 5. Initialize Dispatch Tool
        dispatch_config = DispatchToolConfig(
            executor_max_iterations=self.config.executor_max_iterations,
            max_dispatch_count=self.config.max_dispatch_count,
            dispatch_timeout=self.config.dispatch_timeout,
            total_timeout=self.config.total_timeout,
            auto_inject_context=self.config.auto_inject_context,
            context_max_file_size=self.config.context_max_file_size,
            context_max_files=self.config.context_max_files,
        )
        
        # Pass the single OS instance
        self._dispatch_tool = DispatchTool(
            agent_os=self._core_os,
            config=dispatch_config,
            workspace=self.workspace,
        )

        # 6. Register Orchestration Tools (Core Only)
        self._core_os.register_tool(
            name="Dispatch",
            func=self._dispatch_tool.dispatch,
            description=DISPATCH_TOOL_DEF["description"],
            parameters=DISPATCH_TOOL_DEF["parameters"],
            roles=["core"],
        )
        self._core_os.register_tool(
            name="Verify",
            func=self._dispatch_tool.verify,
            description=VERIFY_TOOL_DEF["description"],
            parameters=VERIFY_TOOL_DEF["parameters"],
            roles=["core"],
        )

        logger.info(
            f"DualAgentOrchestrator initialized on Single Kernel: "
            f"workspace={self.workspace}, "
            f"tools={self._core_os.list_tools()}"
        )

    @property
    def _dispatch_count(self) -> int:
        """Backward-compatible accessor (delegates to DispatchTool)."""
        return self._dispatch_tool._dispatch_count

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
        # Reset dispatch state for this run
        self._dispatch_tool.reset()

        logger.info(f"🚀 DualAgent run started: {goal[:100]}...")

        result = await self._core_os.run(goal, role="core")

        status = self._dispatch_tool.get_status()
        logger.info(
            f"🏁 DualAgent run completed: "
            f"status={result.status}, "
            f"dispatches={status['dispatch_count']}, "
            f"elapsed={status['elapsed_seconds']}s"
        )

        return result

    # =========================================================================
    # Status & Cleanup
    # =========================================================================

    def get_status(self) -> Dict[str, Any]:
        """Get current orchestrator status."""
        dispatch_status = self._dispatch_tool.get_status()
        return {
            **dispatch_status,
            "core_tools": self._core_os.list_tools(),
        }

    def cleanup(self) -> None:
        """Clean up resources."""
        self._dispatch_tool.cleanup()
