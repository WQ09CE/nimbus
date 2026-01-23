# Agent Framework 技术设计文档

> **版本**: v0.2  
> **作者**: Dennis  
> **日期**: 2025-01-21  
> **状态**: Draft - Review Incorporated

---

## 变更记录

| 版本 | 日期 | 变更内容 | Reviewer |
|------|------|---------|----------|
| v0.1 | 2025-01-21 | 初稿 | - |
| v0.2 | 2025-01-21 | 整合 Gemini Review 反馈 | Gemini |

### v0.2 主要变更（基于 Gemini Review）

1. **新增 3.3.5 DAG 持久化**
   - SurrealDB 存储 DAG 和 Task 状态
   - 支持服务重启后断点续跑

2. **新增 3.2.8 Re-planning 机制**
   - 支持动态重规划
   - Checkpoint 自动标记
   - 条件边支持

3. **新增 3.1.6 Pinned Context**
   - 置顶上下文永不压缩
   - 解决 Notebook 场景文件元信息丢失问题

4. **扩展 AgentResponse**
   - 新增 `artifacts` 字段支持多模态输出
   - 新增 `suggestions` 字段支持后续操作建议

5. **更新实现计划**
   - 工时从 12.5d 调整为 17.5d
   - 新增 MVP 路径建议（10-12d）

---

## 1. 项目概述

### 1.1 背景

当前主流 Agent 框架（LangChain、AutoGen）存在以下问题：
- 过度封装，难以针对特定场景优化
- Memory 管理粗糙，长对话易导致上下文爆炸
- 缺乏健壮的任务调度和容错机制

本项目目标是构建一个**轻量级、可特化、生产可用**的 Agent 基础框架。

### 1.2 设计目标

| 目标 | 描述 | 优先级 |
|------|------|--------|
| **可扩展** | 通过 Skill 机制支持快速特化 | P0 |
| **健壮性** | 任务失败不影响整体，支持降级 | P0 |
| **高效** | 支持并行执行，避免串行阻塞 | P1 |
| **可观测** | 完善的日志和状态追踪 | P1 |
| **易集成** | 可与现有 Wukong 框架无缝对接 | P2 |

### 1.3 非目标（Out of Scope）

- 不实现 GUI / Web 界面
- 不实现多 Agent 协作协议（后续版本考虑）
- 不实现自动 Skill 发现和编排

---

## 2. 整体架构

### 2.1 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        BaseAgent                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    User Interface                         │  │
│  │                 run(user_input) -> str                    │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                  │
│              ┌───────────────┼───────────────┐                  │
│              ▼               ▼               ▼                  │
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────┐      │
│  │    Memory      │ │    Planner     │ │    Runtime     │      │
│  │    Manager     │ │    (DAG)       │ │    (Async)     │      │
│  └────────────────┘ └────────────────┘ └────────────────┘      │
│         │                   │                   │               │
│         ▼                   ▼                   ▼               │
│  ┌────────────────────────────────────────────────────────┐    │
│  │                   Skill Registry                        │    │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐       │    │
│  │  │  chat   │ │ search  │ │ analyze │ │  ...    │       │    │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘       │    │
│  └────────────────────────────────────────────────────────┘    │
│                              │                                  │
│              ┌───────────────┼───────────────┐                  │
│              ▼               ▼               ▼                  │
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────┐      │
│  │   LLM Client   │ │   Vector DB    │ │   Checkpoint   │      │
│  │   (Anthropic)  │ │   (SurrealDB)  │ │   (File/Redis) │      │
│  └────────────────┘ └────────────────┘ └────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件职责

| 组件 | 职责 | 类比 |
|------|------|------|
| **MemoryManager** | 管理对话历史、任务状态、知识检索 | 海马体 |
| **Planner** | 任务拆解、依赖分析、生成执行计划 | 前额叶 |
| **Runtime** | 并行执行、超时控制、错误处理 | 小脑 |
| **SkillRegistry** | Skill 注册、发现、调用封装 | 工具箱 |

### 2.3 数据流

```
User Input
    │
    ▼
┌─────────────────┐
│ Memory.load()   │ ──► 获取相关上下文
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Planner.plan()  │ ──► 生成 TaskDAG
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Runtime.execute │ ──► 并行执行 Skills
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Aggregate       │ ──► 聚合结果
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Memory.save()   │ ──► 更新记忆
└─────────────────┘
    │
    ▼
Response
```

---

## 3. 模块详细设计

### 3.1 Memory Manager

#### 3.1.1 设计目标

- 避免上下文无限增长导致 token 爆炸
- 保留关键信息，自动压缩冗余内容
- 支持崩溃恢复

#### 3.1.2 分层架构

```
┌─────────────────────────────────────────────────┐
│              Context Window (16K)               │
├─────────────────────────────────────────────────┤
│  Working Memory    │  4K  │ 当前任务状态        │
├────────────────────┼──────┼─────────────────────┤
│  Episodic Memory   │  8K  │ 对话历史 + 摘要     │
├────────────────────┼──────┼─────────────────────┤
│  Semantic Memory   │  4K  │ RAG 检索结果        │
└─────────────────────────────────────────────────┘
```

#### 3.1.3 压缩策略

```python
# 触发条件
IF episodic_tokens > EPISODIC_BUDGET:
    oldest_n_turns = episodic[:6]
    summary = LLM.summarize(oldest_n_turns)  # 压缩为 ~100 tokens
    episodic = [summary] + episodic[6:]
```

#### 3.1.4 接口定义

```python
class IMemoryManager(Protocol):
    """Memory Manager 接口"""
    
    async def add_turn(self, role: str, content: str) -> None:
        """添加一轮对话"""
        ...
    
    def get_context(self, current_goal: str) -> str:
        """获取与当前目标相关的上下文"""
        ...
    
    def update_working_memory(self, key: str, value: Any) -> None:
        """更新当前任务状态"""
        ...
    
    async def checkpoint(self) -> None:
        """持久化当前状态"""
        ...
    
    async def restore(self) -> None:
        """从持久化恢复状态"""
        ...
```

