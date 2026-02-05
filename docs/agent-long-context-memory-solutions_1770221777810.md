# AI Council Report: 委员会帮我想想 现在不靠落盘维持 agent 长上下文能力的方案有哪些，要适合 nimbus 现在 mmu 架构的

2026/2/5 00:16:17

### Opinion from Claude Opus 4.5 (Deep Thinker)

好的，我已经详细了解了 nimbus 的 MMU 架构。让我以 AI 顾问委员会的身份，系统性地分析不依赖落盘的长上下文方案。

---

# 🎯 长上下文方案咨询报告

## 📊 当前架构分析

根据 nimbus 的 MMU 设计，你已经实现了：

| 现有能力 | 状态 |
|---------|------|
| Anchor & Stream 架构 | ✅ |
| Sliding Window + Hot Context | ✅ |
| StateManager (确定性状态追踪) | ✅ |
| Global Summary (全局摘要) | ✅ |
| ScrollHistory (滚动历史) | ✅ |
| Tool Call 标记/丢弃 | ✅ |

---

## 💡 委员会建议方案

### 方案一：**KV Cache 复用 (Prompt Caching)**

**原理**：Claude/OpenAI 都支持 Prompt Caching，相同前缀的 prompt 可以复用 KV cache

**适配 MMU 的方式**：
```
┌─────────────────────────────────┐
│   Static Prefix (可缓存)         │  ← 1024+ tokens 触发缓存
│   - System Rules                │
│   - Workspace Info              │
│   - Capabilities                │
├─────────────────────────────────┤
│   Dynamic Suffix (不缓存)        │
│   - Summary + History           │
└─────────────────────────────────┘
```

**实现要点**：
- 保持 `PinnedContext` 内容稳定（避免频繁变更）
- 利用 Anthropic 的 `cache_control` 显式标记可缓存区域
- 可降低 90% 的 Anchor token 计费

---

### 方案二：**层级摘要 (Hierarchical Summarization)**

**原理**：维护多层级的内存摘要，不同粒度服务不同需求

```
┌─────────────────────────────────────────┐
│  L0: Global Summary (全局)               │  ~500 tokens
│  "正在重构 auth 模块，已完成 3/5 个文件"   │
├─────────────────────────────────────────┤
│  L1: Episode Summaries (情景)            │  ~1000 tokens
│  - Episode 1: "分析了现有架构"            │
│  - Episode 2: "重构了 user.py"           │
├─────────────────────────────────────────┤
│  L2: Hot Messages (热点)                 │  ~3000 tokens
│  最近 5-10 条完整消息                     │
└─────────────────────────────────────────┘
```

**实现建议**：
```python
class HierarchicalSummary:
    global_summary: str       # L0: 一句话概括
    episode_summaries: List[str]  # L1: 每个阶段
    hot_messages: List[Message]   # L2: 最近消息
    
    def compress_episode(self, messages) -> str:
        """当一个阶段结束时，压缩为一句话"""
        pass
```

---

### 方案三：**结构化状态快照 (Structured State Snapshot)**

**原理**：你的 `StateManager` 已经有基础，可以扩展为更丰富的结构化记忆

```python
@dataclass
class EnhancedState:
    # 文件状态 (已有)
    files: Dict[str, FileState]
    
    # 新增：任务追踪
    tasks: Dict[str, TaskState]  # 待办/进行中/完成
    
    # 新增：关键决策
    decisions: List[Decision]    # 为什么选择方案A而非B
    
    # 新增：阻塞问题
    blockers: List[str]          # 当前卡住的点
    
    # 新增：上下文变量
    context_vars: Dict[str, Any] # 类似 shell 环境变量
```

**优势**：完全确定性，无 LLM 误差，token 开销极低

---

### 方案四：**语义去重 + 重要性排序**

**原理**：相似内容合并，按重要性保留

```python
class SemanticCompressor:
    def compress(self, messages: List[Message]) -> List[Message]:
        # 1. 识别重复的 Read 操作 (同一文件多次读取)
        # 2. 合并相似的 Bash 输出
        # 3. 按 recency * importance 排序
        pass

    def importance_score(self, msg: Message) -> float:
        """计算消息重要性"""
        score = 1.0
        if msg.role == "user":
            score += 2.0  # 用户指令最重要
        if "error" in msg.content.lower():
            score += 1.5  # 错误信息重要
        if msg.meta.get("is_goal"):
            score += 3.0  # 目标信息最重要
        return score
```

---

### 方案五：**Retrieval Buffer (按需召回)**

