# Nimbus 记忆系统改进方案 v2

**日期**: 2026-02-16
**作者**: Architect
**状态**: 修订稿 (基于 Review Committee 意见修订)
**前序文档**:
- 原始对比报告: `docs/memory-comparison-report.md`
- Review 意见: `docs/reviews/20260216_104247_nimbus-memory-improvement-report.md`

---

## 0. 修订摘要

本文档是 Nimbus 记忆系统改进方案的第二版。在保留原方案"增量式路线图、代码引用、P0/P1/P2 分层"优势的基础上，根据两位审稿人（`anthropic/claude-opus-4-6` 评分 7/10, `openai-codex/gpt-5.3-codex` 评分 8.4/10）的共识意见做出以下关键修订:

| 修订项 | 变化 | 驱动意见 |
|--------|------|----------|
| Global Memo | "全量注入" -> "预算化分层注入" | Opus C3, Codex Issue 1 |
| SearchHistory FTS5 | P1 -> P0 (与 Global Memo 并行) | Codex Rec#1 |
| 结构化摘要 | P0 -> P1 (需先建 Schema) | Codex Issue 2 |
| Token 预算表 | 新增，覆盖所有注入源 | Opus Rec#2, Codex Issue 1 |
| 验收指标 | 新增量化标准 | Opus m3, Codex Issue 5 |
| 并发模型 | 新增 Global Memo 并发安全设计 | Opus C1 |

---

## 1. 现状与核心问题

### 1.1 架构现状

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

**证据**: `src/nimbus/core/memory/mmu.py:54-66` (MMUConfig), `src/nimbus/core/memory/mmu.py:540-561` (assemble_context Memo 注入)

### 1.2 核心痛点

**"能记住但无法回忆"** -- Nimbus 有 JSONL 永久存储 (Session logs)，但不可检索、不可跨会话复用。

| 痛点 | 现状 | 影响 |
|------|------|------|
| 跨会话失忆 | Memo 与 session_id 绑定 (`memo_{session_id}.md`) | 新会话从零开始 |
| 无检索能力 | JSONL 无索引，Agent 无法搜索历史 | 无法回答"上次关于 X 的结论是什么？" |
| 压缩级联丢失 | 多次 LLM 摘要后信息递减 | 长任务后期关键信息消失 |
| 摘要非结构化 | `generate_summary` 仅输出 `NEW_MILESTONES` + 自由文本 | 无法按类型检索事实 |

**证据**: `src/nimbus/tools/memo.py:55-59` (session_id 绑定), `src/nimbus/agentos.py:1084-1093` (仅解析 MILESTONES)

---

## 2. Token 预算总表

> **审稿意见驱动**: Opus Rec#2 指出"Every new context injection point must specify maximum token allocation"; Codex Issue 1 指出"全量注入会与 Anchor/Hot context 争抢 token"。

本方案所有新增注入源均需遵守以下预算契约:

```
Context Window: 200,000 tokens (output buffer 20k, effective 180k)
│
├── Pinned Context (Anchor):       10,000  [existing, unchanged]
│   ├── System Rules
│   ├── Workspace Info
│   ├── Capabilities
│   ├── User Goal
│   └── Milestones
│
├── Global Summary:                   600  [existing, unchanged]
│
├── Session Memo:                   2,000  [existing, ADD hard cap]
│
├── Global Memo Header:               800  [NEW - P0-1, hard cap]
│   └── 常驻摘要块，超限自动 LLM 压缩
│
├── Project State Monitor:          1,000  [existing, unchanged]
│   ├── File Working Set
│   └── Last Command Status
│
├── Relevant Facts:                 2,000  [NEW - P1-2, on-demand]
│   └── 按当前任务相关性注入，来自 FactStore
│
├── Search Results:                 3,000  [NEW - P0-2, on-demand]
│   └── 仅 SearchHistory 工具调用时占用
│
├── Hot Context (~15 msgs):       ~30,000  [existing, unchanged]
│
└── Historical Window:           remaining  [existing, absorbs pressure]
    └── ≈130,600 tokens (最小保障 >100k)
```

**预算规则**:
1. **Hard cap**: Anchor, Global Summary, Session Memo, Global Memo Header 为硬上限，超限触发压缩/裁剪
2. **On-demand**: Search Results, Relevant Facts 仅在工具调用时临时占用，不常驻
3. **Pressure sink**: Historical Window 作为弹性缓冲，吸收其他分区的波动
4. **监控**: `assemble_context` 中每个 section 记录实际 token 消耗到 debug log

**证据**: `src/nimbus/core/memory/mmu.py:503-569` (现有 assemble_context 已有 token_count 累加逻辑)

---

## 3. 修订后的优先级路线

