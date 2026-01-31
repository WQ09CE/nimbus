# Agent OS: Linux Process Mechanism Integration

> **Version**: 1.0.0
> **Status**: Proposed
> **Author**: Architect (Yi-Fenshen)
> **Date**: 2026-01-26
> **Based on**: Von Neumann Architecture v2.0 + Linux Process Model

## Summary

本文档设计将 **Linux Process 机制** 融入 Nimbus 的冯·诺依曼架构中。核心洞察：**多 Agent 协作本质上是多进程协作**，我们可以借鉴 Linux 的 PCB、fork、IPC、调度器等成熟机制来构建健壮的多 Agent 系统。

## Design

### 架构概述

```
+===========================================================================+
|                          LAYER 2: APPLICATION                              |
|  +---------------------------------------------------------------------+  |
|  |  CodeAgent | ChatAgent | NovelAgent (Config-driven Process Def)    |  |
|  +---------------------------------------------------------------------+  |
+===========================================================================+
                                    |
                                    | System Calls (Kernel API)
                                    v
+===========================================================================+
|                    LAYER 1: AGENT OS (Kernel)                              |
|  +=====================================================================+  |
|  ||                     vCPU (Virtual Processor)                       ||  |
|  ||  +------------------+  +------------------+  +------------------+  ||  |
|  ||  | Control Unit     |  | MMU              |  | Interrupt Handler|  ||  |
|  ||  | (AgenticRunner)  |  | (TieredMemory)   |  | (Error Recovery) |  ||  |
|  ||  +------------------+  +------------------+  +------------------+  ||  |
|  +=====================================================================+  |
|                                                                            |
|  +=====================================================================+  |
|  ||           Process Management Subsystem (NEW!)                      ||  |
|  ||                                                                     ||  |
|  ||  +------------------+  +------------------+  +------------------+  ||  |
|  ||  | ProcessManager   |  | Process Table    |  | ContextFactory   |  ||  |
|  ||  | fork/wait/kill   |  | (PCB Storage)    |  | (Context Forking)||  |
|  ||  +------------------+  +------------------+  +------------------+  ||  |
|  ||                                                                     ||  |
|  ||  +------------------+  +------------------+                        ||  |
|  ||  | IPC Bus          |  | Signal Handler   |                        ||  |
|  ||  | (Message Queue)  |  | (SIGTERM/SIGKILL)|                        ||  |
|  ||  +------------------+  +------------------+                        ||  |
|  +=====================================================================+  |
|                                                                            |
|  +=====================================================================+  |
|  ||                     Scheduler Subsystem                            ||  |
|  ||  +------------------+  +------------------+  +------------------+  ||  |
|  ||  | DAGOrchestrator  |  | ProcessScheduler |  | TimeoutWatchdog  |  ||  |
|  ||  | (AsyncRuntime)   |  | (Asyncio Tasks)  |  | (Orphan Handler) |  ||  |
|  ||  +------------------+  +------------------+  +------------------+  ||  |
|  +=====================================================================+  |
|                                                                            |
|  +=====================================================================+  |
|  ||                     File System & Security                         ||  |
|  ||  +------------------+  +------------------+  +------------------+  ||  |
|  ||  | CheckpointFS     |  | VirtualFSView    |  | PermissionMgr    |  ||  |
|  ||  | (Process State)  |  | (chroot-like)    |  | (ACL/Capability) |  ||  |
|  ||  +------------------+  +------------------+  +------------------+  ||  |
|  +=====================================================================+  |
+===========================================================================+
                                    |
                                    | Hardware Abstraction Layer
                                    v
+===========================================================================+
|                       LAYER 0: INFRASTRUCTURE (Hardware)                   |
|  +---------------------------------------------------------------------+  |
|  |  ALU (LLM Adapter)  |  ISA (Tool Interfaces)  |  Registers (Prompt) |  |
|  +---------------------------------------------------------------------+  |
+===========================================================================+
```

### Process Mechanism 组件在三层架构中的位置

