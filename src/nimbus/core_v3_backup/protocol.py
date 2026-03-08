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

# NimFS auto-offload threshold: outputs larger than this will be stored in
# NimFS and replaced with a nimfs:// reference to avoid context overflow.
NIMFS_OFFLOAD_THRESHOLD = 8_000  # characters

# =============================================================================
# 1. Action Instruction Set (ISA)
# =============================================================================

ActionKind = Literal[
    "TOOL_CALL",  # Syscall: Execute external tool
    "SUB_CALL",  # Control Flow: Push stack frame (spawn subprocess)
    "RETURN",  # Control Flow: Pop stack frame (return result)
    "REPLY",  # UI: User-facing response (Conversational)
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

    # NimFS IPC: when output exceeds NIMFS_OFFLOAD_THRESHOLD, it is offloaded
    # to NimFS and this field carries the nimfs://artifact/{id} reference.
    # Consumers should call NimFSReadArtifact(ref) to retrieve the full content.
    artifact_ref: Optional[str] = None  # nimfs://artifact/{artifact_id}


@dataclass
class StepResult:
    """
    Standard return value for a single tick (step) of the VCPU.

    This captures the actions requested by the LLM (THINK/DECODE) and their 
    execution results (ACT) before the VCPU suspends for the OS to handle
    streaming, memory compaction, or other daemon tasks.

    Attributes:
        actions: The instructions issued by the LLM in this step.
        results: The results from executing those actions.
        is_final: True if the Agent has completed its goal or encountered an unrecoverable error.
        final_result: The final synthesized ToolResult if is_final is True.
        fault: Any unrecoverable fault encountered that halted the step.
        timing_ms: Sub-system timing breakdown.
    """
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


# =============================================================================
# 6. NimFS Integration Helpers
# =============================================================================


def offload_to_nimfs(
    result: "ToolResult",
    workspace: str,
    task_id: str,
    producer: str = "agent",
) -> "ToolResult":
    """
    Offload a large ToolResult output to NimFS if it exceeds the threshold.

    When output exceeds NIMFS_OFFLOAD_THRESHOLD characters, stores the full
    content in NimFS artifacts/ and replaces output with a compact summary
    containing the nimfs:// reference.

    Non-destructive: if output is small enough, returns the original result
    unchanged (zero overhead for normal-sized outputs).

    Args:
        result:    The ToolResult to potentially offload.
        workspace: Current workspace path (used to construct NimFSManager).
        task_id:   Task identifier for artifact grouping.
        producer:  Agent role name for artifact provenance.

    Returns:
        The original ToolResult if small enough, otherwise a new ToolResult
        with output replaced by a compact reference message and artifact_ref
        set to the nimfs:// URI.

    Example:
        result = offload_to_nimfs(big_result, workspace="/path/to/ws",
                                   task_id="task-explore-1", producer="explore-agent")
        # If offloaded: result.artifact_ref == "nimfs://artifact/task-explore-1-abc123"
        #               result.output == "[NimFS] Full content stored at nimfs://... (45230 bytes)"
    """
    output_str = str(result.output) if result.output is not None else ""
    if len(output_str) <= NIMFS_OFFLOAD_THRESHOLD:
        return result  # Small enough — no offload needed

    try:
        # Lazy import to avoid circular deps at module load time
        from nimbus.core.nimfs.manager import NimFSManager
        from nimbus.core.nimfs.models import ArtifactTTL

        manager = NimFSManager(workspace)
        ref = manager.write_artifact(
            content=output_str,
            task_id=task_id,
            producer=producer,
            artifact_type="text",
            ttl=ArtifactTTL.SESSION,
            summary=output_str[:200].replace("\n", " "),
        )

        compact_output = (
            f"[NimFS Offload] Output too large for inline context "
            f"({len(output_str):,} chars > {NIMFS_OFFLOAD_THRESHOLD:,} threshold).\n"
            f"Full content stored at: {ref}\n"
            f"Use NimFSReadArtifact tool or call read_artifact('{ref}') to retrieve it.\n\n"
            f"Preview (first 500 chars):\n{output_str[:500]}..."
        )

        return ToolResult(
            status=result.status,
            output=compact_output,
            is_final=result.is_final,
            fault=result.fault,
            artifacts=result.artifacts,
            timing_ms=result.timing_ms,
            cost=result.cost,
            version=result.version,
            meta=result.meta,
            artifact_ref=ref,
        )
    except Exception:
        # NimFS offload failed — return original result unchanged (graceful degradation)
        return result
