# Nimbus V3 内存系统架构提案

**文档版本**: v1.1  
**日期**: 2026-03-05  
**作者**: Architect  
**审阅**: Implementer Agent  
**状态**: 草案 (RFC · 已经批判性审阅，待修订确认)  
**前序文档**:
- V2 架构分析: `docs/design/mmu-architecture-analysis.md`
- 记忆改进方案 v2: `docs/memory-upgrade-plan-v2.md`
- 无限上下文洞察: `docs/infinite-context-insight.md`
- 对比报告: `docs/memory-comparison-report.md`

---

## 0. TL;DR

> Nimbus V2 的 MMU 是优秀的**会话内上下文管理器**，但它缺乏真正的**跨会话记忆**。
> V3 的核心目标是：在不破坏 V2 简洁性的前提下，引入三层认知记忆模型（情景 / 语义 / 程序化），让 Agent 真正做到"用过就会、见过就认、做过不忘"。

**一句话变化**：从 `Rolling Buffer + Summary` → `三层记忆 + 智能检索 + 主动巩固`。

---

## 1. 背景与动机

### 1.1 V2 的成就

Nimbus V2 MMU 实现了：
- ✅ **Anchor-Stream 分层**：锚点与消息流解耦，目标不漂移
- ✅ **NimFS Offload**：超大工具输出自动外存，显著延缓 Token 消耗
- ✅ **Smart Drop**：基于信息价值而非时间的丢弃策略
- ✅ **StateManager**：文件工作集 + 命令状态的确定性追踪

### 1.2 V2 的核心痛点

经过深度运行观测，V2 存在三类不可忽视的结构性缺陷：

#### 痛点 A：信息熵的不可逆损失（最严重）

```
时刻 T1: Agent 读取 vcpu.py (5000 tokens)
时刻 T2: Token 超限 → archive_and_reset()
         vcpu.py 内容 → 被摘要为 "读取并分析了 vcpu.py" (50 tokens)
时刻 T3: Agent 需要 vcpu.py 中的某个函数签名
         → 无法回忆，只能重新 Read → 浪费一次工具调用
```

**根本原因**：`archive_and_reset` 名不副实——旧消息被丢弃而非真正归档。

#### 痛点 B：跨会话知识从零开始（最浪费）

每次新会话，Agent 都要从头探索代码库：项目结构、关键接口、踩过的坑……这些知识在 NimFS Memory 中有留存，但没有被系统性地组织和注入。用户体感：Agent "永远学不会"。

#### 痛点 C：记忆读写不对称（最低效）

V2 有隐式写（Summary 自动生成）但没有显式的**语义检索**——Agent 无法说"我好像之前遇到过这个问题"，然后主动去回忆。现有 Memo 工具是纯文本追加，没有结构化索引。

---

## 2. 设计哲学

### 2.1 认知科学与 2026 前沿映射

结合 2026 年 LLM Agent Memory 的最新实践（Zettelkasten/A-Mem、Hierarchical Subgoals、Stateful Layered Tiers），对人类记忆的三系统模型（Tulving 1972, Baddeley 2012）进行升级映射：

```
人类记忆             2026 架构映射                             Nimbus V3 实现
──────────────────────────────────────────────────────────────────────────────────────────
情景记忆 (Episodic)  ← Event Segmentation / Semantic Episodes   [EM] 动态会话档案 + 效用阈值清理
语义记忆 (Semantic)  ← A-Mem / Zettelkasten (动态图谱)         [SM] 结构索引 + 反向回溯更新 (Retroactive)
程序化记忆 (Procedural) ← Stateful 技能流 / 行动观测对          [PM] 技能库 + 观测总结 (Action-Observation)
工作记忆 (Working)   ← Hierarchical Subgoals (分层子目标)      [WM] 树状 Anchor + 状态片段 (KV Cache 友好)
```

V3 并非重写 V2，而是在 V2 的基础上，融入**动态关联（Associative Linking）**、**分层工作区（Hierarchical Chunks）**和**反向重塑（Retroactive Refinement）**三大 2026 核心机制。

### 2.2 设计原则

1. **简单优先**：新增组件优先复用现有基础设施（NimFS、SQLite），避免引入新依赖
2. **渐进增强**：V2 行为作为 fallback，新机制 opt-in
3. **可观测性**：每次记忆操作可追踪、可回滚
4. **低延迟优先**：记忆检索不能成为主链路瓶颈（< 50ms）