#### 3.1.5 数据结构

```python
@dataclass
class MemoryConfig:
    working_memory_budget: int = 4000      # tokens
    episodic_budget: int = 8000            # tokens
    semantic_budget: int = 4000            # tokens
    compression_threshold: int = 6         # 触发压缩的对话轮数
    checkpoint_interval: int = 5           # 每 N 轮自动 checkpoint

@dataclass
class MemoryState:
    working: dict[str, Any]                # 当前任务状态
    episodic_raw: list[Message]            # 原始对话
    episodic_summaries: list[str]          # 压缩后的摘要
    semantic_cache: dict[str, list[str]]   # RAG 缓存
```

#### 3.1.6 Pinned Context（Gemini Review 补充）

**问题**：在 Notebook 场景，用户可能会反复引用最开始上传的 PDF。如果因为对话轮数多了，把最初关于 PDF 的关键信息压缩丢了，体验会很差。

**解决方案**：增加"置顶上下文"，永远不被压缩。

```python
@dataclass
class PinnedItem:
    """置顶的上下文项"""
    id: str
    type: str                    # "file_meta", "user_instruction", "key_entity"
    content: str
    priority: int = 0            # 优先级，越高越靠前
    created_at: datetime = field(default_factory=datetime.now)
    
    def estimate_tokens(self) -> int:
        return len(self.content) // 3

@dataclass
class MemoryConfigV2:
    working_memory_budget: int = 4000
    episodic_budget: int = 8000
    semantic_budget: int = 4000
    pinned_budget: int = 1000            # 新增：置顶上下文预算
    compression_threshold: int = 6
    checkpoint_interval: int = 5

class TieredMemoryManagerV2:
    """支持 Pinned Context 的 Memory Manager"""
    
    def __init__(self, config: MemoryConfigV2, llm_client):
        self.config = config
        self.llm = llm_client
        
        # 原有层
        self.working: dict = {}
        self.episodic: list[dict] = []
        self.episodic_summaries: list[str] = []
        
        # 新增：置顶层
        self.pinned: list[PinnedItem] = []
    
    def pin(self, item: PinnedItem) -> bool:
        """添加置顶项，超出 budget 返回 False"""
        current_tokens = sum(p.estimate_tokens() for p in self.pinned)
        if current_tokens + item.estimate_tokens() > self.config.pinned_budget:
            return False
        
        self.pinned.append(item)
        self.pinned.sort(key=lambda x: -x.priority)
        return True
    
    def unpin(self, item_id: str) -> bool:
        """移除置顶项"""
        self.pinned = [p for p in self.pinned if p.id != item_id]
        return True
    
    def get_context(self, current_goal: str) -> str:
        """组装上下文，Pinned 永远在最前面"""
        parts = []
        
        # 1. Pinned Context（最高优先级，永不压缩）
        if self.pinned:
            pinned_content = "\n".join(p.content for p in self.pinned)
            parts.append(f"[关键信息 - 请始终记住]\n{pinned_content}")
        
        # 2. Working Memory
        if self.working:
            parts.append(f"[当前任务状态]\n{self.working}")
        
        # 3. Episodic（可能被压缩的历史）
        if self.episodic_summaries:
            parts.append(f"[历史摘要]\n" + "\n".join(self.episodic_summaries[-3:]))
        
        if self.episodic:
            parts.append(f"[最近对话]\n{self.episodic[-10:]}")
        
        return "\n\n".join(parts)
```

**Notebook 场景自动 Pin 规则**：

| 事件 | 自动 Pin 的内容 | 优先级 |
|------|----------------|--------|
| 用户上传文件 | 文件名 + 类型 + 页数/大小 | 10 |
| 用户首次描述任务 | 任务目标摘要（100字内） | 8 |
| 生成 Artifact | Artifact 类型 + 当前状态 | 5 |
| 用户显式标记 | 用户指定的任何内容 | 用户指定 |

**使用示例**：

```python
# 用户上传 PDF 时自动 pin
async def on_file_upload(self, file: UploadedFile):
    meta = PinnedItem(
        id=f"file_{file.id}",
        type="file_meta",
        content=f"用户上传了文件：{file.name}，类型：{file.type}，共 {file.pages} 页",
        priority=10
    )
    self.memory.pin(meta)
```

---

### 3.2 Planner

#### 3.2.1 设计目标

- 区分简单对话和复杂任务
- 生成可并行执行的任务图
- 校验 LLM 输出，防止非法 DAG

#### 3.2.2 规划模式

| 模式 | 触发条件 | 处理方式 |
|------|---------|---------|
| **React** | 简单问答、闲聊 | 直接调用 chat skill |
| **Plan-and-Solve** | 复杂任务、多步骤 | 生成 DAG 并执行 |

#### 3.2.3 DAG 数据结构

```python
class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"       # 上游失败导致跳过

@dataclass
class TaskNode:
    id: str                   # 唯一标识，如 "task_001"
    skill: str                # 要调用的 skill 名称
    params: dict[str, Any]    # skill 参数
    depends_on: list[str]     # 依赖的 task id 列表
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

@dataclass
class TaskDAG:
    id: str                   # DAG 唯一标识
    goal: str                 # 用户原始目标
    nodes: dict[str, TaskNode]
    created_at: datetime
    
    def get_ready_tasks(self) -> list[TaskNode]:
        """返回所有依赖已满足、可执行的任务"""
        ...
    
    def is_completed(self) -> bool:
        """检查 DAG 是否全部完成"""
        ...
    
    def get_results(self) -> dict[str, Any]:
        """收集所有成功任务的结果"""
        ...
```