**原理**：不是全部放进上下文，而是建立内存索引，按需召回

```
┌──────────────────────────────────────────────┐
│  Active Context (LLM 看到的)                  │
│  - Anchor + Summary + Recent                 │
├──────────────────────────────────────────────┤
│  Memory Buffer (LLM 看不到，但可以查询)        │  ← 全部在内存
│  - Index: {file_path -> [messages]}          │
│  - Index: {tool_name -> [results]}           │
│                                              │
│  工具: SearchMemory(query) -> relevant_msgs  │
└──────────────────────────────────────────────┘
```

**实现**：给 Agent 一个 `SearchMemory` 工具，让它主动召回需要的历史

---

### 方案六：**Checkpoint & Resume (内存快照)**

**原理**：定期创建内存中的检查点，支持回滚

```python
class MemoryCheckpoint:
    """内存中的检查点，不落盘"""
    timestamp: float
    summary: str
    state_snapshot: Dict[str, Any]
    
class CheckpointManager:
    checkpoints: List[MemoryCheckpoint]  # 环形缓冲区
    max_checkpoints: int = 5
    
    def create_checkpoint(self, mmu: MMU) -> None:
        """创建一个检查点"""
        pass
    
    def restore(self, index: int) -> None:
        """回滚到某个检查点"""
        pass
```

---

## 🏆 推荐组合策略

基于 nimbus 现有架构，建议按优先级实现：

| 优先级 | 方案 | 难度 | 收益 |
|-------|------|-----|-----|
| P0 | KV Cache 复用 | 低 | 成本直降 90% |
| P1 | 增强 StateManager | 中 | 确定性 + 低开销 |
| P2 | 层级摘要 | 中 | 更好的信息密度 |
| P3 | SearchMemory 工具 | 中 | 突破上下文限制 |

---

## 🔧 快速实施建议

**阶段 1 (1-2 天)**：利用 Prompt Caching
```python
# 在 assemble_context 中标记 cache_control
def assemble_context(self, ...):
    messages = []
    if self._pinned:
        pinned_msg = self._pinned.to_system_message()
        pinned_msg.meta["cache_control"] = {"type": "ephemeral"}  # Anthropic API
        messages.append(pinned_msg.to_dict())
    ...
```

**阶段 2 (3-5 天)**：增强 StateManager
```python
# 添加任务追踪和决策记录
class EnhancedStateManager(StateManager):
    def track_decision(self, decision: str, reason: str):
        """记录关键决策"""
        pass
    
    def track_task(self, task_id: str, status: str):
        """追踪任务状态"""
        pass
```

---

需要我针对某个具体方案展开详细设计吗？


### Opinion from GPT-5.2 (Generalist)

现在我已了解 nimbus 的 MMU 架构。让我为您系统分析**不落盘维持长上下文**的方案。

---

# 🧠 不落盘维持 Agent 长上下文能力方案分析

## 现状：Nimbus MMU 架构特点

```
┌─────────────────────────────────┐
│        The Anchor               │  ← Pinned (Goal + Rules + State)
├─────────────────────────────────┤
│        Global Summary           │  ← Rolling Summary
├─────────────────────────────────┤
│        The Stream               │  ← Sliding Window + Hot Context
└─────────────────────────────────┘
```

当前已有：滑动窗口、摘要机制、StateManager 状态追踪

---

## 方案一：层级记忆压缩 (Hierarchical Memory)

**原理**：模拟人类记忆的"短期→中期→长期"层级

```
┌─────────────────────────────────┐
│  L0: Hot Context (5-10 msgs)    │  ← 完整保留
├─────────────────────────────────┤
│  L1: Session Summary (per hour) │  ← 粗粒度摘要
├─────────────────────────────────┤
│  L2: Task Summary (per task)    │  ← 任务级别摘要
├─────────────────────────────────┤
│  L3: Global Knowledge           │  ← 关键决策/结论
└─────────────────────────────────┘
```

**适配 MMU**：
- 在 `_global_summary` 基础上增加 `_session_summaries: List[str]`
- 每次 compaction 时向上聚合
- 按需展开某层级的详细信息

**优点**：实现简单、token 效率高  
**缺点**：信息丢失不可逆、难以精确召回

---

## 方案二：In-Memory 向量索引 (Semantic Retrieval)

**原理**：用 embedding 索引历史，语义检索相关上下文

