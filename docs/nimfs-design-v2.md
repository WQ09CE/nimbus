# NimFS v2：智能体多层级存储文件系统技术规格书
# NimFS v2: Technical Specification for Agent Hierarchical Storage

> **版本**：v2.0 | **状态**：草稿 (Draft) | **基于**：nimfs-design.md (v1) + ReviewCommittee 评审意见
>
> **核心变更摘要 (Change Summary)**：全局公用存储目录 · 双作用域记忆管理 · 异步蒸馏架构 · 写入状态机

---

## 1. 背景与动机 (Background & Motivation)

### 冯·诺依曼类比 (Von Neumann Analogy)

在传统计算机架构中，计算与存储分离。为解决 Autonomous Agents 在长程任务中的"上下文漂移"与"记忆碎片化"问题，NimFS 将 LLM 视为 **CPU**，并将存储层次进行如下映射：

| 计算机组件 | 智能体对应物 | 说明 |
| :--- | :--- | :--- |
| **寄存器 (Registers)** | **Current Prompt** | 当前正在处理的 Token 片段，极速但容量极小。 |
| **一级缓存 (L1 Cache)** | **Working Context** | 上下文窗口中的活跃部分，包含系统指令和最近对话。 |
| **内存 (RAM)** | **Session Stream** | 当前会话的完整历史，通过 `StackFrame` 管理，随任务结束释放。 |
| **磁盘 (Disk/SSD)** | **NimFS** | 持久化存储层，提供结构化、可检索的长期记忆。 |

### OpenViking 启发 (OpenViking Inspiration)

NimFS 汲取了 **OpenViking** 项目中关于长期记忆分类与分层管理的精华，将其 6 分类记忆体系（Profile, Preferences, Entities, Events, Cases, Patterns）与 L0/L1/L2 结构深度融合，构建了一个既符合认知科学又具备工业级性能的存储层。

### v1 → v2 核心变更动因 (Why v2?)

ReviewCommittee 在对 v1 的评审中提出三个 **Critical** 级问题：

| # | 问题 | 风险等级 | 根因 |
| :--- | :--- | :--- | :--- |
| **C-1** | **缺乏一致性模型**：`write()` 中断时会产生半写入记忆，污染检索结果 | 🔴 Critical | v1 未定义事务边界 |
| **C-2** | **LLM 蒸馏同步执行**：L0/L1 生成阻塞 Agent 主线程，造成响应延迟 | 🔴 Critical | v1 `write()` 同步调用 LLM |
| **C-3** | **记忆冲突与时效性未定义**：长期运行后矛盾记忆并存，造成行为漂移 | 🔴 Critical | v1 缺少 `valid_from`、`confidence`、`source` 字段 |

v2 在保留 v1 全部设计意图的基础上，针对上述三个问题进行了系统性修订。

---

## 2. 存储架构与目录结构 (Architecture & Directory Structure)

### 2.1 设计原则变更：从 Workspace-local 到 Global-shared

**v1 设计**：将记忆存储在 `{workspace}/.nimbus/fs/` 下，记忆随 workspace 的生命周期绑定，不同 session 可能读取不到历史项目记忆。

**v2 设计**：改为全局公用目录 `~/.nimbus/fs/`，天然实现跨 session 共享，不随任何单一 workspace 消失。这与 Claude 的 `~/.claude/` 全局目录策略保持一致。

### 2.2 Project 识别策略 (Project Identification)

借鉴 Claude 的 Project ID 生成规则：**将 workspace 绝对路径的前导 `/` 剥除后，所有 `/` 替换为 `-`**，作为项目目录名。

```python
def get_project_id(workspace_path: str) -> str:
    """
    将绝对路径转换为项目目录名，与 Claude 保持一致。
    
    Example:
        >>> get_project_id("/Users/wangqing/sourcecode/nimbus")
        'Users-wangqing-sourcecode-nimbus'
    """
    return workspace_path.strip('/').replace('/', '-')
```

> **设计决策**：此规则为确定性函数，无需维护额外的路径→ID 映射表，且人类可读，便于手动检查和调试。

### 2.3 完整目录结构 (Full Directory Layout)

