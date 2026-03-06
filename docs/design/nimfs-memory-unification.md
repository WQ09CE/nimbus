# NimFS Memory 统一方案：简洁工具设计

**日期**: 2026-03-06  
**状态**: RFC  
**前序文档**: `docs/design/nimbus-v3-memory-proposal.md`（过度复杂，本文档是其极简替代）

---

## 0. TL;DR

> 把 3 套并行的 Memory 系统统一到 NimFS 上，用 **4 个简洁工具** 替代现有的 11 个工具。
> 核心思路：NimFS 的虚拟文件系统本身就是好的抽象，问题不在 NimFS 而在工具设计太复杂。

**一句话**：砍掉 L0/L1/L2 三层 → 只留 content + summary；砍掉 6 分类 → 无分类，用 tags 自由标注；4 个工具覆盖全部需求。

---

## 1. 现状问题

### 1.1 三套并行系统

| 系统 | 存储位置 | 工具数量 | 问题 |
|------|---------|---------|------|
| NimFS Memory | `~/.nimbus/fs/{project}/memory/` | 6 个 | L0/L1/L2 三层 + 6 分类，太复杂，LLM 不愿用 |
| Memory V3 | `.nimbus/memory/*.md` | 0（用 Write/Bash） | 无隔离、无元数据、无搜索、格式不一致 |
| 旧版 Store | `.nimbus/{procedural,semantic_profile,sessions}/` | 5 个 | 独立于 NimFS，数据分散，功能重叠 |

**总计 11 个 Memory 相关工具**，认知负担极重。

### 1.2 用户核心洞察

> NimFS 天然支持 session 隔离、跨 session 整理、云备份。
> 问题不在 NimFS 而在工具设计太复杂。给 LLM 几个简单好用的工具就行。

---

## 2. 设计原则

1. **工具越少越好**：4 个工具覆盖全部需求（Write / Read / Search / LoadContext）
2. **参数越少越好**：必填参数 ≤ 2 个，可选参数有合理默认值
3. **无分类体系**：不强制 LLM 选分类，用自由 tags 代替
4. **无层级结构**：砍掉 L0/L1/L2，每条记忆就是 content + 可选 summary
5. **scope 自动推断**：大部分场景 LLM 不需要关心 scope

---

## 3. 新工具设计

### 3.1 工具总览

| # | 工具名 | 用途 | 必填参数 | 替代的旧工具 |
|---|--------|------|---------|-------------|
| 1 | **Memo** | 写入/追加记忆 | content | NimFSWriteMemory, NimFSUpdateProfile, WriteProfile, WriteStrategy, Bash 写 .md |
| 2 | **Recall** | 搜索记忆 | query | NimFSSearchMemory, NimFSListMemory, SearchEpisodicLog, ReadProfile, ReadStrategy |
| 3 | **ReadMemo** | 读取完整记忆 | memo_id | NimFSReadMemory |
| 4 | *(自动)* | 上下文加载 | *(无，系统自动)* | NimFSLoadContext |

**从 11 个工具 → 3 个显式工具 + 1 个自动机制**。

### 3.2 工具详细设计

#### 工具 1: `Memo` — 写入记忆

```
Memo(content, title?, tags?, scope?, supersedes?)
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| content | string | ✅ | - | 记忆内容（Markdown 格式，长度不限） |
| title | string | ❌ | 自动从 content 前 50 字生成 | 简短标题，用于搜索和展示 |
| tags | string | ❌ | "" | 逗号分隔的标签，如 "bugfix,vcpu,重要" |
| scope | string | ❌ | "project" | "project"（项目级）或 "global"（跨项目） |
| supersedes | string | ❌ | "" | 被本条取代的旧 memo_id（逗号分隔可填多个） |

**返回值**: `memo_id`（如 `memo-a1b2c3d4`）

**使用示例**:
```
# 最简用法——只传 content
Memo(content="修复了 SSE 心跳超时问题：将间隔从 30s 降到 15s，添加 watchdog 45s 自动重连")

# 带 tags 的用法
Memo(content="vcpu.py 的 interrupt 逻辑：...", tags="vcpu,architecture")