#### 3.2.4 DAG 示例

用户输入: "帮我调研一下 AI 趋势，写一份简报"

```json
{
  "id": "dag_001",
  "goal": "帮我调研一下 AI 趋势，写一份简报",
  "nodes": {
    "t1": {
      "id": "t1",
      "skill": "web_search",
      "params": {"query": "AI 趋势 2025"},
      "depends_on": []
    },
    "t2": {
      "id": "t2",
      "skill": "web_search",
      "params": {"query": "大模型最新进展"},
      "depends_on": []
    },
    "t3": {
      "id": "t3",
      "skill": "analyze",
      "params": {"task": "整理搜索结果"},
      "depends_on": ["t1", "t2"]
    },
    "t4": {
      "id": "t4",
      "skill": "write_report",
      "params": {"format": "简报"},
      "depends_on": ["t3"]
    }
  }
}
```

执行顺序: `t1 || t2` → `t3` → `t4`

#### 3.2.5 校验规则

| 校验项 | 规则 | 失败处理 |
|--------|------|---------|
| Skill 存在性 | `node.skill in registered_skills` | 拒绝，要求重新规划 |
| 依赖存在性 | `all(dep in nodes for dep in node.depends_on)` | 拒绝 |
| 无环检测 | 拓扑排序不存在环 | 拒绝 |
| JSON 格式 | 符合 schema | 降级到 React 模式 |

#### 3.2.6 接口定义

```python
class IPlanner(Protocol):
    """Planner 接口"""
    
    async def create_plan(
        self, 
        goal: str, 
        context: str,
        available_skills: set[str]
    ) -> TaskDAG:
        """
        根据目标创建执行计划
        
        Args:
            goal: 用户输入的目标
            context: 从 Memory 获取的上下文
            available_skills: 当前可用的 skill 列表
            
        Returns:
            TaskDAG: 执行计划
            
        Raises:
            PlanningError: 规划失败时抛出
        """
        ...
    
    def validate_dag(self, dag: TaskDAG) -> list[str]:
        """
        校验 DAG 合法性
        
        Returns:
            错误信息列表，空列表表示合法
        """
        ...
```

#### 3.2.7 Planner Prompt 设计

```markdown
# System Prompt

你是一个任务规划器。根据用户目标，决定是直接回答还是拆解为多个子任务。

## 可用的 Skills
{available_skills}

## 输出格式

如果是简单对话，输出：
```json
{"mode": "react", "response": "直接回答内容"}
```

如果需要多步骤，输出：
```json
{
  "mode": "plan",
  "tasks": [
    {"id": "t1", "skill": "skill_name", "params": {...}, "depends_on": []},
    {"id": "t2", "skill": "skill_name", "params": {...}, "depends_on": ["t1"]}
  ]
}
```

## 规则
1. 如果任务可以并行，不要设置依赖
2. 每个任务只能依赖 id 比自己小的任务
3. skill 必须从可用列表中选择
```

#### 3.2.8 Re-planning 机制（Gemini Review 补充）

**问题**：对于复杂任务，LLM 很难一次性把所有步骤都规划对。比如"先搜索A，根据A的结果决定搜B还是搜C"，静态 DAG 做不到。

**解决方案**：支持动态重规划和条件边。

```python
class ReplanningStrategy(Enum):
    NONE = "none"                    # 不重规划
    ON_FAILURE = "on_failure"        # 任务失败时重规划
    ON_CHECKPOINT = "on_checkpoint"  # 关键节点完成后重规划

@dataclass
class TaskNode:
    # ... 原有字段 ...
    
    # 新增：重规划相关
    is_checkpoint: bool = False           # 是否是检查点（完成后触发重规划）
    conditional_next: Optional[str] = None  # 条件分支表达式

@dataclass 
class ReplanRequest:
    """重规划请求"""
    original_goal: str
    completed_tasks: dict[str, Any]   # task_id -> result
    remaining_tasks: list[str]
    reason: str                        # "checkpoint_reached" | "task_failed"

class AdaptivePlanner(DAGPlanner):
    """支持动态重规划的 Planner"""
    
    async def replan(
        self, 
        request: ReplanRequest,
        context: str,
        available_skills: set[str]
    ) -> Optional[TaskDAG]:
        """
        根据已完成任务的结果，决定是否需要调整计划
        
        Returns:
            新的 DAG（如果需要调整），或 None（继续原计划）
        """
        prompt = f"""
# 任务重规划

## 原始目标
{request.original_goal}

## 已完成的任务及结果
{json.dumps(request.completed_tasks, ensure_ascii=False, indent=2)}

## 原计划中剩余的任务
{request.remaining_tasks}

## 请判断
根据已完成任务的结果，原计划是否仍然合理？

输出：
- action: "continue"（继续原计划）或 "adjust"（调整计划）
- new_tasks: 仅当 adjust 时，给出新的任务列表
"""
        response = await self.llm.complete(prompt)
        parsed = self._parse_response(response)
        
        if parsed.get("action") == "continue":
            return None
        
        return self._build_adjusted_dag(request.completed_tasks, parsed["new_tasks"])
```

**检查点自动标记规则**：

| 条件 | 标记为 Checkpoint |
|------|-------------------|
| 搜索类 Skill（web_search, rag_search） | ✅ |
| 有 2+ 个下游依赖的节点 | ✅ |
| 用户显式标记 | ✅ |

**条件边示例**：

```json
{
  "id": "t1", 
  "skill": "web_search", 
  "params": {"query": "公司财报"},
  "is_checkpoint": true,
  "branches": {
    "positive": {"next": "t2a", "condition": "result.sentiment == 'positive'"},
    "negative": {"next": "t2b", "condition": "result.sentiment == 'negative'"}
  }
}
```

---

### 3.3 Runtime

