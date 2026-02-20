# AI Review Committee: nimbus-loop-refactoring-review

- **Date**: 2026-02-20 12:52:16
- **Focus**: architecture, user-experience, robustness
- **Reviewers**: 3
- **Total Time**: 38.8s

---

## Review by `anthropic/claude-opus-4-6`

(Review failed: LLM call failed: LLM Stream Error: Error code: 429 - {'type': 'error', 'error': {'type': 'rate_limit_error', 'message': "This request would exceed your account's rate limit. Please try again later."}, 'request_id': 'req_011CYJikFLL3rnJ1GQmHMPai'})

---

## Review by `openai-codex/gpt-5.3-codex`

## 1) Overall Assessment

**Score: 8.2 / 10** — 方案方向正确、抓住了核心痛点，但在“格式失配降级路径、状态一致性协议、编排者思考保真”三处还需要补齐工程化细节。

---

## 2) Strengths

1. **问题定义准确且与现网症状强耦合**  
   - “语义模糊 / UI挂起 / 解析失效”三点与 Agentic Loop 常见故障模型一致，优先级判断合理。  
   - 尤其把“System Poke 频发”定位为协议层语义歧义问题，不是简单调参问题，这一点很到位。

2. **“协议 + 运行时”双层改造思路正确**  
   - 提案不是只改 prompt（软约束），而是引入 `<thought>/<reply>` 与 VCPU 角色规则（硬约束），属于可落地的架构改造。  
   - “确定性熔断（两轮无动作注入 RETURN）”能显著提升鲁棒性上界，避免无限循环。

3. **UI 状态从“文本推断”转向“状态机订阅”是关键进步**  
   - 从监听 token 文本改为监听 VCPU 显式状态（IDLE/BUSY/WAIT_USER）是系统边界清晰化的典型改进。  
   - 这将减少“空长条等待”与假忙碌状态，改善用户心理预期管理。

---

## 3) Issues Found

### Issue A
- **Severity**: 🔴 Critical  
- **Location**: 提议方案 1（物理隔离协议）+ 评审重点“软解析”  
- **Description**: 方案强调强制标签，但缺少**非规范输出的分级回退策略**。在真实流量中，LLM 会出现标签缺失、嵌套错误、半截输出、混合自然语言/XML。若只靠“强制”而无 recovery，会回到当前解析失效问题。  
- **Suggestion**: 设计三层解析器（严格→宽松→语义）：
  1) **Strict parser**：仅接受合法 `<thought>/<reply>`。  
  2) **Lenient parser**：容忍大小写、缺闭合、前后噪声，做最小修复。  
  3) **Semantic fallback**：基于意图分类（是否面向用户、是否包含行动计划、是否请求工具）映射为 THOUGHT/RETURN/ACT。  
  并记录 `parse_mode` 指标用于质量监控与回归测试。

---

### Issue B
- **Severity**: 🟡 Major  
- **Location**: 提议方案 2（角色感知 VCPU：编排者回复即结束）  
- **Description**: “回复即结束”对 Orchestrator 过于激进，可能截断“先总结中间结论再继续调度”场景（例如长任务中的阶段性对齐、确认假设、计划更新）。  
- **Suggestion**: 把“回复”细分为两类：  
  - `REPLY_FINAL`（结束）  
  - `REPLY_PROGRESS`（阶段性可见但不结束）  
  并增加显式字段 `continue: true/false` 或 `session_state: RUNNING/COMPLETE`。避免把“面向用户可读输出”误判为“终局输出”。

---

### Issue C
- **Severity**: 🔴 Critical  
- **Location**: 提议方案 4（UI状态同步）  
- **Description**: 只定义了状态枚举（IDLE/BUSY/WAIT_USER），但缺少**状态事件协议**（事件源、序列号、幂等、乱序处理）。高并发和流式下会出现 UI 回退、闪烁、旧状态覆盖新状态。  
- **Suggestion**: 引入状态事件模型：  
  - 字段：`run_id`, `turn_id`, `event_seq`, `state`, `timestamp`, `source`  
  - 规则：单调递增 `event_seq`；前端仅接受最新序列；断线重连支持“最后快照+增量事件”  
  - 传输建议：SSE/WebSocket 均可，但必须有 ack/replay 或至少 last-event-id 恢复机制。

---

### Issue D
- **Severity**: 🟡 Major  
- **Location**: 提议方案 3（两轮无有效动作熔断）  
- **Description**: 熔断触发条件“无有效动作”定义不够明确。若把“思考深化/上下文澄清”都判无效，可能导致过早 RETURN；若太宽松又失去熔断价值。  
- **Suggestion**: 定义“有效动作”判定矩阵：  
  - 工具调用成功/失败（均算推进）  
  - 状态变更（算推进）  
  - 用户可执行问题澄清（算推进）  
  - 重复复述（不算）  
  并增加“同质内容相似度阈值”检测循环（例如 embedding/cosine > 阈值 连续 N 次）。

---

### Issue E
- **Severity**: 🔵 Minor  
- **Location**: 全文（可观测性）  
- **Description**: 缺少上线后验证指标，难判断改造是否真正减少 UI 挂起与 Poke。  
- **Suggestion**: 增加核心 SLO/SLI：  
  - `format_compliance_rate`  
  - `fallback_parse_rate`（分 strict/lenient/semantic）  
  - `avg_idle_gap_ms`（UI空等待）  
  - `forced_return_rate`（熔断率）  
  - `task_success_rate` / `user_interrupt_rate`

---

## 4) Architecture / Design Observations