| 组件 | 层级 | 冯·诺依曼角色 | 说明 |
|------|------|--------------|------|
| AgentProcess (PCB) | Layer 1 | Process Control Block | 存储进程状态、资源配额、内存视图 |
| ProcessManager | Layer 1 | Kernel Service | fork/wait/kill 系统调用 |
| ProcessTable | Layer 1 | Kernel Data Structure | 全局进程表，存储所有 PCB |
| ContextFactory | Layer 1 | Memory Service | 实现 fork 时的上下文"写时复制" |
| IPC Bus | Layer 1 | Kernel Service | 进程间通信管道 |
| Signal Handler | Layer 1 | Interrupt Handler | 处理 SIGTERM/SIGKILL/SIGPAUSE |
| ProcessScheduler | Layer 1 | Scheduler | asyncio.Task 管理，与 AsyncRuntime 集成 |
| TimeoutWatchdog | Layer 1 | Watchdog Timer | 监控进程超时，处理孤儿进程 |
| VirtualFSView | Layer 1 | VFS (chroot) | 为进程分配隔离的文件系统视图 |

### 核心数据结构

#### AgentProcess (PCB - Process Control Block)

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import uuid


class ProcessState(str, Enum):
    """Process lifecycle states (Linux-inspired)."""
    CREATED = "created"       # fork() called, not yet started
    READY = "ready"           # Ready to run, waiting for scheduler
    RUNNING = "running"       # Currently executing
    BLOCKED = "blocked"       # Waiting for I/O or IPC
    COMPLETED = "completed"   # Finished successfully (exit code 0)
    FAILED = "failed"         # Finished with error (exit code != 0)
    ZOMBIE = "zombie"         # Completed but parent hasn't called wait()
    CANCELLED = "cancelled"   # Killed by signal


@dataclass
class AgentProcess:
    """Process Control Block for Agent Process.

    Analogous to Linux's task_struct, this structure holds all
    metadata about an agent "process" including:
    - Identity: pid, parent_pid, role
    - Context: isolated memory space (messages)
    - Resources: token budget, file permissions
    - State: current execution state

    Architecture Layer: 1 (Agent OS - Kernel)
    Von Neumann Role: PCB (Process Control Block)
    """

    # === Identity ===
    pid: str                          # Unique process ID (e.g., "proc_abc123")
    parent_pid: Optional[str]         # Parent process ID (None for init)
    role: str                         # Agent role (eye, body, mind, etc.)

    # === Context Isolation (Address Space) ===
    # Each process has its own message list (memory space)
    memory: List[Dict[str, Any]] = field(default_factory=list)

    # System prompt (inherited/modified from parent)
    system_prompt: str = ""

    # Task assigned to this process
    task_instruction: str = ""

    # Files specifically mounted for this process (subset of parent's view)
    mounted_files: List[str] = field(default_factory=list)

    # === Resource Quotas (cgroups-like) ===
    token_usage: int = 0              # Current token consumption
    max_token_budget: int = 50000     # Maximum allowed tokens
    max_turns: int = 50               # Maximum conversation turns
    current_turn: int = 0             # Current turn counter

    # === State ===
    state: ProcessState = ProcessState.CREATED
    exit_code: int = 0                # 0 = success, non-zero = error
    result: Any = None                # Return value on completion
    error: Optional[str] = None       # Error message if failed

    # === Permissions (Capabilities) ===
    allowed_tools: Set[str] = field(default_factory=set)
    fs_mode: str = "rw"               # "r" = read-only, "rw" = read-write
    allowed_paths: List[str] = field(default_factory=list)  # chroot paths

    # === Scheduling ===
    priority: int = 0                 # Scheduling priority (higher = more urgent)
    nice: int = 0                     # Nice value (lower = higher priority)
    depth: int = 0                    # Process tree depth (max 3)

    # === Timing ===
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    # === Relationships ===
    children: List[str] = field(default_factory=list)  # Child process PIDs

    # === Runtime Reference ===
    _async_task: Optional[Any] = field(default=None, repr=False)  # asyncio.Task

    @classmethod
    def create(
        cls,
        role: str,
        parent_pid: Optional[str] = None,
        depth: int = 0,
        **kwargs
    ) -> "AgentProcess":
        """Factory method to create a new process."""
        return cls(
            pid=f"proc_{uuid.uuid4().hex[:8]}",
            parent_pid=parent_pid,
            role=role,
            depth=depth,
            **kwargs
        )

    @property
    def duration_ms(self) -> Optional[int]:
        """Execution duration in milliseconds."""
        if self.started_at and self.finished_at:
            delta = self.finished_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return None

    def is_terminal(self) -> bool:
        """Check if process is in a terminal state."""
        return self.state in (
            ProcessState.COMPLETED,
            ProcessState.FAILED,
            ProcessState.ZOMBIE,
            ProcessState.CANCELLED
        )

    def add_message(self, role: str, content: str) -> None:
        """Add a message to process memory."""
        self.memory.append({"role": role, "content": content})
        self.token_usage += len(content) // 4  # Rough estimate
        self.current_turn += 1

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "pid": self.pid,
            "parent_pid": self.parent_pid,
            "role": self.role,
            "state": self.state.value,
            "token_usage": self.token_usage,
            "max_token_budget": self.max_token_budget,
            "current_turn": self.current_turn,
            "max_turns": self.max_turns,
            "depth": self.depth,
            "exit_code": self.exit_code,
            "result": self.result,
            "error": self.error,
            "allowed_tools": list(self.allowed_tools),
            "duration_ms": self.duration_ms,
        }