#### 3.3.1 设计目标

- 最大化并行度，减少等待时间
- 单任务失败不阻塞其他任务
- 支持超时和重试

#### 3.3.2 执行流程

```
┌─────────────────────────────────────────────────────────┐
│                     DAG Executor                        │
│                                                         │
│  while not dag.is_completed():                         │
│      ready_tasks = dag.get_ready_tasks()               │
│                                                         │
│      if ready_tasks:                                    │
│          ┌─────────────────────────────────────────┐   │
│          │      asyncio.TaskGroup                  │   │
│          │  ┌─────┐  ┌─────┐  ┌─────┐             │   │
│          │  │ t1  │  │ t2  │  │ t3  │  (并行)     │   │
│          │  └─────┘  └─────┘  └─────┘             │   │
│          └─────────────────────────────────────────┘   │
│                                                         │
│      await asyncio.sleep(0.1)  # 等待状态变化          │
└─────────────────────────────────────────────────────────┘
```

#### 3.3.3 错误处理策略

| 错误类型 | 处理方式 | 下游任务 |
|---------|---------|---------|
| **超时** | 标记 FAILED，记录 "Timeout" | 标记 SKIPPED |
| **异常** | 标记 FAILED，记录 error | 标记 SKIPPED |
| **Skill 不存在** | 标记 FAILED | 标记 SKIPPED |
| **重试成功** | 标记 COMPLETED | 正常执行 |

#### 3.3.4 接口定义

```python
@dataclass
class RuntimeConfig:
    default_timeout: float = 30.0     # 单任务默认超时（秒）
    max_retries: int = 2              # 最大重试次数
    retry_delay: float = 1.0          # 重试间隔（秒）
    max_concurrent: int = 10          # 最大并发数

class IRuntime(Protocol):
    """Runtime 接口"""
    
    async def execute_dag(self, dag: TaskDAG) -> ExecutionResult:
        """
        执行整个 DAG
        
        Returns:
            ExecutionResult: 包含所有任务的执行结果和统计信息
        """
        ...
    
    async def execute_skill(
        self, 
        skill_name: str, 
        params: dict,
        timeout: Optional[float] = None
    ) -> Any:
        """
        执行单个 Skill
        """
        ...

@dataclass
class ExecutionResult:
    dag_id: str
    status: Literal["success", "partial", "failed"]
    results: dict[str, Any]           # task_id -> result
    errors: dict[str, str]            # task_id -> error message
    duration_ms: int
    stats: ExecutionStats

@dataclass
class ExecutionStats:
    total_tasks: int
    completed: int
    failed: int
    skipped: int
    total_duration_ms: int
    parallel_efficiency: float        # 实际时间 / 串行时间
```

#### 3.3.5 DAG 持久化（Critical - Gemini Review 补充）

**问题**：如果 Python 服务重启（部署更新或 Crash），内存里的 DAG 就丢了。PPT 生成到一半会直接中断且无法恢复。

**解决方案**：DAG 状态实时写入 SurrealDB，支持断点续跑。

```python
# DAG 持久化表结构 (SurrealDB)
"""
DEFINE TABLE dag_execution SCHEMAFULL;
DEFINE FIELD id ON dag_execution TYPE string;
DEFINE FIELD user_id ON dag_execution TYPE string;
DEFINE FIELD goal ON dag_execution TYPE string;
DEFINE FIELD status ON dag_execution TYPE string;  -- pending, running, completed, failed
DEFINE FIELD nodes ON dag_execution TYPE array;
DEFINE FIELD created_at ON dag_execution TYPE datetime;
DEFINE FIELD updated_at ON dag_execution TYPE datetime;

DEFINE TABLE task_node SCHEMAFULL;
DEFINE FIELD id ON task_node TYPE string;
DEFINE FIELD dag_id ON task_node TYPE string;
DEFINE FIELD skill ON task_node TYPE string;
DEFINE FIELD params ON task_node TYPE object;
DEFINE FIELD depends_on ON task_node TYPE array;
DEFINE FIELD status ON task_node TYPE string;
DEFINE FIELD result ON task_node TYPE option<object>;
DEFINE FIELD error ON task_node TYPE option<string>;
DEFINE FIELD started_at ON task_node TYPE option<datetime>;
DEFINE FIELD finished_at ON task_node TYPE option<datetime>;

DEFINE INDEX dag_status ON dag_execution FIELDS user_id, status;
"""

class PersistentRuntime(AsyncRuntime):
    """支持持久化的 Runtime"""
    
    def __init__(self, config: RuntimeConfig, db: SurrealDB):
        super().__init__(config)
        self.db = db
    
    async def execute_dag(self, dag: TaskDAG) -> ExecutionResult:
        """执行 DAG，每个状态变更都持久化"""
        
        # 1. 先持久化 DAG 到数据库
        await self._persist_dag(dag)
        
        while True:
            ready_tasks = dag.get_ready_tasks()
            
            if not ready_tasks:
                if dag.is_completed():
                    break
                await asyncio.sleep(0.1)
                continue
            
            async with asyncio.TaskGroup() as tg:
                for task_node in ready_tasks:
                    tg.create_task(self._execute_and_persist(task_node, dag))
        
        # 更新 DAG 最终状态
        await self._update_dag_status(dag)
        return self._collect_results(dag)
    
    async def _execute_and_persist(self, task: TaskNode, dag: TaskDAG):
        """执行单个任务，状态变更实时写库"""
        
        # 标记开始
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        await self._persist_task(task)
        
        try:
            result = await asyncio.wait_for(
                self.skills[task.skill](**task.params),
                timeout=self.config.default_timeout
            )
            task.status = TaskStatus.COMPLETED
            task.result = result
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            self._handle_downstream_skip(task.id, dag)
        
        finally:
            task.finished_at = datetime.now()
            await self._persist_task(task)
    
    async def _persist_task(self, task: TaskNode):
        """持久化单个任务状态"""
        await self.db.query("""
            UPDATE task_node SET 
                status = $status,
                result = $result,
                error = $error,
                started_at = $started_at,
                finished_at = $finished_at
            WHERE id = $id
        """, {
            "id": task.id,
            "status": task.status.value,
            "result": task.result,
            "error": task.error,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
        })
    
    async def resume_incomplete_dags(self, user_id: str) -> list[TaskDAG]:
        """服务启动时，恢复未完成的 DAG"""
        
        incomplete = await self.db.query("""
            SELECT * FROM dag_execution 
            WHERE user_id = $user_id 
            AND status IN ['pending', 'running']
        """, {"user_id": user_id})
        
        dags = []
        for record in incomplete:
            dag = await self._load_dag_from_db(record["id"])
            dags.append(dag)
            # 异步继续执行
            asyncio.create_task(self.execute_dag(dag))
        
        return dags
```

