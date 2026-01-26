# Agent OS Architecture Design

> **Version**: 1.0.0-draft
> **Status**: Proposed
> **Author**: Architect (意分身)
> **Date**: 2026-01-26

## Summary

本文档定义 Nimbus Agent Framework 的三层架构模型，类比计算机系统，将现有模块重新组织为清晰的 LLM CPU -> Agent OS -> Application 分层结构。

## Design

### 架构愿景

```
+================================================================+
|                     Layer 2: Application                        |
|  +----------------------------------------------------------+  |
|  |  CodeAgent | ChatAgent | RAGAgent | CustomAgent          |  |
|  |  Skills (synthesize, search, summarize, delegation)      |  |
|  |  Domain Rules | API Routes | TUI/CLI                     |  |
|  +----------------------------------------------------------+  |
+================================================================+
                              |
                              | Agent OS API
                              v
+================================================================+
|                     Layer 1: Agent OS                           |
|  +----------------------------------------------------------+  |
|  |                      Kernel                               |  |
|  |  +------------------+  +------------------+               |  |
|  |  | Memory Manager   |  | Task Scheduler   |               |  |
|  |  | - Pinned         |  | - DAG Executor   |               |  |
|  |  | - Working        |  | - SubagentRT     |               |  |
|  |  | - Episodic       |  | - Coordinator    |               |  |
|  |  | - Semantic       |  | - Cancellation   |               |  |
|  |  +------------------+  +------------------+               |  |
|  |  +------------------+  +------------------+               |  |
|  |  | Permission Mgr   |  | Context Manager  |               |  |
|  |  | - Tool ACL       |  | - Context Stack  |               |  |
|  |  | - Sandbox        |  | - Snapshot       |               |  |
|  |  | - Audit Log      |  | - Inheritance    |               |  |
|  |  +------------------+  +------------------+               |  |
|  +----------------------------------------------------------+  |
|  |                   Process Manager                         |  |
|  |  +--------------------------------------------------+    |  |
|  |  | Subagent Orchestrator                            |    |  |
|  |  | - Spawn/Cancel | Depth Control | Result Collect  |    |  |
|  |  +--------------------------------------------------+    |  |
|  +----------------------------------------------------------+  |
+================================================================+
                              |
                              | LLM CPU API
                              v
+================================================================+
|                     Layer 0: LLM CPU                            |
|  +----------------------------------------------------------+  |
|  |  +------------------+  +------------------+               |  |
|  |  | LLM Client       |  | Tool Executor    |               |  |
|  |  | - Provider Abs   |  | - Registry       |               |  |
|  |  | - complete()     |  | - execute()      |               |  |
|  |  | - complete_with  |  | - Schema Gen     |               |  |
|  |  |   _tools()       |  +------------------+               |  |
|  |  +------------------+                                     |  |
|  |  +------------------+  +------------------+               |  |
|  |  | Message Protocol |  | Agentic Loop     |               |  |
|  |  | - user/assistant |  | - Iteration Ctrl |               |  |
|  |  | - tool_use       |  | - Stop Condition |               |  |
|  |  | - tool_result    |  | - Event Stream   |               |  |
|  |  +------------------+  +------------------+               |  |
|  +----------------------------------------------------------+  |
+================================================================+
```

### 层次职责定义

#### Layer 0: LLM CPU (基础计算单元)

**职责**: 提供 AI 推理的原子操作能力

| 组件 | 类比 | 职责 |
|------|------|------|
| LLM Client | ALU | 执行推理计算 |
| Tool Executor | I/O Controller | 执行外部操作 |
| Message Protocol | Instruction Set | 定义通信格式 |
| Agentic Loop | Microcode | 基本执行循环 |

**关键特性**:
- 无状态 (stateless)
- 可替换 (LLM provider agnostic)
- 确定性接口 (well-defined API)

#### Layer 1: Agent OS (操作系统层)

**职责**: 管理资源、调度任务、提供抽象

| 组件 | 类比 | 职责 |
|------|------|------|
| Memory Manager | Virtual Memory | 多层内存管理 |
| Task Scheduler | Process Scheduler | DAG/并行执行 |
| Permission Manager | Access Control | 权限检查 |
| Context Manager | Process Context | 上下文切换 |
| Subagent Orchestrator | Process Manager | 子进程管理 |

**关键特性**:
- 有状态 (session-based)
- 资源隔离 (sandboxing)
- 公平调度 (concurrency control)

#### Layer 2: Application (应用层)

**职责**: 实现领域特定的 Agent 逻辑

