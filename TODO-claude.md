# Nimbus Agent Framework — TODO & 架构分析

> 由 Claude 基于代码审阅生成，2026-03-06
> 基于 Pi Coding Agent 设计思路对齐后的现状分析与改进路线

---

## 一、当前架构快照

### 核心隐喻：von Neumann Agent OS

```
Application Layer      (Web UI, TUI, HTTP API)
    ↓
Server Layer           (SSE Streaming, REST API)
    ↓
AgentOS Layer          (进程生命周期, 会话管理)
    ├─ vCPU             Think-Act-Observe FSM 执行引擎     ~1,905 LOC
    ├─ MMU              Anchor & Stream 上下文管理          ~1,184 LOC
    ├─ Scheduler        DAG 任务编排                       ~1,039 LOC
    └─ Heart            后台守护进程框架                    ~175 LOC
    ↓
NimFS Layer            Artifact/Memory 持久化              ~750 LOC
    ↓
LLM Adapters           Anthropic / OpenAI / Gemini / Ollama / LiteLLM
```

### 各模块完成度

| 模块 | 完成度 | 一句话状态 |
|------|--------|-----------|
| vCPU (FSM) | 100% | 完整的 Think-Act-Observe 循环，护栏齐全 |
| Adapter | 95% | LiteLLM 统一接入，extended thinking 支持 |
| MMU | 100% | Anchor & Stream，smart drop，lazy offload，multi-round compaction |
| NimFS Artifacts | 100% | 两阶段提交，TTL GC，claim-check IPC |
| NimFS Memory | 90% | L0/L1/L2 三层金字塔，六类分类，搜索可用 |
| Heart | 40% | 框架完整，但模块生态稀疏，consolidator 是空壳 |
| Process/SubAgent | 60% | spawn/wait 可用，但 process 和 session 概念纠缠 |
| IPC | 50% | Mailbox 通信 + schema 校验，但缺少 pub/sub |
| Specialist Roles | 90% | Explorer/Architect/Implementer/Tester 权限隔离 |
| UI 推送 | 60% | SSE streaming 可用，但前端呈现粗糙 |

---

## 二、待办事项

### P0 — Agent Process 与 SubAgent 系统

**问题**：`AgentOS`（agentos.py，2,324 LOC）是个 God Class，混合了进程管理、会话管理、compaction 调度、NimFS GC、skill 加载。process 概念和 session 概念纠缠不清。

**目标**：让 process 成为纯粹的执行实体（有 PID、有状态机、可 fork/join），session 成为面向用户的会话容器。

- [ ] **拆分 AgentOS**：抽出 `ProcessManager`、`SessionManager`、`CompactionScheduler`、`NimFSGC` 为独立模块
- [ ] **定义 Process 生命周期**：CREATED → READY → RUNNING → BLOCKED → TERMINATED，与 FSM 状态解耦
- [ ] **Process 与 Session 解耦**：一个 Session 可以 spawn 多个 Process，Process 不感知 Session 的存在
- [ ] **引入 lightweight call 路径**：简单委托不需要走全套 SpawnSubAgent + FSM 流程，共享 NimFS 但用独立 context window
- [ ] **实现 `parallel_subagents_design.md` 中的 `spawn_batch`**：支持 wait_all / wait_threshold / race 三种聚合策略
- [ ] **超时抢救（Graceful Scavenging）落地**：`scavenge_last_thought()` 目前仅设计，需要实现 checkpoint recovery

### P1 — Memory 系统整理

**问题**：NimFS Memory 的 L0/L1/L2 存储结构很好，但记忆的自动流转（RAW_EVENT → CASE → PATTERN → PRINCIPLE）还没真正跑起来。Heart 的 memory_consolidator 是空壳。

**目标**：让 agent 在长任务中自然积累和利用记忆，而不是靠 LLM 主循环自己维护状态。

- [ ] **实现 MemoryConsolidator Heart 模块**：
  - 异步监听 vCPU 执行轨迹（通过 HeartInbox 的 `session.iteration` 事件）
  - 从 tool 调用序列中提取 CASE（成功/失败模式）
  - 从多个 CASE 中归纳 PATTERN
  - 定期更新 L0 abstract 供 Anchor 注入
- [ ] **记忆检索优化**：当前 `search_memory` 是基于关键词的，考虑加入 embedding-based 相似度搜索（可选，依赖外部服务）
- [ ] **记忆去重增强**：目前只在 PROFILE/PREFERENCES 做了去重，ENTITIES/EVENTS/CASES 也需要
- [ ] **记忆过期策略**：EVENTS 类记忆应有时间衰减，老事件降低 confidence 或自动归档
- [ ] **跨 Session 记忆传递**：确保 project-scope 记忆在新 session 启动时正确加载到 Anchor

### P2 — vCPU 重构

**问题**：vcpu.py 1,905 LOC 是个 God Class，混合了循环控制、工具执行、错误恢复、doom loop 检测、IPC 通信。难以单独测试。

