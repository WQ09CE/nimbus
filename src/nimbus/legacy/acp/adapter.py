"""Nimbus ACP Adapter - Maps ACP operations to Nimbus internals.

This module provides the adapter layer between the ACP protocol and Nimbus Core.
It handles session management, event translation, and coordinates with the
Nimbus CodeAgent for task execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Union

from nimbus.core.agent import CodeAgent
from nimbus.core.factory import AgentFactory
from nimbus.core.types import TaskStatus

from .types import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ContentBlock,
    ModelInfo,
    SessionMode,
    SessionModelState,
    SessionModeState,
    SessionUpdate,
    TextContent,
    ToolCall,
    ToolCallUpdate,
)

log = logging.getLogger("nimbus.acp.adapter")


@dataclass
class ACPConfig:
    """Configuration for Nimbus ACP Agent.

    Attributes:
        cwd: Working directory for sessions. Defaults to current directory.
        llm_model: LLM model to use. Defaults to claude-3-5-sonnet.
        llm_url: Optional custom LLM API URL.
        api_key_env: Environment variable name for API key.
        system_prompt: Optional system prompt for the agent.
        memory_type: Memory implementation type ("simple" or "tiered").
        planner_type: Planner implementation type ("simple" or "dag").
    """

    cwd: str | None = None
    llm_model: str | None = None
    llm_url: str | None = None
    api_key_env: str = "ANTHROPIC_API_KEY"
    system_prompt: str = ""
    memory_type: str = "simple"
    planner_type: str = "dag"


@dataclass
class NimbusSession:
    """ACP Session state.

    Maps an ACP session to a Nimbus CodeAgent instance and tracks
    session-level metadata.

    Attributes:
        id: ACP session ID.
        cwd: Working directory for this session.
        agent: The underlying Nimbus CodeAgent instance.
        created_at: When the session was created.
        current_mode: Current session mode ID.
        is_running: Whether a prompt is currently being executed.
        cancel_event: Event to signal cancellation.
    """

    id: str
    cwd: str
    agent: CodeAgent | None = None
    created_at: datetime = field(default_factory=datetime.now)
    current_mode: str = "default"
    is_running: bool = False
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


# Default modes that Nimbus supports
DEFAULT_MODES: list[SessionMode] = [
    {
        "id": "default",
        "name": "Default",
        "description": "Standard code exploration and assistance mode",
    },
    {
        "id": "explore",
        "name": "Explore",
        "description": "Focus on code exploration and understanding",
    },
    {
        "id": "generate",
        "name": "Generate",
        "description": "Focus on code generation and editing",
    },
]

# Default model info
DEFAULT_MODEL: ModelInfo = {
    "modelId": "claude-3-5-sonnet",
    "name": "Claude 3.5 Sonnet",
    "description": "Anthropic's Claude 3.5 Sonnet model",
}


class NimbusACPAdapter:
    """Maps ACP operations to Nimbus internals.

    This adapter serves as the bridge between the ACP protocol layer
    and the Nimbus CodeAgent. It handles:
    - Session lifecycle management
    - Creating and configuring CodeAgent instances
    - Translating Nimbus streaming events to ACP format
    - Cancellation coordination

    Attributes:
        config: Adapter configuration.
        sessions: Active sessions keyed by session ID.
    """

    def __init__(self, config: ACPConfig | None = None) -> None:
        """Initialize the adapter.

        Args:
            config: Optional configuration. Uses defaults if not provided.
        """
        self.config = config or ACPConfig()
        self.sessions: dict[str, NimbusSession] = {}
        self._tool_call_counter: int = 0

    async def create_session(
        self,
        cwd: str,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Create a new Nimbus session.

        Args:
            cwd: Working directory for the session.
            mcp_servers: Optional list of MCP server configurations (not yet supported).

        Returns:
            Tuple of (session_id, session_info) where session_info contains
            models and modes state.
        """
        session_id = f"nimbus_{uuid.uuid4().hex[:12]}"

        # Resolve working directory
        resolved_cwd = cwd or self.config.cwd or os.getcwd()
        workspace = Path(resolved_cwd).resolve()

        log.info(f"Creating session {session_id} with cwd={workspace}")

        # Create CodeAgent using factory
        agent = await self._create_agent(workspace)

        # Create session
        session = NimbusSession(
            id=session_id,
            cwd=str(workspace),
            agent=agent,
            current_mode="default",
        )
        self.sessions[session_id] = session

        # Build session info response
        model_id = self.config.llm_model or DEFAULT_MODEL["modelId"]
        session_info: dict[str, Any] = {
            "models": SessionModelState(
                availableModels=[
                    ModelInfo(
                        modelId=model_id,
                        name=DEFAULT_MODEL["name"],
                        description=DEFAULT_MODEL.get("description"),
                    )
                ],
                currentModelId=model_id,
            ),
            "modes": SessionModeState(
                availableModes=DEFAULT_MODES,
                currentModeId="default",
            ),
        }

        log.info(f"Session {session_id} created successfully")
        return session_id, session_info

    async def _create_agent(self, workspace: Path) -> CodeAgent:
        """Create a Nimbus CodeAgent for the session.

        Args:
            workspace: Working directory for the agent.

        Returns:
            Configured CodeAgent instance.
        """
        # Build agent configuration
        llm_config: dict[str, Any] = {
            "model": self.config.llm_model or "claude-3-5-sonnet-20241022",
            "api_key_env": self.config.api_key_env,
            "max_tokens": 8192,
        }

        if self.config.llm_url:
            llm_config["base_url"] = self.config.llm_url

        agent_config: dict[str, Any] = {
            "name": "Nimbus ACP Agent",
            "system_prompt": self.config.system_prompt,
            "llm": llm_config,
            "memory": {
                "type": self.config.memory_type,
            },
            "planner_type": self.config.planner_type,
        }

        # Create agent using factory
        agent = AgentFactory.create_from_dict(agent_config)

        # Set workspace for tool sandbox validation
        agent.workspace = workspace

        return agent

    def get_session(self, session_id: str) -> NimbusSession | None:
        """Get an existing session by ID.

        Args:
            session_id: The session ID to look up.

        Returns:
            NimbusSession if found, None otherwise.
        """
        return self.sessions.get(session_id)

    async def run_prompt(
        self,
        session_id: str,
        content: str,
    ) -> AsyncIterator[SessionUpdate]:
        """Execute a prompt and yield ACP-formatted events.

        This method translates Nimbus streaming events to ACP session updates.
        It yields events as they occur during agent execution.

        Args:
            session_id: The session ID.
            content: The prompt text content.

        Yields:
            ACP SessionUpdate objects.

        Raises:
            ValueError: If session not found.
        """
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        if session.agent is None:
            raise ValueError(f"Session {session_id} has no agent")

        session.is_running = True
        session.cancel_event.clear()

        try:
            # Use the agent's streaming interface
            async for event in session.agent.run_stream(content):
                # Check for cancellation
                if session.cancel_event.is_set():
                    log.info(f"Session {session_id} cancelled")
                    break

                # Translate Nimbus events to ACP format
                acp_update = self._translate_event(event)
                if acp_update is not None:
                    yield acp_update

        except asyncio.CancelledError:
            log.info(f"Session {session_id} execution cancelled")
            raise
        except Exception as e:
            log.exception(f"Error executing prompt in session {session_id}: {e}")
            # Yield error as agent message
            yield AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContent(
                    type="text",
                    text=f"Error: {str(e)}",
                ),
            )
        finally:
            session.is_running = False

    def _translate_event(self, event: dict[str, Any]) -> SessionUpdate | None:
        """Translate a Nimbus streaming event to ACP format.

        Args:
            event: Nimbus event dictionary.

        Returns:
            ACP SessionUpdate object, or None to skip.
        """
        event_type = event.get("type")

        if event_type == "status":
            # Status updates become agent thought chunks
            return AgentThoughtChunk(
                sessionUpdate="agent_thought_chunk",
                content=TextContent(
                    type="text",
                    text=event.get("content", ""),
                ),
            )

        elif event_type == "planning":
            # Planning updates become agent thought chunks
            return AgentThoughtChunk(
                sessionUpdate="agent_thought_chunk",
                content=TextContent(
                    type="text",
                    text=event.get("content", "Planning..."),
                ),
            )

        elif event_type == "task_start":
            # Task start becomes a tool call
            self._tool_call_counter += 1
            tool_call_id = f"tc_{self._tool_call_counter}"
            return ToolCall(
                sessionUpdate="tool_call",
                toolCallId=tool_call_id,
                title=f"Running: {event.get('skill', 'task')}",
                kind="other",
                status="in_progress",
                rawInput={"task_id": event.get("task_id"), "skill": event.get("skill")},
            )

        elif event_type == "task_done":
            # Task completion becomes a tool call update
            # Note: In a full implementation, we'd track tool_call_id mapping
            return AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContent(
                    type="text",
                    text=str(event.get("result", "")),
                ),
            )

        elif event_type == "dag_start":
            # DAG start becomes agent thought
            total = event.get("total_tasks", 0)
            return AgentThoughtChunk(
                sessionUpdate="agent_thought_chunk",
                content=TextContent(
                    type="text",
                    text=f"Starting execution of {total} task(s)...",
                ),
            )

        elif event_type == "dag_complete":
            # DAG completion - results will be in the complete event
            stats = event.get("stats", {})
            completed = stats.get("completed", 0)
            failed = stats.get("failed", 0)
            if failed > 0:
                return AgentThoughtChunk(
                    sessionUpdate="agent_thought_chunk",
                    content=TextContent(
                        type="text",
                        text=f"Completed {completed} task(s), {failed} failed",
                    ),
                )
            return None

        elif event_type == "complete":
            # Final response
            return AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContent(
                    type="text",
                    text=event.get("content", ""),
                ),
            )

        elif event_type == "error":
            # Error becomes agent message
            return AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContent(
                    type="text",
                    text=event.get("content", "An error occurred"),
                ),
            )

        elif event_type == "direct":
            # Direct response (no skills executed)
            return AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContent(
                    type="text",
                    text=event.get("content", ""),
                ),
            )

        # Unknown event type - skip
        log.debug(f"Skipping unknown event type: {event_type}")
        return None

    async def cancel_session(self, session_id: str) -> None:
        """Cancel any running operation in a session.

        Args:
            session_id: The session to cancel.
        """
        session = self.get_session(session_id)
        if session is None:
            log.warning(f"Cannot cancel: session not found: {session_id}")
            return

        if session.is_running:
            log.info(f"Cancelling session {session_id}")
            session.cancel_event.set()

    async def set_mode(self, session_id: str, mode_id: str) -> None:
        """Set the current mode for a session.

        Args:
            session_id: The session ID.
            mode_id: The mode ID to set.

        Raises:
            ValueError: If session not found or mode invalid.
        """
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        # Validate mode
        valid_modes = {m["id"] for m in DEFAULT_MODES}
        if mode_id not in valid_modes:
            raise ValueError(f"Invalid mode: {mode_id}")

        session.current_mode = mode_id
        log.info(f"Session {session_id} mode set to {mode_id}")

    async def close_session(self, session_id: str) -> None:
        """Close and clean up a session.

        Args:
            session_id: The session to close.
        """
        session = self.sessions.pop(session_id, None)
        if session is None:
            return

        # Cancel any running operation
        if session.is_running:
            session.cancel_event.set()

        # Clear agent reference
        session.agent = None
        log.info(f"Session {session_id} closed")

    def get_session_count(self) -> int:
        """Get the number of active sessions.

        Returns:
            Number of active sessions.
        """
        return len(self.sessions)

    def list_sessions(self) -> list[str]:
        """List all active session IDs.

        Returns:
            List of session IDs.
        """
        return list(self.sessions.keys())
