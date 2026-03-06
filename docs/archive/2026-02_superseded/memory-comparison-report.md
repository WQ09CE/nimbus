# Nimbus vs OpenClaw: 记忆系统技术对比报告

**日期**: 2026-02-16
**作者**: Architect
**状态**: 初稿

---

## 1. 概述

本报告对比 Nimbus 当前的记忆系统（"Anchor & Stream" MMU）与 OpenClaw Workspace Memory v2 提案（基于 Markdown Source-of-Truth + Derived Index 架构），旨在识别 Nimbus 可以借鉴的改进方向，并给出具有优先级的实施建议。

**核心发现**: Nimbus 的记忆系统在"会话内上下文管理"方面已经较为成熟，但在"跨会话知识持久化"和"结构化信息检索"两个维度上存在显著差距。OpenClaw 的 Retain/Recall/Reflect 循环为 Nimbus 提供了明确的演进路径。

---

## 2. Nimbus 现状

### 2.1 架构总览

```
┌──────────────────────────────────────────────────────┐
│                  Context Window (200k)                │
├──────────────────────────────────────────────────────┤
│  Pinned Context (Anchor)           ~10k token budget │
│  ├── System Rules                                    │
│  ├── Workspace Info                                  │
│  ├── Capabilities                                    │
│  ├── User Goal (pinned)                              │
│  └── Milestones (auto-extracted)                     │
├──────────────────────────────────────────────────────┤
│  Global Summary                    ~600 tokens       │
│  └── PRIMARY GOAL + EXECUTION STATUS                 │
├──────────────────────────────────────────────────────┤
│  Project State Monitor (deterministic)               │
│  ├── File Working Set (LRU, max 10)                  │
│  └── Last Command Status                             │
├──────────────────────────────────────────────────────┤
│  Memo (好记性不如烂笔头)                              │
│  └── .nimbus/memo_{session_id}.md                    │
├──────────────────────────────────────────────────────┤
│  Historical Window (sliding)       budget-based      │
│  └── Older messages, auto-truncated                  │
├──────────────────────────────────────────────────────┤
│  Hot Context                       last 15 messages  │
│  └── Always visible recent messages                  │
└──────────────────────────────────────────────────────┘
```

### 2.2 核心模块

| 模块 | 文件路径 | 职责 |
|------|----------|------|
| **MMU** | `src/nimbus/core/memory/mmu.py` | 上下文组装、滑动窗口、Token 预算管理 |
| **PinnedContext** | `src/nimbus/core/memory/context.py:156` | 不可压缩的系统锚点 |
| **StateManager** | `src/nimbus/core/memory/state_manager.py` | 确定性追踪文件工作集和命令状态 |
| **Memo** | `src/nimbus/tools/memo.py` | Agent 的显式记忆工具 (read/write/append/clear) |
| **CompactionEngine** | `src/nimbus/core/compaction.py` | LLM 摘要 + 压缩执行 |
| **SessionManager** | `src/nimbus/core/session.py` | JSONL 会话日志持久化 |
| **Persistence** | `src/nimbus/core/persistence.py` | Pydantic 模型，SQLite checkpoint |

### 2.3 压缩/摘要流程

```
Token 使用率 > 85%
       │
       ▼
archive_and_reset() ─────────────────────────────────────
       │                                                  │
       ▼                                                  ▼
  切分消息为 [旧消息] + [保留消息(最近20条)]        读取 Memo 内容
       │                                                  │
       ▼                                                  ▼
  调用 LLM 生成摘要 ←──────── 将 Memo 内容附加到摘要 prompt
       │
       ├── 解析 NEW_MILESTONES: [...] → 注册到 PinnedContext
       │
       ├── 解析 SUMMARY: [...] → 更新 Global Summary
       │
       └── 如果摘要超预算 → LLM 二次压缩
       │
       ▼
  物理截断旧消息 (frame.messages = messages_to_keep)
```

**证据**: `src/nimbus/agentos.py:1014-1111` (generate_summary 函数), `src/nimbus/core/memory/mmu.py:878-957` (archive_and_reset 方法)

### 2.4 持久化方式

| 存储形式 | 位置 | 内容 | 生命周期 |
|----------|------|------|----------|
| Session JSONL | `~/.nimbus/sessions/YYYY-MM-DD/{id}.jsonl` | 完整消息流(树结构) | 永久(但无索引) |
| SQLite Checkpoint | (通过 Pydantic 模型序列化) | MMU + vCPU 快照 | 随会话 |
| Memo 文件 | `.nimbus/memo_{session_id}.md` | Agent 手动记录的笔记 | 随会话 |

