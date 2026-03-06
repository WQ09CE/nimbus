"""
Bootstrap factory for AgentOS.

Provides create_agent_os() -- the main entry point for creating a fully
configured AgentOS instance with tools, profiles, and specialist wiring.

Moved here from nimbus.agentos to keep the core AgentOS class lean.
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.profile import AgentProfile


def create_agent_os(
    llm_client: Any,
    tools: Optional[Dict[str, Callable]] = None,
    system_rules: str = "",
    max_processes: int = 10,
    default_timeout: float = 300.0,
    workspace: Optional["Path"] = None,
    register_defaults: bool = True,
    kernel_tools: bool = True,
    skill_paths: Optional[List[Path]] = None,
    # New arguments
    config: Optional[AgentOSConfig] = None,
    profile: Optional[str | AgentProfile] = None,
    model_id: str = "default",
) -> AgentOS:
    """
    Factory function to create an AgentOS with common defaults.

    Args:
        llm_client: LLM client for vCPUs
        tools: Initial tool registry (additional to defaults)
        system_rules: System rules for all processes
        max_processes: Maximum concurrent processes
        default_timeout: Default execution timeout
        workspace: Workspace path for tool sandboxing
        register_defaults: Whether to register default v2 tools (Read, Write, etc.)
        kernel_tools: Whether to auto-register kernel tools (Read, Write, Edit, Bash)
        profile: AgentProfile configuration (overrides manual config)
        model_id: Model ID for dynamic prompt generation

    Returns:
        Configured AgentOS instance with default tools registered
    """
    from pathlib import Path

    if workspace is None:
        workspace = Path.cwd()

    if config is None:
        config = AgentOSConfig(
            max_processes=max_processes,
            default_timeout=default_timeout,
            system_rules=system_rules or AgentOSConfig.system_rules,
            workspace_info=f"Workspace: {workspace}",
            kernel_tools=kernel_tools,
            skill_paths=skill_paths or [],
        )

    # Allow overriding limits via environment variables (for testing compaction)
    import os as _os
    _max_ctx = _os.environ.get("NIMBUS_MAX_CONTEXT_TOKENS")
    if _max_ctx:
        config.mmu_config.max_context_tokens = int(_max_ctx)
        config.mmu_config.frame_budget = max(int(_max_ctx) - config.mmu_config.pinned_budget, 1000)
        logger.info(f"MMU override: max_context_tokens={config.mmu_config.max_context_tokens}, frame_budget={config.mmu_config.frame_budget}")
    _max_iter = _os.environ.get("NIMBUS_MAX_ITERATIONS")
    if _max_iter:
        config.vcpu_config.max_iterations = int(_max_iter)
        logger.info(f"VCPU override: max_iterations={config.vcpu_config.max_iterations}")

    # Handle profile overrides
    target_profile = None
    if isinstance(profile, str):
        if profile == "executor":
            target_profile = AgentProfile.create_executor(model_id)
        elif profile == "orchestrator":
            target_profile = AgentProfile.create_orchestrator(model_id)
        else:
            target_profile = AgentProfile.create_standard(model_id)
    elif isinstance(profile, AgentProfile):
        target_profile = profile

    if target_profile:
        config.system_rules = target_profile.system_prompt
        # Apply runtime config from profile to VCPU config
        # (env var overrides take precedence over profile defaults)
        if not _max_iter:
            config.vcpu_config.max_iterations = target_profile.max_iterations
        config.vcpu_config.max_consecutive_thoughts = target_profile.max_consecutive_thoughts

    os = AgentOS(llm_client=llm_client, tools=tools, config=config)

    if register_defaults:
        from nimbus.tools import register_default_tools
        ws = workspace

        # If profile is None, workspace is already set above
        # If profile is present, use it to determine tool registration

        if isinstance(profile, str) and profile == "orchestrator":
            # Orchestrator Profile: Specialist tools + basic tools
            # All @tool-decorated tools (visibility controlled by AgentProfile.allowed_tools)
            register_default_tools(os, workspace=ws)

            # --- Register SubmitResult pseudo-tool (for specialist agents only) ---
            # This is a "fake tool" -- VCPU intercepts it in _handle_tool_call before
            # reaching the Gate.  It gives backend specialists an explicit, deterministic
            # way to signal task completion instead of relying on plain-text heuristics.
            async def _submit_result_noop(**kwargs):
                # Should never be called; VCPU intercepts SubmitResult before Gate.
                return kwargs.get("result", "")

            os.register_tool(
                name="SubmitResult",
                func=_submit_result_noop,
                description=(
                    "Submit your final result and end the task. You MUST call this tool "
                    "when your work is complete. Pass your findings/summary as the "
                    "'result' parameter. Plain text output is NOT delivered -- only "
                    "SubmitResult output is returned to the orchestrator."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "result": {
                            "type": "string",
                            "description": "Your final result summary to return to the orchestrator.",
                        },
                    },
                    "required": ["result"],
                },
                category="system",
            )

            # --- Register Specialist Tools ---
            from nimbus.orchestration.specialist_tools import (
                ExploreTool, ImplementTool, DesignTool, TestTool,
            )
            from nimbus.orchestration.tools import (
                EXPLORE_TOOL_DEF, IMPLEMENT_TOOL_DEF, DESIGN_TOOL_DEF, TEST_TOOL_DEF,
                VERIFY_TOOL_DEF,
            )

            explore_tool = ExploreTool(agent_os=os, workspace=ws)
            implement_tool = ImplementTool(agent_os=os, workspace=ws)
            design_tool = DesignTool(agent_os=os, workspace=ws)
            test_tool = TestTool(agent_os=os, workspace=ws)

            for tool_inst, tool_def in [
                (explore_tool, EXPLORE_TOOL_DEF),
                (implement_tool, IMPLEMENT_TOOL_DEF),
                (design_tool, DESIGN_TOOL_DEF),
                (test_tool, TEST_TOOL_DEF),
            ]:
                os.register_tool(
                    name=tool_def["name"],
                    func=tool_inst.execute,
                    description=tool_def["description"],
                    parameters=tool_def["parameters"],
                    category="extension",
                )


            # Register Verify (standalone, no DispatchTool dependency)
            async def _verify_handler(checks=None, **kwargs):
                import json as _json
                if checks is None:
                    checks = kwargs.get("checks", [])
                if isinstance(checks, str):
                    try:
                        checks = _json.loads(checks)
                    except _json.JSONDecodeError:
                        return "[Error] Invalid checks format. Expected a JSON array."
                if not isinstance(checks, list) or not checks:
                    return "[Error] Verify requires a non-empty 'checks' array."
                return await run_verify_checks(checks, ws)

            from nimbus.orchestration.tools import run_verify_checks
            os.register_tool(
                name="Verify",
                func=_verify_handler,
                description=VERIFY_TOOL_DEF["description"],
                parameters=VERIFY_TOOL_DEF["parameters"],
                category="extension",
            )

            # Register ReviewCommittee
            from nimbus.orchestration.review_tool import REVIEW_TOOL_DEF, ReviewTool
            review_tool = ReviewTool(agent_os=os, workspace=ws)
            os.register_tool(
                name="ReviewCommittee",
                func=review_tool.review,
                description=REVIEW_TOOL_DEF["description"],
                parameters=REVIEW_TOOL_DEF["parameters"],
                category="extension",
            )

        else:
            # Standard Profile: All tools for everyone
            register_default_tools(os, workspace=ws)

    elif kernel_tools:
        # No default tools, but kernel tools requested (e.g. basic_tools_only models)
        from nimbus.tools import register_default_tools
        ws = workspace
        register_default_tools(os, workspace=ws, tools=["Bash", "Read", "Write", "Edit"])
        logger.info("🔧 Kernel-only tools registered: Bash, Read, Write, Edit")

    return os