```
Phase 0 (本周)                     Phase 1 (2-4周)              Phase 2 (1-2月)
┌──────────────────┐              ┌──────────────┐            ┌──────────────┐
│ P0-1 预算化 Memo  │              │P1-1 事实分类  │            │P2-1 Bank 库  │
│  (分层注入+并发)   │              │ (Schema 化)   │            │P2-2 Reflect  │
│ P0-2 SearchHistory│──依赖───────▶│P1-2 自动提取  │──依赖─────▶│P2-3 向量检索  │
│  (FTS5 最小闭环)  │              │P1-3 结构化摘要│            │              │
└──────────────────┘              └──────────────┘            └──────────────┘
     ↓                                  ↓                          ↓
  即可部署                          需要 Schema 定义              需要架构评审
  改动 < 200 行                     新增 ~600 行代码              新增 ~2000 行代码
```

**关键变化 vs 原方案**:

| 项目 | 原方案优先级 | 修订后优先级 | 原因 |
|------|------------|------------|------|
| SearchHistory + FTS5 | P1-1 | **P0-2** | Codex Rec#1: "先建 Recall 最小闭环" |
| 结构化摘要 | P0-2 | **P1-3** | Codex Issue 2: "需先建 Schema，避免脆弱文本解析" |
| Global Memo | P0-1 (全量注入) | **P0-1 (预算化分层)** | Opus C3 + Codex Issue 1: 预算化 |
| 压缩感知 | P0-3 | **并入 P1-2** | 依赖结构化提取 Schema，不宜独立 |

**依赖关系**:
- P0-1 (预算化 Memo) 与 P0-2 (SearchHistory) 可并行
- P1-1 (事实分类 Schema) 是 P1-2 (自动提取) 的前提
- P1-3 (结构化摘要) 依赖 P1-1 的 Schema 定义
- P2-1 (Bank) 依赖 P1-1 分类后的事实作为输入
- P2-3 (向量检索) 依赖 P0-2 的 SQLite 基础设施

---

## 4. Phase 0: 立即实施 (本周)

### P0-1: 预算化 Global Memo (分层注入 + 并发安全)

#### 现状
- Memo 与 session_id 绑定: `self.memo_file = self.memo_dir / f"memo_{session_id}.md"` (`src/nimbus/tools/memo.py:59`)
- 新会话创建新 Memo 文件，旧会话记忆不可见
- Memo 全量注入 context，无 token 上限 (`src/nimbus/core/memory/mmu.py:545-561`)

#### 问题
- **Opus C1**: 全局 Memo 引入共享可变状态，无并发模型
- **Opus C3 / Codex Issue 1**: 全量注入无预算控制，会与 Anchor/Hot context 争抢 token
- **Opus Rec#1**: 需在实现前定义并发和一致性模型

#### 改进方案

**4.1.1 分层注入架构**

```
memo_global.md 文件结构:
┌──────────────────────────────────────────┐
│ ## Header (常驻摘要, <=800 tokens)       │  <-- 始终注入 context
│ - 项目核心决策 (3-5 条)                   │
│ - 关键技术栈/约束                         │
│ - 活跃工作流状态                          │
├──────────────────────────────────────────┤
│ ## Body (详细内容, 无限增长)              │  <-- 仅工具检索时返回
│ - 按时间倒序的详细条目                    │
│ - 带 session 标签和时间戳                 │
│ - 过期条目标记 [stale]                   │
└──────────────────────────────────────────┘
```

- **Header**: 由 `<!-- HEADER_START -->` / `<!-- HEADER_END -->` 标记分隔，硬上限 800 tokens
- **Header 超限处理**: 当 Header 超过 800 tokens 时，调用 LLM 自动摘要压缩（复用现有 `compress_summary` 逻辑）
- **Body 检索**: Agent 通过 `Memo(action="read", scope="global")` 工具调用拉取全文，返回结果计入 Search Results 预算 (3000 tokens)

**4.1.2 并发安全模型**

选择 **append-only 语义 + session 标签** 方案:

| 方案 | 优点 | 缺点 | 选择 |
|------|------|------|------|
| A. 文件锁 (flock) | 简单 | 跨平台兼容差，死锁风险 | 不选 |
| B. SQLite WAL | 原子性好 | 引入额外存储层 | 备选 |
| **C. Append-only + session tag** | 无冲突，简单可靠 | Header 更新需合并 | **选择** |

具体设计:
- Body 部分: 所有写入均为 append-only，每条条目带 `[session:{id}] [time:{iso}]` 标签
- Header 部分: 仅在单个 session 的 compaction 结束时由该 session 重新生成（读取全部 Body -> LLM 摘要 -> 覆写 Header）
- Header 覆写冲突: 即使两个 session 几乎同时覆写，由于 Header 是 Body 的派生摘要，last-write-wins 语义可接受（不丢失 Body 数据）
- **硬上限保护**: Global Memo 全文(Header + Body)超过 3000 tokens 时，触发 LLM 自动摘要裁剪 Body 旧条目

