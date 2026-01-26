# Agent OS: Von Neumann Architecture Design

> **Version**: 2.0.0-draft
> **Status**: Proposed
> **Author**: Architect (意分身)
> **Date**: 2026-01-26
> **Based on**: Von Neumann Architecture Metaphor + Previous Three-Layer Design

## Summary

本文档综合**冯·诺依曼计算机架构隐喻**与 Nimbus 现有的三层架构设计，提出一个更精确的 Agent OS 模型。核心洞察：**Agent 的运行本质上是一个计算过程**，我们可以用计算机体系结构的概念来精确定义每个组件的职责边界。

## Design

### 架构愿景 (Von Neumann Metaphor)

```
+===========================================================================+
|                          LAYER 2: APPLICATION                              |
|  +---------------------------------------------------------------------+  |
|  |                      User Space Programs                             |  |
|  |  +---------------+  +---------------+  +---------------+            |  |
|  |  | code_agent.py |  | chat_agent.py |  | custom.yaml  |            |  |
|  |  | (Process Def) |  | (Process Def) |  | (Config)     |            |  |
|  |  +---------------+  +---------------+  +---------------+            |  |
|  |                                                                      |  |
|  |  SOP Scripts: if complexity > 5 then spawn(Architect) else exec()   |  |
|  +---------------------------------------------------------------------+  |
+===========================================================================+
                                    |
                                    | System Calls (OS API)
                                    v
+===========================================================================+
|                          LAYER 1: AGENT OS (Kernel)                        |
|  +---------------------------------------------------------------------+  |
|  |                          vCPU (Virtual Processor)                    |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  |  | Control Unit     |  | MMU              |  | Interrupt Handler|   |  |
|  |  | (AgenticRunner)  |  | (TieredMemory)   |  | (Error Recovery) |   |  |
|  |  | Think->Act->Obs  |  | Registers->L1->  |  | Retry/Failover   |   |  |
|  |  |                  |  | L2->Swap         |  | Self-Healing     |   |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  +---------------------------------------------------------------------+  |
|  |                          Kernel Services                             |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  |  | Scheduler        |  | Process Manager  |  | File System      |   |  |
|  |  | (DAGOrchestrator)|  | (SubagentOrch)   |  | (Checkpoint/     |   |  |
|  |  | Task Queue       |  | Spawn/Cancel     |  |  SharedState)    |   |  |
|  |  | Priority/IPC     |  | Depth Control    |  | Artifacts        |   |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  |                                                                      |  |
|  |  +------------------+  +------------------+                         |  |
|  |  | Permission Mgr   |  | Context Manager  |                         |  |
|  |  | (ACL/Sandbox)    |  | (ContextStack)   |                         |  |
|  |  | Tool Whitelist   |  | Push/Pop/Inherit |                         |  |
|  |  +------------------+  +------------------+                         |  |
|  +---------------------------------------------------------------------+  |
+===========================================================================+
                                    |
                                    | Hardware Abstraction Layer
                                    v
+===========================================================================+
|                       LAYER 0: INFRASTRUCTURE (Hardware)                   |
|  +---------------------------------------------------------------------+  |
|  |                              ALU                                     |  |
|  |  +------------------+  +------------------+                         |  |
|  |  | LLM Adapter      |  | Tool Interfaces  |                         |  |
|  |  | (Provider Abs)   |  | (ISA Definition) |                         |  |
|  |  | Input: Tokens    |  | Only Interface   |                         |  |
|  |  | Output: Tokens   |  | No Retry Logic   |                         |  |
|  |  +------------------+  +------------------+                         |  |
|  +---------------------------------------------------------------------+  |
|  |                           Registers                                  |  |
|  |  +------------------------------------------------------------------+|  |
|  |  |              Current Context Window (Prompt)                     ||  |
|  |  |  System Instruction | Pinned | Working | Recent History          ||  |
|  |  |  (Highest speed, most expensive storage per token)               ||  |
|  |  +------------------------------------------------------------------+|  |
|  +---------------------------------------------------------------------+  |
+===========================================================================+
```

### 对比分析：两个设计的综合

#### 概念一致性

