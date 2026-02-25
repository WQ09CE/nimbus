# AI Review Committee: heart-autonomous-agent-architecture

- **Date**: 2026-02-23 20:25:04
- **Focus**: architecture
- **Reviewers**: 3
- **Total Time**: 120.0s

---

## Review by `anthropic/claude-opus-4-6`

(Review failed: Process proc-24fec2f5 timed out after 120.0s)

---

## Review by `openai-codex/gpt-5.3-codex`

SubmitResult(result='## 1) Overall Assessment

**Score: 8.3/10** — 方向非常正确，核心诉求清晰（从被动响应到持续自治），但在**边界定义、可靠性机制、治理与安全约束**上还需要工程化落地细化。

---

## 2) Strengths

1. **问题定义精准（背景 + 架构映射）**  
   你抓住了当前 Agent 框架的共性缺陷：`user-triggered only`。用 Brain/Heart/Body 映射把“缺什么”讲清楚了，且 Heart 的定位是“系统级常驻能力”而不是“某个 specialist 插件”。

2. **职责拆分完整（Heart 核心职责 1~4）**  
   Memory consolidation、health monitoring、proactive alerting、self-evolution 形成了较完整的闭环（观察→提炼→告警→改进）。特别是“patterns → principles”的分层抽象，体现了长期知识积累意识。

3. **实现路径现实（方案 A/B/C）**  
   提供了 cron、event-driven、hybrid 三种模式，并推荐混合模式，符合生产系统常见实践。说明你不是停留在概念，而是考虑了实际调度范式。

4. **考虑了成本（关键设计问题 #4）**  
   规则优先、LLM按需调用、低成本模型、token budget，这些是让 Heart 可持续运行的关键前提，方向正确。

5. **与现有体系有接口意识（NimFS 集成点）**  
   指出了 Heart 与 Brain 的数据交互渠道（NimFS Memory / Anchor），具备可插拔演进潜力，不必推翻现有 Orchestrator/Specialists。

---

## 3) Issues Found

### Issue 1
- **Severity:** 🔴 Critical  
- **Location:** “Heart 的核心职责”整体 + “与现有 Nimbus 架构的集成点”  
- **Description:** **职责边界尚不严格**。Heart 已覆盖“提醒、优化建议、上下文准备”，容易越界到 Brain 的决策域（policy/plan selection）。若不设边界，可能出现“双脑决策冲突”。  
- **Suggestion:** 明确定义 RACI：  
  - Heart = Observe/Assess/Recommend/Trigger  
  - Brain = Decide/Prioritize/Commit  
  Heart 产出应以 `advisory artifacts`（建议、风险评分、候选上下文）为主，禁止直接改任务计划（除非在受控策略下）。

### Issue 2
- **Severity:** 🔴 Critical  
- **Location:** 方案 C（混合模式）  
- **Description:** 缺少**可靠性语义**：at-least-once / exactly-once、幂等、重试、死信队列、去重键均未定义。事件驱动一旦重复投递会导致重复合并记忆或重复告警。  
- **Suggestion:** 引入事件处理契约：  
  - Event ID + idempotency key  
  - 幂等写（upsert with version）  
  - retry with backoff + DLQ  
  - “告警冷却窗口”防风暴  
  并在 NimFS 上定义 Heart 专用日志/状态表结构。

### Issue 3
- **Severity:** 🟡 Major  
- **Location:** 记忆管理（Memory Consolidation）  
- **Description:** 缺少**记忆模型/生命周期**定义（短期、工作记忆、长期知识、归档），目前只是动作清单。  
- **Suggestion:** 设计 memory schema：  
  - `raw_event` → `case` → `pattern` → `principle`  
  - 每层含：置信度、来源、时间衰减、可追溯引用  
  - TTL + retention policy + merge policy（语义相似阈值）  
  避免“过度压缩导致知识丢失”。

### Issue 4
- **Severity:** 🟡 Major  
- **Location:** 健康监控 + 主动提醒  
- **Description:** 指标提到了，但没有**SLO/阈值/动作策略**。没有阈值就无法做自动化治理。  
- **Suggestion:** 建立最小运行指标集（MVP SLO）：  
  - Task success rate、P95 latency、tool error rate、compaction rate、token burn/day  
  - 每项定义：阈值、持续时间窗口、触发动作（仅告警/降级/熔断）

### Issue 5
- **Severity:** 🟡 Major  
- **Location:** 自我进化（Self-Evolution）  
- **Description:** “Prompt 优化建议”若直接生效存在**自我修改风险**（reward hacking / regression）。  
- **Suggestion:** 采用三段式：  
  1) Heart 生成候选改动  
  2) 离线回放评估（固定基准任务集）  
  3) Brain/人审批后灰度发布  
  强制保留回滚点和变更审计。