```text
~/.nimbus/fs/
│
├── global/                                     # 全局作用域 (Global Scope)
│   │                                           # 跨所有项目生效的用户级偏好
│   ├── profile/                                # 智能体角色定义、用户身份元数据
│   │   ├── <id>.abstract                       # L0 纯文本摘要
│   │   ├── <id>.overview.md                    # L1 结构化概览
│   │   └── <id>.content.md                     # L2 完整原始内容
│   └── preferences/                            # 用户偏好、样式指南、全局约束
│       ├── <id>.abstract
│       ├── <id>.overview.md
│       └── <id>.content.md
│
└── projects/                                   # 项目级作用域 (Project Scope)
    │
    └── Users-wangqing-sourcecode-agent-framework-nimbus/   # 示例项目目录
        ├── memory/
        │   ├── entities/     # 核心对象、组件、文件关联 (What is it?)
        │   ├── events/       # 关键状态变更、里程碑记录 (What happened?)
        │   ├── cases/        # 成功/失败的经验案例 (How to solve?)
        │   └── patterns/     # 抽象的架构模式、技术规范 (Why this way?)
        └── index/            # 项目级索引 (向量索引 / 关键词索引 / BM25)
```

### 2.4 记忆作用域说明 (Scope Semantics)

| 作用域 | 目录 | 包含分类 | 语义 |
| :--- | :--- | :--- | :--- |
| **Global Scope** | `~/.nimbus/fs/global/` | `profile`, `preferences` | 用户级偏好与身份，跨所有项目全局生效 |
| **Project Scope** | `~/.nimbus/fs/projects/<project-id>/` | `entities`, `events`, `cases`, `patterns` | 项目相关记忆，严格隔离在对应项目目录下 |

**设计理由**：`profile` 和 `preferences` 描述的是"我是谁、我喜欢怎么做"，这些属于用户层面的元数据，天然应全局共享；而 `entities`、`events` 等是与特定代码库或任务深度耦合的知识，混在一起会产生跨项目污染。

---

## 3. 内容分层与 Token 经济学 (Hierarchical Storage & Token Economics)

NimFS 通过三层设计平衡存储密度与检索成本：

| 层级 | 文件后缀 | 描述 | 设计意图 |
| :--- | :--- | :--- | :--- |
| **L0 (Abstract)** | `.abstract` | **纯文本语义摘要**。极简、高压缩比。 | 核心结论的秒速加载，单条通常 < 100 Tokens。 |
| **L1 (Overview)** | `.overview.md` | **结构化 Markdown**。包含上下文、原因和结果。 | 提供足够背景信息，用于 RAG 检索后的重排序。 |
| **L2 (Content)** | `.content.md` | **原始完整数据**。详细的 Trace 或日志。 | 仅在需要深入溯源 (Root Cause Analysis) 时按需加载。 |

**关键决策说明：**

- **为什么不用纯向量数据库？** 向量搜索缺乏确定性且难以手动纠错。文件系统天然支持版本控制、人工干预和跨平台迁移。
- **为什么保留 Memo Tool？** Memo 是主动记忆的入口，而 NimFS 是自动记忆的底层设施。
- **为什么 L0 用纯文本？** 为了在 `Anchor` 中以最低的 Token 成本注入最大密度的历史结论。

---

## 4. 记忆数据模型 (Memory Data Model)

> **v2 新增**：补充 `valid_from`、`confidence`、`source` 字段，以支持时效性判断与冲突消解，解决 ReviewCommittee C-3 问题。

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Literal

class MemoryCategory(str, Enum):
    # Global Scope
    PROFILE     = "profile"
    PREFERENCES = "preferences"
    # Project Scope
    ENTITIES    = "entities"
    EVENTS      = "events"
    CASES       = "cases"
    PATTERNS    = "patterns"

class MemoryScope(str, Enum):
    GLOBAL  = "global"
    PROJECT = "project"

class MemoryStatus(str, Enum):
    """写入状态机 (v2 新增，解决 C-1 一致性问题)"""
    PENDING       = "pending"       # L2 已持久化，L0/L1 尚未生成
    MATERIALIZED  = "materialized"  # L0/L1 异步生成完成
    INDEXED       = "indexed"       # 已写入向量/关键词索引
    COMMITTED     = "committed"     # 完整事务提交，可安全检索

