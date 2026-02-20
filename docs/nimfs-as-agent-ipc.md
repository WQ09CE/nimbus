# NimFS as Agent IPC Backbone：从记忆层到 Agent 间通信基建

> **文档类型**：架构预研 (Architecture Pre-Research)  
> **状态**：Draft v1.0  
> **关联文档**：`nimfs-design-v2.md`

---

## 1. 现有通信模型的痛点 (Current Pain Points)

### 1.1 架构现状

Nimbus 当前的多 Agent 通信是**同进程内父子派生模型**，Agent 间唯一的信息载体是字符串：

```
Orchestrator
  └─ AgentOS.spawn(Implement Agent)
       ├─ 独立 VCPU（不共享）
       ├─ 独立 MMU（不共享）
       └─ 执行完毕
            └─ ToolResult.output（最多 16K 字符，超出截断）
                 └─ 写入 Orchestrator MMU ← 唯一信息通道，大产物丢失
```

**核心约束**：
- `ToolResult.output`：MAX_CONTEXT_CHARS = **16,000 字符** 硬上限
- `GoalDocument.context`：同样 **16,000 字符** 硬上限
- 子 Agent 完整执行历史**不传给**父 Agent，只传最终字符串摘要

### 1.2 大产物传递困境

| 产物类型 | 典型大小 | 现有架构效果 |
|---------|---------|------------|
| 函数说明 / 简短分析 | < 2K | ✅ 完整传递 |
| 单文件代码实现 | 5~20K | ⚠️ 部分截断 |
| 完整探索报告 | 20~80K | ❌ 严重截断，关键信息丢失 |
| 多文件代码变更集 | 50K+ | ❌ 完全无法传递 |
| 测试结果 + 堆栈追踪 | 10~50K | ⚠️ 截断，错误上下文丢失 |

**根本问题**：Agent 被迫在"信息完整性"和"上下文容量"之间取舍，大型协作任务天花板明显。

---

## 2. NimFS as Shared Blackboard：核心架构模型

### 2.1 核心思想：从"传递产物"到"共享引用"

```
【现有模式】Pass by Value（值传递，有截断）
  Implement Agent ──ToolResult("...16K截断内容...")──▶ Orchestrator ──▶ Tester Agent

【新模式】Pass by Reference（引用传递，无截断）
  Implement Agent ──write──▶ NimFS artifacts/task-123/content.py
  Tester Agent    ──read───▶ NimFS artifacts/task-123/content.py（完整内容）
                                        ↑
                    Orchestrator 只传 "nimfs://artifact/task-123"（40 字节）
```

### 2.2 Producer-Consumer 模式

Agent 直接通过 NimFS 共享产物，无需经过 Orchestrator 中转：

```
Implement Agent ──write──▶ ~/.nimbus/fs/projects/{id}/artifacts/task-123/
                                        ▲
Tester Agent    ──read───────────────────┘  （直接读取，容量无上限）
```

适用场景：Scheduler DAG 中，上游 Agent 产物直接供下游 Agent 消费。

### 2.3 Claim-Check 模式（企业集成经典模式）

ToolResult 只返回轻量引用，接收方按需拉取完整内容：

```
Step 1: Implement Agent 写大产物
  → write_artifact(content=50K代码) → "nimfs://artifact/task-123"

Step 2: ToolResult 只携带引用（零截断风险）
  → ToolResult(output="实现完成，摘要：...", artifact_ref="nimfs://artifact/task-123")

Step 3: Orchestrator 传引用给下游（仅 40 字节）
  → GoalDocument(context="nimfs://artifact/task-123")

Step 4: Tester Agent 按需展开
  → expand("nimfs://artifact/task-123") → 完整 50K 代码内容
```

### 2.4 两种模式对比

| 维度 | 现有字符串传递 | NimFS IPC (Claim-Check) |
|-----|-------------|------------------------|
| 容量上限 | 16,000 字符 | 无限制（磁盘容量） |
| 传递方式 | Pass by Value（拷贝） | Pass by Reference（引用） |
| 并发读取 | ❌ 不支持 | ✅ 多 Agent 同时读 |
| 可追溯性 | ❌ 无（字符串消失） | ✅ manifest.json 完整记录 |
| 实现复杂度 | 简单 | 中等（新增 read/write API） |
| 向后兼容 | — | ✅ 非破坏性扩展 |