```

#### IPC Message and Signal

```python
from enum import Enum
from dataclasses import dataclass
from typing import Any, Dict, Optional


class Signal(str, Enum):
    """Process signals (Linux-inspired)."""
    SIGSTART = "start"      # Start execution
    SIGPAUSE = "pause"      # Pause execution (can resume)
    SIGCONT = "continue"    # Resume paused process
    SIGTERM = "terminate"   # Graceful termination request
    SIGKILL = "kill"        # Immediate termination (cannot ignore)


class IPCMessageType(str, Enum):
    """IPC message types."""
    TASK_ASSIGNMENT = "task_assignment"   # Parent assigns task to child
    RESULT_RETURN = "result_return"       # Child returns result to parent
    ERROR_REPORT = "error_report"         # Error notification
    PROGRESS_UPDATE = "progress_update"   # Incremental progress
    FILE_TRANSFER = "file_transfer"       # Share file content
    CONTEXT_INJECT = "context_inject"     # Inject additional context


@dataclass
class IPCMessage:
    """Inter-Process Communication message.

    Key insight: Use STRUCTURED data, not natural language,
    for process communication. This enables:
    - Type-safe message handling
    - Efficient parsing
    - Clear contract between processes
    """
    id: str                           # Message ID
    sender_pid: str                   # Sender process ID
    receiver_pid: str                 # Receiver process ID
    type: IPCMessageType              # Message type
    payload: Dict[str, Any]           # Structured payload
    timestamp: datetime = field(default_factory=datetime.now)

    # Optional: correlation for request-response patterns
    correlation_id: Optional[str] = None
```

### 核心接口设计

#### ProcessManager Protocol

```python
from typing import Protocol, List, Optional, Set
from .types import AgentProcess, ProcessState, Signal, IPCMessage


class ContextFilter:
    """Filter for context extraction during fork."""
    include_pinned: bool = True       # Include pinned memory items
    include_working: bool = False     # Include working memory
    include_history: int = 0          # Number of history turns to include
    include_files: List[str] = []     # Specific files to include

    @classmethod
    def minimal(cls) -> "ContextFilter":
        """Minimal context for focused tasks."""
        return cls(include_pinned=True, include_working=False, include_history=0)

    @classmethod
    def standard(cls) -> "ContextFilter":
        """Standard context with recent history."""
        return cls(include_pinned=True, include_working=True, include_history=3)


class ProcessManager(Protocol):
    """Process Manager - fork/wait/kill operations.

    Architecture Layer: 1 (Agent OS - Kernel)
    Von Neumann Role: Kernel Service (Process Management)

    Similar to Linux kernel's process management:
    - fork(): Create child process with isolated context
    - wait(): Block until child completes
    - kill(): Send signal to process
    - ps(): List running processes
    """

    def fork(
        self,
        parent_pid: str,
        role: str,
        task: str,
        context_filter: ContextFilter,
        allowed_tools: Optional[Set[str]] = None,
        max_token_budget: int = 50000,
    ) -> str:
        """Create a child process (fork).

        Key differences from SubagentExecutor.spawn():
        1. Explicit context filtering (not just snapshot)
        2. Returns pid immediately (non-blocking)
        3. Uses ProcessTable for lifecycle management

        Args:
            parent_pid: Parent process ID
            role: Child role (eye, body, mind, etc.)
            task: Task instruction for child
            context_filter: How to extract context from parent
            allowed_tools: Tool whitelist (subset of parent)
            max_token_budget: Resource limit

        Returns:
            Child process ID (pid)
        """
        ...

    async def exec(self, pid: str) -> None:
        """Execute a created process (exec after fork).

        This starts the actual execution. Separating fork/exec
        allows for pre-execution configuration.
        """
        ...

    async def wait(self, pid: str, timeout: Optional[float] = None) -> "ProcessResult":
        """Wait for process to complete (blocking).

        Args:
            pid: Process ID to wait for
            timeout: Optional timeout in seconds

        Returns:
            ProcessResult with exit code and result

        Raises:
            ProcessTimeout: If timeout exceeded
            ProcessNotFound: If pid doesn't exist
        """
        ...

    async def waitpid(self, pid: str, options: int = 0) -> Optional["ProcessResult"]:
        """Non-blocking wait (like waitpid with WNOHANG).

        Returns immediately with result if process completed,
        or None if still running.
        """
        ...

    def kill(self, pid: str, signal: Signal) -> bool:
        """Send signal to process.

        Args:
            pid: Target process ID
            signal: Signal to send

        Returns:
            True if signal sent, False if process not found
        """
        ...

    def ps(self, parent_pid: Optional[str] = None) -> List["AgentProcess"]:
        """List processes (like ps command).

        Args:
            parent_pid: If set, only list children of this parent

        Returns:
            List of process info
        """
        ...

    def getpid(self) -> str:
        """Get current process ID."""
        ...

    def getppid(self) -> Optional[str]:
        """Get parent process ID."""
        ...


