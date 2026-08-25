# ToolCall Gate & ExecutionBackend — Unified Tool Execution Contract

Status: v1 LANDED (steps 1–5, 2026-08-25) — `core/backend.py`, Gate dispatch
loop, traits-driven `tool_policy.py`, `tests/core/test_backend.py` (one test
per §4 row). Three implementation adjudications diverge from the draft:
1. `BackendOutcome` = **raw tool payload** | BackendFault (not ToolResult) —
   truncation/ui_detail stay Gate-side normalization, so backends return what
   executors always returned.
2. Unknown/plugin default traits = `write`, not `execute` — distrust is
   delivered via fail-closed permission ASK + workspace-scoped grant; the
   execute class (OS sandbox + `_sandbox_policy` injection) is opt-in by
   declaration, because injecting kwargs into strict plugin signatures would
   TypeError.
3. §4 "user abort → backend.cancel" is deferred to the first remote backend:
   locally abort reaches tools cooperatively (shared abort event + process
   group kill) and a Gate-side race would cut off the tool's own graceful
   CANCELLED result. v1 calls `backend.cancel()` on the timeout path only.
Anchors: `core/gate.py` @ refactor/core-hardening (cffe79ac working tree), `core/protocol.py`, `server/tool_policy.py`, `core/tools/registry.py`
Lineage: hax hooks.tool_call seam study (2026-08-24/25) + VFS/syscall analysis. The
claim being engineered: *"backends (local, sandbox, MCP, remote executor) plug in
without loop/runtime changes"* — true only if the contract is complete on the
failure side, not just the happy path.

---

## 1. Goal / Non-goals

**Goal.** Make the *execution end* of `KernelGate` pluggable. The Gate keeps
being the single policy spine (normalize → authorize → doom-check → dispatch →
route → attribute); what runs the call becomes an `ExecutionBackend`. VCPU,
RuntimeLoop, and the model-visible tool contract do not change.

**Non-goals (v1).**
- Implementing MCP or a remote executor. v1 ships the seam plus `LocalBackend`
  as a behavioral no-op refactor.
- Changing permission UX or the SSE approval flow.
- Deferred/lazy tool schemas (ToolSearch-style). Noted as future work in §6.

## 2. Current state (code, not memory)

| Fact | Anchor |
|---|---|
| Single execution entry, always local registry | `gate.py:377` `self._executor(tool_name, exec_args)` |
| Capability injection via underscore-args (`_path_context`, `_abort_event`, `_sandbox_policy`, `_parent_model`, `on_update`) | `gate.py:343-374` |
| Sandbox is a *per-action grant* computed at authorization, consumed inside the local Bash tool | `tool_policy.py:36-71`, `gate.py:373-374` |
| Fault taxonomy has no infra/backend class; every non-OK becomes a model-visible result | `protocol.py:99-113`, `gate.py:412-419` |
| Timeout cancels the local coroutine only — a remote call would be orphaned | `gate.py:379` (`asyncio.wait_for`) |
| Abort races the permission wait; abort reaches tools as a shared local `asyncio.Event` | `gate.py:463-487`, `gate.py:367-369` |
| Catalog is static: registry definitions exported per-provider | `registry.py:37-90` |

The design formalizes what the underscore-args already do informally, and adds
the two things the informal version cannot express: **backend-class faults**
and **remote cancellation**.

## 3. The contract