@dataclass
class MemoryEntry:
    # --- 核心标识 ---
    memory_id:   str             # UUID v4
    category:    MemoryCategory
    scope:       MemoryScope
    project_id:  Optional[str]   # Global Scope 时为 None

    # --- 内容层 ---
    l0_abstract: Optional[str]   # 纯文本摘要 (异步填充)
    l1_overview: Optional[str]   # 结构化 Markdown (异步填充)
    l2_content:  str             # 原始完整内容 (write() 同步写入)

    # --- 时效性与可信度 (v2 新增) ---
    valid_from:  datetime        # 记忆生效时间，支持时序冲突消解
    valid_until: Optional[datetime] = None  # None 表示永久有效
    confidence:  float = 1.0    # [0.0, 1.0]，LLM 蒸馏时可降权不确定推断
    source:      str = "agent"  # 记忆来源: "agent" | "user" | "compressor" | "defrag"

    # --- 状态机 (v2 新增) ---
    status:      MemoryStatus = MemoryStatus.PENDING

    # --- 访问热度 ---
    created_at:  datetime = field(default_factory=datetime.utcnow)
    accessed_at: datetime = field(default_factory=datetime.utcnow)
    access_count: int = 0
```

---

## 5. 核心组件 API (Core API Definition)

### 5.1 NimFSManager

```python
class NimFSManager:
    """
    NimFS 核心协调器，管理 L0/L1/L2 生命周期与双作用域记忆。
    
    初始化时必须提供 workspace_path，用于自动推导 project_id。
    """

    def __init__(self, workspace_path: str, base_dir: str = "~/.nimbus/fs/"):
        """
        :param workspace_path: 当前工作区的绝对路径，用于生成 project_id。
        :param base_dir:       NimFS 全局根目录，默认 ~/.nimbus/fs/。
        """
        self.project_id = get_project_id(workspace_path)
        self.base_dir   = os.path.expanduser(base_dir)

    # ------------------------------------------------------------------
    # 写入 (Write) — 解决 C-1 一致性 & C-2 阻塞问题
    # ------------------------------------------------------------------

    def write(
        self,
        category: MemoryCategory,
        content:  str,
        metadata: dict = None,
        confidence: float = 1.0,
        source: str = "agent",
    ) -> str:
        """
        写入新记忆。采用"立即持久化 L2，异步生成 L0/L1"的两阶段策略。

        执行流程：
          1. 同步写入 L2 (content.md)，状态置为 PENDING。
          2. 将 L0/L1 生成任务投递至后台队列 (DistillationQueue)，立即返回。
          3. 后台 Worker 完成蒸馏后，状态依次推进：
             PENDING → MATERIALIZED → INDEXED → COMMITTED。

        :param category:   记忆分类（决定写入 global 或 project 作用域）。
        :param content:    原始完整内容，写入 L2。
        :param metadata:   附加元数据，合并至 MemoryEntry。
        :param confidence: 可信度权重，影响检索排序与冲突消解。
        :param source:     记忆来源标识。
        :return:           memory_id (UUID)，可用于后续轮询状态。
        """
        pass

    def get_status(self, memory_id: str) -> MemoryStatus:
        """查询写入状态机的当前状态（仅 COMMITTED 的记忆参与检索）"""
        pass

    # ------------------------------------------------------------------
    # 读取 (Read)
    # ------------------------------------------------------------------

    def read(self, memory_id: str, layer: int = 1) -> str:
        """
        读取指定层级的记忆内容。
        :param layer: 0=abstract, 1=overview, 2=content
        """
        pass

    # ------------------------------------------------------------------
    # 检索 (Search)
    # ------------------------------------------------------------------

    def search(
        self,
        query:   str,
        top_k:   int = 5,
        scope:   Optional[MemoryScope] = None,
        category: Optional[MemoryCategory] = None,
        min_confidence: float = 0.0,
    ) -> List[MemoryEntry]:
        """
        混合检索：向量相似度 + 关键词匹配 (BM25)。
        仅返回状态为 COMMITTED 的记忆，避免半写入污染。

        :param scope:          限定搜索作用域，None 表示同时搜索 global + project。
        :param min_confidence: 过滤低置信度记忆，防止行为漂移（解决 C-3）。
        """
        pass

    # ------------------------------------------------------------------
    # 上下文加载 (Context Loading) — 双作用域合并
    # ------------------------------------------------------------------

    def load_context(self, current_goal: str) -> str:
        """
        根据当前目标，为 Nimbus Anchor 组装最优的 Context 注入包。

        加载策略（按优先级顺序）：
          1. [Global]  加载 global/profile 的全部 L0 摘要。
          2. [Global]  加载 global/preferences 的全部 L0 摘要。
          3. [Project] 基于 current_goal 语义检索 project scope 的 Top-K 记忆 (L1)。
          4. 合并 global + project 两个 scope，按 (confidence × recency) 排序。
          5. 在 Token 预算内截断，优先保留高置信度、高热度的记忆。

        :param current_goal: 当前任务目标文本，驱动 project scope 的语义检索。
        :return:             已格式化的 Markdown 字符串，可直接注入 PinnedContext。
        """
        pass

    # ------------------------------------------------------------------
    # 维护 (Maintenance)
    # ------------------------------------------------------------------

    def touch(self, memory_id: str):
        """更新记忆的访问热度 (Recency)，用于 LRU 淘汰算法。"""
        pass

    def defrag(self):
        """
        碎片整理：
          - 合并相似记忆 (Deduplication)，防止知识污染。
          - 对 valid_until 已过期的记忆降权或归档。
          - 对低 confidence 且低 access_count 的僵尸记忆执行软删除。
        """
        pass