```python
class MemoryIndex:
    def __init__(self):
        self.embeddings = []  # numpy array, in-memory
        self.messages = []    # 原始消息引用
        
    def add(self, message: Message):
        embedding = embed(message.content)
        self.embeddings.append(embedding)
        self.messages.append(message)
        
    def retrieve(self, query: str, top_k: int = 5) -> List[Message]:
        query_emb = embed(query)
        scores = cosine_similarity(query_emb, self.embeddings)
        return [self.messages[i] for i in top_k_indices(scores)]
```

**适配 MMU**：
- 在 MMU 中添加 `_memory_index: MemoryIndex`
- `assemble_context` 时，除了滑动窗口，还检索相关历史
- 用 `text-embedding-3-small` 或 `bge-m3` 做 embedding

**优点**：精确召回、不丢失信息  
**缺点**：需要 embedding API/模型、内存占用较大

---

## 方案三：Working Memory + Reference Memory

**原理**：分离"活跃思考"和"背景知识"

```
┌─────────────────────────────────┐
│    Working Memory (WM)          │  ← 当前任务相关, 完整保留
│    - Current Goal Context       │
│    - Active Files/Code          │
│    - Recent Decisions           │
├─────────────────────────────────┤
│    Reference Memory (RM)        │  ← 压缩的背景知识
│    - Project Structure          │
│    - Past Learnings             │
│    - Key Milestones             │
└─────────────────────────────────┘
```

**适配 MMU**：
- `PinnedContext` 扩展为 Reference Memory
- `StackFrame` 中添加 `working_memory: Dict[str, Any]`
- Agent 可主动调用 `promote_to_reference(key, value)` 将重要信息升级

**优点**：符合认知模型、Agent 可控  
**缺点**：需要 Agent 有"记忆管理"能力

---

## 方案四：增量式流摘要 (Streaming Summarization)

**原理**：不是等到 compaction 再摘要，而是**持续维护**

```python
class StreamingSummarizer:
    def __init__(self):
        self.buffer = []
        self.running_summary = ""
        self.chunk_size = 10  # 每 10 条消息更新一次
        
    def add_message(self, msg: Message):
        self.buffer.append(msg)
        if len(self.buffer) >= self.chunk_size:
            self.running_summary = self._merge_summary(
                self.running_summary, 
                self._summarize(self.buffer)
            )
            self.buffer = []
```

**适配 MMU**：
- `add_message` 时触发增量摘要检查
- `_global_summary` 变成动态更新的流式摘要
- 可以用小模型（如 Gemini Flash）做快速摘要

**优点**：低延迟、摘要时效性好  
**缺点**：摘要调用频繁、成本较高

---

## 方案五：关键帧 + 差分编码 (Keyframe + Delta)

**原理**：借鉴视频编码，只保留"关键帧"，其他用差分表示

```
Timeline:
[K1] --- [D] --- [D] --- [K2] --- [D] --- [D] --- [K3]
  │                        │                        │
  └──────────────────────────────────────────────────┘
         只保留 Keyframes，Deltas 可从 K 推断
```

**Keyframe 触发条件**：
- 用户新任务
- 重要决策点
- Agent 主动标记

**适配 MMU**：
- `Message.meta["is_keyframe"] = True`
- `assemble_context` 时优先保留 keyframes
- 非 keyframe 可被摘要/丢弃

**优点**：保留决策点、压缩率高  
**缺点**：需要定义好 keyframe 标准

---

## 🎯 推荐组合方案（适合 Nimbus MMU）

综合考虑**实现成本、效果、架构兼容性**，推荐：

### Phase 1: 关键帧 + 层级摘要
```python
# 1. 扩展 Message 支持 keyframe 标记
class Message:
    meta: Dict = {"is_keyframe": False, "importance": 0.5}

# 2. 扩展 MMU 支持层级摘要
class MMU:
    _l1_summary: str = ""  # 近期摘要 (last hour)
    _l2_summary: str = ""  # 任务摘要 (current task)
    _keyframes: List[Message] = []  # 关键帧缓存
```

### Phase 2: 添加语义检索
```python
# 3. 可选：添加轻量级向量检索
class MMU:
    _memory_index: Optional[MemoryIndex] = None
    
    def assemble_context(self, ...):
        # 滑动窗口 + 语义召回
        relevant = self._memory_index.retrieve(current_goal)
        ...
```

### Phase 3: Agent 可控的记忆管理
```python
# 4. 暴露 tool 让 Agent 管理记忆
tools = [
    "remember(key, value)",      # 主动记忆
    "recall(query)",             # 主动召回
    "mark_keyframe()",           # 标记当前为关键点
]
```

---

## 实现优先级建议