| 组件 | 类比 | 职责 |
|------|------|------|
| CodeAgent | Text Editor | 代码探索/分析 |
| ChatAgent | Chat App | 对话交互 |
| Skills | Libraries | 可复用能力 |
| Domain Rules | Business Logic | 领域规则 |
| Server/CLI | User Interface | 用户接口 |

**关键特性**:
- 领域特定 (domain-specific)
- 可组合 (composable)
- 面向用户 (user-facing)

### 核心组件

#### 1. LLM CPU Core (`nimbus.cpu`)

```python
# 核心接口
class LLMClient(Protocol):
    async def complete(self, prompt: str) -> str: ...
    async def complete_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolSchema],
    ) -> CompletionResult: ...

class ToolExecutor(Protocol):
    def register(self, definition: ToolDefinition, func: Callable): ...
    async def execute(self, name: str, args: Dict) -> ToolResult: ...

class AgenticLoop:
    def __init__(self, client: LLMClient, executor: ToolExecutor): ...
    async def run(self, goal: str) -> AsyncIterator[Event]: ...
```

#### 2. Agent OS Kernel (`nimbus.os`)

```python
# 内存管理
class MemoryManager(Protocol):
    def pin(self, item: PinnedItem) -> bool: ...
    def get_context(self) -> str: ...
    async def compress(self) -> None: ...
    async def checkpoint(self) -> str: ...

# 任务调度
class TaskScheduler(Protocol):
    async def schedule(self, dag: TaskDAG) -> ExecutionResult: ...
    def cancel(self, task_id: str) -> bool: ...
    def get_status(self, task_id: str) -> TaskStatus: ...

# 权限管理
class PermissionManager(Protocol):
    def check(self, operation: str, resource: str) -> bool: ...
    def request(self, permission: Permission) -> PermissionRequest: ...
    async def await_decision(self, request_id: str) -> bool: ...

# 上下文管理
class ContextManager(Protocol):
    def push(self, frame: ContextFrame) -> None: ...
    def pop(self) -> ContextFrame: ...
    def snapshot(self) -> ContextSnapshot: ...

# 子进程管理
class SubagentOrchestrator(Protocol):
    async def spawn(self, config: SubagentConfig) -> str: ...
    async def wait(self, agent_id: str) -> SubagentResult: ...
    async def cancel(self, agent_id: str) -> bool: ...
```

#### 3. Application Framework (`nimbus.app`)

```python
# Agent 基类
class BaseAgent:
    def __init__(self, os: AgentOS): ...
    def register_skill(self, name: str, func: Callable): ...
    async def run(self, input: str) -> AgentResponse: ...

# CodeAgent (具体实现)
class CodeAgent(BaseAgent):
    def __init__(self, os: AgentOS):
        super().__init__(os)
        self._register_code_skills()
        self._register_code_rules()
```

### 数据流

```
User Input
    |
    v
+-------------------+
| Application Layer |
|   CodeAgent       |
+-------------------+
    |
    | run(input)
    v
+-------------------+
| Agent OS          |
|   Scheduler       |<-----> Memory Manager
|   Permission Mgr  |<-----> Context Manager
|   Subagent Orch   |
+-------------------+
    |
    | schedule(dag) / spawn(subagent)
    v
+-------------------+
| LLM CPU           |
|   LLM Client      |
|   Tool Executor   |
|   Agentic Loop    |
+-------------------+
    |
    v
External Resources (LLM APIs, Files, Network)
```

## Decisions

### Decision 1: 采用接口抽象而非物理分包

- **决策**: 通过 Protocol 定义层间接口，保持现有包结构，逐步重构
- **理由**:
  1. 最小化破坏性变更
  2. 支持渐进式迁移
  3. 保持向后兼容
- **备选方案**:
  - A: 直接物理分包 (nimbus.cpu, nimbus.os, nimbus.app) - 风险高
  - B: 仅添加文档标记 - 约束力弱
- **风险**: 接口可能不够清晰，需要严格的 Code Review

### Decision 2: ToolRegistry 下沉到 Layer 0

- **决策**: ToolRegistry 属于 LLM CPU 层，工具定义和执行是原子操作
- **理由**:
  1. 工具执行是基础能力，不依赖上下文
  2. 权限控制由 OS 层的 PermissionManager wrap
  3. 保持 Layer 0 的可替换性
- **备选方案**: 工具放在 OS 层 - 会导致层间耦合
- **风险**: 权限检查需要额外的 wrapper

### Decision 3: SubagentExecutor 上移到 Agent OS