```python
# core/backend.py (new)

@dataclass(frozen=True)
class PreparedCall:
    """Everything a backend may need, decided by the Gate, immutable.

    Formalizes today's underscore-arg injection. A backend receives ONLY this —
    no reaching back into Gate/session internals.
    """
    call_id: str
    tool: str
    args: Dict[str, Any]              # normalized (gate.py:230)
    deadline_monotonic: Optional[float]  # None = unbounded (spawn_agent rule)
    path_context: Optional[AgentPathContext]
    sandbox_grant: Dict[str, Any]     # AuthorizationDecision.sandbox, verbatim
    on_stream: Optional[Callable[[str, Optional[Dict]], None]]  # chunk, ui_detail


@dataclass
class BackendFault:
    """A failure of the CHANNEL, not of the tool.

    The routing bit the current taxonomy cannot express: whether the model
    should ever hear about this.
    """
    code: str                  # BACKEND_UNAVAILABLE | LEASE_LOST | NETWORK | INTERNAL
    message: str
    retryable: bool            # gate may re-dispatch silently
    model_visible: bool        # False → pure runtime concern until retries exhaust


BackendOutcome = Union[ToolResult, BackendFault]


class ExecutionBackend(Protocol):
    # -- catalog face --
    def advertise(self) -> List[ToolDefinition]: ...
    def catalog_version(self) -> int: ...        # bump ⇒ schemas re-exported next step

    # -- lease face (stateful backends; local returns None) --
    async def open_lease(self, session_id: str) -> Optional[Lease]: ...
    async def close_lease(self, lease: Lease) -> None: ...

    # -- dispatch face --
    async def run(self, call: PreparedCall, lease: Optional[Lease]) -> BackendOutcome: ...
    async def cancel(self, call_id: str, lease: Optional[Lease]) -> None: ...
```

```python
@dataclass(frozen=True)
class Lease:
    """Session affinity handle for stateful backends (fd analogue).

    Runtime state, NOT message-surface state: never persisted into the session
    log, never survives crash/resume. On resume the first call observes
    LEASE_LOST and the model is told state was lost — no pretended continuity.
    """
    backend_id: str
    lease_id: str
    expires_at_monotonic: Optional[float]
```

Design rules the contract encodes:

1. **One call, one result** (hax pairing invariant): every dispatched
   `PreparedCall` produces exactly one `ToolResult` item in the message
   surface, whatever the backend did. `BackendFault` is a Gate-internal value;
   it never leaks as an exception and never produces zero results.
2. **The Gate stays the only choke point.** Backends run things; they do not
   decide policy, truncate output, attach ui_detail, or emit events.
3. **Backends receive `PreparedCall`, nothing else.** Kills the stringly-typed
   underscore channel at the boundary (internally LocalBackend may keep it).

## 4. Fault routing (the errno moment)

Extend `FaultDomain` with `"BACKEND"`. Routing table — Gate behavior per
outcome; the invariant column is what tests assert:

