from typing import Any, Dict, Optional
from nimbus.tools.base import ToolDefinition, ToolParameter, ToolExecutionError
from nimbus.core.logging import get_logger

logger = get_logger("nimbus.core.ipc")

def create_spawn_subagent_tool(agentos_ref: Any, parent_pid: str) -> tuple[ToolDefinition, Any]:
    """
    Creates the 'SpawnSubAgent' tool, allowing an agent to delegate work to a child process.
    """
    definition = ToolDefinition(
        name="SpawnSubAgent",
        description=(
            "Spawn a new sub-agent to handle a specific delegation. "
            "Returns the PID of the new agent. You can communicate with it using SendMessage."
        ),
        parameters=[
            ToolParameter(
                name="goal",
                type="string",
                description="The detailed mission objective for the sub-agent. Be extremely precise."
            ),
            ToolParameter(
                name="role",
                type="string",
                description="The role profile for the sub-agent ('engineer', 'architect', 'researcher', etc.)",
                enum=["engineer", "architect", "researcher"] # Basic presets, AgentOS can expand this
            )
        ]
    )

    async def execute(goal: str, role: str) -> str:
        # We prefix the user goal with [DELEGATION from {parent_pid}]
        delegation_goal = f"[DELEGATION from {parent_pid}]\nMission: {goal}\n\nWhen you are finished, use the SendMessage tool to send your final results to target_pid: '{parent_pid}'."
        
        try:
            from nimbus.core.profile import AgentProfile
            child_pid = agentos_ref.spawn(
                goal=delegation_goal,
                profile=AgentProfile(role=role)
            )
            # Execute the process asynchronously in the background so it runs parallel to the parent
            import asyncio
            # We don't block here, the AgentOS run loop handles task dispatch
            # Wait, AgentOS API says we must call wait() to start it if we don't use run()
            process = agentos_ref._processes[child_pid]
            process.task = asyncio.create_task(agentos_ref._run_process(process))
            
            return f"SubAgent spawned successfully with PID: {child_pid}. Use SendMessage to send it specific data contracts."
        except Exception as e:
            raise ToolExecutionError(f"Failed to spawn sub-agent: {e}")

    return definition, execute