class ContextFactory(Protocol):
    """Context Factory - implements fork's "copy-on-write" semantics.

    Architecture Layer: 1 (Agent OS - Kernel)
    Von Neumann Role: Memory Service

    Key insight: Don't copy full parent context. Instead:
    1. Extract essential information
    2. Compress/summarize history
    3. Inject task-specific prompt

    This is the "write-once" optimization for LLM context.
    """

    def extract_for_fork(
        self,
        parent_memory: "TieredMemoryManager",
        task_instruction: str,
        context_filter: ContextFilter,
        role: str,
    ) -> List[Dict[str, Any]]:
        """Extract and transform parent context for child process.

        This is NOT a simple copy. It:
        1. Filters based on context_filter
        2. Compresses history if needed
        3. Formats as clean Initial Prompt
        4. Adds role-specific system prompt

        Returns:
            List of messages for child's initial context
        """
        ...

    def build_system_prompt(
        self,
        role: str,
        task: str,
        allowed_tools: Set[str],
        workspace: str,
    ) -> str:
        """Build role-specific system prompt.

        Each role (eye, body, mind, etc.) has different
        instructions and constraints.
        """
        ...


class IPCBus(Protocol):
    """IPC Bus - Inter-Process Communication.

    Architecture Layer: 1 (Agent OS - Kernel)
    Von Neumann Role: Kernel Service (IPC)

    Provides message passing between processes.
    Key design: STRUCTURED messages, not natural language.
    """

    async def send(self, message: IPCMessage) -> None:
        """Send message to another process."""
        ...

    async def receive(
        self,
        receiver_pid: str,
        timeout: Optional[float] = None
    ) -> Optional[IPCMessage]:
        """Receive next message for this process."""
        ...

    async def broadcast(
        self,
        sender_pid: str,
        message_type: IPCMessageType,
        payload: Dict[str, Any],
    ) -> None:
        """Broadcast to all children of sender."""
        ...


class VirtualFSView(Protocol):
    """Virtual File System View - chroot-like isolation.

    Architecture Layer: 1 (Agent OS - Kernel)
    Von Neumann Role: VFS / Security

    Provides file system isolation for each process.
    Similar to Docker's filesystem isolation or chroot.
    """

    def __init__(
        self,
        root_dir: str,
        allowed_paths: List[str],
        mode: str = "rw"
    ):
        """Initialize FS view.

        Args:
            root_dir: Root directory for this view
            allowed_paths: List of allowed path patterns
            mode: "r" for read-only, "rw" for read-write
        """
        ...

    def validate_access(self, path: str, operation: str) -> bool:
        """Validate file access.

        Args:
            path: Absolute path to access
            operation: "read" or "write"

        Returns:
            True if access allowed

        Raises:
            PermissionError: If access denied
        """
        ...

    def resolve_path(self, path: str) -> str:
        """Resolve path within the view's root."""
        ...