- **决策**: 将 SubagentExecutor 从 tools/ 移动到 core/os/
- **理由**:
  1. 子进程管理是 OS 职责
  2. 需要与 Memory Manager 和 Context Manager 协作
  3. 当前位置破坏了模块职责边界
- **备选方案**: 保持在 tools/ 但添加 OS 层 wrapper
- **风险**: 需要更新 import 路径

### Decision 4: Memory 和 Planner 明确归属 Agent OS

- **决策**: TieredMemoryManager 和 PlannerPipeline 归属 Agent OS Kernel
- **理由**:
  1. 内存管理是典型的 OS 职责
  2. 任务规划是调度器的一部分
  3. 这些组件管理共享资源
- **备选方案**: 分散在各应用中 - 会导致重复实现
- **风险**: 无

### Decision 5: AgenticRunner 属于 Layer 0

- **决策**: AgenticRunner 是 LLM CPU 的执行循环，归属 Layer 0
- **理由**:
  1. 它是基础的 tool-use 循环
  2. 不依赖于 session 或 memory
  3. 是构建更高层调度器的基础
- **备选方案**: 归属 OS 层 - 会使 Layer 0 过于简单
- **风险**: 需要确保与 OS 层的清晰边界

## Tradeoffs

### 1. 兼容性 vs 清晰度

**选择**: 优先兼容性，通过接口抽象实现分层

- 代价: 短期内层次边界可能模糊
- 收益: 不破坏现有功能，支持渐进迁移
- 理由: 项目处于 Alpha 阶段，稳定性优先

### 2. 性能 vs 抽象

**选择**: 保持当前性能特性，接口抽象不引入额外开销

- 代价: 某些优化可能跨层
- 收益: 保持现有性能
- 理由: Agent 性能瓶颈在 LLM 调用，不在框架

### 3. 灵活性 vs 简单性

**选择**: 为 OS 层提供完整的抽象，应用层保持简单

- 代价: OS 层接口较多
- 收益: 应用层开发者体验好
- 理由: 大多数用户只关心应用层

## Constraints

### 技术约束
- Python 3.10+ 类型系统限制
- asyncio 并发模型
- FastAPI 服务器框架
- 现有 LLM Provider 接口

### 业务约束
- 必须兼容 OpenCode TUI
- 必须支持 AI SDK v6 协议
- 必须支持 ACP 协议

### 设计约束
- 每层只能调用下层接口
- 禁止跨层依赖
- 接口必须使用 Protocol 定义

## Risks

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 接口定义不完整导致跨层调用 | 中 | 中 | 严格 Code Review，自动化检测 |
| 重构过程中破坏现有功能 | 中 | 高 | 分阶段重构，充分测试 |
| 性能退化 | 低 | 中 | 性能基准测试 |
| 开发者学习曲线陡峭 | 中 | 低 | 完善文档，渐进式 API |

## Evidence

### 来源

| 文件 | 行号 | 发现 |
|------|------|------|
| `src/nimbus/core/agent.py` | 1-984 | CodeAgent 混合三层职责 |
| `src/nimbus/core/memory.py` | 90-588 | TieredMemoryManager 是典型 OS 组件 |
| `src/nimbus/core/runtime/executor.py` | 49-756 | AsyncRuntime 是调度器 |
| `src/nimbus/tools/subagent.py` | 297-874 | SubagentExecutor 应属于 OS 层 |
| `src/nimbus/tools/base.py` | 1-200 | ToolRegistry 是基础组件 |
| `src/nimbus/core/planner/pipeline.py` | 1-551 | PlannerPipeline 是调度器一部分 |
| `src/nimbus/core/runtime/agentic.py` | 1-200 | AgenticRunner 是基础循环 |

### 假设

1. **假设**: 当前 LLM Provider 接口稳定，短期内不会大改
2. **假设**: 子进程管理需求会增加（多 Agent 协作）
3. **假设**: 内存管理会扩展（支持更多层或压缩策略）

## Next Steps

### Phase 1: 接口定义 (Week 1-2)

1. 定义 Layer 0 Protocol (`nimbus.cpu.protocols`)
   - LLMClient
   - ToolExecutor
   - MessageProtocol

2. 定义 Layer 1 Protocol (`nimbus.os.protocols`)
   - MemoryManager
   - TaskScheduler
   - PermissionManager
   - ContextManager
   - SubagentOrchestrator

3. 定义 Layer 2 基类 (`nimbus.app.base`)
   - BaseAgent
   - BaseSkill

### Phase 2: 组件标记 (Week 2-3)

1. 为现有模块添加 `__layer__` 标记
2. 添加自动化检测跨层依赖的 lint 规则
3. 更新 CLAUDE.md 和 README