| 概念 | 之前的设计 | 冯·诺依曼隐喻 | 综合后 |
|------|-----------|--------------|--------|
| LLM Client | ALU | ALU | **ALU** - 计算核心 |
| Tool Executor | I/O Controller | ISA (指令集) | **ISA** - 只定义接口，不含重试 |
| Message Protocol | Instruction Set | Registers | **Registers** - 当前 Prompt |
| Agentic Loop | Microcode | Control Unit | **Control Unit** - Think/Act/Observe |
| Memory Manager | Virtual Memory | MMU | **MMU** - 多级存储管理 |
| Task Scheduler | Process Scheduler | Scheduler | **Scheduler** - DAG 编排 |
| Error Recovery | (未明确) | Interrupt Handler | **Interrupt Handler** - 新增 |

#### 冯·诺依曼隐喻的新洞察

1. **Registers = Current Prompt**
   - 最高速、最昂贵的存储
   - 每次 LLM 调用的实际输入
   - 之前设计中没有明确这一层

2. **Interrupt Handler = Error Recovery**
   - 自愈能力是 OS 的核心职责
   - 之前分散在各处，现在统一归属 vCPU

3. **ISA 只定义接口**
   - 工具的重试逻辑属于 OS 层
   - Layer 0 应该是"干净的硬件抽象"

4. **Application 应该轻量化**
   - CodeAgent 应该是 Config + 轻量 Setup
   - 不应包含复杂的 Class 定义

#### 之前设计中更完善的部分

1. **ContextManager / ContextStack**
   - 支持 push/pop/inherit
   - 冯·诺依曼隐喻中没有直接对应

2. **分层 Protocol 定义**
   - 明确的接口抽象
   - 支持渐进式重构

3. **详细的模块归属表**
   - 明确每个文件的归属层次

## Core Concepts (核心概念详解)

### 1. Registers (寄存器) - Current Context Window

```
+------------------------------------------------------------------+
|                    Context Window (16K tokens)                    |
+------------------------------------------------------------------+
| System Instruction | Pinned Context | Working Memory | History   |
|     (1K)          |     (1K)       |     (4K)       |   (8K)    |
+------------------------------------------------------------------+
       ^                   ^                ^               ^
       |                   |                |               |
    Immutable          High Priority    Task State    Conversation
```

**关键特性**:
- 这是每次 LLM 调用的**实际输入**
- Token 预算管理由 MMU 负责
- "Registers 满了"意味着需要 MMU 介入压缩

### 2. vCPU (Virtual Processor) - Atomic Agent Runtime

```python
# vCPU = Control Unit + MMU + Interrupt Handler
class vCPU:
    """单个 Agent 线程的执行环境"""

    def __init__(self):
        self.control_unit = AgenticRunner()      # Think -> Act -> Observe
        self.mmu = TieredMemoryManager()         # Context 管理
        self.interrupt_handler = ErrorRecovery() # 中断/错误处理

    async def execute(self, goal: str) -> str:
        """执行一个目标"""
        # Control Unit: Think -> Act -> Observe 循环
        while not done:
            # 1. MMU: 组装 Registers (Context Window)
            context = self.mmu.assemble_context()

            # 2. Control Unit: Think (LLM 推理)
            action = await self.control_unit.think(goal, context)

            # 3. Control Unit: Act (执行工具)
            try:
                result = await self.control_unit.act(action)
            except Exception as e:
                # 4. Interrupt Handler: 错误处理
                result = await self.interrupt_handler.handle(e)

            # 5. Control Unit: Observe (更新状态)
            self.control_unit.observe(result)

            # 6. MMU: 更新 Memory
            await self.mmu.update(action, result)

        return final_response
```

**Control Unit (控制器)** - `AgenticRunner`:
- 实现 Think -> Act -> Observe 循环
- 管理 Message History (Stack)
- 调用 LLM API
- 选择和执行工具

**MMU (内存管理单元)** - `TieredMemoryManager`:
- 管理四层存储：Pinned / Working / Episodic / Semantic
- 决定什么在 Registers、什么 Swap、什么丢弃
- 自动压缩 (LRU / LLM Summarization)
- 组装 Context Window

**Interrupt Handler (中断处理)** - `ErrorRecovery`:
- Tool 执行失败处理
- Retry with backoff
- Failover 策略
- Self-healing (on_failure handler)

### 3. Scheduler (调度器) - DAG Orchestrator