**4.1.3 Memo 工具接口变更**

```python
# 扩展 MEMO_TOOL_DEF.parameters.properties
"scope": {
    "type": "string",
    "enum": ["session", "global"],
    "default": "session",
    "description": "session: current session only; global: cross-session persistent"
}
```

#### 影响范围

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/nimbus/tools/memo.py` | MemoManager 增加 `global_memo_file` 属性，`scope` 参数路由 | 核心变更 |
| `src/nimbus/tools/memo.py:18-47` | MEMO_TOOL_DEF 增加 `scope` 参数 | 工具定义 |
| `src/nimbus/core/memory/mmu.py:545-561` | assemble_context 分离注入: session memo + global header | 注入逻辑 |
| `src/nimbus/agentos.py:418-421` | 创建 memo_manager 时初始化 global memo | 初始化 |

#### 验收标准
- [ ] Global Memo Header 始终 <=800 tokens（超限自动压缩）
- [ ] 新会话启动时能看到上一会话写入的 Global Memo Header
- [ ] 两个并发 session append 到 Global Memo Body 不丢数据
- [ ] `assemble_context` debug log 显示 Global Memo Header 实际 token 消耗

---

### P0-2: SearchHistory -- FTS5 最小闭环

#### 现状
- Session JSONL 是 append-only 的平面文件，无索引: `src/nimbus/core/session.py:133-156`
- Agent 无法搜索历史会话，完全依赖当前 context window

#### 问题
- **Codex Rec#1**: "先建立可检索回忆的最小闭环"是最高优先
- **Opus O1**: Nimbus 最缺的是 Retrieval Contract，而不是更多摘要
- **Codex Observation 2**: "Nimbus 当前最缺的是 Retrieval Contract"

#### 改进方案

**4.2.1 存储架构**

```
~/.nimbus/
├── sessions/                    # 真相源 (JSONL, 不变)
│   └── YYYY-MM-DD/
│       └── {session_id}.jsonl
└── memory_index.sqlite          # 派生索引 (可重建)
    ├── messages_fts (FTS5)      # 全文索引
    ├── sessions_meta            # 会话元数据
    └── index_state              # 索引状态追踪
```

**4.2.2 SQLite Schema**

```sql
-- 会话元数据
CREATE TABLE sessions_meta (
    session_id TEXT PRIMARY KEY,
    start_time TEXT NOT NULL,
    workspace TEXT,
    goal_summary TEXT,
    entry_count INTEGER DEFAULT 0
);

-- FTS5 全文索引
CREATE VIRTUAL TABLE messages_fts USING fts5(
    session_id,
    role,
    content,
    entry_id UNINDEXED,
    timestamp UNINDEXED,
    tokenize='unicode61'
);

