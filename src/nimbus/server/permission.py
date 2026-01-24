"""Permission Manager for tool execution control.

This module provides:
- PermissionManager: Manages tool permission rules and requests
- ask/allow_once/allow_always/deny strategies
- Pending permission request handling
"""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

from .models import PermissionDecision


@dataclass
class PendingPermission:
    """A pending permission request waiting for user decision."""
    request_id: str
    session_id: str
    tool: str
    args: Dict[str, Any]
    created_at: datetime
    future: asyncio.Future


class PermissionManager:
    """
    Manages tool execution permissions.

    Permission levels:
    - ask: Always ask user before executing (default for dangerous tools)
    - allow_once: Allow this specific execution
    - allow_always: Always allow this tool
    - deny: Always deny this tool

    Dangerous tools (default to 'ask'):
    - bash, shell, exec
    - write_file, delete_file
    - Any tool with 'dangerous' or 'destructive' in name
    """

    # Tools that default to 'ask' permission
    DANGEROUS_TOOLS = {
        "bash", "shell", "exec", "execute",
        "write_file", "delete_file", "remove_file",
        "rm", "rmdir", "mv", "move",
    }

    # Tools that default to 'allow_always'
    SAFE_TOOLS = {
        "read_file", "list_directory", "search",
        "chat", "summarize", "analyze",
    }

    def __init__(self):
        """Initialize permission manager."""
        self._rules: Dict[str, PermissionDecision] = {}
        self._pending: Dict[str, PendingPermission] = {}
        self._lock = asyncio.Lock()

        # Initialize default rules
        for tool in self.DANGEROUS_TOOLS:
            self._rules[tool] = PermissionDecision.ASK
        for tool in self.SAFE_TOOLS:
            self._rules[tool] = PermissionDecision.ALLOW_ALWAYS

    def get_rule(self, tool: str) -> PermissionDecision:
        """
        Get the permission rule for a tool.

        Args:
            tool: Tool name.

        Returns:
            Permission decision for the tool.
        """
        if tool in self._rules:
            return self._rules[tool]

        # Check if tool name suggests it's dangerous
        tool_lower = tool.lower()
        if any(d in tool_lower for d in ["dangerous", "destructive", "delete", "remove", "exec"]):
            return PermissionDecision.ASK

        # Default to ask for unknown tools
        return PermissionDecision.ASK

    def set_rule(self, tool: str, decision: PermissionDecision) -> None:
        """
        Set the permission rule for a tool.

        Args:
            tool: Tool name.
            decision: Permission decision.
        """
        self._rules[tool] = decision

    def get_all_rules(self) -> Dict[str, PermissionDecision]:
        """Get all permission rules."""
        return dict(self._rules)

    async def check_permission(
        self,
        session_id: str,
        tool: str,
        args: Dict[str, Any],
        on_permission_request: Optional[Callable[[str, str, Dict], None]] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if a tool execution is permitted.

        Args:
            session_id: Session making the request.
            tool: Tool to execute.
            args: Tool arguments.
            on_permission_request: Callback when permission is requested.

        Returns:
            Tuple of (allowed, request_id).
            If allowed is False and request_id is set, await resolve_permission().
        """
        rule = self.get_rule(tool)

        if rule == PermissionDecision.ALLOW_ALWAYS:
            return True, None

        if rule == PermissionDecision.DENY:
            return False, None

        # ASK or ALLOW_ONCE - need to request permission
        request_id = f"perm_{uuid.uuid4().hex[:8]}"

        async with self._lock:
            future = asyncio.get_event_loop().create_future()
            pending = PendingPermission(
                request_id=request_id,
                session_id=session_id,
                tool=tool,
                args=args,
                created_at=datetime.now(),
                future=future,
            )
            self._pending[request_id] = pending

        # Notify about permission request
        if on_permission_request:
            on_permission_request(request_id, tool, args)

        return False, request_id

    async def wait_for_permission(
        self,
        request_id: str,
        timeout: float = 300.0
    ) -> tuple[bool, PermissionDecision]:
        """
        Wait for a permission decision.

        Args:
            request_id: Permission request ID.
            timeout: Maximum time to wait in seconds.

        Returns:
            Tuple of (allowed, decision).

        Raises:
            asyncio.TimeoutError: If timeout exceeded.
            KeyError: If request_id not found.
        """
        async with self._lock:
            if request_id not in self._pending:
                raise KeyError(f"Permission request not found: {request_id}")
            pending = self._pending[request_id]

        try:
            decision = await asyncio.wait_for(pending.future, timeout=timeout)
            return decision != PermissionDecision.DENY, decision
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)

    async def resolve_permission(
        self,
        request_id: str,
        decision: PermissionDecision
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve a pending permission request.

        Args:
            request_id: Permission request ID.
            decision: User's decision.

        Returns:
            Request info if found, None otherwise.
        """
        async with self._lock:
            pending = self._pending.get(request_id)
            if not pending:
                return None

            # If allow_always, update the rule
            if decision == PermissionDecision.ALLOW_ALWAYS:
                self._rules[pending.tool] = PermissionDecision.ALLOW_ALWAYS
            elif decision == PermissionDecision.DENY:
                # Could optionally set to deny always
                pass

            # Resolve the future
            if not pending.future.done():
                pending.future.set_result(decision)

            return {
                "request_id": request_id,
                "session_id": pending.session_id,
                "tool": pending.tool,
                "args": pending.args,
                "decision": decision,
                "resolved_at": datetime.now(),
            }

    def get_pending_requests(
        self,
        session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all pending permission requests.

        Args:
            session_id: Optional filter by session.

        Returns:
            List of pending request info.
        """
        requests = []
        for pending in self._pending.values():
            if session_id and pending.session_id != session_id:
                continue
            requests.append({
                "request_id": pending.request_id,
                "session_id": pending.session_id,
                "tool": pending.tool,
                "args": pending.args,
                "created_at": pending.created_at,
            })
        return requests

    def cancel_pending(self, session_id: str) -> int:
        """
        Cancel all pending requests for a session.

        Args:
            session_id: Session to cancel requests for.

        Returns:
            Number of cancelled requests.
        """
        cancelled = 0
        to_remove = []

        for request_id, pending in self._pending.items():
            if pending.session_id == session_id:
                if not pending.future.done():
                    pending.future.set_result(PermissionDecision.DENY)
                to_remove.append(request_id)
                cancelled += 1

        for request_id in to_remove:
            self._pending.pop(request_id, None)

        return cancelled


class PermissionMiddleware:
    """
    Middleware to wrap tool execution with permission checks.

    Example usage:
        middleware = PermissionMiddleware(permission_manager, sse_hub)
        result = await middleware.execute_with_permission(
            session_id="sess_123",
            tool="bash",
            args={"command": "ls -la"},
            executor=bash_executor,
        )
    """

    def __init__(self, manager: PermissionManager, sse_hub=None):
        """
        Initialize permission middleware.

        Args:
            manager: Permission manager instance.
            sse_hub: Optional SSE hub for sending permission requests.
        """
        self.manager = manager
        self.sse_hub = sse_hub

    async def execute_with_permission(
        self,
        session_id: str,
        tool: str,
        args: Dict[str, Any],
        executor: Callable[..., Any],
        timeout: float = 300.0,
    ) -> tuple[bool, Any]:
        """
        Execute a tool with permission checking.

        Args:
            session_id: Session making the request.
            tool: Tool name.
            args: Tool arguments.
            executor: Function to execute the tool.
            timeout: Permission wait timeout.

        Returns:
            Tuple of (executed, result).
            executed is False if permission was denied.
        """
        # Define callback for SSE notification
        async def on_request(request_id: str, tool: str, args: Dict):
            if self.sse_hub:
                await self.sse_hub.publish(
                    session_id,
                    "permission_request",
                    {
                        "request_id": request_id,
                        "tool": tool,
                        "args": args,
                    }
                )

        # Check permission
        allowed, request_id = await self.manager.check_permission(
            session_id, tool, args,
            on_permission_request=lambda rid, t, a: asyncio.create_task(on_request(rid, t, a))
        )

        if allowed:
            # Permission granted, execute
            result = await executor(**args)
            return True, result

        if request_id:
            # Wait for user decision
            try:
                allowed, decision = await self.manager.wait_for_permission(
                    request_id, timeout=timeout
                )
                if allowed:
                    result = await executor(**args)
                    return True, result
            except asyncio.TimeoutError:
                pass

        # Permission denied or timed out
        return False, None