### Issue 6
- **Severity:** 🟡 Major  
- **Location:** 成本控制  
- **Description:** 只有 token budget，缺少**预算分层和降级策略**。  
- **Suggestion:** 增加 budget policy：  
  - per-hour/per-day/per-capability 配额  
  - 超预算时降级到规则模式  
  - 低价值任务延迟批处理（nightly）  
  - 保底预算留给错误分析和健康告警

### Issue 7
- **Severity:** 🔵 Minor  
- **Location:** 方案 A 心跳频率（5-10 分钟）  
- **Description:** 固定周期可能产生“无效唤醒”与抖动，尤其低活动时浪费。  
- **Suggestion:** 改为**自适应心跳**：基于事件密度与系统负载动态调整（1/5/15/60 分钟档）。

### Issue 8
- **Severity:** 🔵 Minor  
- **Location:** 与 Wukong 六根融合  
- **Description:** 概念映射清晰，但缺少对“六根输出标准化”的说明，Heart 可能无法稳定消费。  
- **Suggestion:** 统一 specialist telemetry/event schema（工具名、耗时、结果码、artifact 引用、风险标签）。

---

## 4) Architecture/Design Observations

1. **这是从“Agent Runtime”走向“AgentOS”的关键一步**  
   你的 Heart 本质是 control-plane（治理/监控/知识运维），Brain/Body 是 data-plane（推理/执行）。这是架构成熟度升级信号。

2. **建议把 Heart 设计成“策略执行器 + 分析器”双核**  
   - Policy Engine（规则、预算、阈值、节流）  
   - Intelligence Engine（LLM 归纳、模式发现）  
   先把 policy engine 做硬，再渐进增强 intelligence，风险最低。

3. **记忆维护应“可追溯优先于聪明”**  
   长期系统中，最怕“总结很聪明但不可验证”。每条 pattern/principle 都应能反查到原始 case 与会话证据。

4. **主动性要有“权限边界”**  
   Heart 可以主动提醒、准备上下文，但不应默认主动触发高风险执行动作。把 autonomy 分级（read-only / suggest / auto-act with guardrail）。

---

## 5) Actionable Recommendations (Top 3, prioritized)

### 1) 先定义 Heart 的治理契约（最高优先级）
输出一份 `Heart Governance Spec`：  
- 职责边界（Heart vs Brain）  
- 事件语义（幂等、重试、DLQ、去重）  
- 动作权限等级（建议/半自动/全自动）  
这是防止后续系统复杂化失控的地基。

### 2) 落地最小可用 Heart MVP（4 个能力即可）
- 会话结束 consolidation（事件触发）  
- 基础健康看板（5 个核心指标）  
- 超阈值告警（含冷却窗口）  
- 日报生成（规则优先，LLM可选）  
先跑 2 周收集真实数据，再扩展 self-evolution。

### 3) 建立“自我进化安全流水线”
任何 prompt/tool/workflow 优化建议必须经过：  
`candidate -> offline replay -> approval -> canary -> rollback`  
没有这条线，self-evolution 会成为系统不稳定源。

---

**一句话结论：**提案方向很强，已经具备产品级愿景；下一步要把“概念 Heart”收敛成“可治理、可追溯、可回滚”的工程 Heart。')

---

## Review by `google/gemini-3.1-pro-preview`

### 1. Overall Assessment
**Score: 9/10** — A highly innovative and necessary architectural shift that successfully maps OS-level background daemon patterns (like Garbage Collection and cron jobs) into cognitive agent design.

### 2. Strengths
*   **Decoupling of Lifecycle from Execution (Background Context):** Moving memory consolidation and health checks out of the synchronous critical path of the VCPU loop is brilliant. It solves the latency vs. context-quality tradeoff.
*   **NimFS as the Shared Bus:** Utilizing NimFS as the integration point (Shared Memory) rather than building complex RPCs between Brain and Heart simplifies the architecture significantly.
*   **Cost-Awareness (Key Design Questions):** Explicitly calling out the use of rules/heuristics first, and falling back to cheap models (Flash) for complex tasks, prevents the "Heart" from burning the token budget.
*   **Pragmatic Implementation Strategy (Plan C):** The hybrid Event-Driven + Cron approach correctly mirrors real-world system maintenance (immediate reaction to faults + periodic defragmentation).