**Worker 启动流程**：

```
┌─────────────────────────────────────────────────────────┐
│                   Worker Startup                        │
│                                                         │
│  1. Connect to SurrealDB                               │
│                    │                                    │
│                    ▼                                    │
│  2. Query: SELECT * FROM dag_execution                 │
│            WHERE status IN ['pending', 'running']      │
│                    │                                    │
│                    ▼                                    │
│  3. For each incomplete DAG:                           │
│     ┌─────────────────────────────────────────────┐    │
│     │  - Load task nodes from DB                  │    │
│     │  - Skip already COMPLETED tasks             │    │
│     │  - Resume from first PENDING task           │    │
│     └─────────────────────────────────────────────┘    │
│                    │                                    │
│  4. Start accepting new requests                       │
└─────────────────────────────────────────────────────────┘
```

---

### 3.4 Skill Registry

#### 3.4.1 设计目标

- 统一的 Skill 注册和调用接口
- 支持从 Markdown 文件加载 Skill 定义
- 支持运行时动态注册

#### 3.4.2 Skill 定义格式

```markdown
# Skill: web_search

## Description
搜索互联网获取信息

## Parameters
| Name | Type | Required | Description |
|------|------|----------|-------------|
| query | string | Yes | 搜索关键词 |
| max_results | int | No | 最大结果数，默认 5 |

## Returns
```json
{
  "results": [
    {"title": "...", "url": "...", "snippet": "..."}
  ]
}
```

## Examples
Input: {"query": "Python asyncio tutorial"}
Output: {"results": [...]}
```

#### 3.4.3 接口定义

```python
@dataclass
class SkillMeta:
    name: str
    description: str
    parameters: dict[str, ParameterDef]
    returns: str
    examples: list[dict]

@dataclass
class ParameterDef:
    name: str
    type: str
    required: bool
    default: Optional[Any]
    description: str

SkillFunc = Callable[..., Awaitable[Any]]

class ISkillRegistry(Protocol):
    """Skill Registry 接口"""
    
    def register(
        self, 
        name: str, 
        func: SkillFunc,
        meta: Optional[SkillMeta] = None
    ) -> None:
        """注册一个 Skill"""
        ...
    
    def load_from_markdown(self, path: Path) -> None:
        """从 Markdown 文件加载 Skill 定义"""
        ...
    
    def get(self, name: str) -> Optional[SkillFunc]:
        """获取 Skill 函数"""
        ...
    
    def list_skills(self) -> list[SkillMeta]:
        """列出所有已注册的 Skills"""
        ...
    
    def get_skills_prompt(self) -> str:
        """生成供 Planner 使用的 Skills 描述"""
        ...
```

---

### 3.5 BaseAgent

#### 3.5.1 主入口

```python
class BaseAgent:
    """Agent 基类"""
    
    def __init__(
        self,
        llm_client: ILLMClient,
        config: AgentConfig,
        skills: Optional[dict[str, SkillFunc]] = None,
    ):
        self.llm = llm_client
        self.config = config
        
        # 初始化三大组件
        self.memory = TieredMemoryManager(config.memory, llm_client)
        self.planner = DAGPlanner(llm_client)
        self.runtime = AsyncRuntime(config.runtime)
        self.skills = SkillRegistry()
        
        # 注册默认 skills
        self._register_default_skills()
        
        # 注册自定义 skills
        if skills:
            for name, func in skills.items():
                self.skills.register(name, func)
    
    async def run(self, user_input: str) -> AgentResponse:
        """
        主入口：处理用户输入，返回响应
        
        Args:
            user_input: 用户输入文本
            
        Returns:
            AgentResponse: 包含响应文本和元信息
        """
        try:
            # 1. 恢复状态
            await self.memory.restore()
            
            # 2. 记录输入
            await self.memory.add_turn("user", user_input)
            
            # 3. 获取上下文
            context = self.memory.get_context(user_input)
            
            # 4. 规划
            available_skills = set(self.skills.list_names())
            dag = await self.planner.create_plan(
                user_input, context, available_skills
            )
            
            # 5. 执行
            if dag.is_simple_response():
                response_text = dag.get_simple_response()
            else:
                exec_result = await self.runtime.execute_dag(
                    dag, self.skills
                )
                response_text = await self._aggregate_results(
                    user_input, exec_result
                )
            
            # 6. 记录输出
            await self.memory.add_turn("assistant", response_text)
            
            # 7. 保存状态
            await self.memory.checkpoint()
            
            return AgentResponse(
                text=response_text,
                dag=dag,
                memory_stats=self.memory.get_stats(),
            )
            
        except Exception as e:
            logger.exception("Agent run failed")
            return AgentResponse(
                text=f"抱歉，处理时出现错误: {str(e)}",
                error=str(e),
            )

@dataclass
class AgentResponse:
    text: str
    dag: Optional[TaskDAG] = None
    memory_stats: Optional[dict] = None
    error: Optional[str] = None
    
    # 新增：Artifacts 支持（Gemini Review 建议）
    artifacts: list["Artifact"] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

@dataclass
class Artifact:
    """
    Agent 产出的结构化产物
    
    用于 Notebook 场景，输出不仅仅是文本，还可能是文件、图表、代码等。
    """
    id: str
    type: str                    # "file", "chart", "code", "table", "image"
    title: str
    data: Any                    # 具体内容，类型取决于 type
    mime_type: Optional[str] = None
    url: Optional[str] = None    # 如果是文件，可以提供下载链接
    metadata: dict = field(default_factory=dict)

# Artifact 类型定义
class ArtifactType:
    FILE = "file"           # PPT, Word, PDF 等文件
    CHART = "chart"         # 图表（ECharts, Plotly 配置）
    CODE = "code"           # 代码块
    TABLE = "table"         # 表格数据
    IMAGE = "image"         # 图片
    MARKDOWN = "markdown"   # Markdown 文档

# 使用示例
"""
response = AgentResponse(
    text="好的，我已经为您生成了分析报告。",
    artifacts=[
        Artifact(
            id="artifact_001",
            type=ArtifactType.FILE,
            title="AI趋势分析报告.pptx",
            data=pptx_bytes,
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            url="/downloads/ai_report.pptx"
        ),
        Artifact(
            id="artifact_002",
            type=ArtifactType.CHART,
            title="市场份额分布",
            data={"type": "pie", "series": [...]}  # ECharts 配置
        )
    ],
    suggestions=[
        "您想针对这份报告生成演讲稿吗？",
        "需要我把图表导出为 PNG 吗？",
        "要不要深入分析某个具体领域？"
    ]
)
"""
```