```
+------------------+    +------------------+    +------------------+
|   Task Queue     |    | DAG Executor     |    | IPC (Results)    |
|   (Ready Queue)  |--->| (Parallel Exec)  |--->| (Dependency Inj) |
+------------------+    +------------------+    +------------------+
         ^                      |                       |
         |                      v                       v
+------------------+    +------------------+    +------------------+
| Priority Rules   |    | Concurrency Ctrl |    | Checkpointing    |
| (Cost-based)     |    | (Semaphore)      |    | (Durability)     |
+------------------+    +------------------+    +------------------+
```

**职责**:
- 多线程（多 Agent）调度
- DAG 依赖解析
- 并发控制 (max_concurrent)
- 进程间通信 (Result Injection)
- 检查点持久化

### 4. File System (文件系统) - Shared State

```
+------------------------------------------------------------------+
|                        File System                                |
+------------------------------------------------------------------+
| /workspace/          | Project files (read/write)                 |
| /checkpoints/        | DAG state snapshots                        |
| /artifacts/          | Generated outputs                          |
| /sessions/           | Conversation persistence                   |
+------------------------------------------------------------------+
```

**关键洞察**:
- 所有线程（Agent）共享的"硬盘"
- Checkpoint = 进程状态快照
- Artifacts = 任务产出物

## Module Mapping (模块映射表)

### Layer 0: Infrastructure (Hardware)

| Nimbus 模块 | 冯·诺依曼对应 | 文件路径 | 说明 |
|-------------|--------------|----------|------|
| LLM Adapter | **ALU** | `nimbus/llm/` | 纯算力，Input/Output Tokens |
| ToolRegistry (定义) | **ISA** | `tools/base.py` | 只定义接口，不含重试逻辑 |
| ToolDefinition | **Instruction Format** | `tools/base.py` | 指令格式定义 |
| Current Prompt | **Registers** | (Runtime concept) | 每次 LLM 调用的实际输入 |

**Layer 0 职责边界**:
- 不包含业务逻辑
- 不包含重试逻辑
- 不包含状态管理
- 只提供算力和接口定义

### Layer 1: Agent OS (Kernel)

| Nimbus 模块 | 冯·诺依曼对应 | 文件路径 | 说明 |
|-------------|--------------|----------|------|
| AgenticRunner | **Control Unit** | `core/runtime/agentic.py` | Think->Act->Observe |
| TieredMemoryManager | **MMU** | `core/memory.py` | 四层存储管理 |
| ToolRetryMiddleware | **Interrupt Handler** | `tools/middleware.py` | 重试/错误恢复 |
| AsyncRuntime | **Scheduler** | `core/runtime/executor.py` | DAG 并行调度 |
| SubagentExecutor | **Process Manager** | `tools/subagent.py` | 子进程管理 |
| CheckpointManager | **File System (部分)** | `utils/checkpoint.py` | 状态持久化 |
| PermissionManager | **ACL** | `core/permission.py` | 权限控制 |
| ContextStack | **Context Switch** | `core/context.py` | 上下文切换 |
| PlannerPipeline | **Scheduler (规划)** | `core/planner/pipeline.py` | 任务规划 |

**vCPU 组成**:
```
vCPU = {
    Control Unit:      AgenticRunner (core/runtime/agentic.py)
    MMU:              TieredMemoryManager (core/memory.py)
    Interrupt Handler: ToolRetryMiddleware + ErrorRecovery
}
```

### Layer 2: Application (User Space)

| Nimbus 模块 | 冯·诺依曼对应 | 文件路径 | 说明 |
|-------------|--------------|----------|------|
| CodeAgent | **Process Definition** | `core/agent.py` | 应该轻量化 |
| SubagentConfig | **Thread Definition** | `data/agents/*.yaml` | 分身定义 |
| Skills | **Libraries/DLLs** | `skills/*.py` | 可复用能力 |
| Server/CLI | **Shell/GUI** | `server/`, `cli/` | 用户接口 |
| Domain Rules | **SOP Scripts** | `data/rules/*.yaml` | 领域规则 |

**轻量化目标**:
```python
# 理想的 Application 层代码
# code_agent.py - 应该只是配置 + 轻量 setup

architect = os.create_thread(role="Architect", tools=[read_only_fs])
coder = os.create_thread(role="Coder", tools=[full_fs, python_repl])
reviewer = os.create_thread(role="Reviewer", tools=[lint, grep])

def main(user_input):
    complexity = os.eval_complexity(user_input)
    if complexity > 5:
        plan = os.exec(architect, user_input)
        code = os.exec(coder, plan)
    else:
        code = os.exec(coder, user_input)
    return os.exec(reviewer, code)
```