```

### 与现有模块的关系

| 新模块 | 现有模块 | 关系 | 说明 |
|--------|---------|------|------|
| **ProcessManager** | SubagentExecutor | 扩展 | ProcessManager 封装 SubagentExecutor，添加 PCB 生命周期管理 |
| **AgentProcess (PCB)** | SubagentContext | 替代 | PCB 包含更完整的进程元数据，SubagentContext 可简化为 PCB 的一部分 |
| **ContextFactory** | TieredMemoryManager.create_snapshot | 扩展 | ContextFactory 使用 create_snapshot 但添加更智能的过滤和格式化 |
| **IPC Bus** | AsyncRuntime (Result Injection) | 扩展 | IPC Bus 提供通用的进程间通信，不仅仅是结果注入 |
| **VirtualFSView** | PermissionManager | 协作 | VirtualFSView 专注于路径隔离，PermissionManager 专注于工具权限 |
| **ProcessScheduler** | AsyncRuntime | 集成 | ProcessScheduler 管理 asyncio.Task，通过 AsyncRuntime 执行 DAG |
| **TimeoutWatchdog** | RuntimeConfig.default_timeout | 扩展 | Watchdog 添加全局超时监控和孤儿进程回收 |
| **Signal Handler** | CancellationToken | 扩展 | Signal 提供更丰富的进程控制（不仅仅是取消） |

### 集成架构图

```
+-----------------------------------------------------------------------+
|                          ProcessManager                                |
|  +-------------------+  +-------------------+  +-------------------+   |
|  | fork()            |  | wait()            |  | kill()            |   |
|  +--------+----------+  +--------+----------+  +--------+----------+   |
|           |                      |                      |              |
|           v                      v                      v              |
|  +--------+----------+  +--------+----------+  +--------+----------+   |
|  | ContextFactory    |  | ProcessTable      |  | Signal Handler    |   |
|  | (context extract) |  | (PCB storage)     |  | (SIGTERM/SIGKILL) |   |
|  +--------+----------+  +--------+----------+  +--------+----------+   |
|           |                      |                      |              |
+-----------|----------------------|----------------------|--------------+
            |                      |                      |
            v                      v                      v
+-----------|----------------------|----------------------|------------+
|           |    SubagentExecutor  |      AsyncRuntime    |            |
|  +--------+----------+  +--------+----------+  +--------+--------+   |
|  | spawn()           |  | _execute_subagent |  | execute_dag()   |   |
|  | (existing impl)   |  | (create CodeAgent)|  | (DAG parallel)  |   |
|  +-------------------+  +-------------------+  +------------------+   |
+----------------------------------------------------------------------+
            |                      |                      |
            v                      v                      v