```

### 5.2 DistillationQueue（异步蒸馏队列）

> **v2 新增**：解决 ReviewCommittee C-2（LLM 蒸馏阻塞主线程）问题。

```python
class DistillationQueue:
    """
    后台异步蒸馏队列。
    
    职责：
      - 接收来自 write() 的 L2 蒸馏任务。
      - 使用轻量 LLM (如 GPT-4o-mini) 异步生成 L0/L1。
      - 生成完成后推进状态机至 MATERIALIZED，触发索引写入后推进至 COMMITTED。
    
    实现策略：
      - 单进程内使用 asyncio.Queue + background task。
      - 多进程/分布式场景可替换为 Redis Queue 或 Celery。
    """

    async def enqueue(self, memory_id: str, l2_content: str, category: MemoryCategory):
        """投递蒸馏任务，立即返回，不阻塞调用方。"""
        pass

    async def _worker(self):
        """后台 Worker 循环，消费队列并执行 LLM 蒸馏。"""
        pass
```

### 5.3 写入状态机流转图 (State Machine Diagram)

```
  write() 调用
      │
      ▼
  ┌─────────┐    L2 文件写入完成     ┌───────────────┐
  │ PENDING │ ─────────────────────► │ (enqueue 投递) │
  └─────────┘                        └───────┬────────┘
                                             │ 异步 Worker 完成 L0/L1 生成
                                             ▼
                                    ┌──────────────┐
                                    │ MATERIALIZED │
                                    └──────┬───────┘
                                           │ 索引写入完成
                                           ▼
                                    ┌──────────┐
                                    │ INDEXED  │
                                    └─────┬────┘
                                          │ 事务确认
                                          ▼
                                    ┌───────────┐
                                    │ COMMITTED │  ◄── 仅此状态参与 search()
                                    └───────────┘
```

---

## 6. load_context() 双作用域合并逻辑 (Dual-Scope Context Loading)

```
load_context(current_goal)
        │
        ├──► [Global Scope]
        │         ├── global/profile    → 全量加载 L0 摘要
        │         └── global/preferences → 全量加载 L0 摘要
        │
        └──► [Project Scope]
                  └── projects/<project-id>/memory/
                            ├── entities   ┐
                            ├── events     │ 基于 current_goal
                            ├── cases      │ 语义检索 Top-K (L1)
                            └── patterns   ┘
                                  │
                                  ▼
                    合并 global + project 结果
                    按 (confidence × recency_score) 降序排列
                    在 Token 预算内截断
                                  │
                                  ▼
                    返回格式化 Markdown → 注入 PinnedContext (Anchor)