---

## 3. OpenClaw 方案

### 3.1 架构总览

```
┌─────────────────────────────────────────────────────┐
│              Markdown Source-of-Truth                 │
├─────────────────────────────────────────────────────┤
│  memory.md              核心记忆, always in context  │
│  memory/YYYY-MM-DD.md   每日日志, append-only        │
│  bank/                  策展知识库                    │
│  ├── entities/*.md      实体页 (@Peter, @warelay)    │
│  ├── opinions/*.md      观点页 (带置信度)             │
│  ├── experience/*.md    经验页                       │
│  └── world/*.md         世界知识页                    │
├─────────────────────────────────────────────────────┤
│  .memory/index.sqlite   FTS5 派生索引 (可重建)       │
│  └── Optional: embeddings                           │
└─────────────────────────────────────────────────────┘
```

### 3.2 Retain / Recall / Reflect 循环

```
            ┌─────────┐
            │  Retain  │ ◄── 从日志中提取结构化事实
            │ (提取)   │     类型: W(世界)/B(经验)/O(观点)/S(摘要)
            └────┬────┘     实体标签: @entity_name
                 │
                 ▼
            ┌─────────┐
            │  Recall  │ ◄── 多维检索
            │ (检索)   │     词法(FTS5) + 实体 + 时间 + 观点查询
            └────┬────┘     返回带引用的结果包 (file:line)
                 │
                 ▼
            ┌─────────┐
            │ Reflect  │ ◄── 定期整理任务
            │ (反思)   │     更新实体页, 演化观点置信度
            └─────────┘     合并冗余, 清理过时信息
```

### 3.3 核心理念

1. **Markdown 是唯一真相源**: 所有记忆都以 Markdown 文件存储，人类可读可编辑，SQLite 索引可随时从 Markdown 重建
2. **结构化事实类型**: 每条提取的事实都有分类(W/B/O/S)、实体标签、置信度
3. **实体中心**: 以实体为核心组织知识，每个重要实体有独立页面
4. **离线优先**: 所有数据本地存储，无需网络即可全功能运行
5. **观点演化**: 观点不是二元的，而是有置信度的，随着新证据更新

---

## 4. 对比分析

### 4.1 能力矩阵

| 能力维度 | Nimbus 现状 | OpenClaw 方案 | 差距评估 |
|----------|------------|---------------|----------|
| **会话内上下文管理** | Anchor+Stream 滑动窗口，Hot/Historical 分区 | 不涉及(设计层次不同) | Nimbus 已完善 |
| **跨会话持久化** | 无(每次新会话从零开始) | memory.md + bank/ 跨会话复用 | **严重缺失** |
| **信息提取** | LLM 摘要(非结构化) + Milestone 解析 | Retain: 结构化事实提取(W/B/O/S) | **显著差距** |
| **信息检索** | 无搜索能力 | Recall: FTS5 + 实体 + 时间多维检索 | **完全缺失** |
| **信息整理** | Milestone 自动提取到 PinnedContext | Reflect: 实体页更新 + 观点演化 | **显著差距** |
| **Agent 显式记忆** | Memo 工具(read/write/append/clear) | memory.md (类似但跨会话) | **接近但不够** |
| **持久化存储** | JSONL(无索引) + SQLite checkpoint | Markdown(FTS5 索引) | 中等差距 |
| **知识分类** | 无(扁平文本) | W/B/O/S 四类事实 + 实体标签 | **显著差距** |
| **人类可读性** | Memo 可读, JSONL 不可读 | 全部 Markdown, 完全可读 | 中等差距 |
| **压缩抗衰减** | 多次压缩后信息级联丢失 | 结构化提取后事实独立存储 | **严重差距** |

### 4.2 核心差异

```
Nimbus:  会话 → 滑动窗口 → LLM 摘要 → 覆盖旧摘要 → 信息丢失
                                                    ↓
                                          Memo (手动补救)

OpenClaw: 日志 → Retain(结构化提取) → Bank(分类存储) → Recall(精准检索)
                       ↓                      ↑
                  Reflect(定期整理) ──────────┘
```

关键洞察:

1. **Nimbus 的 Memo 是 OpenClaw memory.md 的原始版本** -- 两者都是"Agent 主动写入的持久笔记"。但 Nimbus 的 Memo 与会话绑定(`.nimbus/memo_{session_id}.md`)，会话结束即不再可见；OpenClaw 的 memory.md 跨会话持续存在。