### 2.3 前沿研究对齐 (2024/2025)

1. **Memory Evolution (Retroactive Refinement)**: 记忆不只是被动追加 (Append-only)。根据 Xu et al. (Feb 2025) 的研究，新经验会主动、逆向地精炼和重组已有的语义与情景记忆，形成动态树状的"层级图谱 (Hierarchical Schemas)"，使记忆随经验积累而越发精确。
2. **Hierarchical Working Memory**: 根据 HiAgent (Hu et al., 2024) 的方法，工作记忆 (WM) 不能是扁平的 token 流。在 Subgoal (子目标) 边界，细粒度的 "Action-Observation" 对将被总结提炼，形成层级化、上下文相关的结构。这从理论上补强了 V2 的 StackFrames 理念。
3. **Utility-based Eviction & Event Segmentation**: 放弃单纯的 FIFO 或基于阈值的 Drop 策略。根据 2024/2025 年多项研究 (Xiong et al., Yin et al., Mem0/Nemori)，应采用基于效用 (Utility-based) 和检索历史的淘汰策略来防止记忆膨胀。同时，对连续事件流进行"语义分段 (Event Segmentation)"，构建支持多跳和时序检索的片段网络。

---

## 3. V3 架构总览

### 3.1 整体内存层级

```
┌══════════════════════════════════════════════════════════════════╗
║                    WORKING MEMORY (WM)                           ║
║  ┌─────────────────────────────────────────────────────────┐    ║
║  │  Anchor (Pinned)         ~10k tokens                    │    ║
║  │  ├── System Rules                                       │    ║
║  │  ├── User Goal                                          │    ║
║  │  ├── [NEW] Semantic Snapshot (从 SM 注入的知识摘要)     │    ║
║  │  └── [NEW] Procedural Hints (当前任务的最佳实践提示)   │    ║
║  ├─────────────────────────────────────────────────────────┤    ║
║  │  Global Summary          ~800 tokens                    │    ║
║  │  └── [ENHANCED] 包含 Episodic Pointers (档案引用)       │    ║
║  ├─────────────────────────────────────────────────────────┤    ║
║  │  Environment State       ~500 tokens                    │    ║
║  │  └── 文件工作集 + 命令状态 (V2 StateManager 不变)       │    ║
║  ├─────────────────────────────────────────────────────────┤    ║
║  │  Message Stream          budget-based                   │    ║
║  │  └── Hot Messages + NimFS Offloaded refs                │    ║
║  └─────────────────────────────────────────────────────────┘    ║
╠══════════════════════════════════════════════════════════════════╣
║                  EPISODIC MEMORY (EM)                            ║
║  存储: NimFS + SQLite                                            ║
║  ├── Session Archives: 压缩后的完整会话片段                      ║
║  ├── Tool Call Log: 结构化工具调用记录 (tool/args/result/ts)     ║
║  └── Decision Log: 关键决策点记录 (why + what + outcome)        ║
╠══════════════════════════════════════════════════════════════════╣
║                  SEMANTIC MEMORY (SM)                            ║
║  存储: NimFS Memory (现有) + FTS5 索引                           ║
║  ├── entities: 文件、模块、接口的知识条目                        ║
║  ├── patterns: 设计模式、最佳实践                                ║
║  ├── cases: Bug 修复案例、解决方案                               ║
║  └── [NEW] Derived Index: 自动构建的关键词→条目映射              ║
╠══════════════════════════════════════════════════════════════════╣
║               PROCEDURAL MEMORY (PM)                             ║
║  存储: skills/ 目录 + YAML 配置                                  ║
║  ├── Skills: 可复用的工具调用序列                                ║
║  ├── Workflow Templates: 任务类型→执行模板映射                   ║
║  └── [NEW] Auto-learned Shortcuts: 频繁操作序列自动提炼          ║
╚══════════════════════════════════════════════════════════════════╝
```

### 3.2 层间流转机制

```
                    记忆流转示意图
                    
  ┌─────────────────────────────────────────┐
  │              WM (工作记忆)               │
  │         Anchor + Stream                 │
  └───────┬──────────────────┬─────────────┘
          │                  │
     写入(Consolidation)   读取(Retrieval)
          │                  │
  ┌───────▼──────────────────▼─────────────┐
  │           Memory Router                 │  ← 新增核心组件
  │  根据内容类型决定流向/来源              │
  └───┬─────────┬────────────┬─────────────┘
      │         │            │
      ▼         ▼            ▼
   [EM]      [SM]          [PM]
  情景档案  语义知识库    程序化技能库
```