```

**Token 预算分配建议**：

| 区域 | 预算 | 说明 |
| :--- | :--- | :--- |
| Global profile | ≤ 500 tokens | 极简身份摘要 |
| Global preferences | ≤ 1,000 tokens | 全局行为约束 |
| Project memories (L1) | ≤ 3,000 tokens | 语义相关的项目知识 |
| **合计** | **≤ 4,500 tokens** | 占 `pinned_budget` (10k) 的 45%，余量留给系统规则 |

---

## 7. 集成路线图 (Integration Strategy)

NimFS v2 作为 Nimbus MMU 的底层支撑，集成于以下关键节点：

1. **Compaction（压缩机制）**：在 `SessionCompressor` 触发时，不再只是简单丢弃 Token，而是将即将移除的 `StackFrame` 通过 `NimFS.write()` 进行蒸馏存储。由于 `write()` 已改为异步，此过程不阻塞压缩主流程。

2. **AgentOS.spawn()**：智能体初始化时，调用 `NimFS.load_context()` 预加载 `global/profile` 和 `global/preferences` 到 `PinnedContext`，同时注入当前 project 的相关记忆。

3. **Memo Tool 重构**：Memo Tool 变为 NimFS 的前端接口，支持 `@` 符号检索特定类别的记忆；写入时自动判断 scope（用户偏好 → global，项目知识 → project）。

4. **SessionCompressor**：作为压缩算法的后端，确保"丢失的信息"已在磁盘中妥善备份，且通过状态机保证仅 `COMMITTED` 的记忆才被纳入下一次 `load_context()`。

5. **defrag() 调度**：建议在 `AgentOS.spawn()` 的冷启动阶段，或会话数量超过阈值时，后台触发 `defrag()`，清理过期和低质量记忆，避免行为漂移（解决 C-3）。

---

## 8. 实现路线图 (Roadmap)

### Phase 0: Foundation（Day 1–3）

- [ ] 实现 `get_project_id(workspace_path)` 工具函数，覆盖单元测试。
- [ ] 建立 `~/.nimbus/fs/global/` 与 `~/.nimbus/fs/projects/` 目录规范。
- [ ] 实现 `NimFSManager.__init__()` 的路径解析与目录初始化逻辑。
- [ ] 实现 `MemoryEntry` 数据模型，支持序列化为 JSON sidecar 文件。

### Phase 1: Write Pipeline & State Machine（Day 4–7）

- [ ] 实现同步 L2 写入 + `DistillationQueue` 异步投递（asyncio 版本）。
- [ ] 实现状态机四步流转：`PENDING → MATERIALIZED → INDEXED → COMMITTED`。
- [ ] 集成轻量 LLM（GPT-4o-mini）执行 L0/L1 自动蒸馏。
- [ ] 实现 `get_status(memory_id)` 轮询接口。

### Phase 2: Retrieval & Context Loading（Day 8–12）

- [ ] 集成 `sqlite-vec` 或轻量级向量库，实现向量 + BM25 混合检索。
- [ ] 实现 `load_context()` 双作用域合并逻辑，支持 Token 预算截断。
- [ ] 实现 `min_confidence` 过滤，防止低质量记忆影响检索结果。
- [ ] 实现记忆去重逻辑（Deduplication）。

### Phase 3: Maintenance & Integration（Day 13–15）

- [ ] 实现 `defrag()`：过期清理、相似合并、僵尸记忆软删除。
- [ ] 将 NimFS v2 挂载至 `Nimbus.CoreAgent`，替换 v1 的 workspace-local 路径。
- [ ] 完整闭环测试：跨 session 记忆持久化 → 新 session 召回验证。
- [ ] Memo Tool 重构，接入 NimFS v2 双作用域写入。

---

## 9. v1 → v2 变更对照表 (Change Log)

| 维度 | v1 设计 | v2 修订 | 变更原因 |
| :--- | :--- | :--- | :--- |
| **存储位置** | `{workspace}/.nimbus/fs/` | `~/.nimbus/fs/` | 天然跨 session 共享，不随 workspace 消失 |
| **Project 识别** | 无显式 Project ID | `get_project_id(path)` 路径转换 | 与 Claude 策略一致，确定性且人类可读 |
| **作用域** | 单一平铺结构 | Global + Project 双作用域 | 用户级偏好与项目知识应分离管理 |
| **load_context()** | 单一 workspace 检索 | 合并 global + project 两个 scope | 确保全局偏好和项目知识均被注入 Anchor |
| **write() 执行** | 同步阻塞（含 LLM 蒸馏） | L2 同步写入，L0/L1 异步蒸馏 | 解决 C-2：不阻塞 Agent 主线程 |
| **一致性模型** | 无事务保证 | 四状态状态机 (PENDING→COMMITTED) | 解决 C-1：防止半写入记忆污染检索 |
| **记忆数据模型** | 无时效/置信度字段 | 增加 `valid_from`, `confidence`, `source` | 解决 C-3：支持冲突消解与时效性管理 |
| **search()** | 无过滤条件 | 增加 `scope`、`min_confidence` 过滤 | 防止低质量、跨项目记忆干扰检索 |

---

*Created by Architect Agent for the Nimbus Project.*
*Based on nimfs-design.md (v1) + ReviewCommittee Critical Issues (C-1, C-2, C-3).*
