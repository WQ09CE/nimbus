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
    "RETURN",     # Signal task completion
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
# 2. Tool & Execution Results (ABI)
# =============================================================================

ResultStatus = Literal["OK", "ERROR", "CANCELLED", "TIMEOUT"]


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


@dataclass
class StepResult:
    """Result of a single vCPU step (Think-Act-Observe)."""
    actions: List[ActionIR] = field(default_factory=list)
    results: List[ToolResult] = field(default_factory=list)
    is_final: bool = False
    final_result: Optional[ToolResult] = None
    fault: Optional["Fault"] = None
    timing_ms: Dict[str, int] = field(default_factory=dict)


# =============================================================================
# 3. Fault Taxonomy
# =============================================================================

FaultDomain = Literal["LLM", "TOOL", "KERNEL", "PERMISSION", "RESOURCE"]

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
    "FAULT_RAISED",      # Fault occurred
    "INTERRUPTED",       # Execution interrupted with partial results
    "CONTEXT_COMPACTED", # MMU compaction triggered
]


@dataclass
class Event:
    """Observable event for UI/debugging. Does not affect execution."""
    type: EventType
    pid: str
    data: Dict[str, Any] = field(default_factory=dict)
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))