---

## 3. Artifact 分区设计 (Artifact Partition Design)

### 3.1 目录结构

在 v2 设计的 6 分类记忆体系之外，新增 `artifacts/` 专用分区：

```
~/.nimbus/fs/
├── global/                              # 跨项目全局记忆
│   ├── profile/
│   └── preferences/
└── projects/
    └── {project_id}/                    # e.g. Users-wangqing-sourcecode-nimbus
        ├── memory/                      # 长期记忆（知识沉淀）
        │   ├── profile/
        │   ├── preferences/
        │   ├── entities/
        │   ├── events/
        │   ├── cases/
        │   └── patterns/
        └── artifacts/                   # ← 新增：Agent 间管道产物（IPC）
            ├── index.json               # 全局产物索引
            └── {task_id}/
                ├── manifest.json        # 产物元数据
                ├── content.*            # 实际内容（.py/.md/.diff/.json 等）
                └── refs/                # 消费记录（谁读了此产物）
                    └── {consumer_id}.json
```

### 3.2 manifest.json 结构

```json
{
  "artifact_id": "task-123-impl",
  "task_id": "task-123",
  "producer": "implement-agent",
  "producer_session": "session-abc",
  "type": "code",
  "filename": "content.py",
  "size_bytes": 45230,
  "created_at": "2025-01-15T10:00:00Z",
  "ttl": "session",
  "status": "committed",
  "summary": "实现了 NimFSManager.write() 方法，包含异步蒸馏队列",
  "tags": ["python", "implementation", "nimfs"],
  "supersedes": null
}
```

### 3.3 Memory vs Artifacts 本质区别

| 维度 | `memory/` | `artifacts/` |
|-----|---------|-----------|
| **定位** | 知识沉淀（Knowledge） | 管道产物（Pipeline Product） |
| **生命周期** | 永久 / 手动清理 | 任务级 / 自动 GC |
| **写入频率** | 低频、精华 | 高频、原始 |
| **内容性质** | 结构化记忆（L0/L1/L2） | 任意格式（代码/报告/diff） |
| **GC 策略** | `defrag()` 人工触发 | TTL 到期自动清理 |
| **检索方式** | 语义检索（向量+关键词） | 精确 ID 引用 |
| **类比** | 数据库（Database） | 消息队列临时存储（MQ Broker） |

---

## 4. 与现有架构的集成点 (Integration Points)

### 4.1 ToolResult 非破坏性扩展

```python
@dataclass
class ToolResult:
    status: str
    output: str                           # 现有字段，完全兼容
    artifact_ref: Optional[str] = None    # 新增：nimfs://artifact/{task_id}

# 自动 offload 逻辑（在 Gate 层实现）：
# if len(output) > OFFLOAD_THRESHOLD (8K):
#     ref = nimfs.write_artifact(output, task_id=...)
#     output = f"[产物已写入 NimFS] 摘要：{output[:500]}..."
#     artifact_ref = f"nimfs://artifact/{ref}"
```

### 4.2 GoalDocument 支持引用展开

```python
@dataclass
class GoalDocument:
    mission: str
    context: str      # 支持 nimfs:// 引用，render() 时按需展开
    workspace: str

    def render(self) -> str:
        ctx = self.context
        # 展开所有 nimfs:// 引用（最大展开深度 = 2，防止循环）
        for ref in extract_nimfs_refs(ctx):
            ctx = ctx.replace(ref, nimfs.read_artifact(ref))
        return build_markdown(self.mission, ctx, ...)
```

### 4.3 Scheduler DAG 产物自动注入

```
Task A: Implement
  → 完成 → artifact written: nimfs://artifact/task-a-impl
               │
               │  Scheduler 解析 DAG 依赖关系
               ▼
Task B: Test（依赖 Task A 输出）
  → GoalDocument.context 自动注入 nimfs://artifact/task-a-impl 引用
  → Tester Agent render() 时展开引用，读取完整实现代码（无截断）
```

Scheduler Result Store 升级：从存储字符串结果，升级为存储 `nimfs://` 引用。

### 4.4 SessionCompressor 大结果 Offload

```
压缩时发现超大 tool_result message（> 8K）：
  ↓
自动调用 nimfs.write_artifact(message.content, ttl="session")
  ↓
message.content 替换为 nimfs:// 引用（< 100 字节）
  ↓
MMU Token 预算显著释放，压缩效率大幅提升
```