-- 索引状态 (用于增量更新和崩溃恢复)
CREATE TABLE index_state (
    session_id TEXT NOT NULL,
    last_indexed_entry_id TEXT,
    last_indexed_offset INTEGER,  -- JSONL 文件字节偏移
    status TEXT CHECK(status IN ('pending','indexed','failed','rebuilding')),
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id)
);
```

**4.2.3 索引一致性策略** (回应 Opus C2, Codex Issue 3)

选择 **增量同步 + 启动时 gap scan** 方案:

- **写入时**: `session.py:_persist_entry` 成功写入 JSONL 后，同步更新 FTS 索引
- **写入失败**: 如果 SQLite 写入失败 (磁盘满等)，记录 `status='failed'`，不阻塞主流程
- **启动时**: gap scan -- 对比 JSONL 实际条目数与 `index_state.last_indexed_offset`，补索引缺口
- **重建命令**: 提供 `nimbus index rebuild` CLI (遍历全部 JSONL 重建 FTS)
- **重建 SLO**: 10,000 条消息 < 30 秒 (SQLite FTS5 批量插入性能)
- **幂等键**: `entry_id` (JSONL 中已有 UUID)，防止重复索引

**4.2.4 JSONL 树结构处理** (回应 Opus C2)

JSONL 存储的是树结构消息 (通过 `parentId` 关联)。FTS 索引采用扁平化策略:
- 每条 JSONL entry 独立索引为一行
- `entry_id` 保留用于回溯到原始树结构
- 搜索结果返回时包含 `session_id` + `entry_id`，Agent 可进一步通过 session 导航获取上下文

**4.2.5 SearchHistory 工具定义**

```python
SEARCH_HISTORY_TOOL_DEF = {
    "name": "SearchHistory",
    "description": (
        "Search through past session history using full-text search. "
        "Returns matching messages with session_id and context. "
        "Use when you need to recall past decisions, configurations, or discussions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (supports FTS5 syntax: AND, OR, NOT, phrase \"...\")"
            },
            "session_scope": {
                "type": "string",
                "enum": ["current", "recent", "all"],
                "default": "all",
                "description": "current: this session; recent: last 7 days; all: everything"
            },
            "limit": {
                "type": "integer",
                "default": 5,
                "description": "Max results to return (each result ~200 tokens)"
            }
        },
        "required": ["query"]
    }
}
```

**结果格式** (回应 Opus m4):
- 每条结果: snippet (前后 50 chars context) + session_id + timestamp + role
- 单条结果 ~200 tokens，默认 5 条 = ~1000 tokens (在 3000 token 预算内)
- 最大 limit=15 (15 * 200 = 3000 tokens)

**调用策略**: Agent 自主决定何时搜索 (工具描述中提供足够指引)，不做自动触发。

#### 影响范围

| 文件 | 变更 | 说明 |
|------|------|------|
| 新文件: `src/nimbus/tools/search_history.py` | SearchHistory 工具 + FTS 查询逻辑 | 核心新增 |
| 新文件: `src/nimbus/core/memory/memory_index.py` | SQLite 索引管理 (创建/更新/重建) | 索引层 |
| `src/nimbus/core/session.py:_persist_entry` | 写入 JSONL 后调用索引更新 hook | 同步钩子 |
| `src/nimbus/agentos.py` | 注册 SearchHistory 工具 | 工具注册 |

#### 验收标准
- [ ] 能回答"上次关于 X 的结论是什么？"并给出 session_id + 时间戳引用
- [ ] 跨会话信息引用成功率 > 80% (20 个预设问题的 recall 测试)
- [ ] FTS 查询延迟 < 100ms (1 万条消息规模)
- [ ] JSONL 写入失败不影响主流程 (索引降级为 pending)
- [ ] `nimbus index rebuild` 能从 JSONL 完整重建索引

---

## 5. Phase 1: 短期目标 (2-4 周)

### P1-1: 事实分类 Schema 化

#### 现状
- 摘要输出为非结构化文本，仅解析 `NEW_MILESTONES:` 和 `SUMMARY:`
- 无事实分类体系

#### 问题
- **Codex Issue 2**: "结构化抽取缺少模式约束 (Schema) 与失败回退路径"
- **Opus M2**: "Adding more structured fields multiplies the parsing fragility"
- **Opus M4**: W/B/O/S 分类需针对 Nimbus 实际工作负载验证

#### 改进方案

**5.1.1 Nimbus 适配的事实分类**

> 采纳 Opus M4 建议: 不直接照搬 OpenClaw 的 W/B/O/S，而是基于编程 Agent 工作负载设计分类。

| 类型代码 | 名称 | 描述 | 示例 |
|----------|------|------|------|
| `CFG` | Configuration | 项目配置、环境变量、连接参数 | "Colima Docker 用 192.168.5.2 做 host gateway" |
| `DEC` | Decision | 技术决策和设计选择 | "选择 JWT 而非 Session 做认证" |
| `BUG` | Bug/Fix | 问题诊断和修复记录 | "SandboxError 构造参数顺序: (path, workspace, message)" |
| `PAT` | Pattern | 可复用的经验模式 | "pip install 有 7 种策略包括 --break-system-packages" |
| `SUM` | Summary | 阶段性摘要 | "Phase 2 完成了 base_url 硬编码消除" |

**5.1.2 Pydantic Schema** (回应 Codex Issue 2)

```python
# src/nimbus/core/memory/fact_schema.py

from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime

class FactV1(BaseModel):
    """Version 1 fact schema - structured knowledge unit."""
    schema_version: Literal["fact_v1"] = "fact_v1"
    fact_type: Literal["CFG", "DEC", "BUG", "PAT", "SUM"]
    content: str                         # 事实内容 (自然语言)
    source_session_id: str               # 来源会话
    source_message_id: Optional[str]     # 来源消息 ID (可追溯)
    confidence: Literal["verified", "inferred", "uncertain"] = "inferred"
    created_at: datetime = Field(default_factory=datetime.now)
    tags: list[str] = Field(default_factory=list)  # 自由标签

class SummaryV1(BaseModel):
    """Version 1 summary schema - compaction output."""
    schema_version: Literal["summary_v1"] = "summary_v1"
    milestones: list[str] = Field(default_factory=list)
    key_files: list[str] = Field(default_factory=list)
    key_decisions: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    summary_text: str                    # 自由文本摘要
    source_message_range: tuple[str, str] = ("", "")  # (first_id, last_id)
    extracted_facts: list[FactV1] = Field(default_factory=list)
```

**5.1.3 解析失败降级** (回应 Codex Issue 2)

```
LLM 输出 -> JSON 解析
  ├── 成功 -> FactV1/SummaryV1 对象 -> 存入 FactStore
  ├── 部分成功 -> 尽量解析已有字段 + 缺失字段置空 -> 标记 confidence="uncertain"
  └── 完全失败 -> 保存原始文本 -> 标记 _parse_failed=True -> 加入重试队列
