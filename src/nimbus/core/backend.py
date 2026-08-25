"""
ExecutionBackend — the pluggable execution end of the KernelGate.

Design: docs/design/toolcall-gate-execution-backend.md

The Gate stays the single policy spine (normalize → authorize → doom →
dispatch → route → attribute); a backend only runs calls. Contract rules:

- One dispatched PreparedCall yields exactly one ToolResult item in the
  message surface; BackendFault is a Gate-internal value and never escapes
  the Gate as an exception or a missing result.
- A backend receives a PreparedCall and nothing else — no reaching back into
  Gate or session internals.
- Backends return the RAW tool payload (str, or the split dict with
  output/ui_detail); truncation, ui_detail attachment, and event emission
  stay Gate-side normalization.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Union

from .path_context import AgentPathContext
from .protocol import ToolTraits
from .tools.registry import ToolDefinition


@dataclass(frozen=True)
class Lease:
    """Session-affinity handle for stateful backends (the fd analogue).

    Runtime state, not message-surface state: never persisted into the
    session log and never survives crash/resume — the first call after a
    resume observes LEASE_LOST and the model is told state was lost.
    D1: the type is part of the contract now; machinery lands with the first
    stateful backend (everything threads None until then).
    """

    backend_id: str
    lease_id: str
    expires_at_monotonic: Optional[float] = None


@dataclass(frozen=True)
class PreparedCall:
    """Everything a backend may need, decided by the Gate, immutable.

    Formalizes the historical underscore-arg injection channel.
    """

    call_id: str
    tool: str
    args: Dict[str, Any]
    traits: ToolTraits
    deadline_s: Optional[float]  # per-attempt wall budget; None = unbounded
    # Step-6 finding #2: a backend managing its own leases must know for whom.
    # Session-scoped backends may ignore it; shared/pooled backends key on it.
    session_id: str = ""
    path_context: Optional[AgentPathContext] = None
    sandbox_grant: Dict[str, Any] = field(default_factory=dict)
    # (chunk, ui_detail=None) — Gate-built wrapper that also emits SSE deltas.
    on_stream: Optional[Callable[..., None]] = None


@dataclass
class BackendFault:
    """A failure of the CHANNEL, not of the tool.

    The routing bit the Fault taxonomy alone cannot express: whether the
    model should ever hear about this. Routed by the Gate per the design
    doc §4 table; on retry exhaustion every fault degrades to a
    model-visible ERROR result.
    """

    code: str  # BACKEND_UNAVAILABLE | LEASE_LOST | NETWORK | AUTH_REQUIRED | AUTH_EXPIRED
    message: str
    retryable: bool = False
    model_visible: bool = False
    # Step-6 finding #6: the retry budget is a property of the CHANNEL, and
    # the backend producing the fault is the one that knows the channel.
    # None → the Gate's default budget applies.
    retry_budget: Optional[int] = None


# Raw tool payload (str, or split dict with output/ui_detail) — or a channel fault.
BackendOutcome = Union[Any, BackendFault]


class ExecutionBackend(Protocol):
    """Where a tool call actually runs. See module docstring for the rules."""

    backend_id: str

    # -- catalog face --
    def advertise(self) -> List[ToolDefinition]: ...
    def catalog_version(self) -> int: ...

    # -- lease face (stateful backends; local returns None) --
    async def open_lease(self, session_id: str) -> Optional[Lease]: ...
    async def close_lease(self, lease: Lease) -> None: ...

    # -- dispatch face --
    async def run(self, call: PreparedCall, lease: Optional[Lease] = None) -> BackendOutcome: ...
    async def cancel(self, call_id: str, lease: Optional[Lease] = None) -> None: ...


class LocalBackend:
    """In-process execution against the tool-registry executor.

    Maps PreparedCall back onto the historical underscore-arg convention that
    builtin tools consume (`_path_context`, `_abort_event`, `_sandbox_policy`,
    `on_update`); that convention stays an internal detail of this backend.
    """

    backend_id = "local"

    def __init__(
        self,
        executor: Callable[[str, Dict[str, Any]], Awaitable[Any]],
        abort_event: Optional[asyncio.Event] = None,
        parent_model: Optional[str] = None,
        parent_base_url: Optional[str] = None,
    ):
        self._executor = executor
        self._abort_event = abort_event
        self._parent_model = parent_model
        self._parent_base_url = parent_base_url

    def advertise(self) -> List[ToolDefinition]:
        # The local registry is still the catalog authority; the merger
        # arrives with the first remote backend (design doc §6).
        return []

    def catalog_version(self) -> int:
        return 0

    async def open_lease(self, session_id: str) -> Optional[Lease]:
        return None

    async def close_lease(self, lease: Lease) -> None:
        return None

    async def run(self, call: PreparedCall, lease: Optional[Lease] = None) -> BackendOutcome:
        args = dict(call.args)
        if call.path_context is not None:
            args["_path_context"] = call.path_context
        if self._abort_event is not None:
            args["_abort_event"] = self._abort_event
        if call.on_stream is not None:
            args["on_update"] = call.on_stream
        # Only a declared execute-class tool consumes an OS-sandbox grant;
        # injecting elsewhere would hand unknown kwargs to strict handlers.
        if call.sandbox_grant and call.traits.side_effects == "execute":
            args["_sandbox_policy"] = dict(call.sandbox_grant)
        if call.tool == "spawn_agent":
            if self._parent_model:
                args["_parent_model"] = self._parent_model
            if self._parent_base_url:
                args["_parent_base_url"] = self._parent_base_url
        return await self._executor(call.tool, args)

    async def cancel(self, call_id: str, lease: Optional[Lease] = None) -> None:
        # Local delivery is cooperative: asyncio cancellation reaches the
        # coroutine and the shared abort event reaches process groups (bash).
        # Nothing extra to kill here; remote backends implement this for real.
        return None


class RoutingBackend:
    """Route each call to a per-tool backend; `default` catches the rest.

    The Gate's "resolve backend" pipeline step made concrete (design doc §7).
    Cancel must reach the backend that RAN the call: an in-flight entry is
    kept when run() is cancelled (the Gate's timeout path cancels run first,
    then calls cancel), and dropped on normal completion.
    """

    backend_id = "router"

    def __init__(self, default: ExecutionBackend, routes: Dict[str, ExecutionBackend]):
        self._default = default
        self._routes = dict(routes)
        self._inflight: Dict[str, ExecutionBackend] = {}

    def _all_backends(self) -> List[ExecutionBackend]:
        seen: List[ExecutionBackend] = [self._default]
        for b in self._routes.values():
            if all(b is not s for s in seen):
                seen.append(b)
        return seen

    def advertise(self) -> List[ToolDefinition]:
        defs: List[ToolDefinition] = []
        for b in self._all_backends():
            defs.extend(b.advertise())
        return defs

    def catalog_version(self) -> int:
        return sum(b.catalog_version() for b in self._all_backends())

    async def open_lease(self, session_id: str) -> Optional[Lease]:
        return None  # sub-backends self-manage their leases (session-scoped)

    async def close_lease(self, lease: Lease) -> None:
        return None

    async def run(self, call: PreparedCall, lease: Optional[Lease] = None) -> BackendOutcome:
        backend = self._routes.get(call.tool, self._default)
        self._inflight[call.call_id] = backend
        try:
            outcome = await backend.run(call, lease)
        except asyncio.CancelledError:
            # Keep the entry: the Gate's cancel() for this call is coming.
            raise
        except Exception:
            self._inflight.pop(call.call_id, None)
            raise
        self._inflight.pop(call.call_id, None)
        return outcome

    async def cancel(self, call_id: str, lease: Optional[Lease] = None) -> None:
        backend = self._inflight.pop(call_id, None)
        if backend is not None:
            await backend.cancel(call_id, lease)
            return
        # Unknown call (already reaped, or raced): best-effort fan-out.
        for b in self._all_backends():
            await b.cancel(call_id, lease)
