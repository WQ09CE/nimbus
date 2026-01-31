"""OpenNotebook Core Module.

Execution model: CodeAgent uses v2 AgentOS exclusively.
The previous v1 task/runtime modules have been removed.
"""

from .types import (
    Task,
    TaskType,
    Plan,
    AgentResponse,
    NotebookResponse,  # Backward compatibility alias
    # DAG types (used by v2 scheduler)
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
    # Retry loop support (ADR-007)
    RetryLoopConfig,
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
# Planner components
from .planner import (
    # Legacy (backward compatibility)
    SimplePlanner,
    DAGPlanner,
    AdaptivePlanner,
    ReplanRequest,
    ReplanningStrategy,
    # Phase 3-4: Pipeline components
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
    # Core agent config (Phase 5)
    CoreAgentConfig,
    load_core_agent_config,
    reset_core_config_cache,
)
from .factory import (
    AgentFactory,
    create_agent,
)
from .agent_config import (
    SubagentConfig,
    SubagentConfigLoader,
    SubagentRegistry,
    get_default_registry,
    reset_default_registry,
)
from .permission import (
    PermissionRule,
    PermissionSet,
    PermissionManager,
    READONLY_PERMISSIONS,
    CODER_PERMISSIONS,
    FULL_ACCESS_PERMISSIONS,
    SAFE_BASH_PERMISSIONS,
    EXPLORER_PERMISSIONS,
    create_permission_manager,
    create_subagent_permissions,
)
from .memory import (
    SubagentContextSnapshot,
    SubagentContext,
)
from .context import (
    ContextFrame,
    ContextStack,
    FrameFactory,
    ContextStackOverflow,
    ContextStackUnderflow,
)
from .executor import SimpleExecutor
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
    # Retry loop support (ADR-007)
    "RetryLoopConfig",
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
    # Core agent config (Phase 5)
    "CoreAgentConfig",
    "load_core_agent_config",
    "reset_core_config_cache",
    # Factory (Phase 3)
    "AgentFactory",
    "create_agent",
    # Subagent Config (Phase 4)
    "SubagentConfig",
    "SubagentConfigLoader",
    "SubagentRegistry",
    "get_default_registry",
    "reset_default_registry",
    # Permission System (Phase 4)
    "PermissionRule",
    "PermissionSet",
    "PermissionManager",
    "READONLY_PERMISSIONS",
    "CODER_PERMISSIONS",
    "FULL_ACCESS_PERMISSIONS",
    "SAFE_BASH_PERMISSIONS",
    "EXPLORER_PERMISSIONS",
    "create_permission_manager",
    "create_subagent_permissions",
    # Subagent Context (Phase 4)
    "SubagentContextSnapshot",
    "SubagentContext",
    # Context Stack (ADR-010)
    "ContextFrame",
    "ContextStack",
    "FrameFactory",
    "ContextStackOverflow",
    "ContextStackUnderflow",
    # Planner components
    "SimplePlanner",
    "DAGPlanner",
    "AdaptivePlanner",
    "ReplanRequest",
    "ReplanningStrategy",
    "PlanningMode",
    "PlanningContext",
    "PlannerStage",
    "ValidationResult",
    "DAGValidator",
    "RulePlanner",
    "LLMEnhancer",
    "PipelineConfig",
    "PlannerPipeline",
    "SimpleExecutor",
    # Main agent
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
