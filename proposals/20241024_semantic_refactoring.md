# 提案：Nimbus Agentic Loop 语义重构 (Semantic Refactoring)

**状态：** Draft
**角色：** Architect Agent
**日期：** 2024-10-24

## 1. 执行摘要 (Executive Summary)

当前 Nimbus 系统的 `InstructionDecoder` 采用启发式匹配（Heuristic Matching），导致 VCPU 无法在协议层面区分 LLM 的“内部自省”与“用户可见回复”。这不仅引发了频繁的 `max_consecutive_thoughts` 限制和无效的 System Poke，也导致 Web-UI 状态机由于缺乏明确的语义边界而产生“空长条”。

本提案建议通过以下三项核心变革进行语义重构：
1. **显式指令集架构 (ISA) 升级**：引入 `REPLY` 与 `THOUGHT` 指令区分。
2. **角色感知状态机 (Role-Aware FSM)**：区分 Orchestrator（策略者）与 Specialist（执行者）的运行逻辑。
3. **非阻塞观察流 (Non-blocking Observation Stream)**：解耦 UI 同步与 VCPU 步进。

---

## 2. 核心问题分析与解决方案

### 2.1 协议层：区分内部思考与外部答复

**现状：** `InstructionDecoder` 将所有非工具调用的文本统一归类为 `THOUGHT`。VCPU 在执行 `THOUGHT` 后默认进入下一轮推理，若无工具调用则触发循环限制。

**重构方案：**
在 `protocol.ActionIR` 中引入明确的语义标签。

- **`THOUGHT` (Internal)**: 对应 LLM 的 `<thought>` 标签或 ReAct 的 `Thought:`。该内容**不应**直接流向用户 UI，而是作为 VCPU 的内部状态。
- **`REPLY` (External)**: 显式标记为发给用户的消息。一旦解析到 `REPLY`，VCPU 立即进入 `HALT` 状态，等待用户输入。

**容错处理 (LLM 不按格式输出时)：**
引入 **"Semantic Fallback"** 逻辑：
1. **优先匹配 XML/Markdown 标记**。
2. **无标记文本分析**：
   - 若文本包含后续计划（如 "Next, I will..."），标记为 `THOUGHT` 并追加 `POKE`。
   - 若文本为结束语或提问（如 "How can I help?"），标记为 `REPLY`。
   - 若模型在一次输出中混合了工具调用和文本，强制拆分为 `(THOUGHT, CALL)` 序列。

### 2.2 VCPU 角色感知逻辑 (Orchestrator vs Specialist)

**现状：** 所有 Agent 共享同一套 `max_consecutive_thoughts` 逻辑。

**重构方案：**
引入 `ExecutionMode` 状态机。

| 角色 (Role) | 核心行为 | VCPU 策略 |
| :--- | :--- | :--- |
| **Orchestrator** | 任务拆解、分发、汇总 | 允许较高的 `max_thoughts`；`REPLY` 仅用于最终结果反馈。 |
| **Specialist** | 具体工具执行（如 Coder, Searcher） | 严格限制 `max_thoughts=1`；强制要求工具调用或返回结果。 |

**状态转换图建议：**
- **Orchestrator 模式**：增加 `DELEGATE` 指令，用于触发子任务，VCPU 进入 `WAIT_SUBTASK`。
- **Specialist 模式**：指令解码器对 `CALL` 的缺失极其敏感，直接报错而非尝试补全。

### 2.3 非阻塞式 UI 状态同步

**现状：** UI 监听 VCPU 的步进。当 VCPU 执行长耗时工具或进行多次连续思考时，UI 容易产生“假死”或显示空的执行条。

**重构方案：**
引入 **"Shadow State Sync"** 机制。

1. **心跳与意图流 (Intent Stream)**：
   - VCPU 在开始思考前，向 UI 发送 `Intent(type="thinking", scope="file_analysis")`。
   - UI 立即渲染占位符，而非等待 `ActionIR` 生成。
2. **原子化观察 (Atomic Observation)**：
   - 将 `Observation` 拆分为 `ExecutionUpdate` (进度) 和 `FinalResult` (结果)。
   - UI 实时通过 WebSocket 接收进度片段，消除“空长条”。
3. **消除 System Poke**：
   - 取消强制的 `System: Please continue`。改为 VCPU 内部自循环。只有当模型明确需要外部信息时，才通过 `REPLY(type="request_info")` 挂起。

---

## 3. 技术路线图 (Implementation Roadmap)

### Phase 1: Decoder 重写 (Protocol Layer)
- 修改 `src/nimbus/core/protocol.py`，增加 `ActionType.REPLY` 和 `ActionType.DELEGATE`。
- 实现 `SemanticDecoder` 类，替代当前的猜测逻辑。

### Phase 2: VCPU 状态机增强 (Runtime Layer)
- 在 `VCPU.run()` 中实现基于 `ActionType` 的动态分支。
- 引入 `RoleProfile` 对象，决定 `max_consecutive_thoughts` 的动态阈值。

### Phase 3: UI 协议对齐 (Frontend Layer)
- 更新 Web-UI，支持区分“思考区块”与“回复区块”。
- 实现非阻塞的状态流转监听。

---

## 4. Review Committee 待评估项

1. **启发式分析的开销**：在 `InstructionDecoder` 中引入语义分析（甚至小型判别模型）是否会显著增加延迟？
2. **向后兼容性**：现有的 Specialist Agent 提示词（Prompt）是否能平滑过渡到新的 `REPLY` 机制？
3. **调试可见性**：当 VCPU 进入 `INTERNAL_THOUGHT` 时，开发者控制台是否应该强制显示这些“隐藏”的思考以备调试？

---
**Architect Agent**
*Nimbus Architecture Group*