| 方案 | 难度 | 效果 | 建议优先级 |
|------|------|------|-----------|
| 层级摘要 | ⭐⭐ | ⭐⭐⭐ | **P0** |
| 关键帧标记 | ⭐ | ⭐⭐⭐ | **P0** |
| 流式摘要 | ⭐⭐⭐ | ⭐⭐⭐⭐ | P1 |
| 向量检索 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | P2 |
| Agent 记忆工具 | ⭐⭐ | ⭐⭐⭐⭐ | P1 |

---

需要我详细展开某个方案的具体实现吗？


### Opinion from Gemini 3 Pro High (Reasoning)

根据我对 nimbus MMU 架构（Anchor & Stream 模式）的深入分析，以下是**委员会关于不落盘维持 Agent 长上下文能力的方案建议**：

---

## 🎯 委员会方案报告：纯内存长上下文维持策略

### 当前架构基线

```
┌─────────────────────────────────┐
│        The Anchor               │  ← Pinned Context (Goal/Rules)
├─────────────────────────────────┤
│        Global Summary           │  ← Rolling Summary
├─────────────────────────────────┤
│     Historical Window (可滚动)   │  ← Sliding Window
├─────────────────────────────────┤
│     Hot Context (最近5条)        │  ← Always Visible
└─────────────────────────────────┘
```

现有问题：**信息熵不可逆丢失** — Summary 压缩后细节永久消失。

---

## 📋 方案一：分层语义压缩 (Hierarchical Semantic Compression)

**核心思想**：不同类型的信息采用不同压缩策略，保留"骨架"丢弃"脂肪"。

```python
class HierarchicalCompressor:
    """
    信息分类压缩策略:
    - L0 (决不压缩): Goal, Errors, Decisions
    - L1 (轻度压缩): Code changes, Test results  
    - L2 (重度压缩): Exploration, Read outputs
    - L3 (可丢弃):   Failed attempts, Verbose logs
    """
    
    def compress(self, messages: List[Message]) -> List[Message]:
        # 提取 L0 关键事件保留原文
        critical = self._extract_critical_events(messages)
        
        # L1 用结构化摘要替代
        code_changes = self._summarize_code_changes(messages)
        
        # L2/L3 用统计摘要
        exploration_summary = f"探索了 {n} 个文件，执行了 {m} 条命令"
        
        return [critical, code_changes, exploration_summary]
```

**适配 MMU**：在 `archive_and_reset` 中替换现有的 LLM 摘要逻辑。

---

## 📋 方案二：增强 StateManager 为"确定性记忆核"

**核心思想**：扩展现有 `StateManager`，用确定性规则提取和维护结构化状态，**不依赖 LLM 摘要**。

```python
class EnhancedStateManager:
    """
    确定性状态追踪 - 无 LLM 参与，零幻觉风险
    """
    def __init__(self):
        # 现有
        self.files: Dict[str, FileState] = {}
        self.last_cmd_status: str = ""
        
        # 新增：关键决策链
        self.decisions: List[Decision] = []  # (时间, 决策, 原因)
        
        # 新增：错误历史 (防止重蹈覆辙)
        self.error_history: List[ErrorRecord] = []  # (工具, 参数, 错误类型)
        
        # 新增：里程碑清单
        self.milestones: List[Milestone] = []  # (描述, 完成时间)
        
        # 新增：变量/路径字典
        self.working_memory: Dict[str, str] = {}  # {"main_file": "vcpu.py"}
    
    def render(self) -> str:
        """渲染为 Pinned Context 的一部分"""
        return f"""
🛡️ [Deterministic Memory Core]
**Active Files**: {self._render_files()}
**Decisions Made**: {self._render_decisions()}
**Errors Encountered**: {self._render_errors()}
**Milestones**: {self._render_milestones()}
**Key Variables**: {self.working_memory}
"""
```

**优势**：信息提取是确定性的，不依赖 LLM 总结，**零幻觉累积风险**。

---

## 📋 方案三：Context Caching (上下文缓存/KV Cache 复用)

**核心思想**：利用 LLM Provider 的 **Prompt Caching** 特性，将 Pinned Context 缓存在服务端。

```
┌─────────────────────────────────┐
│   Cached Prefix (Anchor)        │  ← Provider 端缓存，不重复传输
│   - System Rules                │
│   - Project State               │
│   - Historical Summary          │
├─────────────────────────────────┤
│   Dynamic Suffix (Stream)       │  ← 每次请求只传这部分
│   - Recent Messages             │
└─────────────────────────────────┘
```