# 跨项目的通用经验
Memo(content="Next.js Route Handler 优先级高于 rewrites", scope="global", tags="nextjs,sse")
```

**设计决策**:
- 没有 `category` 参数。之前的 6 分类（profile/preferences/entities/events/cases/patterns）改为自由 tags。LLM 想打什么标签就打什么标签。
- 没有 `summary` 参数。系统自动生成摘要（用于搜索索引和上下文注入），策略见下方。
- 没有 `confidence` / `source` 参数。这些元数据对 LLM 毫无意义。
- `scope` 默认 "project"，99% 的场景不需要改。
- 可选的 `supersedes` 参数用于更新/替换旧记忆（见 Q2）。

**摘要自动生成策略**:

硬截取前 200 字可能截到 Markdown 样板或无意义的背景描述。采用以下策略保障信噪比：

1. **跳过标题行**：忽略 content 开头的 `#` / `##` 等 Markdown 标题行
2. **提取首个实质段落**：取第一个非空、非标题的文本段落，截取前 200 字
3. **Prompt 规范兜底**：在 System Prompt 中要求 LLM "Memo 内容的第一段写核心结论，背景和细节放后面"

```python
def extract_summary(content: str, max_len: int = 200) -> str:
    """从 content 中提取高质量摘要"""
    for line in content.split("\n"):
        stripped = line.strip()
        # 跳过空行和 Markdown 标题
        if not stripped or stripped.startswith("#"):
            continue
        # 跳过纯格式行（如 ---, ```, |---|）
        if stripped.startswith(("---", "```", "|")):
            continue
        # 找到第一个实质内容行
        return stripped[:max_len]
    # fallback: 硬截取
    return content[:max_len]
```

#### 工具 2: `Recall` — 搜索记忆

```
Recall(query, scope?, top_k?)
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| query | string | ✅ | - | 搜索关键词，如 "SSE 心跳" 或 "vcpu interrupt" |
| scope | string | ❌ | "all" | "project" / "global" / "all" |
| top_k | int | ❌ | 10 | 最多返回条数 |

**返回值**: 匹配的记忆列表，每条包含 `memo_id`、`title`、`preview`（前 200 字）、`tags`、`created_at`。

**使用示例**:
```
# 搜索项目内记忆
Recall(query="SSE 远端访问")

# 搜索跨项目经验
Recall(query="Next.js proxy", scope="global")

# 列出所有记忆
Recall(query="*")
```

**设计决策**:
- 搜索范围：title + tags + content 前 200 字（即 summary）。使用全文匹配，不需要 FTS5（当前规模 < 1000 条，fnmatch 足够）。
- 返回 preview 而非全文，LLM 需要详情时用 `ReadMemo`。
- 同时搜索 NimFS Memory 和旧版 episodic logs（向后兼容）。

#### 工具 3: `ReadMemo` — 读取完整记忆

```
ReadMemo(memo_id)
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| memo_id | string | ✅ | - | 从 Recall 结果中获取的 memo_id |

**返回值**: 完整的记忆内容（Markdown 文本）。

**使用示例**:
```
ReadMemo(memo_id="memo-a1b2c3d4")
```

**设计决策**:
- 极简，只有一个参数。
- 如果记忆不存在，返回明确的错误信息。

#### 机制 4: 自动上下文加载（无工具，系统内置）

每次对话开始时，系统自动执行 `load_context(current_goal)`：
1. 加载 global scope 下所有 profile/preferences 类记忆的 summary
2. 基于用户首条消息的关键词，搜索 project scope 下最相关的 top-5 记忆 summary
3. 注入到 Anchor 的 `### Relevant Knowledge` 区域（budget: 2000 tokens）

**这替代了之前的 `NimFSLoadContext` 工具**——LLM 不需要手动调用，系统自动完成。

### 3.3 工具权限分配

| 角色 | Memo | Recall | ReadMemo |
|------|------|--------|----------|
| Orchestrator | ✅ | ✅ | ✅ |
| Chat（直接对话） | ✅ | ✅ | ✅ |
| Implementer | ❌ | ✅（只读） | ✅（只读） |
| Explorer | ❌ | ✅（只读） | ✅（只读） |
| Architect | ❌ | ✅（只读） | ✅（只读） |
| Tester | ❌ | ❌ | ❌ |

**设计决策**: 只有 Orchestrator 和 Chat 角色能写入记忆。Specialist agents 可以搜索和读取，但不能写入（避免低质量的自动写入污染记忆库）。

---

## 4. NimFS 存储简化

### 4.1 砍掉 L0/L1/L2 三层

**之前**:
```
memory/{category}/{memo_id}/
├── meta.json      # 元数据
├── l0.abstract    # 超短摘要（< 100 tokens）
├── l1.overview.md # 结构化概览
└── l2.content.md  # 完整内容
```

**之后**:
```
memory/{memo_id}/
├── meta.json      # 元数据（含 title, tags, summary, created_at, scope）
└── content.md     # 完整内容
```