+-----------|----------------------|----------------------|------------+
|  +--------+----------+  +--------+----------+  +--------+--------+   |
|  | TieredMemory      |  | PermissionManager |  | VirtualFSView   |   |
|  | (context storage) |  | (tool ACL)        |  | (path isolation)|   |
|  +-------------------+  +-------------------+  +------------------+   |
+----------------------------------------------------------------------+
```

## Decisions

### Decision 1: 扩展而非替换 SubagentExecutor

- **决策**: ProcessManager 作为 SubagentExecutor 的上层封装，而非替换
- **理由**:
  1. SubagentExecutor 已经实现了核心的并发控制和子 Agent 执行
  2. 保持 API 兼容性，现有代码无需修改
  3. ProcessManager 添加 PCB 生命周期管理和 IPC 功能
- **备选方案**: 完全重写 SubagentExecutor
- **风险**: 可能存在概念冗余，需要清晰的职责边界文档

### Decision 2: AgentProcess (PCB) 作为核心数据结构

- **决策**: 引入 AgentProcess 作为统一的进程元数据容器
- **理由**:
  1. 现有 SubagentContext 缺少资源配额、调度信息
  2. PCB 模式是成熟的操作系统设计
  3. 便于实现进程表、调度器等高级功能
- **与 SubagentContext 的关系**:
  - SubagentContext 的 `context_snapshot` -> PCB 的 `memory`
  - SubagentContext 的 `allowed_tools` -> PCB 的 `allowed_tools`
  - 新增: 资源配额、调度优先级、进程状态机

### Decision 3: 结构化 IPC 而非自然语言

- **决策**: 进程间通信使用 IPCMessage 结构化数据
- **理由**:
  1. 类型安全，便于解析
  2. 避免 LLM 理解偏差
  3. 高效，无需 LLM 参与简单的消息传递
- **消息类型**:
  - TASK_ASSIGNMENT: 父进程分配任务
  - RESULT_RETURN: 子进程返回结果
  - ERROR_REPORT: 错误通知
  - PROGRESS_UPDATE: 进度更新

### Decision 4: VirtualFSView 与 PermissionManager 分工

- **决策**:
  - VirtualFSView: 负责路径级别的隔离（类似 chroot）
  - PermissionManager: 负责工具级别的 ACL
- **理由**:
  1. 单一职责原则
  2. VirtualFSView 专注于"能访问哪些路径"
  3. PermissionManager 专注于"能使用哪些工具"
- **集成方式**: 工具执行时先检查 PermissionManager，再检查 VirtualFSView

### Decision 5: fork + exec 分离模式

- **决策**: 采用 fork() 创建进程 + exec() 开始执行的两阶段模式
- **理由**:
  1. 允许在执行前配置进程（设置环境变量、调整资源配额）
  2. 支持批量创建进程后统一调度
  3. 与 Linux 模型一致
- **与现有 spawn() 的关系**:
  - `spawn(run_in_background=False)` = `fork() + exec() + wait()`
  - `spawn(run_in_background=True)` = `fork() + exec()` (不等待)

## Tradeoffs

### 1. 概念完整性 vs 实现成本

**选择**: 引入完整的 Process 抽象，但分阶段实现

- 代价: 增加代码复杂度
- 收益: 概念统一，易于理解和扩展
- 策略: Phase 1-2 实现核心功能，Phase 3-6 按需添加高级功能

### 2. 隔离强度 vs 上下文效率

**选择**: "写时复制"的轻量级隔离

- 代价: 不是真正的进程隔离（共享 Python 进程）
- 收益: 低开销，快速 fork
- 实现: ContextFactory 提取关键信息，不复制完整历史

### 3. 通用性 vs 领域特化

**选择**: OS 层保持通用，领域逻辑放 Application

- **黄金切割测试**: Novel Agent 也需要 PCB、fork、IPC
- 代价: OS 层可能稍显抽象
- 收益: 真正实现"OS 一行不改"的目标

## Constraints

### 技术约束
- Python 3.10+ (asyncio.TaskGroup)
- 最大递归深度: 3 (防止 fork bomb)
- 最大并发进程: 5 (受限于 LLM API 并发)

### 资源约束
- 默认 Token 预算: 50K per process
- 默认超时: 30s per task, 5min per process
- 内存: PCB 约 1KB，ProcessTable 约 100KB for 100 processes

### 安全约束
- 子进程工具权限必须是父进程的子集
- VirtualFSView 只允许访问 workspace 下的路径
- 敏感路径（/etc, /usr）默认禁止

## Risks

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 概念过度抽象导致性能下降 | 中 | 中 | 性能基准测试，必要时简化 |
| 与现有 SubagentExecutor 集成困难 | 中 | 高 | 分阶段实现，保持 API 兼容 |
| IPC 消息序列化开销 | 低 | 低 | 使用高效序列化（msgpack） |
| 孤儿进程资源泄漏 | 中 | 中 | TimeoutWatchdog 定期清理 |
| fork 时上下文提取不完整 | 中 | 中 | 单元测试覆盖各种场景 |

## Evidence

### 代码分析

| 文件 | 行号 | 发现 |
|------|------|------|
| `tools/subagent.py` | 36-38 | `__layer__ = 1, __role__ = "Process_Manager"` - 已标记为 OS 层 |
| `tools/subagent.py` | 120-182 | SubagentContext 实现上下文隔离，但缺少资源配额 |
| `tools/subagent.py` | 314-376 | SubagentExecutor 已实现 semaphore 并发控制 |
| `core/memory.py` | 634-679 | TieredMemoryManager.create_snapshot() 提供快照功能 |
| `core/runtime/executor.py` | 37-38 | `__layer__ = 1, __role__ = "Scheduler"` - 已标记为调度器 |
| `core/runtime/executor.py` | 321-328 | 已有 CancellationToken 机制，可扩展为 Signal |
| `core/permission.py` | 36-37 | `__layer__ = 1, __role__ = "ACL"` - 已标记为安全模块 |
| `core/context.py` | 1-10 | ContextStack 实现上下文切换，类似 CPU context switch |

### 假设

1. **假设**: asyncio 足以满足并发需求（不需要多进程）
2. **假设**: LLM API 并发限制不超过 5
3. **假设**: 子进程不需要真正的内存隔离（共享 Python 堆）

## Implementation Roadmap

### Phase 1: 基础数据结构 (Week 1)

**目标**: 定义核心类型，不改变现有行为

```
src/nimbus/core/process/
├── __init__.py
├── types.py          # AgentProcess, ProcessState, Signal, IPCMessage
├── protocol.py       # ProcessManager, ContextFactory, IPCBus protocols
└── table.py          # ProcessTable implementation
```

**任务清单**:
- [ ] 定义 `AgentProcess` (PCB) dataclass
- [ ] 定义 `ProcessState`, `Signal`, `IPCMessageType` enums
- [ ] 定义 `IPCMessage` dataclass
- [ ] 定义 `ProcessManager`, `ContextFactory`, `IPCBus` Protocols
- [ ] 实现 `ProcessTable` (simple dict-based)

**验收标准**:
- 单元测试通过
- 类型检查通过 (mypy)
- 与现有代码无冲突

### Phase 2: Process 生命周期管理 (Week 2)

**目标**: 实现 fork/wait/kill 核心功能

```
src/nimbus/core/process/
├── manager.py        # ProcessManager implementation
└── factory.py        # ContextFactory implementation
```

**任务清单**:
- [ ] 实现 `ProcessManagerImpl.fork()` - 封装 SubagentExecutor
- [ ] 实现 `ProcessManagerImpl.wait()` - 封装 asyncio.Task.result()
- [ ] 实现 `ProcessManagerImpl.kill()` - 使用 CancellationToken
- [ ] 实现 `ContextFactoryImpl.extract_for_fork()` - 使用 TieredMemory.create_snapshot()
- [ ] 实现 `ProcessManagerImpl.ps()` - 列出进程

**验收标准**:
- fork + wait 基本流程工作
- 与现有 SubagentExecutor 功能等价
- 集成测试通过

### Phase 3: 上下文隔离增强 (Week 3)

**目标**: 实现智能的上下文"写时复制"

**任务清单**:
- [ ] 实现 `ContextFilter` 配置类
- [ ] 实现 `ContextFactory.build_system_prompt()` - 角色特定 prompt
- [ ] 优化 `extract_for_fork()` - 支持压缩历史
- [ ] 集成 TieredMemoryManager 的四层存储
- [ ] 添加上下文大小估算和自动截断

**验收标准**:
- 子进程上下文 < 2000 tokens
- 关键信息保留完整
- 性能测试: fork 延迟 < 100ms

### Phase 4: IPC 机制 (Week 4)

**目标**: 实现进程间通信

```
src/nimbus/core/process/
├── ipc.py            # IPCBus implementation
└── signals.py        # Signal handler
```

**任务清单**:
- [ ] 实现 `IPCBusImpl` - 基于 asyncio.Queue
- [ ] 实现 `IPCBusImpl.send()` / `receive()`
- [ ] 实现 `SignalHandler` - 处理 SIGTERM/SIGKILL/SIGPAUSE
- [ ] 集成到 ProcessManager
- [ ] 定义标准消息类型和 payload schema

**验收标准**:
- 父子进程可以通过 IPC 交换结构化消息
- Signal 可以中断运行中的进程
- 无消息丢失

### Phase 5: 资源隔离 (Week 5)

**目标**: 实现文件系统视图和资源配额

```
src/nimbus/core/process/
├── fs_view.py        # VirtualFSView implementation
└── quota.py          # Resource quota enforcement
```

**任务清单**:
- [ ] 实现 `VirtualFSView` - chroot-like 路径隔离
- [ ] 集成 `VirtualFSView` 到工具执行
- [ ] 实现 Token Budget 强制执行
- [ ] 实现 Turn Limit 强制执行
- [ ] 集成 PermissionManager

**验收标准**:
- 子进程无法访问 allowed_paths 之外的文件
- Token 超限时自动终止进程
- 与 PermissionManager 协作正确

### Phase 6: 调度优化 (Week 6)

**目标**: 实现高级调度功能

```
src/nimbus/core/process/
├── scheduler.py      # ProcessScheduler implementation
└── watchdog.py       # TimeoutWatchdog implementation
```

**任务清单**:
- [ ] 实现 `ProcessScheduler` - 进程优先级调度
- [ ] 实现 `TimeoutWatchdog` - 全局超时监控
- [ ] 实现 Orphan Handler - 孤儿进程回收
- [ ] 优化 asyncio 任务调度
- [ ] 性能调优

**验收标准**:
- 无孤儿进程泄漏
- 超时进程被正确回收
- 高优先级进程优先执行

## Code Examples

### Example 1: 基本的 fork + wait 流程

```python
from nimbus.core.process import ProcessManager, ContextFilter