---

## 4. 核心新增组件详解

### 4.1 MemoryRouter（核心路由器）

**职责**：所有记忆操作的统一入口，决定内容流向哪一层、何时触发巩固。

```python
class MemoryRouter:
    """
    V3 记忆路由器：统一协调 WM/EM/SM/PM 四层记忆的读写。
    """
    
    async def consolidate(self, event: MemoryEvent) -> None:
        """
        巩固阶段：将工作记忆中的内容固化到长期记忆。
        由 CompactionService 在 archive_and_reset 或 Subgoal 完成时触发。
        采用分层工作记忆 (Hierarchical Working Memory) 机制，
        总结细粒度 action-observation，形成层次化的上下文片段。
        
        路由规则:
        - tool_result(Write/Edit) → EM.tool_call_log + SM.entities 异步演化
        - decision_point         → EM.decision_log
        - bug_fix                → SM.cases
        - skill_usage > 3次      → PM.learned_shortcuts
        """
        ...
    
    async def retrieve(self, query: RetrievalQuery) -> MemoryContext:
        """
        检索阶段：根据当前任务意图，从长期记忆组装注入内容。
        在 assemble_context() 开始时调用。
        
        检索策略:
        1. 语义相似度（FTS5 全文检索）→ SM 条目
        2. 近期档案引用              → EM 最近 N 个 session archives
        3. 任务类型匹配              → PM workflow templates
        
        时间限制: < 50ms (本地查询，无 LLM 调用)
        """
        ...
    
    async def reflect(self, session_summary: str) -> None:
        """
        反思阶段：会话结束时，LLM 辅助将 EM 内容提炼进 SM。
        异步执行，不阻塞主链路。
        """
        ...
```

### 4.2 EpisodicStore（情景记忆存储）

**职责**：对 V2 `archive_and_reset` 的关键补充——让"归档"真实发生。

```python
@dataclass
class EpisodicEntry:
    session_id: str
    part_id: int                    # 会话内第几次压缩
    timestamp: datetime
    messages: List[Message]         # 完整消息（存 NimFS）
    nimfs_ref: str                  # 指向 NimFS artifact
    summary: str                    # 50 字以内摘要
    tags: List[str]                 # 自动提取的关键词标签
    decision_points: List[str]      # 本段的重要决策摘要

class EpisodicStore:
    """
    基于 SQLite（索引）+ NimFS（内容）的情景记忆。
    SQLite 只存元数据，完整内容在 NimFS 里。
    """
    
    def archive(self, messages: List[Message], summary: str) -> EpisodicEntry:
        """
        archive_and_reset 或 Subgoal 完成时调用。将旧消息序列化到 NimFS，
        进行语义分段 (Event Segmentation)，元数据存 SQLite，返回 EpisodicEntry。
        采用基于效用 (Utility-based) 的策略，不再依赖简单的 FIFO。
        """
        ...
    
    def query_recent(self, n: int = 5) -> List[EpisodicEntry]:
        """获取最近 n 个情景档案的摘要（不加载全文）"""
        ...
    
    def evict_low_utility(self) -> None:
        """
        [2025 新增特性]: 效用驱逐策略。
        根据检索历史和使用频率，清理低价值情景片段，防止记忆膨胀。
        """
        ...
    
    def recall(self, keywords: List[str]) -> List[EpisodicEntry]:
        """基于关键词和语义片段进行多跳、时序回忆"""
        ...
```

**与 V2 的差异**：V2 的 `archive_and_reset` 在截断后直接丢弃旧消息；V3 在截断前先序列化到 `EpisodicStore`，然后在 `Global Summary` 末尾追加一行 `[Archive: part_N 已归档，含 <tags>，可通过 RecallMemory 工具读取]`。

### 4.3 SemanticIndex（语义记忆索引增强）

**职责**：在现有 NimFS Memory 基础上，增加 FTS5 全文检索索引，提升知识检索效率。

**现状问题**：
- NimFS Memory 写入后，只能按 tag/category 过滤，无法语义搜索
- 同一类知识分散在多个条目，缺乏关联

**V3 增强方案**：