1. **你们正在从“生成式控制”走向“协议驱动控制”，这是正确演进路径**  
   但协议必须包含“失败语义”，即 LLM 不守约时系统如何确定性退化。否则“强协议”在生产中会变“脆协议”。

2. **角色语义应与执行生命周期解耦**  
   “谁说话（orchestrator/sub-agent）”不应直接绑定“是否结束”。结束条件应由任务状态机统一裁决，角色只影响默认策略。

3. **UI 体验问题本质是分布式一致性问题，不只是前端展示问题**  
   你们已经迈出第一步（显式状态），下一步应补齐事件时序与恢复机制，否则高并发时会出现“看起来随机”的前端异常。

---

## 5) Actionable Recommendations (Top 3, prioritized)

1. **先落地“分层软解析 + 失败回退状态机”**（最高优先级）  
   - 严格/宽松/语义三级解析 + parse_mode 打点  
   - 无论输出多脏，都能给 UI 一个可解释状态（至少 BUSY→WAIT_USER/IDLE 可闭环）

2. **重构结束语义：引入 `REPLY_FINAL` vs `REPLY_PROGRESS`**  
   - 避免“回复即结束”误杀长程编排  
   - 用任务状态机统一收敛终止条件，角色规则仅作为 default hint

3. **定义状态事件协议并做乱序防护**  
   - `run_id + turn_id + event_seq` 必备  
   - 前端按 seq 去重与丢弃旧事件  
   - 支持断线重放（last-event-id / snapshot+delta）  
   这是解决“空长条/状态跳变”的关键工程保障。

--- 

总体来说，这个提案已经具备上线价值；把上述三处补全后，架构稳定性和 UX 一致性会从“可用”提升到“可依赖”。

---

## Review by `google/gemini-3.1-pro-preview`

Here is the structured review of the "Nimbus Agentic Loop Semantic Refactoring Design Proposal" from the perspective of an architecture, UX, and robustness expert.

### 1. Overall Assessment
**Score: 8/10**
**Summary**: A strong, pragmatic proposal that successfully shifts the Agentic Loop from implicit guessing to explicit state management, though it requires robust fallback mechanisms for LLM non-compliance.

### 2. Strengths
*   **Architectural Clarity (UI State Sync)**: Shifting UI updates from parsing raw text streams to listening for explicit VCPU states (IDLE/BUSY/WAIT_USER) is an excellent architectural decision. It decouples the frontend from the LLM output volatility and solves the "empty long bar" UX issue.
*   **Enhanced Robustness (Deterministic Circuit Breaker)**: The "2 rounds no action -> force `RETURN`" rule acts as a solid fail-safe against infinite loops or LLM hallucinations, preventing system lockups and API budget drain.
*   **Role-Aware Processing**: Differentiating the VCPU logic based on agent roles (Orchestrator vs. Sub-agent) smartly handles the different lifecycle expectations of hierarchical agents.

### 3. Issues Found

*   **🔴 Critical | Issue: Rigid physical isolation protocol**
    *   *Location*: Proposed Solution 1 (Forced `<thought>` and `<reply>` tags) & Review Point 1.
    *   *Description*: Relying strictly on XML/HTML tags reduces robustness. If a smaller or less capable LLM (or a highly conversational model) drops the tags, the parser will fail, potentially discarding valid responses or triggering false circuit breakers.
    *   *Suggestion*: Implement a "Soft-Parsing Fallback". If no tags are found, use heuristics: if a tool call is present in the payload, treat the text as `<thought>`; if no tool call is present, treat it as `<reply>`. 

*   **🟡 Major | Issue: Premature truncation of Orchestrator CoT**
    *   *Location*: Proposed Solution 2 (Orchestrator reply = end) & Review Point 2.
    *   *Description*: If the Orchestr

... [Output truncated, 386 characters hidden. If you need the full content, use specific tools to read segments.] ...

>` (e.g., "I am delegating this now...") while remaining in a `BUSY` state to spawn sub-agents, rather than abruptly ending the loop.

*   **🔵 Minor | Issue: UI State Sync Granularity in Streaming**
    *   *Location*: Proposed Solution 4 (UI State Sync) & Review Point 3.
    *   *Description*: While shifting to VCPU state sync is good, if the VCPU holds the text buffer until a state transition occurs, the user loses the typewriter-effect streaming experience, leading to perceived latency.
    *   *Suggestion*: Implement a dual-channel sync. Use a metadata channel for VCPU state transitions (IDLE/BUSY), and a parallel fast-stream channel that flushes partial `<thought>` or `<reply>` tokens directly to the UI buffer.

### 4. Architecture/Design Observations
The proposal marks a necessary evolution from a **Reactive Parsing Architecture** to a **State-Machine Driven Architecture**. By formalizing the VCPU's state independently of the LLM's text stream, Nimbus becomes much more resilient to "semantic drift." However, the architectural assumption that LLMs can be perfectly constrained by prompting (Solution 1) is a known anti-pattern in production AI systems. The architecture must adopt a "trust but verify (and fallback)" approach to output parsing.

### 5. Actionable Recommendations
1.  **Implement a Two-Tier Parser**: Build a strict parser looking for `<thought>`/`<reply>`, backed by a heuristic fallback parser that infers intent based on the presence of tool calls when tags are missing.
2.  **Decouple Streaming from State Management**: Maintain the real-time token stream for UI UX (so users see the AI typing), but use the explicit VCPU state machine to control the UI layout (e.g., showing a spinner, expanding a thought block).
3.  **Refine the Orchestrator Lifecycle**: Modify the "reply = end" rule for the Orchestrator. Introduce an explicit `TERMINATE` tool or signal, allowing the Orchestrator to converse with the user while keeping the overarching task loop active.

---