## Interface Definitions (接口定义)

### Layer 0 Protocols

```python
# nimbus/infrastructure/protocols.py

class ALU(Protocol):
    """算术逻辑单元 - LLM 推理"""
    async def compute(
        self,
        registers: List[Message],  # Context Window
        instruction_set: List[ToolSchema],  # Available Tools
    ) -> CompletionResult:
        """纯计算，无副作用，无重试"""
        ...

class ISA(Protocol):
    """指令集架构 - 工具接口定义"""
    def get_instruction(self, name: str) -> ToolDefinition: ...
    def list_instructions(self) -> List[str]: ...
    # 注意：不包含 execute，执行由 OS 层负责
```

### Layer 1 Protocols (vCPU)

```python
# nimbus/os/protocols.py

class ControlUnit(Protocol):
    """控制单元 - Agentic Loop"""
    async def step(
        self,
        goal: str,
        context: str,
    ) -> Action:
        """单步执行：Think -> Act"""
        ...

    def observe(self, result: ToolResult) -> None:
        """观察工具结果"""
        ...

class MMU(Protocol):
    """内存管理单元"""
    def assemble_context(self) -> str:
        """组装 Registers (Context Window)"""
        ...

    async def compress(self) -> None:
        """压缩内存"""
        ...

    def allocate(self, tier: MemoryTier, data: Any) -> bool:
        """分配内存"""
        ...

class InterruptHandler(Protocol):
    """中断处理器"""
    async def handle(
        self,
        error: Exception,
        context: ErrorContext,
    ) -> Recovery:
        """处理中断/错误"""
        ...
```

### Layer 1 Protocols (Kernel Services)

```python
# nimbus/os/kernel.py

class Scheduler(Protocol):
    """调度器"""
    async def schedule(self, dag: TaskDAG) -> ExecutionResult: ...
    def get_ready_tasks(self) -> List[TaskNode]: ...
    def cancel(self, task_id: str) -> bool: ...

class ProcessManager(Protocol):
    """进程管理器"""
    async def spawn(self, config: ThreadConfig) -> str: ...
    async def wait(self, pid: str) -> ProcessResult: ...
    async def cancel(self, pid: str) -> bool: ...
    def list_processes(self) -> List[ProcessInfo]: ...

class FileSystem(Protocol):
    """文件系统"""
    async def checkpoint(self, data: Any) -> str: ...
    async def restore(self, checkpoint_id: str) -> Any: ...
    def get_workspace(self) -> Path: ...

class Kernel:
    """Agent OS 内核"""
    def __init__(
        self,
        alu: ALU,
        isa: ISA,
    ):
        # vCPU
        self.vcpu = vCPU(alu, isa)

        # Kernel Services
        self.scheduler = Scheduler()
        self.process_manager = ProcessManager()
        self.file_system = FileSystem()
        self.permission_manager = PermissionManager()
        self.context_manager = ContextManager()

    def create_thread(
        self,
        role: str,
        tools: List[str],
    ) -> ThreadHandle:
        """创建线程（子 Agent）"""
        ...

    async def exec(
        self,
        thread: ThreadHandle,
        goal: str,
    ) -> str:
        """执行线程"""
        ...
```

### Layer 2 Interface

```python
# nimbus/app/base.py

class BaseAgent:
    """应用层 Agent 基类 - 应该很轻"""

    def __init__(self, kernel: Kernel, config: AgentConfig):
        self._kernel = kernel
        self._config = config
        self._setup_threads()

    def _setup_threads(self):
        """设置线程定义 - 由子类实现"""
        pass

    async def run(self, user_input: str) -> str:
        """主入口"""
        return await self._kernel.exec(self._main_thread, user_input)

# 具体 Agent 应该很轻
class CodeAgent(BaseAgent):
    def _setup_threads(self):
        self.architect = self._kernel.create_thread("Architect", ["Read", "Glob", "Grep"])
        self.coder = self._kernel.create_thread("Coder", ["Read", "Write", "Edit", "Bash"])
        self.reviewer = self._kernel.create_thread("Reviewer", ["Read", "Grep"])
```

