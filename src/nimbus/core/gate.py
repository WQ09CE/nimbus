"""
Kernel Gate — The syscall layer for tool execution.

All tool calls flow through the Gate. It provides:
1. Arg normalization (fix LLM hallucinated param names)
2. Doom loop detection (same tool+args repeated N times)
3. Timeout enforcement (asyncio.wait_for)
4. Output truncation (prevent context blowup)
5. Event emission (TOOL_STARTED / TOOL_FINISHED)

This is the single bottleneck point for all side-effects.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .backend import BackendFault, ExecutionBackend, LocalBackend, PreparedCall
from .path_context import AgentPathContext
from .protocol import ActionIR, Event, Fault, ToolResult, ToolTraits
from .tools import builtin_tool_traits
from .tools.registry import DEFAULT_TRAITS

logger = logging.getLogger("nimbus.gate")

# Channel-fault retry budget (design doc §4): retryable faults re-dispatch at
# most this many extra times, with a small linear backoff, before degrading to
# a model-visible ERROR.
BACKEND_RETRY_MAX = 2
BACKEND_RETRY_BACKOFF_S = 0.05


class _BackendFaultError(Exception):
    """Gate-internal carrier for a terminal BackendFault. Never escapes."""

    def __init__(self, fault: BackendFault):
        super().__init__(fault.message)
        self.fault = fault


@dataclass(frozen=True)
class AuthorizationDecision:
    """Product policy verdict for one proposed tool action.

    Core execution only depends on ``allowed``. The remaining fields are an
    explanation surface persisted into the session trace and projected to the
    UI, so an operator can answer who authorized an effect and under which
    sandbox posture.
    """

    allowed: bool
    decision: str
    source: str
    explanation: str
    request_id: Optional[str] = None
    sandbox: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "decision": self.decision,
            "source": self.source,
            "explanation": self.explanation,
            "request_id": self.request_id,
            "sandbox": dict(self.sandbox),
        }


PolicyNotifier = Callable[[str, Dict[str, Any]], None]
ToolAuthorizer = Callable[
    [ActionIR, PolicyNotifier], Awaitable[AuthorizationDecision]
]
AuditSink = Callable[[str, Dict[str, Any]], Any]


# =============================================================================
# Doom Loop Detector (inlined — single responsibility, ~50 lines)
# =============================================================================

DOOM_LOOP_THRESHOLD = 3

DOOM_GUIDANCE = {
    "Edit": "Read the file first to get current content, then retry with exact text.",
    # Repetition-neutral wording: the detector fires on REPETITION, not on
    # failure — a command may repeat while succeeding every time. Telling the
    # model it "keeps failing" then would be misinformation (found by eval).
    "Read": "You have read this exact file repeatedly. You already have its content — use it. If the read failed, locate the correct path with Bash instead of retrying.",
    "Bash": "You have run this exact command repeatedly. You already have its result — use it and take the next step instead of re-running it.",
    "spawn_agent": "Same sub-agent goal keeps repeating. Revise your approach or handle the goal directly.",
}


class DoomLoopDetector:
    """Detect when the same tool call is repeated consecutively."""

    def __init__(self, threshold: int = DOOM_LOOP_THRESHOLD):
        self.threshold = threshold
        self._recent: List[Tuple[str, str]] = []
        self.trip_count = 0

    def check(self, tool_name: str, args: Dict) -> Optional[str]:
        """Check for doom loop. Returns guidance string if detected, None otherwise."""
        key = json.dumps({"t": tool_name, "a": args}, sort_keys=True)
        self._recent.append((tool_name, key))
        if len(self._recent) > self.threshold:
            self._recent = self._recent[-self.threshold:]

        if len(self._recent) == self.threshold and all(c[1] == key for c in self._recent):
            self.trip_count += 1
            self._recent.clear()
            return DOOM_GUIDANCE.get(
                tool_name,
                f"Tool '{tool_name}' is repeating with same args. Try a different approach.",
            )
        return None


# =============================================================================
# Arg Normalization
# =============================================================================

_ARG_ALIASES: Dict[str, Dict[str, str]] = {
    "Read":  {"path": "file_path", "filename": "file_path", "file": "file_path"},
    "Write": {"path": "file_path", "filename": "file_path", "file": "file_path"},
    "Edit":  {"path": "file_path", "file": "file_path",
              "old": "old_text", "oldText": "old_text",
              "new": "new_text", "newText": "new_text"},
    "Bash":  {"cmd": "command", "script": "command"},
    "Grep":  {"query": "pattern", "search": "pattern", "dir": "path"},
    "spawn_agent": {"timeout": "timeout_seconds", "task": "goal"},
}


def _normalize_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    aliases = _ARG_ALIASES.get(tool_name)
    if not aliases:
        return args
    normalized = dict(args)
    for alias, canonical in aliases.items():
        if alias in normalized and canonical not in normalized:
            normalized[canonical] = normalized.pop(alias)
    return normalized


def _public_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe, bounded action arguments for policy/UI/audit surfaces.

    Internal injected capabilities are intentionally excluded. Large model
    payloads (for example a full Write body) are represented by a preview;
    the authoritative assistant message still retains the exact tool call.
    """
    public: Dict[str, Any] = {}
    for key, value in args.items():
        if str(key).startswith("_"):
            continue
        if isinstance(value, str):
            public[key] = value if len(value) <= 2_000 else value[:2_000] + "…"
        elif value is None or isinstance(value, (bool, int, float)):
            public[key] = value
        elif isinstance(value, (list, dict)):
            try:
                encoded = json.dumps(value, ensure_ascii=False, default=str)
                public[key] = value if len(encoded) <= 2_000 else encoded[:2_000] + "…"
            except (TypeError, ValueError):
                public[key] = str(value)[:2_000]
        else:
            public[key] = str(value)[:2_000]
    return public