---

## 5. 并发安全与生命周期 (Concurrency & Lifecycle)

### 5.1 写入策略：Write-Once Immutable

- Artifact 一旦 `status=committed`，内容**不可变**
- 新版本追加为新 artifact（`manifest.supersedes` 字段引用旧版本）
- 天然避免多 Agent 并发写同一文件的竞态条件

### 5.2 并发读取：天然安全

- 文件系统读操作天然支持多进程并发读
- 多个 Agent 可同时 read 同一 artifact，无需锁

### 5.3 生命周期分级

| TTL 级别 | 描述 | 自动 GC 时机 |
|---------|------|------------|
| `task` | 任务级产物 | task 完成后 30 分钟 |
| `session` | 会话级产物 | session 结束时清理 |
| `project` | 项目级产物 | 手动触发或 `defrag()` |
| `permanent` | 永久（升级为 memory） | 不自动 GC |

---

## 6. 实现优先级与 Go/No-Go 建议

### 6.1 与 nimfs-design-v2.md Roadmap 合并

| Phase | 原 Roadmap | 新增 IPC 内容 | 时间 |
|-------|-----------|-------------|------|
| **Phase 0** | 基础 CRUD，文件系统规范 | + `artifacts/` 目录结构 + `manifest.json` + `write_artifact` / `read_artifact` | Day 1-3 |
| **Phase 1** | L0/L1/L2 自动生成 | + `ToolResult.artifact_ref` 扩展 + `GoalDocument` `nimfs://` 展开 | Day 4-7 |
| **Phase 2** | 向量检索，load_context | + Scheduler DAG 产物注入 + TTL GC 机制 | Day 8-12 |
| **Phase 3** | 系统集成，闭环测试 | + SessionCompressor 自动 offload + 完整 IPC 闭环测试 | Day 13-15 |

### 6.2 Go/No-Go 决策建议

**结论：✅ Go，但职责分层必须清晰**

**✅ Go 的理由：**
1. **共享磁盘是最自然的解法**：文件系统天然解决跨进程大数据共享，无需引入 Redis/MQ 等额外依赖
2. **Claim-Check 是成熟模式**：企业集成领域验证多年，非发明新轮子
3. **非破坏性扩展**：`ToolResult` / `GoalDocument` 只需新增可选字段，现有代码零改动
4. **`artifacts/` 与 `memory/` 分区清晰**：IPC 职责不污染记忆层语义
5. **跨 session 天然支持**：`~/.nimbus/fs/` 全局目录，session 重启后产物引用依然有效

**⚠️ 需要管控的风险：**
1. **磁盘膨胀**：GC 机制必须在 Phase 0 就设计好，不能推后
2. **空引用问题**：`nimfs://` 引用 TTL 过期后，Agent 展开得到空内容，需明确错误处理（返回 `ArtifactExpiredError`）
3. **展开深度**：引用嵌套引用可能失控，建议硬限最大展开深度 = 2

**❌ 不适用场景（不影响 Go 决策）：**
- 实时性要求 < 1ms 的 IPC（Agent 任务通常秒级，文件系统完全够用）

---

## 7. 总结 (Summary)

> NimFS 不仅是 Nimbus 的**记忆层（Memory Layer）**，  
> 更是多 Agent 协作的**虚拟共享磁盘（Virtual Shared Disk）**。  
> 通过 `artifacts/` 分区和 Claim-Check 模式，  
> Agent 从"传递产物"进化为"共享引用"，  
> 从根本上突破 16K 上下文截断瓶颈，让大规模 Agent 协作成为可能。

```
【架构进化路径】

v0（现在）：Orchestrator 是唯一信息中转站
  A ──16K字符──▶ Orchestrator ──16K字符──▶ B
                      ↑
                  信息瓶颈

v1（NimFS IPC）：NimFS 成为共享黑板
  A ──write──▶ ~/.nimbus/fs/ ◀──read── B
                      ↑
       Orchestrator 只传引用（40字节），容量瓶颈彻底消除
```

---

*Created for the Nimbus Project | NimFS Architecture Pre-Research*  
*关联文档：`nimfs-design-v2.md` | 状态：Draft，待评审后合并入正式设计*