---

## 4. 配置与特化

### 4.1 配置文件格式

```yaml
# agents/finance_analyst.yaml

name: "金融分析师"
version: "1.0.0"

# LLM 配置
llm:
  model: "claude-3-5-sonnet-20241022"
  temperature: 0.3
  max_tokens: 4096

# Memory 配置
memory:
  working_memory_budget: 4000
  episodic_budget: 8000
  semantic_budget: 4000
  compression_threshold: 6
  checkpoint_backend: "file"  # file | redis | surrealdb
  checkpoint_path: "./checkpoints/finance"

# Runtime 配置
runtime:
  default_timeout: 30
  max_retries: 2
  max_concurrent: 5

# Skill 配置
skills:
  - path: "./skills/common/chat.md"
  - path: "./skills/common/web_search.md"
  - path: "./skills/finance/stock_quote.md"
  - path: "./skills/finance/calculate_ratio.md"
  - path: "./skills/finance/generate_chart.md"

# System Prompt
system_prompt: |
  你是一个严谨的金融分析师。
  
  ## 工作原则
  1. 对于任何金融问题，必须先查询最新数据
  2. 给出的数据必须标注来源和时间
  3. 不确定的信息要明确标注
  
  ## 输出风格
  - 使用专业术语
  - 数据精确到小数点后两位
  - 重要结论加粗显示

# RAG 配置（可选）
rag:
  enabled: true
  vector_db: "surrealdb"
  collections:
    - "finance_reports"
    - "market_analysis"
  top_k: 5
```

### 4.2 AgentFactory

```python
class AgentFactory:
    """Agent 工厂类"""
    
    @classmethod
    def create(cls, config_path: Path) -> BaseAgent:
        """从配置文件创建 Agent 实例"""
        config = cls._load_config(config_path)
        
        # 初始化 LLM Client
        llm_client = cls._create_llm_client(config["llm"])
        
        # 加载 Skills
        skills = {}
        for skill_config in config.get("skills", []):
            skill = SkillLoader.load(skill_config["path"])
            skills[skill.name] = skill.func
        
        # 创建 Agent
        agent = BaseAgent(
            llm_client=llm_client,
            config=AgentConfig.from_dict(config),
            skills=skills,
        )
        
        # 设置 System Prompt
        if "system_prompt" in config:
            agent.set_system_prompt(config["system_prompt"])
        
        return agent
```

---

## 5. 与 Wukong 框架集成

### 5.1 集成方案

Wukong 的六根（六个 Agent）可以作为 Skills 注册到 BaseAgent：

```python
# 将 Wukong 的 Agent 包装为 Skill
from wukong import Explorer, Analyst, Reviewer, Tester, Implementer, Architect

def create_wukong_skills():
    return {
        "explorer": wrap_wukong_agent(Explorer()),
        "analyst": wrap_wukong_agent(Analyst()),
        "reviewer": wrap_wukong_agent(Reviewer()),
        "tester": wrap_wukong_agent(Tester()),
        "implementer": wrap_wukong_agent(Implementer()),
        "architect": wrap_wukong_agent(Architect()),
    }

def wrap_wukong_agent(agent) -> SkillFunc:
    """将 Wukong Agent 包装为 Skill 函数"""
    async def skill_func(**kwargs) -> dict:
        result = await agent.run(**kwargs)
        return {
            "output": result.output,
            "evidence": result.evidence,
            "confidence": result.confidence,
        }
    return skill_func
```

### 5.2 Wukong 特化配置

```yaml
# agents/wukong_dev.yaml

name: "悟空开发助手"
version: "1.0.0"

llm:
  model: "claude-3-5-sonnet-20241022"

skills:
  # 基础 Skills
  - path: "./skills/common/chat.md"
  - path: "./skills/common/web_search.md"
  
  # Wukong 六根
  - type: "wukong"
    agents: ["explorer", "analyst", "reviewer", "tester", "implementer", "architect"]

system_prompt: |
  你是悟空开发助手，拥有六根能力：
  - 探索者（眼根）：搜索和发现信息
  - 分析者（耳根）：深度分析问题
  - 审阅者（鼻根）：代码审查和质量检查
  - 测试者（舌根）：编写和执行测试
  - 实现者（身根）：编写代码实现
  - 架构师（意根）：系统设计和架构决策
  
  根据用户需求，灵活调用不同的能力组合。
```

