# Nimbus 系统执行效率优化提案 (Architecture Proposal)

## 0. 现状分析 (Problem Statement)
在最近的 Session 中，系统表现出严重的“高能耗、低产出”特征：
- **资源浪费**：75% 的时间（30/40 min）消耗在超时等待中。
- **可靠性瓶颈**：Specialist Agents 在大规模上下文（Explore）和高复杂度任务（Design）下频繁崩溃。
- **Promise Gate 顽疾**：模型仅作出口头承诺（Pure Text）而不触发工具调用，导致系统空转并最终超时。
- **无状态重试**：缺乏 Checkpoint 机制，导致超时后的重试必须从零开始，浪费已生成的中间产出。

---

## 1. 超时策略优化：从“静态等待”到“心跳/增量产出”
**问题**：目前超时是硬性断开，导致 10 分钟的等待后颗粒无收。

### 方案：Streaming Output & Progressive Timeout
- **增量提交 (Incremental Flush)**：
  - 修改 `Write` 工具，支持 `is_partial=True` 参数。Specialist 在完成阶段性工作（如写完 3 个文件中的 1 个）时，立即将内容写入 NimFS Artifacts。
- **动态心跳 (Active Heartbeat)**：
  - vCPU 监控 Specialist 的输出。只要 Specialist 在持续产生有效 Token 或触发工具调用，就自动延长 60s 租约，而不是死等固定 Timeout。
- **超时软着陆 (Soft Timeout)**：
  - 当 Timeout 到达时，vCPU 不直接 Kill 进程，而是发送 `SIGUSR1` 信号触发 Specialist 的 `finalize_and_exit` 逻辑，强行上报已完成的部分。

**预期收益**：减少 90% 的“完全浪费”时间。
**实现复杂度**：中（需要修改 vCPU 信号处理与工具协议）。

---

## 2. 任务拆分策略：Orchestrator 的“分而治之”
**问题**：Explore 任务一次扫描 110 个文件导致 OOM 或超时。

### 方案：Auto-Chunking Specialist Delegate
- **基于权重的拆分 (Weight-based Splitting)**：
  - Orchestrator 在委派前先进行 `ls -R` 或文件计数。如果文件数 > 20 或估算 Token > 32k，强制将任务拆分为 `[0-20]`, `[21-40]` 等批次并行执行。
- **结果聚合器 (MapReduce Pattern)**：
  - 引入一个轻量级的 `Aggregator` Specialist，专门负责将多个 Explore 的子结果汇总，避免 Orchestrator 亲自处理海量原始数据。

**预期收益**：Explore 成功率从 <30% 提升至 >90%。
**实现复杂度**：低（Orchestrator Prompt 调整 + 简单的循环逻辑）。

---

## 3. Specialist 失败恢复：基于 NimFS 的 Checkpoint 机制
**问题**：Opus 写完第一个文件后卡死，结果全部丢失。

### 方案：Stateful Task Resume
- **Task Context Shadowing**：
  - 每次 Specialist 成功调用 `Write` 后，vCPU 自动在 `nimfs://tasks/{task_id}/checkpoint` 下保存当前 Conversation History 的快照。
- **断点续传 (Resume from Artifact)**：
  - 当 Orchestrator 重新委派失败的任务时，可以选择 `resume_from={last_artifact_ref}`。新的 Specialist 启动时会预加载之前的对话上下文，直接从“Next step”开始。

**预期收益**：重试成本降低 50%-80%。
**实现复杂度**：高（需要深度集成 NimFS 与 vCPU 状态管理）。

---

## 4. Promise Gate 根治：Force Tool Call 强制约束
**问题**：LLM 给出文本承诺但不输出 JSON 工具调用，且 context validation 裁掉了这些文本导致循环。

### 方案：Grammar Constraints & Feedback Loop
- **强制 Schema (Regex/JSON Schema Mode)**：
  - 对于 Design 等关键任务，通过 API 强制要求 `response_format={"type": "json_object"}`，或者在 System Prompt 中加入强约束：`ANY text output MUST be followed by a tool call.`。
- **空转拦截器 (Nop-Response Interceptor)**：
  - 如果模型输出包含 "I will now write", "Let me create" 等关键词但没有工具调用，vCPU 立即在下一轮注入强力提示：`"You made a promise but didn't call the tool. Call the tool NOW or you will be terminated."`。
- **保留 Trailing Messages**：
  - 改进 Context Validation 逻辑，不直接裁掉最后的 Assistant 文本，而是将其作为证据反馈给下一次推理。

**预期收益**：彻底消除“空转超时”现象。
**实现复杂度**：中。

---

## 5. Orchestrator 自适应决策：性价比模型
**问题**：委派浪费 25 分钟，自己干只需 2 分钟。

### 方案：Heuristic Cost-Benefit Analysis
- **复杂度评估函数**：
  - Orchestrator 在委派前评估任务：
    - `Simple` (1-2 个文件，明确逻辑) -> **Direct Action** (自己 Bash/Write)。
    - `Complex` (需要设计、多文件联动) -> **Delegate** (委派 Specialist)。
- **失败即接管 (Fail-fast Takeover)**：
  - 如果 Specialist 第一次委派失败且原因非“任务太重”，Orchestrator 应立即判定为“委派开销过大”，转为手动执行。

**预期收益**：简单任务处理速度提升 10 倍。
**实现复杂度**：低。

---

## 6. 实施路线图 (Priority)
1. **P0**: Promise Gate 空转拦截器 & 任务拆分逻辑。
2. **P1**: Orchestrator 自适应决策逻辑 & 增量产出 (Write partial)。
3. **P2**: 基于 NimFS 的 Checkpoint 恢复机制。
