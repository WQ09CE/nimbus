# Nimbus 架构复盘 — 2026 年 2 月

> **日期**: 2026-02-27  
> **目的**: 全面梳理 Nimbus 当前架构现状、演进轨迹、文档 vs 实现差距、技术债务与下一步方向  

---

## 1. 架构演进轨迹

```
2025-01  OpenNotebook          最初的 Notebook Agent，DAG 并行执行引擎
   │
2026-01  Agent Framework v0.2  DAG 持久化、Re-planning、Pinned Context
   │
2026-01  AgentOS 架构           冯·诺依曼隐喻引入 (vCPU/MMU/Scheduler)
   │
2026-02  成熟期 (当前)           NimFS v2、多 Agent 编排、Heart、Compaction、Skills
```

项目从一个简单的 NotebookAgent 演化为完整的 **Agent Operating System**，历时约 14 个月。核心架构隐喻从"DAG 任务执行"转变为"冯·诺依曼计算机"——这是一次根本性的范式转换。

---

## 2. 当前实际架构

### 2.1 分层总览

```
┌─────────────────────────────────────────────────────────┐
│                    Application Layer                     │
│   Web UI (Next.js)  │  TUI (Terminal)  │  HTTP API      │
├─────────────────────────────────────────────────────────┤
│                    Server Layer                          │
│   session_v2.py (SSE streaming)  │  api.py (REST)       │
├─────────────────────────────────────────────────────────┤
│                    AgentOS Layer                          │
│   agentos.py — Process lifecycle, session mgmt           │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│   │  vCPU    │ │   MMU    │ │ Scheduler│ │   Heart   │ │
│   │ 1905 LOC │ │ 1184 LOC │ │ 1039 LOC │ │  175 LOC  │ │
│   │ 执行引擎  │ │ 内存管理  │ │ DAG 调度  │ │ 后台守护   │ │
│   └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬─────┘ │
│        │            │            │              │        │
│   ┌────┴────────────┴────────────┴──────────────┘       │
│   │              NimFS (750 LOC)                         │
│   │    三层持久化：Artifact (临时) + Memory (永久)         │
│   └─────────────────────────────────────────────────────│
├─────────────────────────────────────────────────────────┤
│                  Orchestration Layer                      │
│   specialist_tools.py — Explore/Implement/Design/Test    │
│   prompts.py — 系统提示词模板                              │
│   review_tool.py — ReviewCommittee 多模型评审              │
├─────────────────────────────────────────────────────────┤
│                    LLM Adapters                           │
│   Anthropic │ OpenAI │ LiteLLM (Gemini/Ollama/...)       │
└─────────────────────────────────────────────────────────┘
```

### 2.2 核心模块代码量

| 模块 | 文件 | LOC | 角色 |
|------|------|-----|------|
| **AgentOS** | `agentos.py` | 2,324 | 进程生命周期、会话管理、compaction 调度 |
| **vCPU** | `runtime/vcpu.py` | 1,905 | Think-Act-Observe 循环、工具执行、Doom Loop 检测 |
| **MMU** | `memory/mmu.py` | 1,184 | 上下文组装、NimFS offload、工具结果管理、栈帧 |
| **Scheduler** | `scheduler.py` | 1,039 | DAG 任务图、并行调度、结果注入、事件流 |
| **NimFS** | `nimfs/manager.py` | 750 | Artifact/Memory CRUD、三层检索、GC |
| **Specialist** | `specialist_tools.py` | 246 | 子 Agent 委派（4 种角色） |
| **Heart** | `heart.py` | 175 | 后台 cron + 消息驱动模块系统 |
| **总计** | — | **7,623** | 核心代码 |

---

## 3. 冯·诺依曼隐喻落地评估

| 计算机组件 | Agent 对应 | 实现状态 | 评价 |
|-----------|-----------|---------|------|
| **CPU** | vCPU (agentic loop) | ✅ 完全实现 | Think-Act-Observe 循环是核心引擎，1900+ LOC，功能完整但有 God Class 问题 |
| **RAM** | MMU (context window) | ✅ 完全实现 | 栈帧、context 组装、offload、compaction 链路完整 |
| **硬盘** | NimFS | ✅ 完全实现 | Artifact(临时) + Memory(永久)，三层分层(L0/L1/L2)，搜索+GC |
| **进程管理** | AgentOS + Scheduler | ✅ 实现 | Process 抽象、spawn/wait、DAG 并行调度 |
| **中断/IPC** | Heart + NimFS IPC | ⚠️ 部分实现 | Heart 框架在但模块较少；NimFS 作为 IPC 已设计但 IPC 语义还较弱 |
| **系统调用** | Gate (syscall_tool) | ✅ 实现 | 工具调用通过 Gate 层做权限检查和日志 |
| **Shell** | Orchestrator prompt | ✅ 实现 | 系统提示词扮演 shell 角色 |
| **页面置换** | Compaction | ✅ 实现 | 上下文超限时自动压缩，支持多轮 |
| **文件系统权限** | Profile + 工具限制 | ✅ 实现 | 不同角色有不同工具集权限 |