## Decisions (设计决策)

### Decision 1: Registers = Current Prompt (新增概念)

- **决策**: 将"当前 Prompt"明确定义为 Registers
- **理由**:
  1. 这是 LLM 调用的实际输入
  2. 是最高速、最昂贵的存储
  3. 帮助理解为什么需要 MMU（内存压缩）
- **影响**: 需要更新文档和概念模型

### Decision 2: 重试逻辑上移到 OS 层

- **决策**: Tool 的重试逻辑从 Layer 0 移到 Layer 1 (Interrupt Handler)
- **理由**:
  1. Layer 0 应该是"干净的硬件抽象"
  2. 重试策略是 OS 级别的决策
  3. 便于统一管理和配置
- **当前状态**: `ToolRetryMiddleware` 已在 `tools/middleware.py`，符合预期

### Decision 3: AgenticRunner 是 Control Unit

- **决策**: `AgenticRunner` 归属 Layer 1 (vCPU 的 Control Unit)
- **理由**:
  1. Think->Act->Observe 是 OS 级别的执行循环
  2. 不是纯粹的"硬件"（ALU 只负责计算）
  3. 需要与 MMU 和 Interrupt Handler 协作
- **与之前设计的差异**: 之前将 Agentic Loop 放在 Layer 0，现在上移

### Decision 4: Application 轻量化目标

- **决策**: `CodeAgent` 应该逐步演进为 Config + 轻量 Setup
- **理由**:
  1. 复杂逻辑应该在 OS 层
  2. 便于用户自定义 Agent
  3. 符合"OS 一行不改"的目标
- **当前状态**: `CodeAgent` 还比较重（984行），需要重构
- **目标**: CodeAgent 代码量 < 100 行

### Decision 5: Kernel 类作为 OS 统一入口

- **决策**: 引入 `Kernel` 类作为 Layer 1 的统一入口
- **理由**:
  1. 提供清晰的 OS API
  2. 管理所有 Kernel Services
  3. 实现资源隔离和生命周期管理
- **实现方式**: 新增 `nimbus/os/kernel.py`

## Tradeoffs (权衡取舍)

### 1. 概念纯净性 vs 实现便利性

**选择**: 在概念上保持冯·诺依曼架构的纯净性，但在实现上允许适度妥协

- **概念层面**: 严格区分 ALU/Registers/MMU/Control Unit
- **实现层面**: 可以有跨层的便捷方法
- **理由**: 架构指导思想，不是教条

### 2. 渐进式重构 vs 大规模重写

**选择**: 渐进式重构

- 代价: 短期内层次边界可能模糊
- 收益: 不破坏现有功能
- 策略: Protocol 先行，物理分离后做

### 3. OS 通用性 vs 领域优化

**选择**: OS 层保持通用，领域逻辑放 Application

- **目标**: "不管是 Code Agent 还是写小说的 Agent，OS 一行不改"
- **实现**: 通过 Config 和 Thread Definition 实现领域定制

## Risks (风险分析)

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 重构过程中破坏现有功能 | 中 | 高 | 分阶段，充分测试 |
| 概念映射不完美导致混淆 | 中 | 中 | 文档清晰，团队培训 |
| Application 轻量化困难 | 高 | 中 | 先提取接口，再迁移逻辑 |
| Kernel 类引入新的耦合 | 低 | 中 | 使用依赖注入 |

## Evidence (证据来源)

### 代码分析

| 文件 | 行号 | 发现 |
|------|------|------|
| `core/runtime/agentic.py` | 163-200 | AgenticRunner 实现 Think->Act->Observe |
| `core/memory.py` | 90-588 | TieredMemoryManager 是 MMU 的完整实现 |
| `core/runtime/executor.py` | 49-756 | AsyncRuntime 是 Scheduler |
| `tools/middleware.py` | - | ToolRetryMiddleware 是 Interrupt Handler |
| `tools/subagent.py` | - | SubagentExecutor 是 Process Manager |
| `core/agent.py` | 1-984 | CodeAgent 目前较重，需要轻量化 |
| `tools/base.py` | 346-558 | ToolRegistry 是 ISA 实现 |

### 假设

1. **假设**: LLM Provider 接口稳定
2. **假设**: 四层内存架构满足大多数场景
3. **假设**: 用户愿意接受 Config-driven 的 Agent 定义方式

