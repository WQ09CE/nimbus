"""
Nimbus Next Protocol — The System Spine

Core data structures (ISA/ABI) that all components communicate through.

Types:
- ActionIR: Instruction format for vCPU (the "assembly language")
- ToolResult: Return value for any side-effect (split: output for LLM, ui_detail for UI)
- StepResult: Single tick of vCPU execution
- Fault: Structured exception for recovery routing
- Event: Observable events for UI/debugging

Design influenced by pi-coding-agent's structured split tool results:
- output: text/JSON consumed by the LLM
- ui_detail: structured data for rich UI rendering (charts, diffs, tables, etc.)
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# =============================================================================
# 1. Action Instruction Set (ISA)
# =============================================================================

ActionKind = Literal[
    "TOOL_CALL",  # Execute external tool (syscall)
    "REPLY",      # User-facing response
    "THOUGHT",    # Internal chain-of-thought
    "RETURN",     # Signal goal completion
    "CANCEL",     # Cancel current operation
]


@dataclass
class ActionIR:
    """Standard instruction format for vCPU.

    All LLM outputs are decoded into ActionIR before execution.
    """
    kind: ActionKind
    name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    meta: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# 1b. Tool Traits (catalog-level policy contract)
# =============================================================================

SideEffects = Literal["none", "read", "write", "execute"]
LatencyClass = Literal["local", "network", "batch"]
DataPlane = Literal["inline", "by_reference"]
# Repeatability — what a SECOND execution of the same call does (recovery axis,
# orthogonal to side_effects which is the authority/sandbox axis):
#   free  : pure read/compute, rerun is free
#   keyed : rerun with the same idempotency key collapses at the receiver
#   once  : non-idempotent external effect (send a message, run a shell command
#           against unknown state) — never rerun automatically. The default.
# Same three classes as HTTP safe / idempotent / neither (RFC 9110) and MCP's
# readOnlyHint / idempotentHint; a tool that declares nothing is treated as once.
Repeat = Literal["free", "keyed", "once"]


@dataclass(frozen=True)
class ToolTraits:
    """Declared behavior class of a tool, carried by its catalog entry.

    Policy derives from traits instead of name-matching (design:
    docs/design/toolcall-gate-execution-backend.md §6): ``side_effects``
    selects the sandbox/approval class, ``needs_auth`` marks connector-class
    tools running on user credentials, ``latency_class`` and ``data_plane``
    inform dispatch. Name-specific rules remain possible as overrides.
    """

    side_effects: SideEffects
    needs_auth: bool = False
    latency_class: LatencyClass = "local"
    data_plane: DataPlane = "inline"
    repeat: Repeat = "once"


# =============================================================================
# 2. Tool & Execution Results (ABI)
# =============================================================================

ResultStatus = Literal["OK", "ERROR", "CANCELLED", "TIMEOUT", "SKIPPED", "PAUSED"]


@dataclass
class ToolResult:
    """Standard return value for any side-effect.

    Split result pattern (inspired by pi-coding-agent):
    - output: Text/JSON for the LLM to consume (kept concise)
    - ui_detail: Structured data for rich UI rendering (diffs, charts, tables)

    This separation lets the LLM work with minimal text summaries while
    the UI gets structured data it can render without parsing text output.
    """
    status: ResultStatus = "OK"
    output: Any = None
    ui_detail: Optional[Dict[str, Any]] = None  # Structured data for UI rendering
    is_final: bool = False
    fault: Optional["Fault"] = None
    timing_ms: Dict[str, int] = field(default_factory=dict)
    cost: Dict[str, Any] = field(default_factory=dict)
    # Declarative turn conclusion (dsh concludesTurn): a tool result may
    # declare that it ends the turn — evidence-carrying termination, instead
    # of the framework keeping a name list of terminal tools. Set by a tool
    # returning {"concludes_turn": True} (see KernelGate).
    concludes_turn: bool = False


@dataclass
class StepResult:
    """Result of a single vCPU step (Think-Act-Observe)."""
    actions: List[ActionIR] = field(default_factory=list)
    results: List[ToolResult] = field(default_factory=list)
    is_final: bool = False
    final_result: Optional[ToolResult] = None
    fault: Optional["Fault"] = None
    timing_ms: Dict[str, int] = field(default_factory=dict)
    steering_messages: List[str] = field(default_factory=list)
    usage: Optional[Any] = None  # TokenUsage from LLM response


# =============================================================================
# 3. Fault Taxonomy
# =============================================================================

FaultDomain = Literal["LLM", "TOOL", "KERNEL", "PERMISSION", "RESOURCE", "BACKEND"]

FaultCode = Literal[
    "ILL_INSTRUCTION",   # Hallucination detected
    "CTX_OVERFLOW",      # Context window exceeded
    "BAD_FORMAT",        # Invalid LLM output format
    "RATE_LIMIT",        # API rate limit hit
    "TOOL_NOT_FOUND",    # Unknown tool name
    "TOOL_FAILURE",      # Runtime error in tool
    "INVALID_ARGS",      # Bad tool arguments
    "PERMISSION_DENIED", # Gate rejected action
    "TIMEOUT",           # Execution timed out
    "BUDGET_EXCEEDED",   # Token/cost budget exceeded
    "SYSTEM_ERROR",      # Unexpected kernel error
    # BACKEND domain — the execution channel failed, not the tool itself
    "BACKEND_UNAVAILABLE",  # Channel down/unreachable after retries
    "LEASE_LOST",           # Stateful backend lost session state
    "NETWORK",              # Transient transport failure (retryable class)
    "AUTH_REQUIRED",        # Connector-class backend needs user authorization
    "AUTH_EXPIRED",         # Credential expired; silent refresh failed
]


@dataclass
class Fault(Exception):
    """Structured exception for recovery routing.

    Carries enough info for the error handler to decide:
    retry, fallback, or escalate.
    """
    domain: FaultDomain
    code: FaultCode
    message: str
    retryable: bool = False
    context: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.domain}:{self.code}] {self.message}"

    def __repr__(self) -> str:
        return f"Fault({self.domain!r}, {self.code!r}, {self.message!r})"


# =============================================================================
# 4. Event Stream
# =============================================================================

EventType = Literal[
    # Coarse (step-level)
    "STEP_STARTED",      # vCPU step began
    "STEP_FINISHED",     # vCPU step completed

    # Fine-grained (pi-style streaming events)
    "TEXT_DELTA",        # LLM streaming text chunk
    "TOOL_CALL_START",   # Tool call decoded, about to execute
    "TOOL_CALL_DELTA",   # Streaming output from tool (e.g. bash stdout)
    "TOOL_CALL_DONE",    # Tool execution completed with result
    "ACTION_EMITTED",    # ActionIR produced (legacy compat)

    # Lifecycle
    "TOOL_STARTED",      # Tool execution began (Gate-level)
    "TOOL_FINISHED",     # Tool execution completed (Gate-level)
    "POLICY_REQUESTED",  # Human authorization is required before execution
    "POLICY_DECIDED",    # Durable allow/deny decision for a proposed action
    "AUTH_REQUESTED",    # Connector-class backend needs the user to authorize
    "SANDBOX_STATUS",    # Effective sandbox backend/posture and verdict
    "FAULT_RAISED",      # Fault occurred
    "INTERRUPTED",       # Execution interrupted with partial results
    "PAUSED",            # Execution paused at a clean step seam (verbatim-resumable)
    "CONTEXT_COMPACTED", # MMU compaction triggered
]


@dataclass
class Event:
    """Observable event for UI/debugging. Does not affect execution."""
    type: EventType
    pid: str
    data: Dict[str, Any] = field(default_factory=dict)
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))

# =============================================================================
# 5. IPC Message (Restored from V3 for server compatibility)
# =============================================================================

@dataclass
class IPCMessage:
    """Inter-process communication message.
    
    IPC messages carry references (not data) between processes.
    """
    channel: str
    key: str
    value_ref: str
    meta: Dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"