- `summary` 字段移入 `meta.json`（自动从 content 截取前 200 字）
- 删除 `l0.abstract` / `l1.overview.md` / `l2.content.md`，统一为 `content.md`
- 删除 `category` 目录层级，所有 memo 平铺在 `memory/` 下

### 4.2 砍掉 6 分类

**之前**: `MemoryCategory` 枚举 = profile, preferences, entities, events, cases, patterns

**之后**: 无分类。用 `tags` 自由标注。

如果 LLM 想标注 `tags="profile"` 或 `tags="bugfix,vcpu"`，完全自由。系统不强制任何分类体系。

### 4.3 保留的特殊处理

**Profile / Preferences**: 仍然存在 `global/` scope 下，但不再是特殊分类。LLM 写 `Memo(content="用户偏好: 使用中文回复", scope="global", tags="preference")` 即可。

`load_context` 自动加载时，通过 `tags` 包含 "profile" 或 "preference" 来识别，而非通过 `category`。

### 4.4 新的目录结构

```
~/.nimbus/fs/
├── global/
│   └── memory/
│       ├── memo-xxxx/          # 跨项目记忆
│       │   ├── meta.json
│       │   └── content.md
│       └── ...
└── projects/{project_id}/
    ├── artifacts/              # IPC（不变）
    │   ├── index.json
    │   └── {task_id}/
    └── memory/
        ├── memo-xxxx/          # 项目级记忆
        │   ├── meta.json
        │   └── content.md
        └── ...
```

### 4.5 meta.json 格式

```json
{
  "memo_id": "memo-a1b2c3d4",
  "title": "修复 SSE 心跳超时问题",
  "summary": "将心跳间隔从 30s 降到 15s，添加 watchdog 45s 自动重连...",
  "tags": ["bugfix", "sse", "heartbeat"],
  "scope": "project",
  "created_at": "2026-03-06T10:30:00Z",
  "updated_at": "2026-03-06T10:30:00Z",
  "superseded_by": null,
  "supersedes": []
}
```

- `superseded_by`: 当本条被新记忆取代时，系统自动填入新 memo_id。非 null 的记忆在 Recall/LoadContext 中默认不返回。
- `supersedes`: 本条取代的旧 memo_id 列表（由写入时的 `supersedes` 参数填充）。

---

## 5. Strategy（Condition→Action）的处理

### 现状
`ProceduralStore` 存储在 `.nimbus/procedural/strategies.json`，提供 `ReadStrategy` / `WriteStrategy` 工具。

### 方案：合并到 Memo

Strategy 本质上就是一条带有特定格式的记忆。不需要单独的工具。

**迁移方式**:
```
# 旧方式
WriteStrategy(condition="当 pytest 失败时", action="先检查 import 路径，再检查 fixture")

# 新方式
Memo(
  content="## 策略\n**条件**: 当 pytest 失败时\n**动作**: 先检查 import 路径，再检查 fixture",
  tags="strategy,pytest"
)
```

System prompt 中引导 LLM 用特定格式写策略类记忆即可，不需要专门的工具。

---

## 6. Episodic（日志搜索）的处理

### 现状
`EpisodicStore` 搜索 `.nimbus/sessions/{date}/*.jsonl`，提供 `SearchEpisodicLog` 工具。

### 方案：保留但简化

Episodic log 是系统自动记录的原始会话日志，与 LLM 主动写入的 Memo 性质不同。保留搜索能力，但合并到 `Recall` 工具中。

**实现**: `Recall` 工具在搜索 NimFS Memory 的同时，也搜索 episodic logs。结果中用 `source` 字段区分：

```
[
  { "memo_id": "memo-xxx", "source": "memory", "title": "...", "preview": "..." },
  { "memo_id": "episodic-xxx", "source": "session_log", "title": "2026-03-05 session", "preview": "..." }
]
```

---

## 7. System Prompt 修改

### 新的 NIMFS_MEMORY_RULES

