"""OpenNotebook Core Module."""

from .types import (
    Task,
    TaskType,
    Plan,
    AgentResponse,
    NotebookResponse,  # Backward compatibility alias
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
    # Planner refactor Phase 2 types
    TaskSource,
    Constraint,
    ReplanRecord,
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
# Planner (legacy + new pipeline)
from .planner import (
    # Legacy (backward compatibility)
    SimplePlanner,
    DAGPlanner,
    AdaptivePlanner,
    ReplanRequest,
    ReplanningStrategy,
    # Phase 3-4: New pipeline
    PlanningMode,
    PlanningContext,
    PlannerStage,
    ValidationResult,
    DAGValidator,
    RulePlanner,
    LLMEnhancer,
    PipelineConfig,
    PlannerPipeline,
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
from .runtime import (
    AsyncRuntime,
    ReplanCoordinator,
    CoordinatorConfig,
    CancellationToken,
)
from .agent import CodeAgent, NotebookAgent  # NotebookAgent is backward compatibility alias
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
    "AgentResponse",
    "NotebookResponse",  # Backward compatibility alias
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
    # Planner refactor Phase 2 types
    "TaskSource",
    "Constraint",
    "ReplanRecord",
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
    # Core components - Legacy Planners
    "SimplePlanner",
    "DAGPlanner",
    # Re-planning (Phase 3)
    "AdaptivePlanner",
    "ReplanRequest",
    "ReplanningStrategy",
    # Phase 3-4: Planner Pipeline
    "PlanningMode",
    "PlanningContext",
    "PlannerStage",
    "ValidationResult",
    "DAGValidator",
    "RulePlanner",
    "LLMEnhancer",
    "PipelineConfig",
    "PlannerPipeline",
    # Executor & Runtime
    "SimpleExecutor",
    "AsyncRuntime",
    "ReplanCoordinator",
    "CoordinatorConfig",
    "CancellationToken",
    "CodeAgent",
    "NotebookAgent",  # Backward compatibility alias
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
