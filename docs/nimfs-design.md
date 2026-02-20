# NimFS: 智能体多层级存储文件系统技术规格书 (Technical Specification)

## 1. 背景与动机 (Background & Motivation)

### 冯·诺依曼类比 (Von Neumann Analogy)
在传统计算机架构中，计算与存储分离。为解决 Autonomous Agents 在长程任务中的“上下文漂移”与“记忆碎片化”问题，NimFS 将 LLM 视为 **CPU**，并将存储层次进行如下映射：

| 计算机组件 | 智能体对应物 | 说明 |
| :--- | :--- | :--- |
| **寄存器 (Registers)** | **Current Prompt** | 当前正在处理的 Token 片段，极速但容量极小。 |
| **一级缓存 (L1 Cache)** | **Working Context** | 上下文窗口中的活跃部分，包含系统指令和最近对话。 |
| **内存 (RAM)** | **Session Stream** | 当前会话的完整历史，通过 `StackFrame` 管理，随任务结束释放。 |
| **磁盘 (Disk/SSD)** | **NimFS** | 持久化存储层，提供结构化、可检索的长期记忆。 |

### OpenViking 启发 (OpenViking Inspiration)
NimFS 汲取了 **OpenViking** 项目中关于长期记忆分类与分层管理的精华，将其 6 分类记忆体系（Profile, Preferences, Entities, Events, Cases, Patterns）与 L0/L1/L2 结构深度融合，构建了一个既符合认知科学又具备工业级性能的存储层。

---

## 2. 存储架构与目录结构 (Architecture & Directory Structure)

NimFS 强制要求在工作区根目录建立以下结构：

```text
{workspace}/.nimbus/fs/
├── memory/
│   ├── profile/      # 智能体自身角色定义、元数据 (Who am I?)
│   ├── preferences/  # 用户偏好、样式指南、约束条件 (How to act?)
│   ├── entities/     # 核心对象、组件、文件关联 (What is it?)
│   ├── events/       # 关键状态变更、里程碑记录 (What happened?)
│   ├── cases/        # 成功/失败的经验案例 (How to solve?)
│   └── patterns/     # 抽象的架构模式、技术规范 (Why this way?)
└── index/            # 索引层 (向量索引/关键词索引/BM25)
```

---

## 3. 内容分层与 Token 经济学 (Hierarchical Storage)

NimFS 通过三层设计平衡存储密度与检索成本：

| 层级 | 文件后缀 | 描述 | 设计意图 (Design Intent) |
| :--- | :--- | :--- | :--- |
| **L0 (Abstract)** | `.abstract` | **纯文本语义摘要**。极简、高压缩比。 | 核心结论的秒速加载，单条通常 < 100 Tokens。 |
| **L1 (Overview)** | `.overview.md` | **结构化 Markdown**。包含上下文、原因和结果。 | 提供足够的背景信息，用于 RAG 检索后的重排序。 |
| **L2 (Content)** | `content.md` | **原始完整数据**。详细的 Trace 或日志。 | 仅在需要深入溯源（Root Cause Analysis）时按需加载。 |

**关键决策说明：**
- **为什么不用纯向量数据库？** 向量搜索缺乏确定性且难以手动纠错。文件系统天然支持版本控制、人工干预和跨平台迁移。
- **为什么保留 Memo Tool？** Memo 是主动记忆的入口，而 NimFS 是自动记忆的底层设施。
- **为什么 L0 用纯文本？** 为了在 `Anchor` 中以最低的 Token 成本注入最大密度的历史结论。

---

## 4. 核心组件 API (Core API Definition)

```python
class NimFSManager:
    """NimFS 核心协调器，管理 L0/L1/L2 生命周期"""
    
    def write(self, category: MemoryCategory, content: str, metadata: dict = None) -> str:
        """
        写入新记忆。自动触发 L0/L1/L2 分层生成。
        :param category: profile, preferences, entities, events, cases, patterns
        """
        pass

    def read(self, memory_id: str, layer: int = 1) -> str:
        """读取指定层级的记忆内容"""
        pass

    def search(self, query: str, top_k: int = 5) -> List[MemoryEntry]:
        """混合检索：向量相似度 + 关键词匹配"""
        pass

    def touch(self, memory_id: str):
        """更新记忆的访问热度（Recency），用于淘汰算法"""
        pass

    def defrag(self):
        """碎片整理：合并相似记忆 (Deduplication) 并清理低频无效信息"""
        pass

    def load_context(self, current_goal: str) -> str:
        """根据当前目标，为 Nimbus Anchor 组装最优的 Context 注入包"""
        pass
```

---

## 5. 集成路线图 (Integration Strategy)

NimFS 作为 Nimbus MMU 的底层支撑，集成于以下关键节点：

1.  **Compaction (压缩机制)**：在 `SessionCompressor` 触发时，不再只是简单丢弃 Token，而是将即将移除的 `StackFrame` 通过 `NimFS.write()` 进行蒸馏存储。
2.  **AgentOS.spawn()**：智能体初始化时，调用 `NimFS.load_context()` 预加载 `profile` 和 `preferences` 到 `PinnedContext`。
3.  **Memo Tool 重构**：Memo Tool 变为 NimFS 的前端接口，支持 `@` 符号检索特定类别的记忆。
4.  **SessionCompressor**：作为压缩算法的后端，确保“丢失的信息”已在磁盘中妥善备份。

---

## 6. 实现路线图 (Roadmap)

### Phase 0: Foundation (Day 1-3)
- [ ] 定义文件系统规范，建立 `.nimbus/fs` 结构。
- [ ] 实现 `NimFSManager` 基础的 CRUD (文件操作层)。
- [ ] 支持基于文件名的关键词搜索。

### Phase 1: Hierarchical Engine (Day 4-7)
- [ ] 开发 `L0/L1/L2` 自动生成逻辑（集成 GPT-4o-mini 进行蒸馏）。
- [ ] 实现记忆去重逻辑 (Deduplication)，防止知识污染。

### Phase 2: Intelligence & Retrieval (Day 8-12)
- [ ] 集成 `sqlite-vec` 或轻量级向量库。
- [ ] 实现 `load_context` 逻辑，支持基于当前 Goal 的语义感知检索。

### Phase 3: System Integration (Day 13-15)
- [ ] 将 NimFS 挂载至 `Nimbus.CoreAgent`。
- [ ] 完整闭环测试：长程任务中，自动将历史栈帧持久化并在需要时召回。

---
*Created by Executor Agent for the Nimbus Project.*