```
SQLite Schema（新增 semantic_index 表）
─────────────────────────────────────────
memory_fts (FTS5):
  - entry_id    TEXT   ← NimFS memory entry ID
  - title       TEXT
  - content     TEXT   ← 摘要 + 标签的合并文本
  - category    TEXT
  - tags        TEXT
  - created_at  REAL

触发器：每次 NimFSWriteMemory 后自动更新 FTS5 索引
```

```python
class SemanticIndex:
    def search(self, query: str, top_k: int = 5, 
               category: Optional[str] = None) -> List[MemoryEntry]:
        """
        FTS5 全文检索，< 10ms 响应。
        返回相关度排序的 memory entries（仅 L0 摘要，不加载全文）。
        """
        ...
    
    def get_anchor_injection(self, goal: str, budget: int = 2000) -> str:
        """
        根据当前 goal，检索最相关的 SM 条目，
        生成适合注入 Anchor 的精简 Markdown（严格控制在 budget tokens 内）。
        """
        ...
    
    def retroactive_refinement(self, new_entry: MemoryEntry) -> None:
        """
        [2025 新增特性]: 逆向重塑。
        当收到新的经验片段时，异步扫描库中相似条目，进行归纳融合，
        形成"层次化 Schema"而非单纯堆叠。
        """
        ...
```

### 4.4 ProceduralHints（程序化记忆提示）

**职责**：将"做过的事怎么做"编码为可复用的提示注入 Anchor。

这是最轻量的新增模块，实质上是对现有 `skills/` 目录的结构化包装：

```yaml
# skills/workflow/code-debug.yaml (示例)
name: "代码调试工作流"
trigger_keywords: ["bug", "error", "fix", "pytest failed"]
steps:
  - "1. 先运行 pytest 确认错误现象"
  - "2. 读取相关文件，定位问题根因"
  - "3. 修改代码，再次运行 pytest 验证"
  - "4. 如果修复正确，用 Bash 提交或记录到 NimFS Memory"
hint_text: |
  > 当前任务类型：代码调试
  > 推荐工作流：确认现象 → 定位根因 → 修复验证
  > 参考案例：[SM: cases/bugfix-*]
```

---

## 5. 关键改进流程

### 5.1 增强版 archive_and_reset

```
V2 流程:                          V3 流程（新增步骤用 [NEW] 标注）:
─────────────────────────         ─────────────────────────────────────
Token 超 85% 阈值                 Token 超 85% 阈值
       ↓                                  ↓
切分 [旧消息] + [保留消息]         切分 [旧消息] + [保留消息]
       ↓                                  ↓
LLM 生成摘要                       LLM 生成摘要（附加结构化提取）
       ↓                           [NEW] 提取 decision_points + tags
丢弃旧消息                         [NEW] EpisodicStore.archive(旧消息)
                                          → 序列化到 NimFS
                                          → 元数据存 SQLite
       ↓                                  ↓
更新 Global Summary                更新 Global Summary
                                   [NEW] 追加 Episodic Pointer:
                                         "[Archive ref=nimfs://xxx tags=[...]]"
       ↓                                  ↓
       ↓                           [NEW] MemoryRouter.consolidate()
                                         → 异步提取 SM 更新项
                                         → 写入 semantic_index
重置上下文                         重置上下文
```

### 5.2 增强版 assemble_context

```
V2 assemble_context:              V3 assemble_context:
─────────────────────             ─────────────────────────────────────
                                  [NEW] Step 0: Memory Retrieval
                                    MemoryRouter.retrieve(current_goal)
                                    → 检索 SM: top-3 相关知识条目
                                    → 检索 EM: 最近 2 个 session 摘要
                                    → 匹配 PM: workflow hints
                                    （总 budget: 2000 tokens, < 50ms）
                                          ↓
Step 1: Collect Anchor            Step 1: Collect Enhanced Anchor
  System Rules                      System Rules
  User Goal                         User Goal
  NimFS L0 摘要                     [ENHANCED] SM 检索结果注入
                                     [NEW] PM workflow hints
                                          ↓
Step 2: Collect Summary           Step 2: Collect Summary
  Global Summary                    Global Summary
                                    [NEW] Episodic Pointers 可见
                                          ↓
Step 3: Environment State         Step 3: Environment State（不变）
                                          ↓
Step 4: Message Stream            Step 4: Message Stream（不变）
  Smart Drop / NimFS Expand         Smart Drop / NimFS Expand
```

### 5.3 新增：主动回忆（Active Recall）

为 Agent 暴露 `RecallMemory` 工具，允许在任务执行中主动检索历史：

