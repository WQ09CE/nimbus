"""ACP Permission handling for Nimbus.

Handles the bidirectional permission flow between Nimbus and Toad:
1. Nimbus requests permission for tool execution
2. Request is forwarded to Toad via session/request_permission
3. User responds in Toad UI
4. Response is returned to Nimbus
"""

from dataclasses import dataclass, field
from typing import Callable, Awaitable, cast
from datetime import datetime
import asyncio
import uuid

from .types import (
    PermissionOption,
    PermissionOptionKind,
    ToolCallUpdatePermissionRequest,
    RequestPermissionOutcome,
    ToolKind,
)


@dataclass
class PendingPermission:
    """A pending permission request."""

    id: str
    session_id: str
    tool_call: ToolCallUpdatePermissionRequest
    options: list[PermissionOption]
    created_at: datetime = field(default_factory=datetime.now)
    future: asyncio.Future | None = None


class ACPPermissionHandler:
    """Handles permission requests between Nimbus and ACP client (Toad).

    This handler manages the permission flow for tool execution:
    - Tracks "always allow" and "always deny" rules per tool
    - Creates permission requests with standard options
    - Sends requests to ACP client via configurable callback
    - Processes responses and updates rules accordingly

    Example usage:
        handler = ACPPermissionHandler()
        handler.set_request_sender(my_sender_callback)

        allowed, option_id = await handler.request_permission(
            session_id="sess-123",
            tool_name="Bash",
            tool_input={"command": "ls -la"},
        )
    """

    # Standard permission options matching ACP protocol
    DEFAULT_OPTIONS: list[PermissionOption] = [
        {"optionId": "allow_once", "kind": "allow_once", "name": "Allow Once"},
        {"optionId": "allow_always", "kind": "allow_always", "name": "Always Allow"},
        {"optionId": "reject_once", "kind": "reject_once", "name": "Deny"},
        {"optionId": "reject_always", "kind": "reject_always", "name": "Always Deny"},
    ]

    # Mapping from tool names to ACP ToolKind
    TOOL_KIND_MAPPING: dict[str, ToolKind] = {
        # Read operations
        "read_file": "read",
        "Read": "read",
        # Search operations
        "grep_content": "search",
        "Grep": "search",
        "glob_files": "search",
        "Glob": "search",
        # Execute operations
        "bash": "execute",
        "Bash": "execute",
        # Edit operations
        "edit_file": "edit",
        "Edit": "edit",
        "write_file": "edit",
        "Write": "edit",
    }

    def __init__(self) -> None:
        """Initialize the permission handler."""
        self._pending: dict[str, PendingPermission] = {}
        self._always_allowed: set[str] = set()  # tool names
        self._always_denied: set[str] = set()  # tool names

        # Callback to send request to ACP client
        self._request_sender: (
            Callable[
                [str, ToolCallUpdatePermissionRequest, list[PermissionOption]],
                Awaitable[RequestPermissionOutcome],
            ]
            | None
        ) = None

    def set_request_sender(
        self,
        sender: Callable[
            [str, ToolCallUpdatePermissionRequest, list[PermissionOption]],
            Awaitable[RequestPermissionOutcome],
        ],
    ) -> None:
        """Set the callback function to send permission requests to ACP client.

        Args:
            sender: Async callback that sends permission request to ACP client.
                    Takes (session_id, tool_call, options) and returns outcome.
        """
        self._request_sender = sender

    async def request_permission(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict,
        tool_call_id: str | None = None,
    ) -> tuple[bool, str]:
        """Request permission for a tool execution.

        Args:
            session_id: ACP session ID.
            tool_name: Name of the tool.
            tool_input: Tool input parameters.
            tool_call_id: Optional existing tool call ID.

        Returns:
            Tuple of (allowed: bool, option_id: str).

        Raises:
            RuntimeError: If no permission request sender is configured.
        """
        # Check always allowed/denied rules first
        if tool_name in self._always_allowed:
            return True, "allow_always"
        if tool_name in self._always_denied:
            return False, "reject_always"

        # Build tool call for permission request
        if tool_call_id is None:
            tool_call_id = f"tc-{uuid.uuid4().hex[:8]}"

        tool_call: ToolCallUpdatePermissionRequest = {
            "toolCallId": tool_call_id,
            "title": tool_name,
            "kind": self._get_tool_kind(tool_name),
            "rawInput": tool_input,
            "status": "pending",
        }

        # Add location if path is available in input
        path = tool_input.get("path") or tool_input.get("file_path")
        if path:
            tool_call["locations"] = [{"path": path}]

        # Create pending request
        pending = PendingPermission(
            id=f"perm-{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            tool_call=tool_call,
            options=list(self.DEFAULT_OPTIONS),  # Copy to avoid mutations
            future=asyncio.Future(),
        )
        self._pending[pending.id] = pending

        try:
            # Send request to ACP client
            if self._request_sender is None:
                raise RuntimeError("No permission request sender configured")

            outcome = await self._request_sender(
                session_id,
                tool_call,
                pending.options,
            )

            # Process outcome
            return self._process_outcome(tool_name, outcome)

        finally:
            self._pending.pop(pending.id, None)

    def _process_outcome(
        self, tool_name: str, outcome: RequestPermissionOutcome
    ) -> tuple[bool, str]:
        """Process the permission outcome.

        Args:
            tool_name: Name of the tool.
            outcome: The permission outcome from ACP client.

        Returns:
            Tuple of (allowed: bool, option_id: str).
        """
        # Handle cancelled outcome
        if outcome.get("outcome") == "cancelled":
            return False, "cancelled"

        option_id: str = cast(str, outcome.get("optionId", ""))

        # Handle always allow/deny - update rules for future requests
        if option_id == "allow_always":
            self._always_allowed.add(tool_name)
            return True, "allow_always"
        elif option_id == "reject_always":
            self._always_denied.add(tool_name)
            return False, "reject_always"
        elif option_id == "allow_once":
            return True, "allow_once"
        elif option_id == "reject_once":
            return False, "reject_once"

        # Unknown option, default to deny for safety
        return False, option_id if option_id else "unknown"

    def _get_tool_kind(self, tool_name: str) -> ToolKind:
        """Map tool name to ACP ToolKind.

        Args:
            tool_name: Name of the tool.

        Returns:
            The corresponding ToolKind.
        """
        return self.TOOL_KIND_MAPPING.get(tool_name, "other")

    def clear_always_rules(self) -> None:
        """Clear all 'always allow' and 'always deny' rules."""
        self._always_allowed.clear()
        self._always_denied.clear()

    def is_always_allowed(self, tool_name: str) -> bool:
        """Check if tool is always allowed.

        Args:
            tool_name: Name of the tool.

        Returns:
            True if tool is in always-allowed set.
        """
        return tool_name in self._always_allowed

    def is_always_denied(self, tool_name: str) -> bool:
        """Check if tool is always denied.

        Args:
            tool_name: Name of the tool.

        Returns:
            True if tool is in always-denied set.
        """
        return tool_name in self._always_denied

    def add_always_allowed(self, tool_name: str) -> None:
        """Add a tool to the always-allowed set.

        Args:
            tool_name: Name of the tool.
        """
        self._always_allowed.add(tool_name)
        self._always_denied.discard(tool_name)

    def add_always_denied(self, tool_name: str) -> None:
        """Add a tool to the always-denied set.

        Args:
            tool_name: Name of the tool.
        """
        self._always_denied.add(tool_name)
        self._always_allowed.discard(tool_name)

    def get_pending_count(self) -> int:
        """Get the number of pending permission requests.

        Returns:
            Number of pending requests.
        """
        return len(self._pending)


# Singleton instance
_permission_handler: ACPPermissionHandler | None = None


def get_permission_handler() -> ACPPermissionHandler:
    """Get the global permission handler instance.

    Returns:
        The singleton ACPPermissionHandler instance.
    """
    global _permission_handler
    if _permission_handler is None:
        _permission_handler = ACPPermissionHandler()
    return _permission_handler


def reset_permission_handler() -> None:
    """Reset the global permission handler instance.

    Useful for testing or when restarting sessions.
    """
    global _permission_handler
    _permission_handler = None