---

## 6. 错误处理与降级

### 6.1 错误分类

| 错误级别 | 类型 | 处理方式 |
|---------|------|---------|
| **L1-可恢复** | 超时、网络错误 | 重试 |
| **L2-可降级** | LLM 规划失败、Skill 不可用 | 降级到简单模式 |
| **L3-需人工** | 配置错误、权限问题 | 抛出异常，记录日志 |

### 6.2 降级策略

```python
class DegradationStrategy:
    """降级策略"""
    
    @staticmethod
    async def on_planning_failure(goal: str, error: Exception) -> TaskDAG:
        """规划失败时的降级处理"""
        logger.warning(f"Planning failed, degrading to chat mode: {error}")
        
        # 降级到直接对话模式
        return TaskDAG.create_simple(
            skill="chat",
            params={"message": goal}
        )
    
    @staticmethod
    async def on_skill_failure(
        task: TaskNode, 
        error: Exception,
        dag: TaskDAG
    ) -> None:
        """Skill 执行失败时的处理"""
        task.status = TaskStatus.FAILED
        task.error = str(error)
        
        # 标记所有下游任务为 SKIPPED
        for downstream in dag.get_downstream_tasks(task.id):
            downstream.status = TaskStatus.SKIPPED
            downstream.error = f"Skipped due to upstream failure: {task.id}"
```

### 6.3 重试策略

```python
@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    
    def get_delay(self, attempt: int) -> float:
        """计算第 N 次重试的延迟时间（指数退避）"""
        delay = self.base_delay * (self.exponential_base ** attempt)
        return min(delay, self.max_delay)
    
    def should_retry(self, attempt: int, error: Exception) -> bool:
        """判断是否应该重试"""
        if attempt >= self.max_attempts:
            return False
        
        # 只重试特定类型的错误
        retryable_errors = (
            asyncio.TimeoutError,
            ConnectionError,
            # 可以添加更多可重试的错误类型
        )
        return isinstance(error, retryable_errors)
```

---

## 7. 可观测性

### 7.1 日志规范

```python
# 使用 structlog 进行结构化日志
import structlog

logger = structlog.get_logger()

# 示例日志
logger.info(
    "task_started",
    dag_id=dag.id,
    task_id=task.id,
    skill=task.skill,
    params=task.params,
)

logger.info(
    "task_completed",
    dag_id=dag.id,
    task_id=task.id,
    duration_ms=duration,
    result_size=len(str(result)),
)

logger.error(
    "task_failed",
    dag_id=dag.id,
    task_id=task.id,
    error=str(e),
    traceback=traceback.format_exc(),
)
```

### 7.2 Metrics

| Metric | 类型 | 描述 |
|--------|------|------|
| `agent.request.total` | Counter | 总请求数 |
| `agent.request.duration_ms` | Histogram | 请求耗时分布 |
| `agent.task.total` | Counter | 任务总数（按 skill 分组） |
| `agent.task.failed` | Counter | 失败任务数 |
| `agent.memory.tokens` | Gauge | 当前 Memory 使用的 token 数 |
| `agent.memory.compression` | Counter | Memory 压缩次数 |

### 7.3 Tracing

```python
# 使用 OpenTelemetry 进行分布式追踪
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

async def run(self, user_input: str):
    with tracer.start_as_current_span("agent.run") as span:
        span.set_attribute("user_input_length", len(user_input))
        
        with tracer.start_as_current_span("memory.load"):
            context = self.memory.get_context(user_input)
        
        with tracer.start_as_current_span("planner.plan"):
            dag = await self.planner.create_plan(...)
        
        with tracer.start_as_current_span("runtime.execute"):
            result = await self.runtime.execute_dag(dag)
```

---

## 8. 实现计划

> **更新说明**：根据 Gemini Review 反馈，增加了持久化、Re-planning、Pinned Context 的工作量。
> 
> **总工时调整**：10 天 → **12-14 天**

### 8.1 Phase 1: 内核（Week 1）

| 任务 | 产出 | Owner | 预估 |
|------|------|-------|------|
| Memory Manager 实现 | `memory.py` | - | 1.5d |
| **Pinned Context 支持** | `memory.py` | - | **0.5d** |
| Planner 实现 | `planner.py` | - | 1.5d |
| Runtime 实现 | `runtime.py` | - | 1d |
| 单元测试 | `tests/` | - | 1d |

**里程碑**: 用 Mock Skill 跑通完整流程（含 Pinned Context）

### 8.2 Phase 2: 协议 + 持久化（Week 2）

| 任务 | 产出 | Owner | 预估 |
|------|------|-------|------|
| Skill Registry 实现 | `skill_registry.py` | - | 1d |
| Markdown Skill Loader | `skill_loader.py` | - | 0.5d |
| 基础 Skills 实现 | `skills/*.md` | - | 1d |
| **SurrealDB 持久化层** | `persistence.py` | - | **1.5d** |
| **断点续跑机制** | `runtime.py` | - | **0.5d** |

**里程碑**: 可以加载外部 Skill 并执行；服务重启后 DAG 可恢复

### 8.3 Phase 3: 工厂 + Re-planning（Week 3 前半）

| 任务 | 产出 | Owner | 预估 |
|------|------|-------|------|
| AgentFactory 实现 | `factory.py` | - | 0.5d |
| YAML 配置解析 | `config.py` | - | 0.5d |
| 示例 Agent 配置 | `agents/*.yaml` | - | 0.5d |
| Wukong 集成适配 | `wukong_adapter.py` | - | 0.5d |
| **Re-planning 机制** | `planner.py` | - | **1d** |
| **Checkpoint 自动标记** | `planner.py` | - | **0.5d** |