```python
@tool
async def recall_memory(query: str, memory_type: str = "all") -> str:
    """
    主动回忆工具。从 EM/SM 检索相关历史信息。
    
    Args:
        query: 检索关键词，如 "vcpu.py 的 interrupt 逻辑"
        memory_type: "episodic" | "semantic" | "all"
    
    Returns:
        相关记忆条目的摘要（含 NimFS refs 供按需展开）
    
    Example:
        recall_memory("NimFS offload bug") 
        → 返回: 上次 session 中修复了 offload preview 质量问题
                详情: nimfs://artifact/session-xxx-part-2
    """
```

系统提示词中加入引导：
> 如果你在历史中见过类似问题，或需要查阅之前的代码/修复方案，请使用 `RecallMemory` 工具。这比重新探索代码库更高效。

---

## 6. Token 预算分配（V3）

```
总上下文窗口: 200,000 tokens
─────────────────────────────────────────────────────
区域                          V2 预算    V3 预算    说明
─────────────────────────────────────────────────────
系统规则 (Anchor-Fixed)        3,000      3,000     不变
用户目标 (Anchor-Pinned)       1,000      1,000     不变
SM 语义知识注入 (Anchor-New)       0      2,000     [NEW] 上限严格控制
PM 程序化提示 (Anchor-New)         0        500     [NEW] 仅 hints，不含全文
EM 情景档案摘要 (Anchor-New)       0      1,000     [NEW] 仅摘要+指针
Global Summary                   800        800     不变
StateManager                     500        500     不变
─────────────────────────────────────────────────────
Anchor 小计                    5,300      8,800     增加 3,500 tokens
─────────────────────────────────────────────────────
Message Stream                174,700    171,200    相应缩减
─────────────────────────────────────────────────────
注：SM/PM/EM 注入均有严格 budget cap，超出部分被截断而非溢出
```

---

## 7. 实现路线图

### Phase 0：基础设施准备（1 周）

- [ ] `EpisodicStore` 类实现（SQLite schema + NimFS 写入）
- [ ] `archive_and_reset` 改造：压缩前调用 `EpisodicStore.archive()`
- [ ] `Global Summary` 中追加 Episodic Pointer

**验收标准**：运行 1 小时长任务后，可以找到所有历史 archive 的 NimFS 引用，旧消息不再被丢弃。

### Phase 1：语义检索增强（1 周）

- [ ] `semantic_index` SQLite FTS5 表创建 + 迁移脚本
- [ ] `NimFSWriteMemory` 写入后自动更新 FTS5 索引
- [ ] `SemanticIndex.search()` 实现 + 性能测试（< 10ms）
- [ ] `NimFSSearchMemory` 工具升级为使用 FTS5

**验收标准**：`NimFSSearchMemory("NimFS offload bug")` 能精准返回相关条目，召回率比现有 tag 匹配提升 > 50%。

### Phase 2：记忆注入增强（1 周）

- [ ] `MemoryRouter.retrieve()` 实现
- [ ] `assemble_context` 集成：Step 0 注入 SM/PM/EM 摘要
- [ ] Anchor 预算精细化分配（见第 6 节）
- [ ] `recall_memory` 工具实现 + 系统提示词更新

**验收标准**：新会话开始时，Agent 能在无用户提示的情况下，在 Anchor 中自动感知到 "上次遇到过相关问题" 并主动引用。

### Phase 3：程序化记忆 + 自动巩固（2 周）

- [ ] `skills/workflow/*.yaml` 结构化定义
- [ ] `ProceduralHints` 任务类型匹配器
- [ ] `MemoryRouter.reflect()` 会话结束时异步调用
- [ ] `MemoryRouter.consolidate()` 工具调用链提炼逻辑

**验收标准**：连续 5 次调试任务后，`debug` workflow hint 自动出现在 Anchor；常见 bug 模式自动写入 SM.cases。

---

## 8. 风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| SM 注入内容相关性差，浪费 Anchor 预算 | 中 | 中 | 严格 budget cap（2000t）；相关度低于阈值时不注入 |
| EpisodicStore 写入增加 archive_and_reset 延迟 | 低 | 低 | NimFS 写入异步化；SQLite 仅存元数据（< 1ms） |
| FTS5 索引与 NimFS Memory 不一致（双写失败） | 高 | 低 | 事务写入；FTS5 可从 NimFS 重建（idempotent rebuild 命令） |
| reflect() 消耗额外 LLM 调用 | 低 | 高 | 默认只在会话正常结束时触发；单次 reflect 限 500 tokens |
| Agent 滥用 RecallMemory 导致延迟 | 中 | 低 | 工具层限速（每 turn 最多 2 次）；结果缓存 |