**总评**: 冯·诺依曼隐喻落地度 **~85%**。核心五件套（vCPU/MMU/NimFS/Scheduler/Gate）全部实现。差距主要在 Heart（作为"守护进程"还不够丰富）和 IPC 机制（Agent 间通信仍依赖 NimFS artifact 传递，缺乏真正的消息队列语义）。

---

## 4. 多 Agent 编排机制

```
                     ┌─────────────┐
                     │ Orchestrator │ ← 用户直接对话的主 Agent
                     │  (gemini-pro)│
                     └──────┬──────┘
                            │ specialist_tools.py
              ┌─────────────┼─────────────┐─────────────┐
              ▼             ▼             ▼             ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Explorer │ │Implementer│ │ Architect│ │  Tester  │
        │(read-only)│ │(full write)│ │(.md only)│ │(read+bash)│
        └──────────┘ └──────────┘ └──────────┘ └──────────┘
```

**工作流程**:
1. Orchestrator 收到用户请求，分析后委派给合适的 Specialist
2. 每个 Specialist 是独立的子进程（独立 vCPU + MMU 实例）
3. Specialist 通过工具权限隔离（Explorer 只能 Read/Bash，Architect 只能写 .md）
4. Specialist 完成后返回结果给 Orchestrator，由 Orchestrator 综合回复用户
5. 多个独立 Specialist 可并行执行（通过 Scheduler DAG）

**当前已知问题**:
- Specialist 超时后上下文丢失（无 checkpoint 恢复）
- Promise Gate：LLM 说"我要做X"但不调用工具，导致空转
- Specialist 有时会往 NimFS Memory 写垃圾（已加防护但非完全杜绝）

---

## 5. 文档 vs 实现的 Gap 分析

### 5.1 文档有、代码已实现 ✅
| 文档 | 状态 |
|------|------|
| NimFS v2 设计 | 完全实现，含 offload/GC/搜索增强 |
| vCPU 内部机制 | 实现，含 Doom Loop 检测 |
| 多 Agent 编排 | 实现，4 种 Specialist 角色 |
| 无限上下文 (Compaction) | 实现，支持多轮压缩 |
| NimFS Offload 优化 | 实现，Lazy Expansion |
| Edit 工具恢复机制 | 实现 |

### 5.2 文档有、未完全落地 ⚠️
| 文档 | 差距 |
|------|------|
| **vCPU 重构方案** (vcpu_refactoring_plan.md) | 方案已设计但 vCPU 仍是 1905 行 God Class，未拆分 |
| **Agent OS Process 机制** (agent-os-process-mechanism.md) | fork/exec/IPC 等 Linux 进程语义仅部分映射，缺少真正的进程间通信 |
| **Session 管理路线图** (session-management-roadmap.md) | 会话恢复/迁移等高级功能未实现 |
| **TUI Dashboard** (tui-dashboard-design.md) | TUI 存在但不是文档中设计的全功能 Dashboard |
| **NimFS 作为 Agent IPC** (nimfs-as-agent-ipc.md) | NimFS 被用于传数据但缺乏 pub/sub 语义 |

### 5.3 文档有、已废弃 ❌
| 文档 | 原因 |
|------|------|
| OpenNotebook 架构 (architecture.md) | 已被 AgentOS 架构完全取代 |
| CopilotKit 方案 | POC 失败，已回退 |
| OpenWork/TOAD 集成 | 外部项目集成方案未推进 |
| 旧 API 参考 (api-reference.md) | 引用 NotebookAgent 等旧 API |

---

## 6. 技术债务盘点

### 🔴 高优先级

| 问题 | 影响 | 所在模块 | LOC |
|------|------|---------|-----|
| **vCPU God Class** | 1905 行单文件，职责过多（循环控制 + 工具执行 + 错误恢复 + Doom Loop + IPC），难以维护和测试 | vcpu.py | 1905 |
| **AgentOS God Class** | 2324 行，混合了进程管理、会话管理、compaction 调度、NimFS GC、技能加载等 | agentos.py | 2324 |
| **Promise Gate** | LLM 输出"我要做X"但不调用工具，导致空循环。有部分修复但未根治 | vcpu.py | — |