**里程碑**: 一键生成特化 Agent；支持动态重规划

### 8.4 Phase 4: Notebook 适配 + 打磨（Week 3 后半）

| 任务 | 产出 | Owner | 预估 |
|------|------|-------|------|
| **Artifact 数据结构** | `types.py` | - | **0.5d** |
| **Artifact 生成集成** | `agent.py` | - | **0.5d** |
| 边界情况处理 | - | - | 1d |
| 集成测试 | `tests/integration/` | - | 1d |
| 日志和 Metrics | - | - | 0.5d |
| 文档 | `docs/` | - | 0.5d |

**里程碑**: 生产可用，支持 Notebook 多模态输出

### 8.5 工时对比

| 阶段 | 原计划 | 新计划 | 新增内容 |
|------|-------|--------|---------|
| Phase 1 | 5d | 5.5d | Pinned Context |
| Phase 2 | 2.5d | 4.5d | SurrealDB 持久化 |
| Phase 3 | 2d | 3.5d | Re-planning |
| Phase 4 | 3d | 4d | Artifact 支持 |
| **总计** | **12.5d** | **17.5d** | **+5d** |

> **建议**：可以先做 MVP（跳过 Re-planning 和 Artifact），用 **10-12 天** 验证核心流程，再迭代补充。

---

## 9. 风险与应对

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|---------|
| LLM 规划输出不稳定 | 高 | 中 | 增加 few-shot 示例；严格 JSON Schema 校验；降级策略 |
| Memory 压缩导致信息丢失 | 中 | 中 | **Pinned Context 机制**；保留关键实体；允许用户标记重要信息 |
| 并行任务竞争条件 | 中 | 高 | 使用 asyncio.Lock；避免共享可变状态 |
| Checkpoint 性能问题 | 低 | 中 | 异步写入；增量 checkpoint |
| Wukong 集成兼容性 | 中 | 低 | 定义清晰的适配层接口 |
| **服务重启导致 DAG 丢失** | 中 | 高 | **SurrealDB 持久化**；断点续跑机制 |
| **静态 DAG 无法适应动态任务** | 中 | 中 | **Re-planning 机制**；条件边支持 |
| **SurrealDB 写入延迟** | 低 | 中 | 批量写入；本地缓存 + 异步刷盘 |

---

## 10. 开放问题

1. **Memory 压缩质量如何评估？**
   - 需要定义压缩前后的信息保留率指标
   - ✅ 已通过 Pinned Context 部分解决

2. **Planner 的 few-shot 示例如何选择？**
   - 需要根据实际场景积累案例

3. **是否需要支持 Skill 的版本管理？**
   - 待讨论

4. **多 Agent 协作如何实现？**
   - 计划在 v2.0 考虑

5. **Re-planning 的触发时机如何优化？**（新增）
   - 过于频繁会增加延迟，过于稀疏会导致计划僵化
   - 需要通过实验确定最佳 checkpoint 密度

6. **Artifact 的存储和生命周期管理？**（新增）
   - 大文件（PPT、PDF）如何存储？本地 vs 对象存储？
   - 过期清理策略？

---

## 附录 A: 目录结构

```
agent-framework/
├── src/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── agent.py           # BaseAgent
│   │   ├── memory.py          # TieredMemoryManager
│   │   ├── planner.py         # DAGPlanner
│   │   ├── runtime.py         # AsyncRuntime
│   │   └── types.py           # 公共类型定义
│   ├── skills/
│   │   ├── __init__.py
│   │   ├── registry.py        # SkillRegistry
│   │   ├── loader.py          # SkillLoader
│   │   └── builtin/           # 内置 Skills
│   │       ├── chat.py
│   │       └── web_search.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── wukong.py          # Wukong 适配器
│   │   └── llm/
│   │       ├── anthropic.py
│   │       └── openai.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── schema.py          # 配置 Schema
│   └── utils/
│       ├── __init__.py
│       ├── logging.py
│       └── metrics.py
├── skills/                     # Skill 定义文件
│   ├── common/
│   │   ├── chat.md
│   │   └── web_search.md
│   └── finance/
│       └── stock_quote.md
├── agents/                     # Agent 配置文件
│   ├── default.yaml
│   └── finance_analyst.yaml
├── tests/
│   ├── unit/
│   └── integration/
├── docs/
│   ├── getting-started.md
│   └── skill-development.md
├── pyproject.toml
└── README.md
```

---

## 附录 B: 依赖清单

```toml
# pyproject.toml

[project]
name = "agent-framework"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "anthropic>=0.40.0",        # LLM Client
    "pydantic>=2.0",            # 数据校验
    "pyyaml>=6.0",              # 配置解析
    "structlog>=24.0",          # 结构化日志
    "aiofiles>=24.0",           # 异步文件操作
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.5",
]
observability = [
    "opentelemetry-api>=1.25",
    "opentelemetry-sdk>=1.25",
    "prometheus-client>=0.20",
]
```

---

## 附录 C: 评审 Checklist

### 架构评审

- [ ] 三层架构（Memory/Planner/Runtime）职责是否清晰？
- [ ] 组件间接口是否解耦？
- [ ] 是否存在循环依赖？

### 健壮性评审

- [ ] 错误处理是否完善？
- [ ] 降级策略是否合理？
- [ ] 是否有潜在的竞争条件？

### 可扩展性评审

- [ ] 新增 Skill 是否方便？
- [ ] 配置机制是否灵活？
- [ ] 是否易于与其他框架集成？

### 实现评审

- [ ] 工作量估算是否合理？
- [ ] 技术选型是否恰当？
- [ ] 是否有遗漏的关键功能？

---

*文档结束*
