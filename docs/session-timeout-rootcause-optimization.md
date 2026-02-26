# Nimbus 多 Agent 40 分钟低效会话复盘与优化方案（架构提案）

## 0. 结论（先给答案）

本次 40 分钟会话的核心瓶颈不是模型能力，而是**执行控制面缺失**：

1. **超时策略过于“硬杀”且无阶段提交**，导致 6 次超时=30 分钟纯损耗。  
2. **Promise Gate 仍有漏网路径**（“承诺下一步但不发工具调用”），触发无效空转。  
3. **委派前复杂度评估缺位**，把“可由 Orchestrator 2 分钟完成”的任务送入高延迟 specialist 流程。  
4. **任务粒度与 timeout 不匹配**（110 文件扫描、设计任务上下文过大），导致可预见性失败。  

> 优先级建议：先做“失败止损 + 可恢复性 + Promise Gate 硬拦截”，再做“智能委派 + 自动分片”。

---

## 1. 本次会话根因分析（按损耗占比）

## 1.1 超时损耗是头号问题（75% 时间浪费）

- 事实：6 次超时，约 30 分钟。  
- 现象：Explore/Design 都有超时，说明并非单模型偶发，而是**系统性执行策略问题**。  
- 根因：
  - timeout 作为单一终止条件，缺少“进展感知（progress-aware）”
  - 超时时没有 `finalize/report partial result` 阶段
  - timeout 参数未根据任务复杂度/规模动态调节

## 1.2 Promise Gate 依旧是关键不稳定点

- Opus 案例：写完第一份文档后输出“Now let me write the second document”但 Tool Calls=0。  
- 之后 trailing assistant message 被裁剪，导致纠错信息丢失，再空转直到超时。  
- 根因：
  - 只做了“部分修复”，未形成**状态机级别强约束**
  - 诊断证据（trailing message）被上下文清洗流程丢弃

## 1.3 委派策略不经济（错误地把低复杂任务外包）

- 事实：Orchestrator 自己 Bash+Write 2 分钟完成最终产出。  
- 说明：当前 router 缺少 “成本收益判断”。  
- 根因：
  - 没有把“预估时延 × 失败概率 × 重试成本”纳入路由
  - 未做“首败即降级接管（takeover）”

## 1.4 任务切分缺失

- Explore 扫 110 docs 文件，单 agent 承载过大。  
- Design 任务 context 过大，容易触发慢响应和超时。  
- 根因：
  - 缺少自动分片/分治模板
  - 缺少 token/context 预算器

---

## 2. 对 Gemini Flash 8 条方案的评估（同意/改进/补充）

| 方案 | 评估 | 建议 |
|---|---|---|
| 心跳续租 | ✅ 同意 | 仅在“有实质进展”时续租，避免无意义续命 |
| Soft Timeout | ✅ 强烈同意 | 必做，超时前进入 finalize 窗口导出 partial |
| 自动分片 | ✅ 同意 | 分片阈值应动态（文件数+总大小+历史耗时） |
| NimFS Checkpoint | ✅ 同意 | 从“每次 Write 后”升级为“阶段完成后 + 工具结果后” |
| Promise Gate 空转拦截器 | ✅ 强烈同意 | 升级为状态机规则，不只是关键词拦截 |
| 保留 Trailing Message | ✅ 同意 | 作为诊断证据与下轮纠错输入，必须保留 |
| 复杂度评估函数 | ✅ 强烈同意 | 委派前置 gate；低复杂任务默认 orchestrator 直做 |
| Fail-fast Takeover | ✅ 同意 | 建议“首败半自动 + 二败强制接管” |

补充：还需要引入**SLO/指标体系**，否则无法验证优化有效性。

---

## 3. 可落地优化方案（按优先级）

## P0（1~3 天）：先止血，立刻减少超时浪费

### P0-1. 超时两阶段化：Hard Kill -> Soft Finalize -> Kill

**具体做法**
1. 每个 specialist timeout 拆为：
   - `T_soft`（例如 hard 的 85%）
   - `T_finalize`（例如 15~30s）
2. 到 `T_soft` 时注入系统指令：
   - 停止新探索
   - 输出已完成清单
   - 调用 Write/NimFS artifact 保存部分产物
3. `T_finalize` 后仍无工具调用才 hard kill。

**预期收益**
- 超时任务“全损”变“部分可用”。
- 以本案估算：30 分钟浪费可回收 30%~50%。

**实现复杂度**：中（调度层改动 + 超时状态机）。

---

### P0-2. Promise Gate 升级为“动作强约束状态机”

**具体做法**
在 vCPU 回合末加入规则：
- 若 assistant 文本包含未来承诺语义（如 `let me`, `next I will`, `现在我将`）且 `tool_calls == 0`：
  1) 标记 `promise_without_action` 事件；
  2) 立即注入纠错 system message：
     - “禁止预告，下一条必须至少一个工具调用或给出完成结论”；
  3) 本轮不计成功进展；连续2次触发则强制 finalize/接管。

同时：**保留 trailing assistant message**（最近 1~2 条）进入下一轮上下文，避免纠错证据丢失。

**预期收益**
- 明显减少“空转直到超时”的失败模式。
- 对 Opus 案例属于直接命中修复。

**实现复杂度**：低~中（loop 判定逻辑 + context 保留策略）。

---

### P0-3. 首次失败即降级策略（Fail-fast Takeover）