### 3. Issues Found

*   🔴 **Severity:** Critical
    *   **Location:** 架构映射 & 核心职责 1 (Memory Consolidation) / NimFS Integration
    *   **Description:** **Concurrency and Race Conditions.** If the Heart is a persistent background process constantly mutating, deduplicating, or deleting memories in NimFS while the Brain/Specialists are actively reading/writing, you will hit severe race conditions. A specialist might hold a reference to a memory node that the Heart just garbage-collected.
    *   **Suggestion:** Implement a robust Read-Write lock mechanism in NimFS, or adopt an MVCC (Multi-Version Concurrency Control) approach. Alternatively, restrict Heart's destructive actions (like GC) to periods when the Brain is idle (the "Sleep" state).

*   🟡 **Severity:** Major
    *   **Location:** 核心职责 3 (Proactive Alerting)
    *   **Description:** **Context Injection Interruptions.** How does the Heart "proactively alert" the Brain without breaking the Orchestrator's current train of thought or corrupting its immediate context window? An unexpected prompt injection mid-task could derail an implementation agent.
    *   **Suggestion:** Implement an asynchronous `MessageQueue` or `Notification Inbox` in the VCPU/Orchestrator. The Brain checks this inbox at safe boundaries (e.g., at the start of a new loop iteration), rather than having the Heart forcibly interrupt.

*   🟡 **Severity:** Major
    *   **Location:** 核心职责 1 (Memory Consolidation)
    *   **Description:** **Unbounded Processing Costs.** Even with Gemini Flash, running consolidation on growing memory stores (e.g., the 100K limit mentioned in NimFS specs) every N minutes will cause severe token burn.
    *   **Suggestion:** Implement a "Dirty Bit" or "Delta Tracking" system. The Heart should only process memory blocks that have been modified or added since the last heartbeat, rather than scanning the entire historical context.

*   🔵 **Severity:** Minor
    *   **Location:** 技术实现方向 (Plan C)
    *   **Description:** **Who monitors the Heart? (Watchdog needed).** If the Heart is a persistent async loop, unhandled exceptions in the LLM call or network timeouts could crash the background task silently, leaving the system thinking it is healthy.
    *   **Suggestion:** Wrap the Heart in a lightweight Watchdog process that restarts it on failure and logs the crash.

### 4. Architecture/Design Observations
*   **The OS Metaphor is Complete:** You have essentially designed an Operating System for LLMs. 
    *   `Brain` = CPU / Scheduler
    *   `Body` = I/O Peripherals / Threads
    *   `NimFS` = RAM / Disk
    *   `Heart` = Systemd / Garbage Collector / Cron
*   **The "Sleep" State:** The biological metaphor implies a difference between "awake" (active user session) and "asleep" (idle). You should formalize the `Idle` state in Nimbus. Deep memory consolidation (like clustering patterns into principles) requires heavy compute and should exclusively happen during "Sleep" to save bandwidth and avoid locks.
*   **Synergy with Recent Optimizations:** Based on the workspace context (NimFS wildcard searches, Subagent compact-continue), the Heart is the perfect place to run the background compaction logic, preventing the VCPU from doing heavy context-compression on the fly.

### 5. Actionable Recommendations (Top 3 Priority)
1.  **Implement a "Dirty/Idle" Mechanism in NimFS:** Before building the Heart's LLM logic, add metadata to NimFS records to track what is new/dirty, and an `is_idle` flag for the Orchestrator. The Heart only runs heavy tasks when `is_idle=True` and only processes `dirty=True` records.
2.  **Build an Async Inbox for the Brain:** Define a standard protocol for how the Heart passes alerts to the Orchestrator without interrupting active execution flows (e.g., `Brain.inbox.append(Alert)`).
3.  **Start with the "Rules-Only" Heart:** Implement Plan A (Cron) using purely deterministic Python code first (e.g., cleaning up known garbage tags, enforcing the 4000-line read limit, simple token math). Prove the persistent daemon architecture works stably before adding LLM-based memory consolidation.

---