```

- 解析成功率作为监控指标 (目标 > 90%)
- 重试队列: 累积 5 条失败后批量重试 (不同 prompt 策略)

#### 影响范围

| 文件 | 变更 | 说明 |
|------|------|------|
| 新文件: `src/nimbus/core/memory/fact_schema.py` | FactV1, SummaryV1 Pydantic 模型 | Schema 定义 |
| 新文件: `src/nimbus/core/memory/fact_store.py` | FactStore: 事实的 CRUD + 分类查询 | 存储层 |
| `src/nimbus/core/persistence.py` | 新增 FactModel, 扩展 MemorySnapshotModel | 持久化扩展 |

#### 验收标准
- [ ] FactV1/SummaryV1 Schema 通过 Pydantic 验证 (100% 已知格式)
- [ ] 解析失败降级为纯文本保存，不丢失任何输出
- [ ] 每条 Fact 可追溯到 source_session_id + source_message_id

---

### P1-2: 自动事实提取 (半自动 Retain)

#### 现状
- Agent 必须手动调用 Memo 工具写入笔记
- Compaction 时读取 Memo 内容作为摘要上下文 (`src/nimbus/agentos.py:1008-1016`)，但不自动提取事实

#### 问题
- Agent 经常忘记写 Memo，关键信息随 compaction 丢失
- 原方案 P0-3 (压缩感知) 缺少 Schema 支撑，现合并到此

#### 改进方案

**Compaction 触发时自动提取流程**:

```
archive_and_reset() 触发
       │
       ▼
  被丢弃的消息 messages_to_archive
       │
       ▼
  规则提取 (正则/确定性):
  ├── 文件路径 -> CFG/PAT
  ├── 错误信息 + 修复 -> BUG
  ├── 配置值 (IP/端口/密码) -> CFG
  └── "决定/选择/改为" 关键词 -> DEC
       │
       ▼
  LLM 提取 (结构化 prompt):
  ├── 输入: 被丢弃消息 + SummaryV1 schema
  ├── 输出: JSON -> SummaryV1 解析
  └── 失败降级: 纯文本保存
       │
       ▼
  去重: 与 FactStore 已有事实比对 (content 相似度)
       │
       ▼
  写入 FactStore + 可选追加到 Global Memo Body
```

- 规则提取优先（零 LLM 成本），LLM 提取作为补充
- 提取结果与 Memo 已有内容去重，避免重复

#### 影响范围

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/nimbus/core/memory/mmu.py:878-957` | archive_and_reset 增加提取 hook | 触发入口 |
| `src/nimbus/agentos.py:1018-1070` | summary_prompt 扩展为 SummaryV1 JSON 输出 | Prompt 改造 |
| `src/nimbus/tools/memo.py` | MemoManager 增加 `auto_append_facts` 方法 | 自动写入 |
| 新文件: `src/nimbus/core/memory/fact_extractor.py` | 规则提取 + LLM 提取流水线 | 提取逻辑 |

#### 验收标准
- [ ] Compaction 后自动提取的事实数 >= 1 (非空验证)
- [ ] 规则提取覆盖 > 50% 的文件路径和配置值 (与人工标注对比)
- [ ] LLM 结构化输出解析成功率 > 90%
- [ ] 关键事实压缩保留率: compaction 前后，人工标注的关键事实 >= 80% 可在 FactStore 或 Memo 中找到

---

### P1-3: 结构化摘要 (基于 Schema)

#### 现状
- `generate_summary` 输出格式: `NEW_MILESTONES: [...]\nSUMMARY: [...]`
- 仅 regex 解析 milestones，summary 为自由文本
- 解析逻辑: `src/nimbus/agentos.py:1084-1093`

#### 问题
- **Opus M2**: "Adding more structured fields multiplies the parsing fragility"
- **Codex Issue 2**: "仅靠 prompt 格式会导致解析脆弱、版本升级困难"

#### 改进方案

将 summary prompt 改为要求 JSON 输出，用 SummaryV1 Schema 解析:

```python
summary_prompt = f"""请作为任务管理者，总结当前执行状态。
请以 JSON 格式输出，schema 如下:
{{
  "milestones": ["milestone1", ...],
  "key_files": ["file1", ...],
  "key_decisions": ["decision1", ...],
  "blockers": ["blocker1", ...],
  "summary_text": "自由文本摘要",
  "extracted_facts": [
    {{"fact_type": "CFG|DEC|BUG|PAT|SUM", "content": "...", "confidence": "verified|inferred"}}
  ]
}}

【对话内容】
{context}

请用中文回复，summary_text 控制在 {target_chars} 字以内。
"""
```