**具体做法**
- Explore/Design 任一 specialist 首次超时后：
  - 相同任务不再同模型重试；
  - 改为 Orchestrator 直做或改用更小任务分片。
- 第二次失败触发“强制接管”，禁用继续委派。

**预期收益**
- 防止“失败重试放大损失”。
- 可将本案 25 分钟无效委派压缩到 5~10 分钟。

**实现复杂度**：低（策略路由层）。

---

## P1（1~2 周）：提升成功率与吞吐

### P1-1. 委派前复杂度评分器（Routing Gate）

**具体做法**
构建评分函数（可先启发式，后续学习化）：

```text
score = w1*文件数 + w2*总字节 + w3*预计工具调用次数 + w4*是否需跨文件综合 + w5*历史该模型失败率
```

决策：
- `score < A`：Orchestrator 直接执行
- `A <= score < B`：委派但强制分片
- `>= B`：先拆任务，再并行委派

**预期收益**
- 避免“低收益委派”。
- 降低平均完成时间和 p95 超时率。

**实现复杂度**：中。

---

### P1-2. 自动分片器（针对 Explore/Docs 扫描）

**具体做法**
- 输入 110 文件时自动按“文件数+大小+目录语义”切分（例如 8~16 片）。
- 每片独立 timeout 与结果摘要。
- 聚合器只汇总结构化输出（要点/证据路径/风险）。

**预期收益**
- 降低单任务长尾超时概率。
- 失败只影响局部分片，整体可用性显著提高。

**实现复杂度**：中。

---

### P1-3. Checkpoint 协议化（不是“写了就算”）

**具体做法**
定义 checkpoint schema：
- `task_id, shard_id, phase, completed_steps, outputs, next_action`
- 在关键阶段落盘到 NimFS artifact：
  - 分片完成
  - 首个文档写完
  - 汇总前
- 重试时先读取最近 checkpoint，增量继续。

**预期收益**
- 超时/崩溃后可恢复，避免重复劳动。
- 对“写完第一个文件后卡死”场景直接止损。

**实现复杂度**：中~高（涉及恢复流程）。

---

## P2（2~4 周）：系统化治理

### P2-1. 进展感知心跳（Progress-based Lease）

**具体做法**
- 仅以下事件触发续租：
  - 成功工具调用
  - checkpoint 写入
  - 新增结构化输出项
- 纯文本思考/重复内容不续租。

**预期收益**
- 兼顾“避免误杀”与“避免僵尸任务”。

**实现复杂度**：中。

---

### P2-2. 统一可观测性与 SLO

**具体做法**
新增指标并打点：
- `timeout_rate_by_role/model`
- `empty_turn_rate`（tool_calls=0 且非终态）
- `partial_salvage_rate`
- `delegate_vs_direct_latency`
- `first_attempt_success_rate`

设 SLO（示例）：
- specialist timeout rate < 15%
- Promise Gate empty-turn 连续2次发生率 < 3%
- 任务首轮成功率 > 70%

**预期收益**
- 优化可量化，防止“修了但不知道是否有效”。

**实现复杂度**：中。

---

## 4. 建议落地顺序（Roadmap）

## Week 1（止血）
1. P0-1 Soft Timeout + Finalize
2. P0-2 Promise Gate 状态机 + trailing 保留
3. P0-3 Fail-fast Takeover

**验收门槛**：
- 超时任务有可用 partial 的比例 > 60%
- 空转超时案例下降 50%

## Week 2（提效）
4. P1-1 复杂度评分器（启发式版）
5. P1-2 自动分片器（Explore 先行）

**验收门槛**：
- docs 扫描场景 p95 时延下降 40%
- 同类任务委派失败率下降 30%

## Week 3-4（稳态）
6. P1-3 Checkpoint 恢复
7. P2-1 进展心跳
8. P2-2 SLO 与报表

---

## 5. 与本次案例逐条对照（你会看到什么变化）

- 20:44 Design #1 超时：会进入 finalize，至少留下结构化半成品。  
- 20:48 Design #2 Promise Gate：第二轮前被强拦截，不会连续空转到 10 分钟。  
- 20:58 Design #3 大上下文：评分器会建议拆分或由 orchestrator 直做。  
- 21:11 Explore 60s 超时：分片后局部失败不致全局失败。  
- 最终 40 分钟预计可压缩到 10~15 分钟，且产出稳定。

---

## 6. 风险与反例

1. **Soft timeout 可能被滥用**：任务一直在 finalize 循环。  
   - 对策：finalize 仅一次，超时后强制终止。
2. **过度接管导致 specialist 利用率下降**。  
   - 对策：按任务类型白名单，逐步放开。
3. **分片过细引入聚合成本**。  
   - 对策：限制并发度，按目录语义分片。

---

## 7. 最小可行改造清单（MVP）

只做 4 件事就能显著改善：

1. `soft_timeout + finalize`  
2. `promise_without_action` 硬拦截  
3. `首败即接管`  
4. `Explore 文件数阈值分片（>20 自动分片）`

> 这 4 项预计能解决本案 70% 以上的无效耗时。

---

## 8. 最终建议（决策层一句话）

把 Nimbus 从“模型驱动”升级为“执行控制驱动”：**先保证可恢复和止损，再追求模型质量**。当前首要投资应在调度与状态机，而不是继续盲调模型/timeout 参数。