### 🟡 中优先级

| 问题 | 影响 |
|------|------|
| **110 个文档** | 70% 过时或重复，新人/AI 阅读成本极高（本次清洗解决） |
| **Specialist 超时无恢复** | 大任务的 Specialist 超时后工作丢失 |
| **Heart 模块稀少** | 作为"守护进程"框架，但实际挂载的模块很少，潜力未释放 |
| **测试覆盖不均** | 核心模块测试较好，但 Orchestration 层测试较少 |

### 🟢 低优先级

| 问题 | 影响 |
|------|------|
| 散落的脚本文件（根目录 patch_*.py, quicksort.py 等） | 项目根目录混乱 |
| NimFS Memory 重复写入 | 有检查但不完美，profile 类条目有大量重复 |
| 前端架构选择未定 | Web UI 经历了 CopilotKit → assistant-ui → 当前方案，需要稳定 |

---

## 7. 架构亮点

值得保持和强化的设计决策：

1. **冯·诺依曼隐喻** — 将计算机架构概念映射到 Agent 系统，让 CPU/内存/磁盘/进程的成熟思维模式自然适用于 Agent 生命周期管理。这不仅是命名美学，确实指导了模块划分和职责边界。

2. **三层截断链** — vCPU(100K 安全网) → MMU offload(auto) → context optimize(view truncation)。多级防护确保永远不会爆 context window。

3. **NimFS 三层记忆** — L0(摘要 for Anchor) → L1(概述) → L2(完整内容)。读取成本按需递增，Anchor 注入只用最廉价的 L0。

4. **Specialist 权限隔离** — Explorer 只读、Architect 只写 .md、Implementer 全权限、Tester 读+执行。最小权限原则在 Agent 系统中的优雅实践。

5. **Compaction（上下文压缩）** — 类似操作系统的页面置换，当 context 接近上限时自动压缩历史对话，实现"无限对话"。

---

## 8. 下一步建议

### 短期（1-2 周）

1. **📋 执行文档清洗** — 按 DOCS_CLEANUP_PLAN.md 执行，将 110 → ~30 个活跃文档
2. **🧹 清理项目根目录** — 移除/归档散落的 patch_*.py、quicksort.py 等临时脚本
3. **📝 更新 README** — 确保新人/AI 看到的第一个文档反映当前真实架构

### 中期（1-2 月）

4. **🔧 vCPU 拆分** — 按 vcpu_refactoring_plan.md 拆分为 Pipeline/Decoder/Executor/DoomLoopDetector，优先级最高的技术债
5. **🔧 AgentOS 拆分** — 将 session 管理、compaction 调度、NimFS GC 等抽离为独立模块
6. **💪 Heart 模块丰富** — 利用 Heart 框架实现更多后台能力：定时 NimFS GC、会话健康检查、自动文档更新等

### 长期（探索方向）

7. **🔬 Agent IPC** — 在 NimFS 基础上构建真正的 pub/sub 消息机制，让 Agent 间通信更高效
8. **📊 可观测性** — 为 vCPU/MMU/NimFS 添加 metrics 采集，构建 Agent 系统的 "top/htop"
9. **🧪 Specialist Checkpoint** — Specialist 超时后可从 checkpoint 恢复继续执行

---

## 附录：核心文件索引

| 文件 | 路径 | 职责 |
|------|------|------|
| AgentOS | `src/nimbus/agentos.py` | 操作系统主入口，进程/会话生命周期 |
| vCPU | `src/nimbus/core/runtime/vcpu.py` | LLM 执行引擎，Think-Act-Observe 循环 |
| MMU | `src/nimbus/core/memory/mmu.py` | 上下文管理，栈帧，NimFS offload |
| NimFS | `src/nimbus/core/nimfs/manager.py` | 持久化文件系统，Artifact + Memory |
| Scheduler | `src/nimbus/core/scheduler.py` | DAG 任务调度 |
| Heart | `src/nimbus/core/heart.py` | 后台守护进程框架 |
| Compaction | `src/nimbus/core/compaction.py` | 上下文压缩算法 |
| Specialist | `src/nimbus/orchestration/specialist_tools.py` | 多 Agent 委派 |
| Prompts | `src/nimbus/orchestration/prompts.py` | 系统提示词模板 |
| Server | `src/nimbus/server/` | HTTP/SSE 服务层 |
| Skills | `src/nimbus/skills/` | 可扩展工具系统 |