- 解析: `json.loads()` -> `SummaryV1.model_validate()`
- 失败降级: 回退到当前 regex 解析逻辑 (NEW_MILESTONES + SUMMARY)
- 逐步替换: 先保留旧解析作为 fallback，新旧并行运行 2 周评估成功率后再移除旧逻辑

#### 影响范围

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/nimbus/agentos.py:1047-1070` | summary_prompt 改为 JSON 格式 | Prompt 重构 |
| `src/nimbus/agentos.py:1084-1093` | 解析逻辑改为 SummaryV1 + fallback | 解析升级 |

#### 验收标准
- [ ] JSON 解析成功率 > 90% (1000 次 compaction 采样)
- [ ] 失败降级不丢失 milestone 提取能力 (回退到旧 regex)
- [ ] SummaryV1 中 extracted_facts 平均 >= 2 条/次 compaction

---

## 6. Phase 2: 长期目标 (1-2 月)

### P2-1: Bank 知识库 (实体中心存储)

```
.nimbus/
├── bank/
│   ├── entities/
│   │   ├── nimbus-project.md
│   │   ├── direct-adapter.md
│   │   └── harbor-integration.md
│   ├── decisions/
│   │   └── adr-001-no-pickle.md
│   └── patterns/
│       ├── docker-colima-gateway.md
│       └── pip-install-strategies.md
```

- 每个实体页: 描述 + 相关事实 (带来源引用) + 关联实体 + 最近更新时间
- 事实来源: FactStore 中 `confidence="verified"` 的条目
- 冲突处理: 事实生命周期模型 `proposed -> verified -> stale -> retracted` (回应 Codex Issue 4)
- 引用链: 每条事实保留 `source_session_id + source_message_id + timestamp`

### P2-2: Reflect 定期整理循环

- **触发策略** (回应 Opus M5): 会话结束时异步触发，不阻塞主对话
- 实现: 后台 job (asyncio.create_task)，可中断、可重试
- 操作: 扫描新增 Facts -> 更新实体页 -> 合并冲突 -> 标记过时条目为 stale

### P2-3: 嵌入向量检索

- 在 FTS5 基础上增加语义检索
- 使用 sqlite-vec 扩展或独立向量存储
- Recall 同时支持词法匹配 (FTS5) + 语义相似度

---

## 7. 量化验收指标

> **审稿意见驱动**: Opus m3 + Codex Issue 5 共识要求"量化验收"

### Phase 0 指标

| 指标 | 目标 | 测量方法 |
|------|------|----------|
| 跨会话信息引用成功率 | > 80% | 20 个预设历史问题，新会话中通过 SearchHistory 或 Global Memo 能正确回答 |
| Global Memo Header token | <= 800 | assemble_context debug log |
| FTS 查询延迟 | < 100ms | 1 万条消息规模下的 P95 |
| 并发写入数据完整性 | 100% | 两个 session 并发 append 50 次，Body 条目数 = 100 |

### Phase 1 指标

| 指标 | 目标 | 测量方法 |
|------|------|----------|
| 历史问题命中率 Recall@5 | > 70% | 50 个标注问题，前 5 条 FTS 结果包含正确答案 |
| 重复提问率下降 | > 30% 降幅 | 对比改进前后，Agent 在同一 workspace 重复问相同问题的次数 |
| 结构化解析成功率 | > 90% | SummaryV1 JSON 解析成功 / 总 compaction 次数 |
| 关键事实压缩保留率 | > 80% | 人工标注 20 个关键事实，compaction 后可在 FactStore/Memo 中找到 |

### Phase 2 指标

| 指标 | 目标 | 测量方法 |
|------|------|----------|
| Bank 实体页覆盖率 | > 60% | workspace 中主要模块/组件有对应实体页 |
| 语义检索 NDCG@5 | > 0.5 | 50 个语义查询的排序质量 |

---

## 8. 不适用的部分

以下 OpenClaw 设计不建议直接移植到 Nimbus (维持原方案判断，根据 Review 微调):

### 8.1 Markdown 作为唯一真相源

**不适用原因**: Nimbus 的会话历史包含大量 tool_calls 和结构化 JSON，Markdown 格式会导致信息损失。JSONL 仍是更合适的原始存储格式。

> **Review 微调** (Opus O2): 认同 "SQLite 做真相源，Markdown 做视图/导出" 可能更适合编程 Agent，但当前阶段维持 "JSONL 真相源 + SQLite 派生索引" 的设计。理由: JSONL 已被广泛使用，迁移成本高；且 JSONL 的人类可读性虽不如 Markdown 但足够调试使用。

### 8.2 每日日志文件

**不适用原因**: Nimbus 已有 JSONL 会话日志，功能重叠。Global Memo 的 Body 部分（append-only, 带时间戳）已承担类似职责。

### 8.3 观点置信度精细管理

**不适用原因**: 编程 Agent 主要处理技术事实，简化为 `verified/inferred/uncertain` 三级即可。

> **Review 微调**: 将原方案的"已验证/待验证"二分扩展为三级 (`verified/inferred/uncertain`)，为未来 Reflect 预留演化空间。

### 8.4 复杂实体关系图

**不适用原因**: 编程 Agent 的"实体"主要是文件/模块/配置项，这些关系已由代码结构本身表达。StateManager (`src/nimbus/core/memory/state_manager.py`) 已以更高效方式追踪文件工作集。

---

## 9. 迁移策略

> **审稿意见驱动**: Opus M3 指出"No migration strategy for existing users"

### 9.1 既有数据处理

| 数据 | 迁移方式 | 风险 |
|------|----------|------|
| 旧 Session JSONL | `nimbus index rebuild` 批量建 FTS 索引 | 大量历史数据首次索引耗时 |
| 旧 session Memo | 不自动合并到 Global Memo (内容质量不确定) | 用户可手动迁移 |
| SQLite Checkpoint | schema_version 升级: v1 -> v2 (新增 FactStore 表) | 需 ALTER TABLE 兼容 |

### 9.2 版本兼容

- 新版 Nimbus 读取旧 workspace: 自动创建缺失的 `memo_global.md` 和 `memory_index.sqlite`
- 旧版 Nimbus 读取新 workspace: 忽略不认识的文件 (Global Memo, FTS index)，不崩溃
- Schema 版本: `SessionCheckpointModel.schema_version` 从 1 升到 2，旧版遇到 v2 报 warning 但仍可加载基础字段

---

## 10. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Global Memo Header 超限导致 context 挤压 | 中 | 高 | 800 token hard cap + LLM 自动压缩 |
| FTS 索引与 JSONL 不同步 | 低 | 中 | 启动时 gap scan + `nimbus index rebuild` |
| 自动提取的事实质量低 (false positive) | 中 | 中 | 规则提取优先 + confidence 标记 + 人工抽查 |
| 并发写入 Global Memo Body 条目交错 | 低 | 低 | append-only 语义，每条带 session tag，交错不影响完整性 |
| 增加工具数量降低 Agent 决策质量 | 低 | 高 | SearchHistory 工具描述精确，不做自动触发 |
| LLM JSON 输出格式不稳定 | 中 | 中 | Pydantic 验证 + fallback 到 regex 解析 |
| 过度工程化 (P2 阶段) | 中 | 中 | 每阶段 gate review，P1 指标未达标则暂缓 P2 |
| 敏感信息泄露到 Global Memo | 低 | 高 | Memo 仅存储于本地 workspace，不联网；P2 考虑 PII redaction |

---

## 11. Review 意见采纳说明

### Opus (claude-opus-4-6) 意见处理

| 编号 | 级别 | 意见 | 处理 | 说明 |
|------|------|------|------|------|
| C1 | Critical | Global Memo 无并发模型 | **采纳** | P0-1 新增 append-only + session tag 并发方案 |
| C2 | Critical | FTS 索引一致性 hand-waved | **采纳** | P0-2 新增索引状态机、gap scan、重建 SLO |
| C3 | Critical | Token 预算影响缺失 | **采纳** | 新增 Section 2 Token 预算总表 |
| M1 | Major | OpenClaw 分析缺少批评 | **部分采纳** | Section 8 微调了不适用理由，但未单独建 Section 3.4 (篇幅考虑) |
| M2 | Major | 结构化摘要假设 LLM 合规 | **采纳** | P1-3 改为 JSON 输出 + Pydantic 验证 + fallback |
| M3 | Major | 无迁移策略 | **采纳** | 新增 Section 9 迁移策略 |
| M4 | Major | W/B/O/S 分类需验证 | **采纳** | P1-1 改为 Nimbus 适配分类 (CFG/DEC/BUG/PAT/SUM) |
| M5 | Major | Reflect 触发策略未定义 | **部分采纳** | P2-2 明确"会话结束异步触发"，详细策略留到 P2 设计阶段 |
| m1 | Minor | 工作量估算偏低 | **采纳** | Phase 0 从 "<100 行" 调整为 "<200 行"，P1 从 "~500 行" 调整为 "~600 行" |
| m2 | Minor | 图表中 20 条 vs 15 条不一致 | **记录** | `keep_recent_messages=20` (archive_and_reset), Hot Context 是 assemble 时的显示窗口，两者是不同参数 |
| m3 | Minor | 缺少验收指标 | **采纳** | 新增 Section 7 量化验收指标 |
| m4 | Minor | SearchHistory 结果格式未定义 | **采纳** | P0-2 明确了 snippet 格式和 token 预算 |
| O1 | Obs | 检索相关性是最难问题 | **记录** | P0-2 先用 FTS5 词法匹配，P2-3 再引入语义检索 |
| O2 | Obs | SQLite 做真相源可能更合适 | **不采纳** | 维持 JSONL 真相源 + SQLite 派生索引，理由见 Section 8.1 |
| O3 | Obs | Memo 到 Bank 是自然演进 | **采纳** | 路线图已体现此渐进结构 |
| O4 | Obs | Context window 可能 10x | **记录** | 当前架构在大 window 下仍有价值 (检索效率 + 成本控制)，不影响设计方向 |
| Rec#1 | - | 先建并发一致性模型 | **采纳** | P0-1 前置 |
| Rec#2 | - | 定义 Token 预算表 | **采纳** | Section 2 |
| Rec#3 | - | 验证事实分类 | **采纳** | P1-1 改为数据驱动分类 |

### Codex (gpt-5.3-codex) 意见处理

| 编号 | 级别 | 意见 | 处理 | 说明 |
|------|------|------|------|------|
| Issue 1 | Critical | Global Memo 缺硬预算 | **采纳** | P0-1 改为预算化分层注入 (Header 800 tokens) |
| Issue 2 | Critical | 结构化提取缺 Schema | **采纳** | P1-1 新增 Pydantic Schema + 失败降级 |
| Issue 3 | Major | FTS 一致性/重建策略 | **采纳** | P0-2 新增索引状态机 |
| Issue 4 | Major | Bank 冲突处理语义 | **部分采纳** | P2-1 明确事实生命周期，详细设计留到 P2 |
| Issue 5 | Major | 缺量化验收指标 | **采纳** | Section 7 |
| Issue 6 | Minor | "跨会话持久化: 无" 表述过绝对 | **采纳** | 改为"有持久化 (JSONL)，无可用 recall" |
| Issue 7 | Minor | 安全/隐私风险 | **部分采纳** | 风险表新增敏感信息条目，PII redaction 列为 P2 考虑 |
| Obs 1 | - | 坚持 SoT 与 Derived Views 分离 | **采纳** | 全方案遵循 |
| Obs 2 | - | 最缺 Retrieval Contract | **采纳** | SearchHistory 提升到 P0 |
| Obs 3 | - | P0-2/P1-2 合并为统一 Pipeline | **部分采纳** | 共用 SummaryV1 Schema，但保持阶段性独立交付 |
| Obs 4 | - | Reflect 应异步化 | **采纳** | P2-2 明确异步 |
| Rec#1 | - | 先建 Recall 最小闭环 | **采纳** | FTS 从 P1 提升到 P0 |
| Rec#2 | - | Schema + 容错 | **采纳** | P1-1 |
| Rec#3 | - | Global Memo 预算化 | **采纳** | P0-1 |

---

## 12. 证据索引

| 文件 | 关键行号 | 内容 |
|------|---------|------|
| `src/nimbus/core/memory/mmu.py` | L54-66 | MMUConfig: max_context_tokens=180000, keep_recent_messages=20 |
| `src/nimbus/core/memory/mmu.py` | L503-569 | assemble_context: 上下文组装 + Memo 注入逻辑 |
| `src/nimbus/core/memory/mmu.py` | L878-957 | archive_and_reset: 压缩入口 + 消息切分 |
| `src/nimbus/tools/memo.py` | L50-141 | MemoManager: session_id 绑定 + CRUD |
| `src/nimbus/tools/memo.py` | L55-59 | memo_file = memo_dir / f"memo_{session_id}.md" |
| `src/nimbus/agentos.py` | L418-421 | Memo 创建 + mmu._memo_manager 挂载 |
| `src/nimbus/agentos.py` | L1008-1016 | Compaction 时读取 Memo 内容 |
| `src/nimbus/agentos.py` | L1047-1070 | summary_prompt 模板 (NEW_MILESTONES + SUMMARY) |
| `src/nimbus/agentos.py` | L1084-1093 | Milestone 解析逻辑 (regex) |
| `src/nimbus/core/session.py` | L133-186 | SessionManager: JSONL + Tree 结构 |
| `src/nimbus/core/persistence.py` | L1-86 | Pydantic 模型: schema_version=1 |
| `src/nimbus/core/memory/state_manager.py` | L35-168 | StateManager: File Working Set |

---

## 13. 下一步行动

1. **本周**: 启动 P0-1 (预算化 Global Memo) 和 P0-2 (SearchHistory FTS5) 并行开发
2. **P0 交付后**: 运行 20 个预设问题的 recall 测试，验证跨会话信息引用成功率 > 80%
3. **第 2 周末**: P0 gate review -- 如指标达标，启动 P1; 如不达标，优先修复
4. **P1 启动前**: 分析 20-30 个真实 Nimbus session，验证事实分类体系 (CFG/DEC/BUG/PAT/SUM)
5. **P1 交付后**: gate review -- 评估是否需要启动 P2，或 P0+P1 已足够满足需求
