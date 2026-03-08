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
            ),
            ToolParameter(
                name="expected_schema",
                type="string",
                description="Optional. A JSON object representing the expected structure of the final payload to be sent back via SendMessage. If provided, the Middleware Verify Gate will enforce this contract automatically."
            ),
            ToolParameter(
                name="timeout",
                type="number",
                description="Optional. The maximum time in seconds the sub-agent is allowed to run before being terminated."
            )
        ]
    )

    async def execute(goal: str, role: str, expected_schema: Optional[str] = None, timeout: Optional[float] = None) -> str:
        # We prefix the user goal with [DELEGATION from {parent_pid}]
        delegation_goal = f"[DELEGATION from {parent_pid}]\nMission: {goal}\n\nWhen you are finished, use the SendMessage tool to send your final results to target_pid: '{parent_pid}'."
        if expected_schema:
            delegation_goal += f"\n\nCRITICAL CONTRACT: Your SendMessage payload MUST strictly conform to the following JSON structure/keys:\n{expected_schema}"
        
        try:
            from nimbus.core.profile import AgentProfile
            child_pid = agentos_ref.spawn(
                goal=delegation_goal,
                profile=AgentProfile(name=role, role=role)
            )
            # Execute the process asynchronously in the background so it runs parallel to the parent
            import asyncio
            # We don't block here, the AgentOS run loop handles task dispatch
            # Wait, AgentOS API says we must call wait() to start it if we don't use run()
            process = agentos_ref._processes[child_pid]
            if expected_schema:
                process.metadata["expected_schema"] = expected_schema
                
            process.task = asyncio.create_task(agentos_ref.wait(child_pid, timeout=timeout))
            
            return f"SubAgent spawned successfully with PID: {child_pid}. Use SendMessage to send it specific data contracts."
        except Exception as e:
            raise ToolExecutionError("SpawnSubAgent", f"Failed to spawn sub-agent: {e}")

    return definition, execute