# 获取 ProcessManager (通过 Kernel)
pm: ProcessManager = kernel.process_manager

# Fork 一个 Eye 进程探索代码库
child_pid = pm.fork(
    parent_pid=pm.getpid(),
    role="eye",
    task="Explore the src/nimbus/tools directory and list all Python files",
    context_filter=ContextFilter.minimal(),
    allowed_tools={"Read", "Glob", "Grep"},
    max_token_budget=10000,
)

# 执行子进程
await pm.exec(child_pid)

# 等待完成
result = await pm.wait(child_pid, timeout=60.0)

if result.exit_code == 0:
    print(f"Exploration result: {result.data}")
else:
    print(f"Exploration failed: {result.error}")
```

### Example 2: 多进程并行探索

```python
async def parallel_exploration(pm: ProcessManager, paths: List[str]):
    """并行探索多个目录"""

    # 批量 fork
    child_pids = []
    for path in paths:
        pid = pm.fork(
            parent_pid=pm.getpid(),
            role="eye",
            task=f"Explore {path} and summarize the code structure",
            context_filter=ContextFilter.minimal(),
            allowed_tools={"Read", "Glob", "Grep"},
        )
        child_pids.append(pid)

    # 批量 exec
    for pid in child_pids:
        await pm.exec(pid)

    # 并行等待所有子进程
    results = await asyncio.gather(*[
        pm.wait(pid, timeout=30.0)
        for pid in child_pids
    ])

    return results