---

## 9. 成功指标

| 指标 | V2 基线 | V3 目标 |
|------|---------|---------|
| 长任务（> 2h）信息丢失率 | ~40% | < 10% |
| 跨会话知识复用率 | ~5% | > 40% |
| 重复探索代码库次数/任务 | ~3 次 | < 1 次 |
| archive_and_reset 额外延迟 | 0ms | < 100ms |
| 记忆检索延迟（assemble 阶段） | 0ms | < 50ms |
| 用户感知"Agent 学会了" | 罕见 | 常态 |

---

## 10. 附录：关键设计决策

### ADR-001：为何选 SQLite + NimFS 而非向量数据库？

**结论**：SQLite FTS5 在当前规模（< 10,000 条记忆）下性能足够（< 10ms），且零外部依赖，与现有架构完全兼容。向量数据库（pgvector/Chroma）在语义相似度上更强，但引入显著的部署复杂度。**等 V3 稳定后，可作为 V4 的 SM 后端升级选项。**

### ADR-002：为何 reflect() 使用 LLM 而非规则？

**结论**："从情景中提炼语义知识"本质上是归纳推理，规则难以覆盖。LLM 在这里是最合适的工具，且单次 reflect 成本极低（< $0.01），仅在会话结束时运行一次。

### ADR-003：为何不在 V3 实现完整的"主动学习"？

**结论**：自动提炼知识 + 自动更新 SM 是高价值但高风险的功能——错误的自动写入比不写更糟糕。V3 的 `reflect()` 保守实现：LLM 生成**候选条目**，由 `NimFSWriteMemory` 写入但标记为 `source=auto`，后续 UI 支持人工审核。

---

*本文档由 Nimbus Architect Agent 基于 V2 运行数据分析生成，欢迎 PR/Issue 讨论。*

---

## 附录：批判性反思（Critical Reflections）

> **审阅者**：Implementer Agent  
> **审阅日期**：2026-03-05  
> **状态**：已纳入提案修订意见

本附录是对提案进行的第二轮推演，目的是在实现前暴露三处潜在的结构性问题，并给出具体的修正建议。

---

### 反思 1：`archive_and_reset` → `EpisodicStore` 的过渡存在两个逻辑漏洞

#### 漏洞 A：Episodic Pointer 的"链式丢失"问题

提案第 4.2 节描述了一个看似完美的机制：`archive()` 结束后，在 `Global Summary` 末尾追加一行 Episodic Pointer，如：

```
[Archive ref=nimfs://artifact/session-xxx-part-2 tags=[vcpu, interrupt]]
```

**问题在于**：`Global Summary` 本身也是有 Token 上限的（提案分配 800 tokens）。在长任务中，随着多次 `archive_and_reset` 的触发，每次都会追加新的 Pointer，最终 Global Summary 本身也会被截断——**靠前的 Episodic Pointer 会率先消失**，形成"档案指针比档案本身消失得更快"的悖论。

**修正方案**：Episodic Pointer 不应存在 `Global Summary`（易挥发区域）中，而应存在 `Anchor` 的专属槽位（固定区域）。具体做法：

```
Anchor 结构（修订后）:
├── System Rules              (固定)
├── User Goal                 (固定)
├── SM 语义知识注入            (动态, budget=2000t)
├── PM 程序化提示              (动态, budget=500t)
└── [NEW] Episode Index       (固定槽位, budget=500t)
    └── 本会话所有 archive 的指针列表（仅元数据，不含全文）
        格式: [part_N | timestamp | tags | nimfs_ref]
        超出 500t 时，最旧的 part 被 compact 为一行"早期档案..."
```

这样 Episodic Pointer 受 Anchor 的"钉住"保护，不会随 Summary 轮转而丢失。

#### 漏洞 B：EpisodicStore.archive() 的失败路径未定义

`archive_and_reset` 是关键路径操作，一旦 NimFS 写入超时或失败，`EpisodicStore.archive()` 应该怎么处理？提案没有说明。

**风险**：如果 archive 失败后阻断了 `archive_and_reset`，整个 MMU 会卡死；如果直接跳过 archive，旧消息依然会被丢弃，但没有任何存档记录。

