"""
Nimbus v2 Protocol - The System Spine

This module defines the core data structures (ISA/ABI) that all components must use.
It is the foundation of the v2 architecture.

Key Types:
- ActionIR: The standard instruction format for vCPU (Instruction Set Architecture)
- ToolResult: The standard return value for any side-effect (Application Binary Interface)
- Fault: Structured exception for self-healing and routing
- Event: Observable events for UI/debugging
- IPCMessage: Inter-process communication message

Design Principles:
- All types are immutable (frozen dataclass) where possible
- All types are JSON-serializable
- Version field for future compatibility
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# =============================================================================
# 1. Action Instruction Set (ISA)
# =============================================================================

ActionKind = Literal[
    "TOOL_CALL",  # Syscall: Execute external tool
    "SUB_CALL",  # Control Flow: Push stack frame (spawn subprocess)
    "RETURN",  # Control Flow: Pop stack frame (return result)
    "THOUGHT",  # Internal: Chain-of-Thought / Logging
    "POST_IPC",  # IPC: Publish reference to IPC bus
    "REQUEST_REPLAN",  # Planner: Request DAG modification
    "CANCEL",  # Control: Cancel operation
]


@dataclass
class ActionIR:
    """
    The standard instruction format for vCPU.

    This is the "assembly language" of the Agent OS. All LLM outputs are
    decoded into ActionIR before execution.

    Attributes:
        kind: The type of action (see ActionKind)
        name: Tool name / IPC channel / reason (context-dependent)
        args: Action arguments (must be JSON-serializable)
        id: Unique identifier for tracking
        meta: Additional metadata (e.g., source location, hints)
        version: Protocol version for compatibility

    Examples:
        # Tool call
        ActionIR(kind="TOOL_CALL", name="Read", args={"file_path": "/path/to/file"})

        # Spawn subprocess
        ActionIR(kind="SUB_CALL", name="explore_codebase", args={"goal": "find auth module"})

        # Return result
        ActionIR(kind="RETURN", name="return", args={"result": "task completed"})
    """

    kind: ActionKind
    name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    meta: Dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"


# =============================================================================
# 2. Tool & Execution Results (ABI)
# =============================================================================

ResultStatus = Literal["OK", "ERROR", "CANCELLED", "TIMEOUT"]


@dataclass
class ArtifactRef:
    """
    Reference to an artifact produced by a tool.

    Artifacts are external resources (files, blobs, diffs) that are too large
    to include inline in the result.

    URI Schemes:
        - artifacts://hash... : Content-addressed storage
        - workspace://path... : Workspace-relative path
        - file:///absolute/path : Absolute file path
    """

    kind: Literal["FILE", "BLOB", "JSON", "DIFF"]
    uri: str
    summary: str = ""


@dataclass
class ToolResult:
    """
    Standard return value for any side-effect.

    This is the "return value ABI" of the Agent OS. All tool executions,
    subprocess completions, and IPC messages produce a ToolResult.

    Attributes:
        status: Execution status (OK, ERROR, CANCELLED, TIMEOUT)
        output: The actual data (JSON-serializable)
        is_final: True if this is a final result (for RETURN action)
        fault: Structured error if status != OK
        artifacts: List of external artifact references
        timing_ms: Execution timing breakdown
        cost: Resource cost breakdown (tokens, API calls, etc.)
        version: Protocol version for compatibility
    """

    status: ResultStatus = "OK"
    output: Any = None
    is_final: bool = False
    fault: Optional["Fault"] = None
    artifacts: List[ArtifactRef] = field(default_factory=list)
    timing_ms: Dict[str, int] = field(default_factory=dict)
    cost: Dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"
    meta: Dict[str, Any] = field(default_factory=dict)  # Additional metadata


# =============================================================================
# 3. Fault Taxonomy
# =============================================================================

FaultDomain = Literal["LLM", "TOOL", "KERNEL", "PERMISSION", "RESOURCE"]

FaultCode = Literal[
    # LLM Domain
    "ILL_INSTRUCTION",  # Decoder intercepted hallucination
    "CTX_OVERFLOW",  # Context window exceeded
    "BAD_FORMAT",  # Invalid output format
    "RATE_LIMIT",  # API rate limit
    # Tool Domain
    "TOOL_NOT_FOUND",  # Tool does not exist
    "TOOL_FAILURE",  # Runtime error in tool
    "INVALID_ARGS",  # Invalid tool arguments
    # Permission Domain
    "PERMISSION_DENIED",  # Gate rejected action
    # Resource Domain
    "TIMEOUT",  # Execution timed out
    "BUDGET_EXCEEDED",  # Token/cost budget exceeded
    # Kernel Domain
    "SYSTEM_ERROR",  # Unexpected kernel panic
]


@dataclass
class Fault(Exception):
    """
    Structured exception for self-healing.

    Faults are not just errors - they carry enough information for the
    InterruptHandler to decide how to recover (retry, fallback, escalate).

    Attributes:
        domain: The subsystem that raised the fault
        code: Specific fault code for routing
        message: Human-readable error message
        retryable: Whether the operation can be retried
        context: Additional context for debugging
        version: Protocol version for compatibility

    Examples:
        # LLM hallucination
        Fault(domain="LLM", code="ILL_INSTRUCTION",
              message="Detected text-based tool simulation", retryable=False)

        # Tool timeout
        Fault(domain="RESOURCE", code="TIMEOUT",
              message="Tool execution exceeded 60s", retryable=True)
    """

    domain: FaultDomain
    code: FaultCode
    message: str
    retryable: bool = False
    context: Dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"

    def __str__(self) -> str:
        return f"[{self.domain}:{self.code}] {self.message}"

    def __repr__(self) -> str:
        return f"Fault({self.domain!r}, {self.code!r}, {self.message!r})"


# =============================================================================
# 4. Event Stream
# =============================================================================

EventType = Literal[
    # Process Lifecycle
    "PROC_SPAWNED",  # New process created
    "PROC_FINISHED",  # Process completed
    # Task Lifecycle
    "TASK_CREATED",  # Task added to scheduler
    "TASK_ASSIGNED",  # Task assigned to process
    "TASK_FINISHED",  # Task completed
    # Step Lifecycle
    "STEP_STARTED",  # vCPU step began
    "ACTION_EMITTED",  # ActionIR produced
    # Tool Execution
    "TOOL_STARTED",  # Tool execution began
    "TOOL_FINISHED",  # Tool execution completed
    # Errors
    "FAULT_RAISED",  # Fault occurred
    # Planner
    "REPLAN_REQUESTED",  # Replan requested
]


@dataclass
class Event:
    """
    Observable event for UI/debugging.

    Events are emitted by various components and collected by the EventStream.
    They are purely observational and do not affect execution.

    Attributes:
        type: Event type (see EventType)
        pid: Process ID that emitted the event
        data: Event-specific data
        ts_ms: Timestamp in milliseconds
        version: Protocol version for compatibility
    """

    type: EventType
    pid: str
    data: Dict[str, Any] = field(default_factory=dict)
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    version: str = "1.0"


# =============================================================================
# 5. IPC Message
# =============================================================================


@dataclass
class IPCMessage:
    """
    Inter-process communication message.

    IPC messages carry references (not data) between processes.
    The actual data is stored in artifacts or a result store.

    Key Rule: Cross-process communication shares REFs, not memory.

    Attributes:
        channel: Message channel (e.g., "task_result", "artifact")
        key: Unique key within the channel (e.g., "t1.output")
        value_ref: Reference to the actual data (artifact ID / store key)
        meta: Additional metadata
        version: Protocol version for compatibility
    """

    channel: str
    key: str
    value_ref: str
    meta: Dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"