# =============================================================================
# Output Truncation
# =============================================================================

MAX_OUTPUT_CHARS = 200_000
TRUNCATION_KEEP = 2_000


def _truncate_output(text: Any) -> Any:
    if not isinstance(text, str) or len(text) <= MAX_OUTPUT_CHARS:
        return text
    cut = text.rfind("\n", 0, TRUNCATION_KEEP)
    cut = cut if cut > TRUNCATION_KEEP * 0.8 else TRUNCATION_KEEP
    return text[:cut] + f"\n\n[Truncated: {len(text):,} chars → first {cut:,}]"


# =============================================================================
# Kernel Gate
# =============================================================================


class KernelGate:
    """Execute tool calls with timeout, doom loop detection, and observability."""

    def __init__(
        self,
        pid: str,
        tool_executor: Optional[Callable] = None,
        event_callback: Optional[Callable[[Event], None]] = None,
        default_timeout: float = 60.0,
        on_tool_output: Optional[Callable[[str, str], None]] = None,
        abort_event: Optional[asyncio.Event] = None,
        path_context: Optional[AgentPathContext] = None,
        parent_model: Optional[str] = None,
        parent_base_url: Optional[str] = None,
        authorizer: Optional[ToolAuthorizer] = None,
        session_id: Optional[str] = None,
        sandbox_mode: str = "off",
        backend: Optional[ExecutionBackend] = None,
        traits_lookup: Optional[Callable[[str], Optional[ToolTraits]]] = None,
    ):
        if tool_executor is None and backend is None:
            raise ValueError("KernelGate needs a tool_executor or a backend")
        self.pid = pid
        self._executor = tool_executor
        self._event_cb = event_callback
        self._default_timeout = default_timeout
        self._doom = DoomLoopDetector()
        # Pi-style: callback for streaming tool output (tool_name, chunk)
        self._on_tool_output = on_tool_output
        # Abort event -- propagated to tools (e.g., bash) for process group kill
        self._abort_event = abort_event
        # Path context for workspace isolation
        self._path_context = path_context
        # Parent LLM context for spawn_agent inheritance
        self._parent_model = parent_model
        self._parent_base_url = parent_base_url
        # Product policy is injected by the server; standalone/core users keep
        # the historical allow-by-default behavior when no authorizer exists.
        self._authorizer = authorizer
        self._session_id = session_id
        self._sandbox_mode = sandbox_mode
        # The pluggable execution end (design doc §3). Default wraps the
        # historical executor closure — a behavioral no-op for local runs.
        self._backend: ExecutionBackend = backend or LocalBackend(
            tool_executor,  # type: ignore[arg-type]  # guarded above
            abort_event=abort_event,
            parent_model=parent_model,
            parent_base_url=parent_base_url,
        )
        self._traits_lookup = traits_lookup
        self._authorization_ms: Dict[str, int] = {}
        # Wired after RuntimeLoop constructs/open its authoritative SessionLog.
        self._audit_sink: Optional[AuditSink] = None

    def set_audit_sink(self, sink: Optional[AuditSink]) -> None:
        """Attach the authoritative trace after loop construction."""
        self._audit_sink = sink

    async def syscall_tool(self, action: ActionIR, timeout: Optional[float] = None) -> ToolResult:
        """Execute a TOOL_CALL action through the gate."""
        tool_name = action.name
        t0 = time.monotonic()

        # 1. Normalize first: policy, audit, UI, and execution must all judge
        # the exact same action rather than different alias spellings.
        action.args = _normalize_args(tool_name, action.args)
        public_args = _public_args(action.args)
        self._audit("action/proposed", {
            "session_id": self._session_id,
            "call_id": action.id,
            "tool": tool_name,
            "args": public_args,
        })

        # Keep the existing real-time tool-card contract: this means proposed
        # (possibly waiting for permission), not yet necessarily executing.
        self._emit("TOOL_STARTED", {
            "tool": tool_name,
            "call_id": action.id,
            "args": public_args,
        })

        # 2. Product authorization. Abort races the human permission wait so
        # Stop never hangs behind an unanswered prompt.
        authorization_started = time.monotonic()
        decision = AuthorizationDecision(
            allowed=True,
            decision="allow",
            source="core-default",
            explanation="No product authorization policy is attached.",
        )
        if self._authorizer is not None:
            try:
                decision = await self._authorize(action)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Tool authorization failed closed")
                decision = AuthorizationDecision(
                    allowed=False,
                    decision="deny",
                    source="policy-error",
                    explanation=f"Authorization service failed: {exc}",
                )
        elif tool_name == "Bash" and self._sandbox_mode != "off":
            # Confined sub-agents/standalone runs may intentionally omit a
            # human authorizer while still inheriting the product sandbox.
            from .tools import sandbox as sandbox_runtime

            writable = list(
                getattr(self._path_context, "writable_roots", None)
                or [getattr(self._path_context, "target_root", "")]
            )
            cwd = getattr(self._path_context, "execution_cwd", "")
            inherited_sandbox = sandbox_runtime.sandbox_plan(
                self._sandbox_mode, writable + [cwd], allow_network=False,
            )
            unavailable_required = bool(
                inherited_sandbox.get("required")
                and inherited_sandbox.get("state") == "unavailable"
            )
            decision = AuthorizationDecision(
                allowed=not unavailable_required,
                decision="deny" if unavailable_required else "allow",
                source="sandbox" if unavailable_required else "inherited-capability",
                explanation=(
                    "The required OS sandbox is unavailable; execution failed closed."
                    if unavailable_required else
                    "Bash is covered by the inherited sandbox capability."
                ),
                sandbox=inherited_sandbox,
            )
        self._authorization_ms[action.id] = int(
            (time.monotonic() - authorization_started) * 1000
        )
        if decision.source != "core-default":
            self._record_policy_decision(action, decision)
        if not decision.allowed:
            cancelled = decision.decision == "cancelled"
            result = ToolResult(
                status="CANCELLED" if cancelled else "ERROR",
                output=(
                    f"Tool '{tool_name}' was not executed: "
                    f"{decision.explanation}"
                ),
                fault=Fault(
                    domain="PERMISSION",
                    code="PERMISSION_DENIED",
                    message=decision.explanation,
                    retryable=False,
                ),
            )
            return self._finish(
                action, t0, self._attach_policy(result, decision)
            )

        # 3. Doom loop check
        doom_msg = self._doom.check(tool_name, action.args)
        if doom_msg:
            if self._doom.trip_count >= 2:
                # Fatal: agent is stuck
                return self._finish(action, t0, self._attach_policy(ToolResult(
                    status="ERROR",
                    output=f"Doom loop terminated: {doom_msg}",
                    fault=Fault(domain="TOOL", code="TOOL_FAILURE", message=doom_msg),
                ), decision))
            # Warning: inject guidance, still execute
            # (first trip gives the agent a chance to self-correct)

        # 4. Execute with timeout
        # spawn_agent manages its own timeout internally; don't double-wrap it.
        if tool_name == "spawn_agent":
            effective_timeout = None
        else:
            effective_timeout = timeout or self._default_timeout

        # Streaming callback for tools that support it (pi-style).
        # Dual-channel: chunk (for agent context) + ui_detail (for frontend SSE)
        on_stream = None
        if self._on_tool_output and tool_name in ("Bash", "spawn_agent"):
            def _on_update(chunk: str, ui_detail: Optional[Dict] = None) -> None:
                assert self._on_tool_output is not None
                self._on_tool_output(tool_name, chunk)
                delta_data: Dict[str, Any] = {
                    "tool": tool_name,
                    "chunk": chunk,
                    "call_id": action.id,
                }
                if ui_detail:
                    delta_data["ui_detail"] = ui_detail
                self._emit("TOOL_CALL_DELTA", delta_data)
            on_stream = _on_update

        # Everything a backend may need, decided here, immutable. The sandbox
        # grant selected at authorization rides the call; only a declared
        # execute-class tool consumes it (LocalBackend rule).
        prepared = PreparedCall(
            call_id=action.id,
            tool=tool_name,
            args=dict(action.args),
            traits=self._traits_for(tool_name),
            deadline_s=effective_timeout,
            session_id=self._session_id or "",
            path_context=self._path_context,
            sandbox_grant=dict(decision.sandbox or {}),
            on_stream=on_stream,
        )

        try:
            raw_output = await self._dispatch(prepared)

            # Handle split tool results (pi-style: output + ui_detail).
            # A tool may also declare {"concludes_turn": True} — carried onto
            # the ToolResult so the VCPU ends the turn on tool-side evidence.
            ui_detail: Dict[str, Any] = {}
            concludes_turn = False
            declared_status = "OK"
            if isinstance(raw_output, dict) and "output" in raw_output:
                raw_text = raw_output["output"]
                ui_detail = raw_output.get("ui_detail") or {}
                concludes_turn = bool(raw_output.get("concludes_turn", False))
                # Split-result tools declare contract status at the top level.
                # Accept ui_detail.status as a compatibility path because older
                # tools (notably spawn_agent) exposed failure only to the UI.
                declared_status = raw_output.get("status", ui_detail.get("status", "OK"))
            else:
                raw_text = raw_output

            valid_statuses = {"OK", "ERROR", "CANCELLED", "TIMEOUT", "SKIPPED"}
            status = str(declared_status).upper()
            if status not in valid_statuses:
                raise ValueError(f"Tool '{tool_name}' returned invalid status {declared_status!r}")

            output = _truncate_output(raw_text)

            # If truncation occurred, store the full raw text in ui_detail so
            # frontend SSE still receives it. Non-string outputs are unchanged.
            if isinstance(raw_text, str) and output != raw_text:
                ui_detail["raw_text_output"] = raw_text

            fault = None
            if status != "OK":
                fault = Fault(
                    domain="TOOL",
                    code="TIMEOUT" if status == "TIMEOUT" else "TOOL_FAILURE",
                    message=str(raw_text),
                    retryable=status == "TIMEOUT",
                )
            result = ToolResult(
                status=status, output=output,
                ui_detail=ui_detail if ui_detail else None,
                fault=fault,
                concludes_turn=concludes_turn and status == "OK",
            )

            # Append doom loop guidance if first warning
            if doom_msg:
                result.output = f"{result.output}\n\n[WARNING: Doom loop detected]\n{doom_msg}"

        except asyncio.TimeoutError:
            result = ToolResult(
                status="TIMEOUT",
                output=f"Tool '{tool_name}' timed out after {effective_timeout}s",
                fault=Fault(domain="RESOURCE", code="TIMEOUT",
                            message=f"Timeout after {effective_timeout}s", retryable=True),
            )
        except _BackendFaultError as bf:
            result = self._backend_fault_result(action, bf.fault)
        except Exception as e:
            result = ToolResult(
                status="ERROR",
                output=f"Tool '{tool_name}' failed: {e}",
                fault=Fault(domain="TOOL", code="TOOL_FAILURE",
                            message=str(e), retryable=False),
            )

        return self._finish(action, t0, self._attach_policy(result, decision))

    def _traits_for(self, tool_name: str) -> ToolTraits:
        """Resolve declared traits; unknown tools get the distrusted default."""
        if self._traits_lookup is not None:
            try:
                found = self._traits_lookup(tool_name)
                if found is not None:
                    return found
            except Exception:
                logger.exception("Traits lookup failed; using default traits")
        return builtin_tool_traits().get(tool_name) or DEFAULT_TRAITS

    async def _dispatch(self, call: PreparedCall) -> Any:
        """Run the call through the backend, resolving channel faults.

        Applies the design-doc §4 routing table: retryable channel faults
        re-dispatch (bounded, audited, backed off); AUTH_EXPIRED gets one
        silent refresh attempt; a terminal fault is raised as the internal
        _BackendFaultError for syscall_tool to turn into exactly one
        model-visible result. Each attempt owns the full deadline; on timeout
        the backend is told to cancel so remote work is never orphaned.
        """
        lease = None  # D1: threaded when the first stateful backend lands
        attempt = 0
        while True:
            try:
                coro = self._backend.run(call, lease)
                if call.deadline_s is not None:
                    outcome = await asyncio.wait_for(coro, timeout=call.deadline_s)
                else:
                    outcome = await coro
            except asyncio.TimeoutError:
                try:
                    await self._backend.cancel(call.call_id, lease)
                except Exception:
                    logger.exception("Backend cancel failed after timeout")
                raise
            if not isinstance(outcome, BackendFault):
                return outcome

            refresh = outcome.code == "AUTH_EXPIRED" and attempt == 0
            budget = (
                outcome.retry_budget
                if outcome.retry_budget is not None else BACKEND_RETRY_MAX
            )
            if (outcome.retryable or refresh) and attempt < budget:
                attempt += 1
                self._audit("backend/retry", {
                    "session_id": self._session_id,
                    "call_id": call.call_id,
                    "tool": call.tool,
                    "backend": getattr(self._backend, "backend_id", "?"),
                    "attempt": attempt,
                    "code": outcome.code,
                    "message": outcome.message,
                })
                await asyncio.sleep(BACKEND_RETRY_BACKOFF_S * attempt)
                continue
            raise _BackendFaultError(outcome)

    def _backend_fault_result(self, action: ActionIR, fault: BackendFault) -> ToolResult:
        """Degrade a terminal channel fault to one model-visible result.

        AUTH faults additionally route to the user (AUTH_REQUESTED event):
        credentials are user-plane, never model-plane.
        """
        code = fault.code
        if code == "AUTH_EXPIRED":
            # The silent refresh in _dispatch already failed → user flow.
            code = "AUTH_REQUIRED"
        known = {"BACKEND_UNAVAILABLE", "LEASE_LOST", "NETWORK", "AUTH_REQUIRED"}
        fault_code = code if code in known else "BACKEND_UNAVAILABLE"

        if code == "AUTH_REQUIRED":
            payload = {
                "session_id": self._session_id,
                "call_id": action.id,
                "tool": action.name,
                "backend": getattr(self._backend, "backend_id", "?"),
                "message": fault.message,
            }
            self._audit("backend/auth_required", payload)
            self._emit("AUTH_REQUESTED", payload)
            output = (
                f"Tool '{action.name}' requires backend authorization: "
                f"{fault.message}. Once the user connects/authorizes it, the "
                f"call can be retried."
            )
        elif code == "LEASE_LOST":
            output = (
                f"Tool '{action.name}' lost its backend session state "
                f"({fault.message}). A fresh execution context will be used on "
                f"the next call; re-establish any state you relied on."
            )
        else:
            output = f"Tool '{action.name}' backend is unavailable: {fault.message}"

        return ToolResult(
            status="ERROR",
            output=output,
            fault=Fault(
                domain="BACKEND",
                code=fault_code,  # type: ignore[arg-type]
                message=fault.message,
                retryable=fault.retryable,
            ),
        )

    async def _authorize(self, action: ActionIR) -> AuthorizationDecision:
        assert self._authorizer is not None

        def notify(phase: str, data: Dict[str, Any]) -> None:
            payload = {
                "session_id": self._session_id,
                "call_id": action.id,
                "tool": action.name,
                "args": _public_args(action.args),
                **data,
            }
            self._audit(f"policy/{phase}", payload)
            if phase == "requested":
                self._emit("POLICY_REQUESTED", payload)

        auth_task = asyncio.create_task(self._authorizer(action, notify))
        if self._abort_event is None:
            return await auth_task

        abort_task = asyncio.create_task(self._abort_event.wait())
        done, pending = await asyncio.wait(
            (auth_task, abort_task), return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        # Abort wins ties, matching RuntimeLoop's PAUSE-vs-ABORT discipline.
        if self._abort_event.is_set():
            if not auth_task.done():
                auth_task.cancel()
                await asyncio.gather(auth_task, return_exceptions=True)
            return AuthorizationDecision(
                allowed=False,
                decision="cancelled",
                source="user",
                explanation="Execution was interrupted while awaiting authorization.",
            )
        return auth_task.result()

    def _record_policy_decision(
        self, action: ActionIR, decision: AuthorizationDecision,
    ) -> None:
        payload = {
            "session_id": self._session_id,
            "call_id": action.id,
            "tool": action.name,
            **decision.to_dict(),
        }
        # SessionLog treats policy/decision as a causal flush barrier: an
        # approval reaches disk before the side effect starts.
        self._audit("policy/decision", payload)
        self._emit("POLICY_DECIDED", payload)

    def _attach_policy(
        self, result: ToolResult, decision: AuthorizationDecision,
    ) -> ToolResult:
        if self._authorizer is None:
            return result
        detail = dict(result.ui_detail or {})
        detail["policy"] = decision.to_dict()
        # Tool-result message metadata is the reload projection; retain the
        # non-OK outcome there so a denied action does not look successful
        # after refreshing the UI.
        detail.setdefault("status", result.status)
        if result.fault is not None:
            detail.setdefault("fault", {
                "domain": result.fault.domain,
                "code": result.fault.code,
                "message": result.fault.message,
                "retryable": result.fault.retryable,
            })
        result.ui_detail = detail
        return result

    def _finish(self, action: ActionIR, t0: float, result: ToolResult) -> ToolResult:
        elapsed = int((time.monotonic() - t0) * 1000)
        authorization_ms = self._authorization_ms.pop(action.id, 0)
        execution_ms = max(0, elapsed - authorization_ms)
        result.timing_ms = {
            "total": elapsed,
            "authorization": authorization_ms,
            "exec": execution_ms,
        }
        if result.ui_detail is not None and (
            "policy" in result.ui_detail or "sandbox_details" in result.ui_detail
        ):
            result.ui_detail.setdefault("timing", dict(result.timing_ms))

        # The event emitted here goes straight to the SSE stream.
        # We check if `raw_text_output` was stashed in ui_detail (meaning LLM context was truncated).
        # Prioritize sending the raw unfettered output to the UI, otherwise default to context output.
        full_output = (result.ui_detail or {}).get("raw_text_output", result.output)

        event_data: Dict[str, Any] = {
            "tool": action.name, "status": result.status,
            "call_id": action.id,
            "duration_ms": elapsed,
            "authorization_ms": authorization_ms,
            "execution_ms": execution_ms,
            "output_preview": str(full_output)[:200] if full_output else None,
            "output": str(full_output) if full_output else None,
        }
        if result.fault is not None:
            event_data["fault"] = {
                "domain": result.fault.domain,
                "code": result.fault.code,
                "message": result.fault.message,
                "retryable": result.fault.retryable,
            }
        # Include ui_detail in event for UI subscribers (pi-style split result)
        if result.ui_detail:
            # Drop the raw_text_output from ui_detail payload itself to avoid duplicate fat JSON
            safe_ui_detail = {k: v for k, v in result.ui_detail.items() if k != "raw_text_output"}
            event_data["ui_detail"] = safe_ui_detail

        sandbox_detail = (result.ui_detail or {}).get("sandbox_details")
        if sandbox_detail:
            sandbox_payload = {
                "session_id": self._session_id,
                "call_id": action.id,
                "tool": action.name,
                **sandbox_detail,
            }
            self._audit("sandbox/result", sandbox_payload)
            self._emit("SANDBOX_STATUS", sandbox_payload)

        self._audit("action/result", {
            "session_id": self._session_id,
            "call_id": action.id,
            "tool": action.name,
            "status": result.status,
            "duration_ms": elapsed,
            "authorization_ms": authorization_ms,
            "execution_ms": execution_ms,
            "fault": event_data.get("fault"),
            "output_preview": event_data["output_preview"],
        })
        self._emit("TOOL_FINISHED", event_data)

        # Now remove raw_text_output entirely from the returned Result so the LLM doesn't see it
        if result.ui_detail and "raw_text_output" in result.ui_detail:
            del result.ui_detail["raw_text_output"]

        return result

    def _audit(self, event_type: str, data: Dict[str, Any]) -> None:
        if self._audit_sink is None:
            return
        try:
            self._audit_sink(event_type, data)
        except Exception:
            # Observability must not become a second availability boundary.
            logger.exception("Gate audit sink failed for %s", event_type)

    def _emit(self, event_type: str, data: Dict) -> None:
        if self._event_cb:
            self._event_cb(Event(type=event_type, pid=self.pid, data=data))