**实现**：
- **Anthropic**: 使用 `cache_control: {"type": "ephemeral"}` 标记 system prompt
- **OpenAI**: 自动缓存相同前缀 (GPT-4 Turbo+)
- **Gemini**: Context Caching API

**适配 MMU**：在 `assemble_context` 中对 Pinned 部分添加缓存标记。

---

## 📋 方案四：关键事件标记 + 虚拟检索 (Key Events + Virtual RAG)

**核心思想**：不落盘，但在内存中维护一个**轻量级事件索引**，实现"伪 RAG"。

```python
class InMemoryEventIndex:
    """
    内存事件索引 - 不落盘的虚拟 RAG
    """
    def __init__(self, max_events: int = 100):
        self.events: List[KeyEvent] = []
        
    def add_event(self, event_type: str, summary: str, full_content: str):
        """
        记录关键事件 (保留原文摘要)
        """
        self.events.append(KeyEvent(
            type=event_type,  # "error", "decision", "code_change", "discovery"
            summary=summary[:200],
            content_hash=hash(full_content),
            timestamp=time.time(),
            # 可选：保留原文 (如果内存允许)
            _raw=full_content if len(full_content) < 2000 else None
        ))
        
    def search(self, query: str, top_k: int = 3) -> List[KeyEvent]:
        """简单关键词匹配 (可升级为 embedding)"""
        return [e for e in self.events if query.lower() in e.summary.lower()][:top_k]
```

**Agent 工具集成**：
```python
# 新增 RecallMemory 工具
def recall_memory(query: str) -> str:
    """从内存事件索引中检索历史"""
    events = index.search(query)
    return "\n".join([f"[{e.type}] {e.summary}" for e in events])
```

---

## 📋 方案五：滑动窗口 + 双锚定抗漂移

**核心思想**：强化现有的滑动窗口，在 **头部和尾部同时锚定**。

```
┌─────────────────────────────────┐
│  HEAD ANCHOR (始终可见)          │
│  - Original Goal                │
│  - Initial Context              │
├─────────────────────────────────┤
│  SLIDING MIDDLE                 │  ← 可滚动历史
│  (压缩/丢弃发生在这里)            │
├─────────────────────────────────┤
│  TAIL ANCHOR (始终可见)          │
│  - Recent N messages            │
│  - Current State Snapshot       │
└─────────────────────────────────┘
```

**实现**：修改 `assemble_context`，确保 **第一条用户消息** 永远保留在 context 中。

---

## 📋 方案六：Token 预算动态分配

**核心思想**：根据任务阶段动态调整各区域的 Token 预算。

```python
class AdaptiveBudgetMMU(MMU):
    def get_budget_allocation(self, task_phase: str) -> Dict[str, int]:
        """
        任务阶段感知的预算分配
        """
        if task_phase == "exploration":
            # 探索阶段：给历史更多空间
            return {"pinned": 5000, "summary": 3000, "history": 80000, "hot": 12000}
        elif task_phase == "execution":
            # 执行阶段：给 hot context 更多空间
            return {"pinned": 5000, "summary": 5000, "history": 60000, "hot": 30000}
        elif task_phase == "debugging":
            # 调试阶段：保留更多错误上下文
            return {"pinned": 5000, "summary": 2000, "history": 50000, "hot": 43000}
```

---

## 🏆 委员会推荐优先级

| 优先级 | 方案 | 改动量 | 收益 | 风险 |
|--------|------|--------|------|------|
| **P0** | 方案二：增强 StateManager | 中 | 高 (零幻觉) | 低 |
| **P0** | 方案五：双锚定抗漂移 | 低 | 中 | 低 |
| **P1** | 方案三：Context Caching | 低 | 高 (成本节省) | 低 |
| **P1** | 方案一：分层压缩 | 中 | 中 | 中 |
| **P2** | 方案四：虚拟 RAG | 高 | 高 | 中 |
| **P2** | 方案六：动态预算 | 中 | 中 | 低 |

---

## 💡 最终建议

**短期 (立即可做)**:
1. **增强 StateManager**：用确定性规则提取关键事件，作为 "记忆核心"
2. **双锚定**：确保初始 Goal 和最近消息同时可见
3. **启用 Context Caching**：对 Anthropic/OpenAI 启用 prompt caching

**中期 (下一迭代)**:
1. **分层压缩**：按信息类型分级压缩
2. **内存事件索引**：实现轻量级的 `RecallMemory` 工具

**核心原则**：**确定性 > LLM 摘要**。能用规则提取的信息，绝不依赖 LLM 总结。

---

以上是委员会的完整建议。需要我针对某个方案展开详细设计吗？