## Refactoring Roadmap (重构路线图)

### Phase 1: 概念对齐 (Week 1)

1. **更新文档**
   - 在代码注释中添加冯·诺依曼映射
   - 更新 CLAUDE.md

2. **标记模块归属**
   ```python
   # 在每个模块顶部添加
   __layer__ = 0  # Infrastructure
   __role__ = "ALU"  # Von Neumann role
   ```

### Phase 2: 接口抽象 (Week 2-3)

1. **定义 Layer 0 Protocols**
   - `ALU`: LLM 推理接口
   - `ISA`: 工具定义接口

2. **定义 Layer 1 Protocols**
   - `ControlUnit`: Agentic Loop
   - `MMU`: Memory Manager
   - `InterruptHandler`: Error Recovery
   - `Scheduler`, `ProcessManager`, `FileSystem`

3. **定义 Kernel 类**
   - 统一 OS API
   - 管理所有服务

### Phase 3: 剥离硬件层 (Week 3-4)

1. **提纯 LLM Adapter**
   - 移除业务逻辑
   - 移除重试逻辑
   - 只保留 `compute()` 方法

2. **提纯 ToolRegistry**
   - 只保留定义和 Schema
   - 执行逻辑移到 OS 层

### Phase 4: 提纯 vCPU (Week 4-5)

1. **重构 AgenticRunner**
   - 明确为 Control Unit
   - 与 MMU 解耦
   - 添加 Interrupt Handler 集成

2. **统一 Error Recovery**
   - 合并分散的错误处理
   - 实现统一的 Interrupt Handler

### Phase 5: 轻量化 Application (Week 5-6)

1. **重构 CodeAgent**
   - 提取核心逻辑到 Kernel
   - CodeAgent 变成 Config + Setup
   - 目标: < 100 行

2. **Config-driven Agent**
   - 支持 YAML 定义 Agent
   - 无需写 Python 代码

### Phase 6: 验证与文档 (Week 6+)

1. **创建新 Agent 验证**
   - 用 Config 定义一个 WriterAgent
   - 确保 "OS 一行不改"

2. **完善文档**
   - 架构图
   - API 文档
   - 迁移指南

## Code Examples (代码示例)

### 理想的 Application 定义 (目标状态)

```yaml
# nimbus/data/agents/code_agent.yaml
name: code_agent
version: "1.0"

threads:
  architect:
    role: "Architect"
    system_prompt: "You are a software architect..."
    allowed_tools: [Read, Glob, Grep]
    permissions:
      file_system: read_only

  coder:
    role: "Coder"
    system_prompt: "You are a Python developer..."
    allowed_tools: [Read, Write, Edit, Bash, Glob, Grep]
    permissions:
      file_system: read_write
      command: allowed

  reviewer:
    role: "Reviewer"
    system_prompt: "You are a code reviewer..."
    allowed_tools: [Read, Grep]
    permissions:
      file_system: read_only

sop:
  main:
    - eval_complexity: user_input
    - if: complexity > 5
      then:
        - exec: architect
          input: user_input
          output: plan
        - exec: coder
          input: plan
          output: code
      else:
        - exec: coder
          input: user_input
          output: code
    - exec: reviewer
      input: code
      output: final
```

```python
# nimbus/app/code_agent.py (目标: < 100 行)
from nimbus.os import Kernel
from nimbus.app import BaseAgent

class CodeAgent(BaseAgent):
    """Code Agent - 使用配置定义"""
    config_path = "nimbus/data/agents/code_agent.yaml"

    async def run(self, user_input: str) -> str:
        # SOP 由 Kernel 根据配置执行
        return await self._kernel.exec_sop("main", user_input)
```

### Kernel 使用示例

```python
from nimbus.os import Kernel
from nimbus.infrastructure import ClaudeAdapter, DefaultISA

# 创建 Kernel
kernel = Kernel(
    alu=ClaudeAdapter(api_key="..."),
    isa=DefaultISA(),
)

# 创建线程
architect = kernel.create_thread(
    role="Architect",
    tools=["Read", "Glob", "Grep"],
)

# 执行
result = await kernel.exec(architect, "Design a user auth system")
```

---

*本文档由意分身 (Architect) 生成，基于冯·诺依曼架构隐喻和 Nimbus v0.2.0 代码库分析。*