2. **Nimbus 的压缩(Compaction)对应 OpenClaw 的 Retain** -- 两者都试图从历史中提取关键信息。但 Nimbus 输出非结构化 LLM 摘要(信息会级联丢失)；OpenClaw 输出结构化事实(独立存储，永不丢失)。

3. **Nimbus 完全缺失 OpenClaw 的 Recall** -- Nimbus 没有任何搜索历史记忆的机制。Agent 只能依赖当前上下文窗口中的信息。

4. **Nimbus 的 Milestone 提取是 OpenClaw Reflect 的雏形** -- Nimbus 在摘要中解析 `NEW_MILESTONES:` 并注册到 PinnedContext (`src/nimbus/agentos.py:1080-1093`)。这与 OpenClaw 的 Reflect 更新实体页有相同的意图，但远不够系统化。

---

## 5. 可借鉴的改进

### P0: 立即可做 (低成本, 高收益)

#### P0-1: 全局 Memo (跨会话知识库)

**现状**: Memo 文件与 session_id 绑定 (`memo_{session_id}.md`)，每次新会话创建新文件。
**改进**: 在 session-specific Memo 之外，增加一个全局 Memo (`.nimbus/memo_global.md`)，始终注入到上下文中。

**影响范围**:
- `src/nimbus/tools/memo.py:55-59` -- MemoManager 初始化，增加 global memo 路径
- `src/nimbus/tools/memo.py:18-47` -- MEMO_TOOL_DEF，增加 `scope` 参数 (session/global)
- `src/nimbus/core/memory/mmu.py:546-561` -- assemble_context 中注入 memo，需同时注入 global memo
- `src/nimbus/agentos.py:418-421` -- 创建 memo_manager 时同时创建 global memo

**预估工作量**: 约 2-3 小时
**收益**: Agent 跨会话的知识不再丢失，用户/Agent 可在全局 Memo 中积累项目级知识

#### P0-2: 摘要输出结构化

**现状**: `generate_summary` 输出格式是 `NEW_MILESTONES: [...]\nSUMMARY: [...]`，但只解析了 milestones，summary 仍是非结构化文本。
**改进**: 在摘要 prompt 中要求 LLM 输出更多结构化字段:

```
NEW_MILESTONES: [...]
KEY_FILES: [file1, file2, ...]
KEY_DECISIONS: [decision1, decision2, ...]
BLOCKERS: [...]
SUMMARY: [...]
```

**影响范围**:
- `src/nimbus/agentos.py:1043-1066` -- summary_prompt 模板
- `src/nimbus/agentos.py:1080-1093` -- 解析逻辑，增加更多字段提取

**预估工作量**: 约 1-2 小时
**收益**: 从 LLM 摘要中提取更多结构化信息，减少信息丢失

#### P0-3: Memo 内容参与压缩感知

**现状**: 压缩时会读取 Memo 内容附加到摘要 prompt (`src/nimbus/agentos.py:1003-1012`)，但仅作为上下文参考。
**改进**: 压缩后自动将 KEY_FILES、KEY_DECISIONS 等结构化信息追加到 Memo 中(如果 Agent 尚未记录)。

**影响范围**:
- `src/nimbus/agentos.py:1091-1105` -- 在 milestone 注册后，同步更新 Memo

**预估工作量**: 约 1 小时
**收益**: 即使 Agent 忘记手动写 Memo，关键信息也能自动保留

---

### P1: 短期目标 (中等工作量, 需要部分重构)

#### P1-1: 会话历史 FTS 索引

**现状**: Session JSONL 是 append-only 的平面文件，无任何搜索能力。
**改进**: 使用 SQLite FTS5 对历史会话建立全文搜索索引。

**设计**:
```
.nimbus/
├── sessions/           # 原有 JSONL (仍是真相源)
└── index.sqlite        # FTS5 派生索引 (可随时从 JSONL 重建)
    ├── messages_fts    # 全文索引
    └── sessions_meta   # 会话元数据
```

**新增 Agent 工具**: `SearchHistory`
```python
SEARCH_HISTORY_TOOL_DEF = {
    "name": "SearchHistory",
    "description": "Search through past session history...",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "session_scope": {"type": "string", "enum": ["current", "all"]},
            "limit": {"type": "integer", "default": 10}
        }
    }
}
```

**影响范围**:
- 新文件: `src/nimbus/tools/search_history.py`
- `src/nimbus/core/session.py` -- 在 `_persist_entry` 中同步更新 FTS 索引
- `src/nimbus/agentos.py` -- 注册新工具