**目标**：拆为职责单一的子模块，保持 FSM 的纯净性。

- [ ] **抽出 ToolExecutor**：从 vCPU 中分离工具执行逻辑（串行/并发/中断处理）
- [ ] **抽出 DoomLoopDetector**：独立的循环检测组件，可注入不同策略
- [ ] **抽出 ErrorRecoveryManager**：指数退避、错误注入、熔断逻辑独立
- [ ] **FSM 纯化**：fsm.py 只负责状态转换定义，不包含业务逻辑
- [ ] **Promise Gate 修复**：LLM 输出 "I will do X" 但不调用工具的空转问题，需要在 Decoder 层检测并强制重试

### P3 — UI 推送与呈现

**问题**：RuntimeLoop 已经 yield 丰富的事件（step/done/interrupted/compaction/budget_summary），但前端消费不充分。

**目标**：让用户实时感知 agent 的执行状态，类似 htop 的可观测性。

- [ ] **新增 `progress` 事件类型**：包含当前 FSM 状态、迭代计数、token 使用量（已用/剩余/预算）、活跃 tool 名称
- [ ] **前端进度面板**：
  - 实时 token 使用条（类似内存使用率）
  - 迭代计数器（当前步 / 最大步）
  - FSM 状态指示灯（THINKING / ACTING / OBSERVING / RECOVERING）
  - compaction 触发时的视觉提示
- [ ] **多 Agent 任务组卡片**：参考 `parallel_subagents_design.md` 中的 UI/UX 设计
  - 每个子任务独立进度条
  - 状态标记（完成 / 部分回收 / 失败）
  - 点击展开查看详细执行轨迹
- [ ] **NimFS Memory 可视化**：展示当前项目的记忆图谱（按类别分组，显示 L0 摘要）

---

## 三、架构层面的设计建议

### 3.1 关于 "显式 TODO 是否影响干活"

这是设计 agent 框架时的核心 tradeoff。当前 Nimbus 的做法是正确的方向：

**推荐方案**：状态维护从主循环外移到 Heart

- 主 agent（vCPU）专注于 Think-Act-Observe，不需要自己维护 TODO
- Heart 的 SessionMonitor 异步观察执行轨迹，自动更新进度状态
- 每轮 `transform_context_hook` 时，从 Heart 的状态快照中注入当前进度到 Anchor
- 用户通过 UI 看到的进度来自 Heart 的视角，而非 agent 的自述

**好处**：
- 不占用主 context window 的 token
- 不打断 agent 的推理流
- 状态更客观（基于实际行为而非 LLM 的自我报告）
- compaction 后状态不丢失（Heart 独立于 MMU）

### 3.2 关于 Process 与 SubAgent 的精简

当前的调用链：`AgentOS.spawn() → Process → RuntimeLoop → vCPU → MMU + Tools`

**建议三级委托模型**：

| 级别 | 适用场景 | 开销 |
|------|----------|------|
| **Inline Call** | 单次 LLM 调用（总结、分类、提取） | 无新进程，共享 MMU 的临时 frame |
| **Light Process** | 独立子任务（探索、审查） | 独立 context window，共享 NimFS，无 Heart 监控 |
| **Full Process** | 长任务（实现、重构） | 完整 FSM + MMU + Heart 监控 + checkpoint |

这样简单的委托不需要付出完整进程的启动和管理成本。

### 3.3 关于 Pi Coding Agent 的对齐

Pi Coding Agent 的核心洞察：

1. **用文件系统作为 agent 间的通信介质**，而非内存中的消息传递 — Nimbus 的 NimFS 已经在做这件事
2. **每个 agent 启动时从文件重建上下文**，而非依赖 context window 的延续 — 对应 NimFS Memory 的 L0 注入
3. **显式的 progress 文件记录当前状态** — Heart 可以承担这个角色，写 `.nimbus/progress.md`

---

## 四、短期清理项

- [ ] 根目录杂文件清理：`quicksort.py`、`hello.py`、`patch_*.py`、`fix_tests.py` 等应移到 `scripts/` 或删除
- [ ] 文档整合：当前 ~110 个 .md 文件，大量过期，建议精简到 ~30 个活跃文档
- [ ] `NIMBUS_STATUS.md` 更新：当前内容过于笼统，应反映真实架构状态
- [ ] 测试覆盖：e2e 测试文件散落在 `tests/` 根目录和子目录，需要统一组织

---

## 五、建议优先级排序

```
Phase 1 (1-2 周)    清理 + Process/Session 解耦
Phase 2 (2-4 周)    vCPU 拆分 + MemoryConsolidator 实现
Phase 3 (4-6 周)    多 Agent 精简（三级委托） + spawn_batch 落地
Phase 4 (持续)      UI 可观测性 + 记忆检索优化
```

---

*Last updated: 2026-03-06 by Claude (based on full codebase review)*