### Phase 3: 接口实现 (Week 3-5)

1. 实现 Layer 0 wrapper
   - `nimbus.cpu.client.LLMClientAdapter`
   - `nimbus.cpu.executor.ToolExecutorAdapter`

2. 实现 Layer 1 wrapper
   - `nimbus.os.memory.MemoryManagerAdapter`
   - `nimbus.os.scheduler.TaskSchedulerAdapter`

3. 实现 Layer 2 重构
   - `nimbus.app.code_agent.CodeAgent` (继承 BaseAgent)

### Phase 4: 物理分离 (可选, Week 6+)

1. 逐步移动文件到新目录结构
2. 更新 import 路径
3. 发布 breaking change 版本

## 附录: 模块归属表

### Layer 0: LLM CPU

| 当前路径 | 建议路径 | 说明 |
|----------|----------|------|
| `nimbus/llm/` | `nimbus/cpu/llm/` | LLM Client 实现 |
| `nimbus/tools/base.py` | `nimbus/cpu/tools/base.py` | 工具定义和注册 |
| `nimbus/core/runtime/agentic.py` | `nimbus/cpu/loop/agentic.py` | Agentic Loop |
| `nimbus/tools/*.py` (具体工具) | `nimbus/cpu/tools/builtin/` | 内置工具实现 |

### Layer 1: Agent OS

| 当前路径 | 建议路径 | 说明 |
|----------|----------|------|
| `nimbus/core/memory.py` | `nimbus/os/memory/` | 内存管理 |
| `nimbus/core/runtime/executor.py` | `nimbus/os/scheduler/` | 任务调度 |
| `nimbus/core/planner/` | `nimbus/os/planner/` | 规划器 |
| `nimbus/core/permission.py` | `nimbus/os/permission/` | 权限管理 |
| `nimbus/core/context.py` | `nimbus/os/context/` | 上下文管理 |
| `nimbus/tools/subagent.py` | `nimbus/os/subagent/` | 子进程管理 |
| `nimbus/storage/` | `nimbus/os/storage/` | 持久化 |
| `nimbus/core/checkpoint.py` | `nimbus/os/checkpoint/` | 检查点 |

### Layer 2: Application

| 当前路径 | 建议路径 | 说明 |
|----------|----------|------|
| `nimbus/core/agent.py` | `nimbus/app/agents/code.py` | CodeAgent |
| `nimbus/skills/` | `nimbus/app/skills/` | 技能 |
| `nimbus/server/` | `nimbus/app/server/` | HTTP API |
| `nimbus/cli/` | `nimbus/app/cli/` | CLI |
| `nimbus/acp/` | `nimbus/app/acp/` | ACP 协议 |

## 附录: 接口示例

### Layer 0 -> Layer 1 接口

```python
# nimbus/os/interfaces.py

class AgentOS:
    """Agent OS 的统一入口"""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        config: OSConfig,
    ):
        self.memory = MemoryManagerAdapter(config.memory)
        self.scheduler = TaskSchedulerAdapter(llm_client, tool_registry)
        self.permission = PermissionManagerAdapter()
        self.context = ContextManagerAdapter()
        self.subagent = SubagentOrchestratorAdapter()

    async def schedule_task(
        self,
        goal: str,
        context: Optional[str] = None,
    ) -> ExecutionResult:
        """调度任务执行"""
        ctx = context or self.memory.get_context()
        dag = await self.scheduler.plan(goal, ctx)
        return await self.scheduler.execute(dag)

    async def spawn_subagent(
        self,
        config: SubagentConfig,
    ) -> SubagentHandle:
        """创建子 Agent"""
        ctx = self.context.snapshot()
        return await self.subagent.spawn(config, ctx)
```

### Layer 1 -> Layer 2 接口

```python
# nimbus/app/base.py

class BaseAgent:
    """应用层 Agent 基类"""

    def __init__(self, os: AgentOS):
        self._os = os
        self._skills: Dict[str, SkillFunc] = {}

    def register_skill(self, name: str, func: SkillFunc) -> None:
        self._skills[name] = func
        self._os.scheduler.register_skill(name, func)

    async def run(self, input: str) -> AgentResponse:
        """执行用户输入"""
        # 更新内存
        self._os.memory.add_turn("user", input)

        # 调度执行
        result = await self._os.schedule_task(input)

        # 记录响应
        self._os.memory.add_turn("assistant", result.text)

        return AgentResponse(text=result.text)
```

---

*本文档由意分身 (Architect) 生成，基于对 Nimbus v0.2.0 代码库的分析。*
