"""Nimbus Core - 核心模块

包含：
- 日志、类型、配置
- vCPU 运行时 (core.runtime)
- 调度器 (core.scheduler)
- 内存管理 (core.memory)
- 上下文压缩 (core.compaction)
"""

from .config import (
    AgentConfig,
    LLMConfig,
)
from .logging import (
    LogEvent,
    LogLevel,
    agent_context,
    catch,
    get_agent_logger,
    get_logger,
    log_context,
    logger,
    setup_logging,
)

# Memory legacy (被 storage 使用)
from .memory_legacy import (
    MemoryConfig,
    MemoryStats,
    MemoryTier,
    Message,
    PinnedItem,
    SimpleMemory,
    TieredMemoryManager,
)
from .types import (
    AgentResponse,
    Artifact,
    ArtifactType,
    ExecutionResult,
    ExecutionStats,
    Plan,
    RuntimeConfig,
    Task,
    TaskDAG,
    TaskNode,
    TaskStatus,
    TaskType,
)

__all__ = [
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
    # Types
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
    # Memory
    "Message",
    "PinnedItem",
    "MemoryConfig",
    "TieredMemoryManager",
    "SimpleMemory",
    "MemoryStats",
    "MemoryTier",
]
