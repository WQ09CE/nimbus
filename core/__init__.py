"""OpenNotebook Core Module."""

from .types import (
    Task,
    TaskType,
    Plan,
    NotebookResponse,
    # DAG types
    TaskStatus,
    TaskNode,
    TaskDAG,
    RuntimeConfig,
    ExecutionStats,
    ExecutionResult,
    # Artifact types (Phase 4)
    Artifact,
    ArtifactType,
)
from .memory import (
    SimpleMemory,
    TieredMemoryManager,
    MemoryConfig,
    MemoryStats,
    PinnedItem,
    Message,
    MemoryTier,
)
from .planner import (
    SimplePlanner,
    DAGPlanner,
    # Phase 3: Re-planning
    AdaptivePlanner,
    ReplanRequest,
    ReplanningStrategy,
)
from .config import (
    AgentConfig,
    LLMConfig,
    SkillConfig,
    MemoryConfigSpec,
    RuntimeConfigSpec,
    SkillType,
)
from .factory import (
    AgentFactory,
    create_agent,
)
from .executor import SimpleExecutor
from .runtime import AsyncRuntime
from .agent import NotebookAgent
from .logging import (
    logger,
    LogLevel,
    LogEvent,
    get_logger,
    get_agent_logger,
    setup_logging,
    catch,
    log_context,
    agent_context,
)
from .tracing import (
    Span,
    Tracer,
    get_tracer,
    reset_tracer,
    trace,
)
from .checkpoint import (
    CheckpointMeta,
    CheckpointSaver,
    JsonCheckpointSaver,
)
from .vector_store import (
    Document,
    SearchResult,
    VectorStore,
    ChromaVectorStore,
    EmbeddingClient,
    OllamaEmbeddingClient,
    MockEmbeddingClient,
)

__all__ = [
    # Types
    "Task",
    "TaskType",
    "Plan",
    "NotebookResponse",
    # DAG types
    "TaskStatus",
    "TaskNode",
    "TaskDAG",
    "RuntimeConfig",
    "ExecutionStats",
    "ExecutionResult",
    # Artifact types (Phase 4)
    "Artifact",
    "ArtifactType",
    # Memory
    "SimpleMemory",
    "TieredMemoryManager",
    "MemoryConfig",
    "MemoryStats",
    "PinnedItem",
    "Message",
    "MemoryTier",
    # Config (Phase 3)
    "AgentConfig",
    "LLMConfig",
    "SkillConfig",
    "MemoryConfigSpec",
    "RuntimeConfigSpec",
    "SkillType",
    # Factory (Phase 3)
    "AgentFactory",
    "create_agent",
    # Core components
    "SimplePlanner",
    "DAGPlanner",
    # Re-planning (Phase 3)
    "AdaptivePlanner",
    "ReplanRequest",
    "ReplanningStrategy",
    # Executor & Runtime
    "SimpleExecutor",
    "AsyncRuntime",
    "NotebookAgent",
    # Logging
    "logger",
    "LogLevel",
    "LogEvent",
    "get_logger",
    "get_agent_logger",
    "setup_logging",
    "catch",
    "log_context",
    "agent_context",
    # Tracing
    "Span",
    "Tracer",
    "get_tracer",
    "reset_tracer",
    "trace",
    # Checkpoint
    "CheckpointMeta",
    "CheckpointSaver",
    "JsonCheckpointSaver",
    # Vector Store
    "Document",
    "SearchResult",
    "VectorStore",
    "ChromaVectorStore",
    "EmbeddingClient",
    "OllamaEmbeddingClient",
    "MockEmbeddingClient",
]