| Outcome from backend | Model sees | Gate does | Invariant |
|---|---|---|---|
| `ToolResult` (OK/ERROR/TIMEOUT/…) | the result | continue (today's path) | unchanged behavior |
| `BackendFault{retryable=True}` (network blip, cold-start race) | nothing (yet) | re-dispatch ≤ N with backoff; on exhaustion degrade to model-visible ERROR + `Fault(BACKEND, …)` | retries are audit-logged; usage/timing accounted per attempt (EV_RETRY discipline) |
| `BackendFault{code=LEASE_LOST}` | ERROR result stating sandbox state was lost | close lease; next call opens a fresh one | model is told — state loss is never silent |
| `BackendFault{retryable=False}` & sandbox required | ERROR, fail closed | matches existing sandbox fail-closed (`tool_policy.py:94-106`) | no fallback to unconfined execution |
| Gate timeout | TIMEOUT result | **`await backend.cancel(call_id, lease)`** then return | fixes the orphan-execution hole at `gate.py:379` |
| User abort | CANCELLED/SKIPPED result | `backend.cancel(...)`; abort still wins ties vs permission wait | matches loop ABORT semantics |
| `BackendFault{code=AUTH_REQUIRED}` (connector never authorized) | ERROR result naming the missing grant | emit `AUTH_REQUESTED` event → user flow (SSE, like permission); call is NOT retried automatically | credentials are user-plane, never model-plane; the model may retry after the user connects |
| `BackendFault{code=AUTH_EXPIRED}` (mid-session expiry) | nothing on first hit | one silent refresh attempt if backend supports it; else degrade to AUTH_REQUIRED row | expiry ≠ user decision; refresh is infra |

Retry policy is Gate-local (small N, jittered backoff) — deliberately NOT the
VCPU fault handler: LLM faults route through `fault_semantics.py` because they
touch the message surface; backend faults are invisible to the surface until
they exhaust, so their blast radius stays inside the Gate.

## 5. Lease semantics

- Local tools: `lease=None` forever. Ambient `path_context` is the state.
- OS sandbox (today): grant stays per-action; no lease needed. The grant rides
  `PreparedCall.sandbox_grant` instead of `exec_args["_sandbox_policy"]`.
- Remote sandbox / cloud executor (future): lease = instance handle; call N+1
  routes to the same instance (cwd/env/process continuity). Lease lives on the
  AgentOS instance, keyed by backend_id.
- Crash/resume: leases are gone by construction. Derived-not-tracked applied to
  execution state: never fake continuity across a boundary that lost it.

## 6. Catalog face

- `ToolRegistry` becomes a merger: built-in definitions + each backend's
  `advertise()`. Collision policy: built-ins win; backend tools are namespaced
  (`mcp__<server>__<tool>` — ecosystem convention).
- `catalog_version()` sum is stamped per step; a change (MCP server connects
  mid-session) emits `CATALOG_CHANGED` and the adapter re-exports schemas on
  the next request. One touch point in context assembly, no rewrite.
- Dynamic/deferred schema loading is future work; v1 catalogs are eager.

**Traits: policy must stop name-matching.** Today `tool_policy.py:36-84`
decides sandbox/approval by hardcoded tool names ("Bash", "Write", "Edit").
With heterogeneous backends the name set is open — policy must derive from
declared traits instead:

```python
@dataclass(frozen=True)
class ToolTraits:
    side_effects: Literal["none", "read", "write", "execute"]  # policy class
    needs_auth: bool = False        # connector-class: user-credentialed
    latency_class: Literal["local", "network", "batch"] = "local"
    data_plane: Literal["inline", "by_reference"] = "inline"
```

Every catalog entry (built-in or advertised) carries traits. The authorizer
maps traits → decision (`side_effects=="none"` ⇒ auto-allow; `"execute"` ⇒
sandbox plan + approval; `needs_auth` ⇒ credential check before dispatch).
Name-specific rules remain possible as overrides, not as the mechanism.

## 6b. Backend taxonomy — the six heterogeneity targets

Each target stresses a different contract dimension; the contract is complete
only if every column has an answer before the first remote backend lands:

| Backend | Lease | Catalog | Dominant fault class | Policy class (traits) | Data plane |
|---|---|---|---|---|---|
| Local tools (今天) | none | static | TOOL_FAILURE | execute/write | inline |
| OS sandbox | per-action grant (今天) | static | sandbox unavailable → fail closed | execute | inline |
| Cloud compute | **yes** — instance affinity | static-ish | NETWORK, LEASE_LOST, orphan-cancel | execute | **by_reference**(artifacts) |
| MCP | none (server-side state) | **dynamic** (CATALOG_CHANGED) | NETWORK, server crash | per-tool declared | inline, size-capped |
| RAG / retrieval | none | static | NETWORK (degrade: answer without) | **none** → auto-allow, no approval UX | inline, budget-capped (image-budget discipline) |
| Connector (user-credentialed SaaS) | none | per-user | **AUTH_REQUIRED / AUTH_EXPIRED** | read or write + needs_auth | inline |
| Plugin (in-process third-party) | none | install-time | TOOL_FAILURE, but **trust boundary ≠ built-in** | declared, distrusted by default | inline |

Two rows force contract additions beyond §4:

- **RAG**: read-only tools must be dispatchable with zero approval friction —
  traits-driven auto-allow (above). Runtime-initiated retrieval (context
  injection without a model tool call) is explicitly out of scope: that is an
  MMU concern, not a Gate concern; when it comes, it reuses the same backend
  via a non-Gate entry.
- **Connector**: auth is a fault class routed to the *user*, not the model.
  New §4 rows below.

## 7. What moves, what doesn't

Gate pipeline after the change (bold = new step, everything else byte-stable):

normalize → audit/emit → authorize → doom-check → **resolve backend + lease →
run(PreparedCall) with deadline → route outcome (§4)** → truncate → attach
policy/ui_detail → finish (timing, audit, events)

- `syscall_tool` keeps its signature. VCPU/RuntimeLoop: zero changes.
- `tool_executor` closure in `agent.py:411` becomes `LocalBackend.run`.
- Bash's sandbox consumption moves from arg-sniffing to reading
  `PreparedCall.sandbox_grant` (same dict, better address).
- spawn_agent stays framework-level, NOT a backend: it is orchestration, not a
  tool channel (it already bypasses the Gate timeout, `gate.py:336-337`).

## 8. Migration steps (dependency-ordered; each independently landable)

1. `PreparedCall` / `BackendFault` / `Lease` / `ExecutionBackend` types +
   `BACKEND` fault domain in `protocol.py` / new `core/backend.py`. No behavior
   change.
2. `LocalBackend` wrapping the current registry executor; Gate calls it.
   Behavioral no-op — existing gate/loop tests must stay green unmodified.
3. Backend-aware timeout/cancel: Gate calls `backend.cancel()` on timeout and
   abort. Observable improvement even locally (today only Bash listens to
   `_abort_event`; the backend verb generalizes it).
4. Fault routing loop (§4) + tests written in the `fault_semantics` table
   style: one test per row, invariant column asserted.
5. Sandbox grant rides `PreparedCall`; delete `_sandbox_policy` arg-sniffing.
6. (future) `McpBackend` / `RemoteBackend` — first real second implementation
   validates the seam; expect one contract revision here, budget for it.

Grounds: ordered by dependency and reversibility; steps 1–5 blast radius =
`gate.py` + `protocol.py` + one closure in `agent.py`; the promise under design
(loop untouched) is enforced by keeping loop/VCPU diffs at zero through step 5.

## 9. Open decisions

| # | Question | Recommendation | Ground |
|---|---|---|---|
| D1 | Lease in v1, or defer to first stateful backend? | Define the type, thread `None` everywhere. Contract complete, zero machinery. | contract stability vs no speculative code |
| D2 | Backend-fault retry: Gate-local vs VCPU fault handler? | Gate-local. VCPU handles surface-touching faults; backend faults are invisible below exhaustion. | blast radius |
| D3 | MCP namespacing: prefix vs flat-with-collision-error? | `mcp__<server>__<tool>` prefix. | ecosystem convention, zero collision handling |
| D4 | Is spawn_agent a backend? | No — orchestration, not a channel. | it composes AgentOS instances, not tool processes |
| D5 | Migrate `tool_policy.py` from name-matching to traits-driven now, or when the first non-built-in backend lands? | Now (step 1.5): built-ins declare traits, authorizer consumes traits, name rules become overrides. Small diff, kills the scaling wart before backends multiply. | contract stability; blast radius = tool_policy.py only |

## 10. Cross-reference (why this shape)

Gate = syscall layer (its own docstring, `gate.py:2`). Backend = VFS
`file_operations`. Lease = fd. §4 = errno taxonomy. Catalog = mount table.
POSIX kept "runtime unchanged" true for decades because the contract was
complete on the failure side — that completeness is what §4 buys. hax keeps the
same seam as one function pointer (`hooks.tool_call`) because it has exactly
one frontend and five tools; nimbus has three provider channels, a server
layer, and a cloud ambition — the seam earns a real interface.
