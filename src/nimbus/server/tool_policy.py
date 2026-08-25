"""Server product policy bridge for KernelGate tool authorization."""

import asyncio
from typing import Any, Callable, Dict, Optional

from nimbus.core.gate import AuthorizationDecision, PolicyNotifier
from nimbus.core.path_context import AgentPathContext
from nimbus.core.protocol import ActionIR, ToolTraits
from nimbus.core.tools import builtin_tool_traits
from nimbus.core.tools import sandbox as sandbox_runtime
from nimbus.core.tools.registry import DEFAULT_TRAITS

from .permission import PermissionManager


class SessionToolAuthorizer:
    """Resolve one concrete action against permission and sandbox policy.

    The core Gate understands only an ``AuthorizationDecision``. This server
    adapter owns product choices: which tools are dangerous, whether a human
    prompt is required, and which sandbox capability accompanies approval.
    """

    def __init__(
        self,
        manager: PermissionManager,
        session_id: str,
        path_context: AgentPathContext,
        sandbox_mode: str,
        permission_timeout: float = 300.0,
        traits_lookup: Optional[Callable[[str], Optional[ToolTraits]]] = None,
    ) -> None:
        self.manager = manager
        self.session_id = session_id
        self.path_context = path_context
        self.sandbox_mode = sandbox_mode
        self.permission_timeout = permission_timeout
        self._traits_lookup = traits_lookup

    def _traits(self, tool: str, args: Dict[str, Any]) -> ToolTraits:
        """Declared traits select the policy class; names stay as overrides.

        The only name override: spawn_agent's effective class depends on the
        requested role (arg-dependent, so it cannot be a static trait) — a
        reader sub-agent is capability-confined and needs no write grant.
        """
        if (
            tool == "spawn_agent"
            and str(args.get("role", "worker")).casefold() != "worker"
        ):
            return ToolTraits(side_effects="read")
        if self._traits_lookup is not None:
            found = self._traits_lookup(tool)
            if found is not None:
                return found
        return builtin_tool_traits().get(tool) or DEFAULT_TRAITS

    def _sandbox_for(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        writable_roots = list(
            self.path_context.writable_roots or [self.path_context.target_root]
        )
        traits = self._traits(tool, args)
        if traits.side_effects == "execute":
            return sandbox_runtime.sandbox_plan(
                self.sandbox_mode,
                writable_roots + [self.path_context.execution_cwd],
                allow_network=False,
            )
        if traits.side_effects == "write":
            return {
                "mode": "path_scope",
                "requested": True,
                "required": True,
                "state": "active",
                "backend": "path-context",
                "network": "not-applicable",
                "allow_network": False,
                "writable_roots": writable_roots,
                "env": "host",
            }
        return {
            "mode": "not_applicable",
            "requested": False,
            "required": False,
            "state": "off",
            "backend": "none",
            "network": "not-applicable",
            "allow_network": False,
            "writable_roots": writable_roots,
            "env": "host",
        }

    def _explanation(self, tool: str, args: Dict[str, Any]) -> str:
        if (
            tool == "spawn_agent"
            and str(args.get("role", "worker")).casefold() == "worker"
        ):
            return "A worker sub-agent can modify files and run commands."
        traits = self._traits(tool, args)
        if traits.side_effects == "execute":
            return f"{tool} can execute arbitrary commands and requires approval."
        if traits.side_effects == "write":
            return f"{tool} can modify files inside the workspace and requires approval."
        return f"{tool} is allowed by the current capability policy."

    async def __call__(
        self, action: ActionIR, notify: PolicyNotifier,
    ) -> AuthorizationDecision:
        tool = action.name
        args = dict(action.args)
        sandbox_info = self._sandbox_for(tool, args)
        explanation = self._explanation(tool, args)

        if (
            sandbox_info.get("required")
            and sandbox_info.get("state") == "unavailable"
        ):
            return AuthorizationDecision(
                allowed=False,
                decision="deny",
                source="sandbox",
                explanation=(
                    "The required OS sandbox is unavailable; execution failed closed."
                ),
                sandbox=sandbox_info,
            )

        rule = self.manager.get_rule(tool, args)
        allowed, request_id = await self.manager.check_permission(
            self.session_id,
            tool,
            args,
            context={
                "call_id": action.id,
                "explanation": explanation,
                "sandbox": sandbox_info,
            },
        )
        if allowed:
            return AuthorizationDecision(
                allowed=True,
                decision=rule.value,
                source="rule",
                explanation=explanation,
                sandbox=sandbox_info,
            )

        if request_id is None:
            return AuthorizationDecision(
                allowed=False,
                decision="deny",
                source="rule",
                explanation=f"{tool} is denied by the saved permission rule.",
                sandbox=sandbox_info,
            )

        notify("requested", {
            "request_id": request_id,
            "explanation": explanation,
            "sandbox": sandbox_info,
        })
        try:
            permitted, resolved = await self.manager.wait_for_permission(
                request_id, timeout=self.permission_timeout,
            )
        except asyncio.TimeoutError:
            return AuthorizationDecision(
                allowed=False,
                decision="deny",
                source="timeout",
                explanation="Authorization timed out; the tool was not executed.",
                request_id=request_id,
                sandbox=sandbox_info,
            )

        return AuthorizationDecision(
            allowed=permitted,
            decision=resolved.value,
            source="user",
            explanation=explanation if permitted else "The user denied this tool action.",
            request_id=request_id,
            sandbox=sandbox_info,
        )