**修正方案**：明确规定 fail-safe 语义：

```python
async def archive_and_reset(self):
    # 1. 生成摘要（原有逻辑）
    summary = await self._generate_summary(old_messages)
    
    # 2. [NEW] 尝试情景归档，失败不阻断，记录警告
    try:
        entry = await episodic_store.archive(old_messages, summary, timeout=2.0)
        self.anchor.episode_index.append(entry.to_pointer())
    except EpisodicArchiveError as e:
        logger.warning(f"EpisodicStore archive failed, messages will be lost: {e}")
        # 继续执行——宁可丢失档案，不阻断主流程
    
    # 3. 重置（原有逻辑）
    self._reset_stream(keep_messages)
```

**原则**：情景记忆是"锦上添花"，不能成为单点故障。V3 必须保持 V2 在 EpisodicStore 不可用时的全功能降级。

---

### 反思 2：MemoryRouter 的职责边界过于模糊，存在隐藏的 LLM 调用

#### 问题：`consolidate()` 的路由规则需要语义理解

提案第 4.1 节给出了 `consolidate()` 的路由规则：

```
tool_result(Write/Edit) → EM.tool_call_log + SM.entities 更新
decision_point          → EM.decision_log
bug_fix                 → SM.cases
skill_usage > 3次       → PM.learned_shortcuts
```

**问题**：如何判断一条消息是 `decision_point` 还是 `bug_fix`？这不是规则判断，而是语义分类任务。如果靠关键词（如"修复了"、"决定了"），误判率极高；如果靠 LLM，则每次 `archive_and_reset` 会触发额外的 LLM 分类调用——这与提案第 2.2 节"低延迟优先"（`< 50ms`）的原则直接矛盾（一次 LLM 调用通常 500ms～2s）。

提案在 `ADR-002` 中对 `reflect()` 使用 LLM 给出了理由，但对 `consolidate()` 的分类机制完全未提及，这是设计盲区。

#### 修正方案：拆分 MemoryRouter，分离热路径与冷路径

**核心原则**：`consolidate()` 在 `archive_and_reset` 的关键路径上（热路径），必须是规则驱动、无 LLM 调用；`reflect()` 在会话结束时（冷路径），可以使用 LLM。

```
原设计（MemoryRouter 一体化）:
  archive_and_reset → MemoryRouter.consolidate(语义分类?) → EM/SM/PM

修正设计（热路径/冷路径分离）:
  ┌── 热路径（同步，< 10ms，规则驱动）────────────────────────┐
  │  archive_and_reset                                        │
  │    → QuickLogger.log(old_messages)                       │
  │        仅记录原始工具调用序列到 EM.tool_call_log         │
  │        无分类，无语义理解，只是结构化存储                 │
  └───────────────────────────────────────────────────────────┘
  
  ┌── 冷路径（异步，会话结束，LLM 驱动）───────────────────────┐
  │  session_end                                              │
  │    → MemoryRouter.reflect(session_summary)               │
  │        LLM 读取 EM.tool_call_log                         │
  │        → 分类提炼 → 写入 SM.cases / SM.entities / PM     │
  └───────────────────────────────────────────────────────────┘
```

这个拆分还有一个好处：`QuickLogger` 极度简单（只是序列化工具调用），可以在 Phase 0 就实现；复杂的 LLM 辅助分类留到 Phase 3，不阻塞早期迭代。

#### 补充：MemoryRouter 应重命名以反映其实际职责

建议将 `MemoryRouter` 拆解为两个更小、职责清晰的组件：

| 组件 | 原名 | 职责 | 何时触发 | 是否用 LLM |
|------|------|------|----------|------------|
| `ContextAssembler` | MemoryRouter.retrieve | 组装 WM 注入内容 | assemble_context 时 | 否 |
| `MemoryConsolidator` | MemoryRouter.reflect | 将 EM 提炼入 SM/PM | 会话结束时 | 是 |
| ~~`MemoryRouter.consolidate`~~ | ~~合并~~ | ~~热路径分类~~ | ~~删除或降级为 QuickLogger~~ | ~~不适用~~ |

---

### 反思 3：上下文预算的"静态注入"问题——会挤压近期消息

#### 问题：3500 tokens 的固定注入是否值得？

提案第 6 节将 SM+PM+EM 的注入预算设定为 `2000+500+1000=3500 tokens`，从 Message Stream 中划出。乍看很合理——从 174,700 缩到 171,200，代价极小。

