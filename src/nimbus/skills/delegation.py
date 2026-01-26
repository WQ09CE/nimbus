import uuid
import logging
import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from nimbus.tools.base import tool, ToolParameter

if TYPE_CHECKING:
    from nimbus.server.session import SessionManager

logger = logging.getLogger(__name__)


class DelegationSkill:
    """Skill for delegating tasks to sub-agents."""

    def __init__(self, session_manager: "SessionManager", parent_session_id: str):
        self.session_manager = session_manager
        self.parent_session_id = parent_session_id

    @tool(
        name="DelegateTask",
        description="Delegate a complex sub-task to a specialized sub-agent. Use this when the task requires multi-step reasoning, independent research, or is a distinct unit of work.",
        parameters=[
            ToolParameter(
                name="goal",
                type="string",
                description="The specific goal for the sub-agent.",
                required=True,
            ),
            ToolParameter(
                name="context",
                type="string",
                description="Relevant context or background info the sub-agent needs to know.",
                required=False,
            ),
        ],
    )
    async def delegate_task(self, goal: str, context: str = "") -> str:
        """Delegate a task to a sub-agent.

        Args:
            goal: The specific goal for the sub-agent.
            context: Relevant context or background info.

        Returns:
            The sub-agent's final response.
        """
        # 1. Create sub-session ID (namespace isolation)
        sub_session_id = f"{self.parent_session_id}_sub_{uuid.uuid4().hex[:8]}"

        logger.info(f"Delegating task to sub-agent {sub_session_id}: {goal[:50]}...")

        try:
            # 2. Get/Create sub-agent
            # Note: We reuse the session manager to create a new isolated session/agent
            sub_agent = await self.session_manager.get_or_create_agent(session_id=sub_session_id)

            # 3. Construct initial prompt
            full_prompt = f"{context}\n\nTask Goal: {goal}" if context else goal

            # 4. Run sub-agent
            # We use run() to wait for the result.
            response = await sub_agent.run(full_prompt)

            logger.info(f"Sub-agent {sub_session_id} finished.")

            return response.text

        except Exception as e:
            logger.error(f"Sub-agent execution failed: {e}")
            return f"Sub-agent execution failed: {str(e)}"
        finally:
            # 5. Cleanup (optional but recommended for ephemeral sub-tasks)
            # We use a try-except to ensure cleanup doesn't block the result
            try:
                await self.session_manager.delete_session(sub_session_id)
            except Exception as e:
                logger.warning(f"Failed to cleanup sub-session {sub_session_id}: {e}")