```markdown
## Memory（长期记忆）

你有 3 个记忆工具：

### `Memo(content, title?, tags?, scope?, supersedes?)` — 记住重要的事
- 完成代码修改、修复 bug、做出架构决策后，**必须**调用 Memo 记录
- 不需要用户要求——如果这个信息下次会话时有用，现在就写
- **内容格式**：第一段写核心结论，背景和细节放后面
- `tags` 用逗号分隔，自由标注（如 "bugfix,vcpu" 或 "architecture,重要"）
- `scope` 默认 "project"，跨项目通用经验用 "global"
- 如果要更新之前的记忆（如修改偏好、修正错误的策略），先 `Recall` 找到旧 memo_id，然后在新 Memo 中填 `supersedes` 参数

### `Recall(query, scope?, top_k?)` — 搜索记忆
- 用户提到"之前"、"上次"、"记得吗" → 先 Recall 搜索
- 当前任务涉及之前做过的模块 → 先 Recall 搜索
- 搜不到时换同义词或英文再试

### `ReadMemo(memo_id)` — 读取完整记忆
- Recall 返回的是摘要，需要详情时用 ReadMemo 读取全文

### 自动上下文
- 每次对话开始时，系统会自动加载相关的历史记忆到你的上下文中
- 你不需要手动加载，但可以用 Recall 搜索更多
```

### 与 V3 Memory Rules 的对比

| 方面 | V3（File-System） | 新方案（NimFS 统一） |
|------|-------------------|---------------------|
| 写入方式 | `Write` / `Bash echo` 写 .md 文件 | `Memo(content=...)` |
| 搜索方式 | `Bash grep` / `Read` | `Recall(query=...)` |
| 路径记忆 | LLM 需记住 `.nimbus/memory/gotchas.md` 等路径 | 不需要记路径，用 tags 标注 |
| 格式一致性 | 依赖 LLM 自律（经常格式混乱） | 系统保证结构化存储 |
| Session 隔离 | 无（所有 session 写同一个文件） | 有（NimFS 天然支持） |
| 搜索能力 | grep 子串匹配 | 结构化搜索（title + tags + summary） |

---

## 8. Artifact 工具保留

现有的 IPC 工具**完全保留不变**：

| 工具 | 用途 | 变化 |
|------|------|------|
| NimFSWriteArtifact | 写入大型临时产物（代码、报告） | 不变 |
| NimFSReadArtifact | 读取 artifact | 不变 |
| NimFSListArtifacts | 列出 artifacts | 不变 |

**Artifact vs Memo 的区别**：
- **Artifact**: 短生命周期 IPC，用于 agent 间传递大型数据，有 TTL 自动过期
- **Memo**: 长期记忆，跨 session 持久化，用于知识积累

---

## 9. 迁移路径

### Phase 1: 实现新工具（不删旧的）

1. 在 `src/nimbus/tools/` 下新建 `memo_tools.py`，实现 `Memo` / `Recall` / `ReadMemo`
2. 底层复用 `NimFSManager`，但使用简化的存储格式（无 L0/L1/L2，无 category 目录）
3. 在 `bootstrap.py` 中注册新工具
4. 更新 `prompts.py` 中的 `NIMFS_MEMORY_RULES`
5. 旧工具暂时保留但不在 prompt 中提及

### Phase 2: 数据迁移

1. 写迁移脚本，将 `.nimbus/memory/*.md` 的内容导入 NimFS Memory
2. 将 `.nimbus/procedural/strategies.json` 的策略导入为 Memo（带 `tags="strategy"`）
3. 将 `.nimbus/semantic_profile/profile.json` 的条目导入为 Memo（带 `tags="profile"`, `scope="global"`）
4. 旧的 NimFS Memory（L0/L1/L2 格式）通过兼容层读取，新写入用新格式

### Phase 3: 清理

1. 删除旧工具：`NimFSWriteMemory`, `NimFSReadMemory`, `NimFSSearchMemory`, `NimFSListMemory`, `NimFSUpdateProfile`, `NimFSLoadContext`
2. 删除旧工具：`ReadProfile`, `WriteProfile`, `SearchEpisodicLog`, `ReadStrategy`, `WriteStrategy`
3. 删除旧 Store：`ProfileStore`, `ProceduralStore`（`EpisodicStore` 保留，被 `Recall` 内部调用）
4. 删除 NimFS Memory 的 L0/L1/L2 相关代码
5. 删除 `MemoryCategory` 枚举

### 需要修改的文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/nimbus/tools/memo_tools.py` | **新建** | 3 个新工具实现 |
| `src/nimbus/core/nimfs/manager.py` | 修改 | 新增简化的 memo CRUD 方法 |
| `src/nimbus/core/nimfs/models.py` | 修改 | 新增 MemoEntry 模型，保留旧模型兼容 |
| `src/nimbus/orchestration/bootstrap.py` | 修改 | 注册新工具，移除旧工具注册 |
| `src/nimbus/orchestration/prompts.py` | 修改 | 更新 NIMFS_MEMORY_RULES |
| `src/nimbus/tools/nimfs_tools.py` | 修改 | 移除 6 个旧 Memory 工具 |
| `src/nimbus/tools/memory_ops.py` | 删除 | 5 个旧工具全部移除 |
| `src/nimbus/core/memory/procedural_store.py` | 删除 | 合并到 Memo |
| `src/nimbus/core/memory/profile_store.py` | 删除 | 合并到 Memo |
| `src/nimbus/core/memory/profile_schema.py` | 删除 | 不再需要 |
| `src/nimbus/core/memory/strategy_schema.py` | 删除 | 不再需要 |