**预估工作量**: 约 1-2 天
**收益**: Agent 获得 Recall 能力，可以搜索"我们之前关于 X 的讨论"

#### P1-2: 事实类型分类

**现状**: 摘要中所有信息都是扁平文本。
**改进**: 在 Retain(摘要提取)阶段对事实进行 W/B/O/S 分类:

| 类型 | 含义 | 示例 |
|------|------|------|
| W (World) | 客观世界知识 | "Python 3.13 引入了 free-threading" |
| B (Experience) | 本项目经验 | "Colima Docker 用 192.168.5.2 做 host gateway" |
| O (Opinion) | 带置信度的判断 | "DirectAdapter 比 PiLLMAdapter 更稳定 (0.8)" |
| S (Summary) | 阶段性摘要 | "Phase 2 完成了 base_url 硬编码消除" |

**影响范围**:
- `src/nimbus/agentos.py:1043-1066` -- 摘要 prompt 增加分类要求
- 新文件: `src/nimbus/core/memory/fact_store.py` -- 事实存储和检索
- `src/nimbus/core/memory/mmu.py` -- 在 assemble_context 中注入相关事实

**预估工作量**: 约 2-3 天
**收益**: 结构化事实独立于摘要存储，不会因多次压缩而级联丢失

#### P1-3: Memo 自动提取 (半自动 Retain)

**现状**: Agent 必须手动调用 Memo 工具写入笔记。
**改进**: 在 Compaction 触发时自动从被丢弃的消息中提取关键事实并追加到 Memo。

**策略**: 不完全替代 Agent 的手动写入，而是作为"安全网":
1. Compaction 时，解析被丢弃消息中的文件路径、错误信息、配置值
2. 与当前 Memo 内容比对，避免重复
3. 追加缺失的关键信息到 Memo 的特定 section

**影响范围**:
- `src/nimbus/core/memory/mmu.py:878-957` -- archive_and_reset，增加自动提取 hook
- `src/nimbus/tools/memo.py` -- MemoManager 增加 `auto_append_facts` 方法

**预估工作量**: 约 1-2 天
**收益**: 降低因 Agent 遗忘写 Memo 导致的信息丢失

---

### P2: 长期目标 (需要重大架构变更)

#### P2-1: Bank 知识库 (实体中心存储)

**借鉴 OpenClaw 的 `bank/` 目录结构**:

```
.nimbus/
├── bank/
│   ├── entities/
│   │   ├── nimbus-project.md     # 项目实体
│   │   ├── direct-adapter.md     # 组件实体
│   │   └── harbor-integration.md # 功能实体
│   ├── decisions/
│   │   ├── adr-001-jwt-auth.md
│   │   └── adr-002-no-pickle.md
│   └── patterns/
│       ├── docker-colima-gateway.md
│       └── sandbox-path-validation.md
```

每个实体页面包含:
- 描述
- 相关事实(带来源引用)
- 关联实体
- 最近更新时间

**预估工作量**: 约 1-2 周
**收益**: 完整的项目知识图谱，Agent 可以按实体维度检索知识

#### P2-2: Reflect 定期整理循环

**借鉴 OpenClaw 的 Reflect 机制**:
- 定时任务(如每 N 次压缩后，或会话结束时)
- 扫描新增事实，更新实体页面
- 合并冲突信息，调整观点置信度
- 清理过时的知识条目

**预估工作量**: 约 1-2 周
**收益**: 知识库保持整洁和时效性，观点随证据演化

#### P2-3: 嵌入向量检索

**在 FTS5 基础上增加语义检索**:
- 对事实/Memo 内容生成 embedding
- 存储在 SQLite (使用 sqlite-vec 扩展) 或本地向量数据库
- Recall 同时支持词法匹配和语义相似度

**预估工作量**: 约 1 周
**收益**: 支持模糊查询和语义联想

---

## 6. 实施路线

```
Phase 0 (本周)                     Phase 1 (2-4周)              Phase 2 (1-2月)
┌──────────────┐                  ┌──────────────┐            ┌──────────────┐
│ P0-1 全局Memo │                  │P1-1 FTS索引  │            │P2-1 Bank库   │
│ P0-2 结构化摘要│ ──依赖──────────▶│P1-2 事实分类  │──依赖─────▶│P2-2 Reflect  │
│ P0-3 压缩感知  │                  │P1-3 自动提取  │            │P2-3 向量检索  │
└──────────────┘                  └──────────────┘            └──────────────┘
     ↓                                  ↓                          ↓
  即可部署                          需要新工具注册               需要架构评审
  改动 < 100 行                     新增 ~500 行代码             新增 ~2000 行代码
```

