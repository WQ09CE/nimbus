"""
Nimbus v2 Core - Protocol, Runtime, Scheduler, Session, and Compaction definitions.
"""

from nimbus.v2.core.protocol import (
    ActionIR,
    ActionKind,
    ToolResult,
    ResultStatus,
    Fault,
    FaultDomain,
    FaultCode,
    Event,
    EventType,
    IPCMessage,
    ArtifactRef,
)

from nimbus.v2.core.scheduler import (
    Task,
    TaskSpec,
    TaskState,
    DAG,
    Scheduler,
    SchedulerConfig,
    EventStream,
    create_dag,
    create_linear_dag,
)

from nimbus.v2.core.session import (
    SessionManager,
    InMemorySessionManager,
    SessionEntry,
    EntryType,
)

from nimbus.v2.core.compaction import (
    CompactionConfig,
    CompactionResult,
    CompactionPreparation,
    CompactionEngine,
    SimpleCompactionEngine,
    DefaultCompactionLLM,
    ContextStackAwareCompaction,
)

__all__ = [
    # Protocol
    "ActionIR",
    "ActionKind",
    "ToolResult",
    "ResultStatus",
    "Fault",
    "FaultDomain",
    "FaultCode",
    "Event",
    "EventType",
    "IPCMessage",
    "ArtifactRef",
    # Scheduler
    "Task",
    "TaskSpec",
    "TaskState",
    "DAG",
    "Scheduler",
    "SchedulerConfig",
    "EventStream",
    "create_dag",
    "create_linear_dag",
    # Session
    "SessionManager",
    "InMemorySessionManager",
    "SessionEntry",
    "EntryType",
    # Compaction
    "CompactionConfig",
    "CompactionResult",
    "CompactionPreparation",
    "CompactionEngine",
    "SimpleCompactionEngine",
    "DefaultCompactionLLM",
    "ContextStackAwareCompaction",
]