**但实际动力学不是这样的**：

```
场景一：短任务（10 轮对话，~5,000 tokens stream 使用）
  固定注入 3500t / 可用 171,200t = 2%
  → 代价低，但相关性高吗？
    如果任务是"帮我改一个函数"，注入"vcpu.py 上次的修复案例"毫无价值
  → 结论：3500t 浪费在噪音上，且为 LLM 注入了干扰信息

场景二：长任务（200 轮对话，Stream 接近 85% 阈值触发 archive）
  已用 ~145,320t，剩余可用 ~25,880t
  固定注入 3500t / 剩余 25,880t = 13.5%
  → 在 Stream 最紧张的时刻，注入比例最高！
  → 每次 archive_and_reset 后，3500t 的静态内容优先占位
    真正的近期消息（用户刚说的话）反而被 Smart Drop 淘汰
  → 结论：固定注入在任务后期会**加速**近期消息的丢失
```

这是一个**反直觉的负反馈**：越是长任务（越需要记忆的场景），固定注入占比越高，越压迫 Message Stream，越需要 archive，越触发更多注入……形成恶性循环。

#### 修正方案：自适应预算（Adaptive Budget）

SM/PM/EM 的注入量不应固定，应与当前 Stream 剩余空间动态绑定：

```python
def compute_memory_injection_budget(
    stream_used: int,
    stream_total: int,
    base_budget: int = 3500
) -> int:
    """
    自适应记忆注入预算：
    - Stream 使用率 < 30%：全额注入（3500t），任务早期，上下文充裕
    - Stream 使用率 30~70%：按比例缩减
    - Stream 使用率 > 70%：最小注入（500t），任务后期，优先保留近期消息
    """
    usage_ratio = stream_used / stream_total
    if usage_ratio < 0.30:
        return base_budget           # 3500t，全额
    elif usage_ratio < 0.70:
        # 线性插值：从 3500 降到 1000
        t = (usage_ratio - 0.30) / 0.40
        return int(base_budget * (1 - t) + 1000 * t)
    else:
        return 500                   # 仅保留 PM hints（500t），完全缩减
```

对应地，注入优先级为：**PM hints > EM recent pointers > SM semantic entries**。在预算压缩时，先砍 SM（相关性最不确定），最后保留 PM（最确定有用的程序化提示）。

#### 补充：SM 注入应按需触发，而非每次 assemble_context 都触发

`ContextAssembler.retrieve()` 不应在每次 `assemble_context` 时都执行全量 FTS5 检索。建议增加**目标漂移检测**：

```python
class ContextAssembler:
    _last_goal_hash: str = ""
    _cached_injection: str = ""
    
    def retrieve(self, current_goal: str, ...) -> str:
        goal_hash = hash(current_goal[:200])  # 取前 200 字做指纹
        if goal_hash == self._last_goal_hash:
            return self._cached_injection      # 命中缓存，0ms
        
        # Goal 变化时才重新检索
        injection = self._do_retrieve(current_goal, ...)
        self._last_goal_hash = goal_hash
        self._cached_injection = injection
        return injection
```

这样在目标稳定的长任务中，`retrieve()` 实际上只在会话开始时执行一次，后续全部命中缓存——既解决了延迟问题，也避免了重复注入相同内容的低效。

---

### 反思总结：三处修正的优先级

| 编号 | 问题 | 影响 | 修正优先级 | 实现阶段 |
|------|------|------|-----------|---------|
| R1-A | Episodic Pointer 链式丢失 | 高（档案白写） | **P0** | Phase 0 |
| R1-B | archive 失败无 fail-safe | 高（MMU 死锁风险） | **P0** | Phase 0 |
| R2   | MemoryRouter 热/冷路径混杂 | 中（延迟违规） | **P1** | Phase 1 重构 |
| R3-A | 固定注入加速消息丢失 | 中（长任务恶化） | **P1** | Phase 2 |
| R3-B | 缺少 goal 缓存导致重复检索 | 低（性能浪费） | **P2** | Phase 2 |

**对提案版本号的建议**：以上修正实质性地改变了三个核心机制的行为，建议将文档版本从 `v1.0` 升级为 `v1.1`，并在实现 Phase 0 前完成 R1-A 和 R1-B 的设计确认。

---

*反思附录由 Implementer Agent 在代码落地前的设计审查阶段撰写。如有异议，请在 PR 讨论中标记 `@architect`。*