```

### Example 3: 使用 IPC 进行父子通信

```python
from nimbus.core.process import IPCBus, IPCMessage, IPCMessageType

async def coder_with_review(pm: ProcessManager, ipc: IPCBus, task: str):
    """Coder + Reviewer 协作模式"""

    # Fork Coder 进程
    coder_pid = pm.fork(
        parent_pid=pm.getpid(),
        role="body",
        task=task,
        allowed_tools={"Read", "Write", "Edit", "Bash", "Glob", "Grep"},
    )

    await pm.exec(coder_pid)
    coder_result = await pm.wait(coder_pid)

    if coder_result.exit_code != 0:
        return coder_result

    # 通过 IPC 传递代码给 Reviewer
    review_msg = IPCMessage(
        id=uuid.uuid4().hex,
        sender_pid=pm.getpid(),
        receiver_pid="",  # Will be set after fork
        type=IPCMessageType.TASK_ASSIGNMENT,
        payload={
            "code_changes": coder_result.data.get("files_modified", []),
            "review_focus": "Check for bugs, security issues, and code style",
        }
    )

    # Fork Reviewer 进程
    reviewer_pid = pm.fork(
        parent_pid=pm.getpid(),
        role="nose",
        task="Review the code changes",
        allowed_tools={"Read", "Glob", "Grep"},
    )

    # 发送 IPC 消息
    review_msg.receiver_pid = reviewer_pid
    await ipc.send(review_msg)

    await pm.exec(reviewer_pid)
    review_result = await pm.wait(reviewer_pid)

    return {
        "coder_result": coder_result.data,
        "review_result": review_result.data,
    }
```

### Example 4: VirtualFSView 使用

```python
from nimbus.core.process import VirtualFSView

# 为 Reviewer 创建只读视图
reviewer_fs = VirtualFSView(
    root_dir="/Users/developer/project",
    allowed_paths=[
        "/Users/developer/project/src/**",
        "/Users/developer/project/tests/**",
    ],
    mode="r"  # Read-only
)

# 验证访问
reviewer_fs.validate_access("/Users/developer/project/src/main.py", "read")  # OK
reviewer_fs.validate_access("/Users/developer/project/.env", "read")  # Denied!
reviewer_fs.validate_access("/Users/developer/project/src/main.py", "write")  # Denied (read-only)
```

## Next Steps

1. **团队评审**: 组织架构评审会议，收集反馈
2. **原型验证**: 实现 Phase 1 的数据结构，验证与现有代码的兼容性
3. **基准测试**: 建立性能基准，确保 Process 抽象不引入显著开销
4. **文档更新**: 更新 CLAUDE.md 和 Architecture 文档
5. **迁移计划**: 制定现有 SubagentExecutor 使用者的迁移指南

---

*本文档由意分身 (Architect) 生成，基于 Linux Process 模型和 Nimbus v0.2.0 冯·诺依曼架构设计。*