**依赖关系**:
- P0-1 (全局 Memo) 是 P1-3 (自动提取) 的前提: 自动提取的事实需要写入全局 Memo
- P0-2 (结构化摘要) 是 P1-2 (事实分类) 的前提: 分类需要结构化的提取格式
- P1-1 (FTS 索引) 是 P2-3 (向量检索) 的前提: 向量索引基于 SQLite 基础设施
- P1-2 (事实分类) 是 P2-1 (Bank 库) 的前提: Bank 需要分类后的事实作为输入

---

## 7. 不适用的部分

以下 OpenClaw 设计不建议直接移植到 Nimbus:

### 7.1 Markdown 作为唯一真相源

**OpenClaw 理念**: 所有数据以 Markdown 存储，SQLite 只是派生索引。
**不适用原因**: Nimbus 的会话历史包含大量 tool_calls 和结构化 JSON，Markdown 格式会导致信息损失。JSONL 仍然是更合适的原始存储格式。SQLite FTS5 应作为索引层而非替代方案。

### 7.2 每日日志文件

**OpenClaw 理念**: 每天一个 `memory/YYYY-MM-DD.md` 日志文件。
**不适用原因**: Nimbus 已有 JSONL 会话日志，功能重叠。增加日志文件只会带来数据同步问题。应复用现有 JSONL 作为日志源。

### 7.3 观点置信度精细管理

**OpenClaw 理念**: 每个观点有数值置信度 (0.0-1.0)，随证据演化。
**不适用原因**: Nimbus 是编程 Agent，主要处理技术事实而非主观判断。"这个 API 参数格式正确"不需要置信度追踪。在 Nimbus 场景下，事实要么正确要么不正确，简化为"已验证/待验证"二分即可。

### 7.4 复杂实体关系图

**OpenClaw 理念**: 实体间有丰富的关系链接 (@entity 引用)。
**不适用原因**: 编程 Agent 的"实体"主要是文件、模块、配置项，这些关系已经由代码结构本身表达。不需要额外的实体关系管理。StateManager (`src/nimbus/core/memory/state_manager.py`) 已经以更高效的方式追踪文件工作集。

---

## 8. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 全局 Memo 文件过大导致 context 溢出 | 中 | 高 | 设置全局 Memo token 上限(如 2000 tokens)，超出时 LLM 压缩 |
| FTS 索引与 JSONL 不同步 | 低 | 中 | 索引可从 JSONL 重建；追加写入时原子更新 |
| 自动提取的事实质量低 | 中 | 中 | 先用规则提取(正则匹配文件路径/错误/配置)，不依赖 LLM |
| 多个 Agent 进程并发写入全局 Memo | 低 | 中 | 使用文件锁或 SQLite WAL 模式 |
| 增加工具数量降低 Agent 决策质量 | 低 | 高 | SearchHistory 仅在需要时暴露，不默认注册 |

---

## 9. 证据索引

本报告引用的核心代码文件:

| 文件 | 关键行号 | 内容 |
|------|---------|------|
| `src/nimbus/core/memory/mmu.py` | L54-66 | MMUConfig 配置项 |
| `src/nimbus/core/memory/mmu.py` | L503-740 | assemble_context 上下文组装 |
| `src/nimbus/core/memory/mmu.py` | L545-561 | Memo 注入逻辑 |
| `src/nimbus/core/memory/mmu.py` | L878-957 | archive_and_reset 压缩入口 |
| `src/nimbus/core/memory/context.py` | L42-148 | Message 数据结构和 token 估算 |
| `src/nimbus/core/memory/context.py` | L156-214 | PinnedContext 定义 |
| `src/nimbus/core/memory/state_manager.py` | L35-168 | StateManager 确定性状态追踪 |
| `src/nimbus/tools/memo.py` | L50-141 | MemoManager 完整实现 |
| `src/nimbus/core/compaction.py` | L133-221 | DefaultCompactionLLM 摘要生成 |
| `src/nimbus/core/session.py` | L133-620 | SessionManager JSONL 持久化 |
| `src/nimbus/core/persistence.py` | L1-86 | Pydantic 序列化模型 |
| `src/nimbus/agentos.py` | L418-421 | Memo 创建和挂载 |
| `src/nimbus/agentos.py` | L1003-1111 | 压缩流程: Memo 读取 + 摘要生成 + Milestone 提取 |
| `docs/infinite-context-insight.md` | 全文 | 现有 Infinite Context 的架构分析和改进方向 |
