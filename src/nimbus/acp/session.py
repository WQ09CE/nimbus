"""ACP Session management for Nimbus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid
import asyncio


@dataclass
class ACPSessionState:
    """ACP Session state.

    Maps an ACP session to a Nimbus internal session, tracking
    runtime state for the ACP protocol layer.

    Attributes:
        id: ACP session ID (e.g., "acp-sess-xxx")
        nimbus_session_id: Nimbus internal session ID
        cwd: Working directory for the session
        mcp_servers: List of MCP server configurations
        created_at: When the session was created
        model_id: Current model ID (e.g., "claude-3-opus")
        mode_id: Current mode ID (if applicable)
        is_busy: Whether the session is currently processing
        current_task: The asyncio Task for the current operation
        cancel_requested: Whether cancellation has been requested
    """
    id: str  # ACP session ID (e.g., "acp-sess-xxx")
    nimbus_session_id: str  # Nimbus internal session ID
    cwd: str  # Working directory
    mcp_servers: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    # Current state
    model_id: str | None = None
    mode_id: str | None = None

    # Runtime state
    is_busy: bool = False
    current_task: asyncio.Task | None = None
    cancel_requested: bool = False


class ACPSessionManager:
    """Manages ACP sessions and their mapping to Nimbus sessions.

    This manager handles:
    - Creating and storing ACP sessions
    - Mapping between ACP session IDs and Nimbus session IDs
    - Tracking runtime state (busy/cancel status)
    - Session lifecycle management

    Thread Safety:
        This class is NOT thread-safe. In async contexts, access should
        be serialized or protected by an external lock if needed.

    Example:
        >>> manager = get_session_manager()
        >>> session = manager.create_session("/path/to/project")
        >>> print(session.id)  # "acp-sess-abc123"
        >>> manager.set_busy(session.id, True)
        >>> manager.request_cancel(session.id)
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ACPSessionState] = {}
        self._nimbus_to_acp: dict[str, str] = {}  # nimbus_id -> acp_id

    def create_session(self, cwd: str, mcp_servers: list[dict] | None = None) -> ACPSessionState:
        """Create a new ACP session.

        Args:
            cwd: Working directory for the session
            mcp_servers: List of MCP server configurations

        Returns:
            New ACPSessionState with generated IDs

        Example:
            >>> session = manager.create_session("/home/user/project")
            >>> print(session.id)  # "acp-sess-a1b2c3d4e5f6"
            >>> print(session.nimbus_session_id)  # "nimbus-x1y2z3..."
        """
        session_id = f"acp-sess-{uuid.uuid4().hex[:12]}"
        nimbus_session_id = f"nimbus-{uuid.uuid4().hex[:12]}"

        session = ACPSessionState(
            id=session_id,
            nimbus_session_id=nimbus_session_id,
            cwd=cwd,
            mcp_servers=mcp_servers or [],
        )

        self._sessions[session_id] = session
        self._nimbus_to_acp[nimbus_session_id] = session_id

        return session

    def get_session(self, session_id: str) -> ACPSessionState | None:
        """Get session by ACP session ID.

        Args:
            session_id: The ACP session ID

        Returns:
            ACPSessionState if found, None otherwise
        """
        return self._sessions.get(session_id)

    def get_session_by_nimbus_id(self, nimbus_id: str) -> ACPSessionState | None:
        """Get session by Nimbus session ID.

        Useful when Nimbus core emits events with its internal session ID.

        Args:
            nimbus_id: The Nimbus internal session ID

        Returns:
            ACPSessionState if found, None otherwise
        """
        if acp_id := self._nimbus_to_acp.get(nimbus_id):
            return self._sessions.get(acp_id)
        return None

    def list_sessions(self) -> list[ACPSessionState]:
        """List all sessions.

        Returns:
            List of all ACPSessionState objects
        """
        return list(self._sessions.values())

    def delete_session(self, session_id: str) -> bool:
        """Delete a session.

        Removes the session from internal tracking. Does not clean up
        the underlying Nimbus session - that should be handled separately.

        Args:
            session_id: The ACP session ID to delete

        Returns:
            True if session was deleted, False if not found
        """
        if session := self._sessions.pop(session_id, None):
            self._nimbus_to_acp.pop(session.nimbus_session_id, None)
            return True
        return False

    def set_busy(self, session_id: str, busy: bool, task: asyncio.Task | None = None) -> None:
        """Set session busy state.

        Call with busy=True when starting a prompt operation.
        Call with busy=False when the operation completes.

        Args:
            session_id: The ACP session ID
            busy: Whether the session is busy
            task: The asyncio Task (required when busy=True)
        """
        if session := self._sessions.get(session_id):
            session.is_busy = busy
            session.current_task = task if busy else None
            if not busy:
                session.cancel_requested = False

    def request_cancel(self, session_id: str) -> bool:
        """Request cancellation of current operation.

        Sets the cancel_requested flag and calls cancel() on the
        current asyncio Task.

        Args:
            session_id: The ACP session ID

        Returns:
            True if cancellation was requested, False if session not found or not busy
        """
        if session := self._sessions.get(session_id):
            if session.is_busy and session.current_task:
                session.cancel_requested = True
                session.current_task.cancel()
                return True
        return False

    def is_cancel_requested(self, session_id: str) -> bool:
        """Check if cancellation was requested for session.

        Useful for cooperative cancellation - the running operation
        can check this flag periodically.

        Args:
            session_id: The ACP session ID

        Returns:
            True if cancellation was requested, False otherwise
        """
        if session := self._sessions.get(session_id):
            return session.cancel_requested
        return False

    def update_model(self, session_id: str, model_id: str) -> bool:
        """Update session's current model.

        Args:
            session_id: The ACP session ID
            model_id: The new model ID (e.g., "claude-3-opus")

        Returns:
            True if updated, False if session not found
        """
        if session := self._sessions.get(session_id):
            session.model_id = model_id
            return True
        return False

    def update_mode(self, session_id: str, mode_id: str) -> bool:
        """Update session's current mode.

        Args:
            session_id: The ACP session ID
            mode_id: The new mode ID

        Returns:
            True if updated, False if session not found
        """
        if session := self._sessions.get(session_id):
            session.mode_id = mode_id
            return True
        return False


# Singleton instance
_session_manager: ACPSessionManager | None = None


def get_session_manager() -> ACPSessionManager:
    """Get the global session manager instance.

    Returns:
        The singleton ACPSessionManager instance

    Example:
        >>> manager = get_session_manager()
        >>> session = manager.create_session("/path/to/project")
    """
    global _session_manager
    if _session_manager is None:
        _session_manager = ACPSessionManager()
    return _session_manager