---

## 10. 开放问题

### Q1: Recall 是否需要 FTS5？
当前方案用 fnmatch 全文匹配，对 < 1000 条记忆足够。如果未来记忆量增长到 10,000+，可以加 SQLite FTS5 索引。但现在 YAGNI。

### Q2: ~~是否需要 Memo 的 update/delete？~~ → 可变知识的冲突解决（已解决）

**问题**：Preferences / Strategies 等排他性知识，如果只有 Append-only 没有 Update/Delete，`Recall` 会返回多条相互冲突的旧记录，LLM 容易执行错误的策略。例如用户先写了"偏好 Tab 缩进"，后来改成"偏好 Space 缩进"，两条都会被搜到。

**方案**：在 `Memo` 工具中增加可选的 `supersedes` 参数：

```
Memo(content, title?, tags?, scope?, supersedes?)
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| supersedes | string | ❌ | "" | 被本条记忆取代的旧 memo_id（逗号分隔可填多个） |

**行为**：
- 写入新 memo 时，如果指定了 `supersedes`，系统在旧 memo 的 `meta.json` 中标记 `"superseded_by": "memo-新id"`
- `Recall` 搜索时，**默认跳过**已被 supersede 的记忆（除非显式传 `include_superseded=true`）
- `load_context` 自动注入时，**始终跳过**已被 supersede 的记忆
- 旧记忆不删除（保留审计轨迹），只是搜索时不再出现

**使用示例**：
```
# 更新用户偏好
old = Recall(query="缩进偏好")  # 返回 memo-abc123
Memo(content="用户偏好: Space 缩进，4 个空格", tags="preference", scope="global", supersedes="memo-abc123")
```

**System Prompt 引导**：在 Memory Rules 中增加一句：
> 如果你要更新之前的记忆（如修改偏好、修正错误的策略），先 `Recall` 找到旧记忆的 memo_id，然后在新 `Memo` 中填写 `supersedes` 参数。

### Q3: load_context 的自动注入是否会注入噪音？
可能。但 budget 限制在 2000 tokens，且只注入 summary，风险可控。如果实践中发现噪音问题，可以加相关度阈值过滤。

### Q4: 旧格式的 NimFS Memory 如何兼容？
`Recall` 和 `ReadMemo` 需要同时支持新格式（`content.md`）和旧格式（`l0.abstract` + `l2.content.md`）。通过检测目录下是否存在 `content.md` 来区分。

### Q5: ReadMemo 对 Episodic Log 的路由（已解决）

**问题**：`Recall` 会返回 `episodic-xxx` 类型的结果（来自 session log），但 `ReadMemo` 的底层存储是 NimFS，直接读取 episodic ID 会失败。

**方案**：`ReadMemo` 内部根据 `memo_id` 前缀做路由分发：

```python
def read_memo(memo_id: str) -> str:
    if memo_id.startswith("memo-"):
        # NimFS Memory 路径
        return nimfs_manager.read_memo(memo_id)
    elif memo_id.startswith("episodic-"):
        # Episodic Store 路径（解析 .jsonl）
        return episodic_store.read_entry(memo_id)
    else:
        raise ValueError(f"Unknown memo_id format: {memo_id}")
```

在设计文档和实现中，明确 `memo_id` 的前缀约定：
- `memo-{uuid8}`: NimFS Memory 条目
- `episodic-{session_id}-{line}`: Episodic 日志条目

---

## 附录 A: 与其他 Agent 框架的对比

| 框架 | Memory 工具数 | 分类体系 | 我们的方案 |
|------|-------------|---------|-----------|
| Claude Code | 1（Memo） | 无 | ✅ 参考了其极简设计 |
| Cursor | 0（纯文件） | 无 | 我们比它多了搜索能力 |
| Aider | 0（纯文件） | 无 | 我们比它多了搜索能力 |
| MemGPT/Letta | 5+ | 复杂 | 我们追求简洁 |
| 我们之前 | 11 | 6 分类 + 3 层 | → 3 工具 + 自由 tags |
