"""Nimbus Core - 仅保留被使用的模块

大部分 v1 核心模块已移动到 nimbus.legacy.core
这里保留 v2 和 storage 需要的共享模块。
"""

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

from .types import (
    Task,
    TaskType,
    Plan,
    AgentResponse,
    TaskStatus,
    TaskNode,
    TaskDAG,
    RuntimeConfig,
    ExecutionStats,
    ExecutionResult,
    Artifact,
    ArtifactType,
)

from .config import (
    AgentConfig,
    LLMConfig,
)

# Memory (被 storage 使用)
from .memory import (
    Message,
    PinnedItem,
    MemoryConfig,
    TieredMemoryManager,
    SimpleMemory,
    MemoryStats,
    MemoryTier,
)

__all__ = [
    # Logging (被 v2 使用)
    "logger",
    "LogLevel",
    "LogEvent",
    "get_logger",
    "get_agent_logger",
    "setup_logging",
    "catch",
    "log_context",
    "agent_context",
    # Types (可能被引用)
    "Task",
    "TaskType",
    "Plan",
    "AgentResponse",
    "TaskStatus",
    "TaskNode",
    "TaskDAG",
    "RuntimeConfig",
    "ExecutionStats",
    "ExecutionResult",
    "Artifact",
    "ArtifactType",
    # Config
    "AgentConfig",
    "LLMConfig",
    # Memory (被 storage 使用)
    "Message",
    "PinnedItem",
    "MemoryConfig",
    "TieredMemoryManager",
    "SimpleMemory",
    "MemoryStats",
    "MemoryTier",
]
