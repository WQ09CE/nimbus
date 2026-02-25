# 嵌套并行 (Nested Parallelism) 评估报告：`orchestrator_parallel_dispatch.md`

## 1. 评估背景
本报告旨在分析 `orchestrator_parallel_dispatch.md` 设计文档中定义的 `ParallelDispatch` 工具对“嵌套并行”（子智能体再次发起并行任务）的支持程度，并识别潜在的架构风险。

## 2. 嵌套并行支持度分析

### 2.1 设计现状
根据当前文档，`ParallelDispatch` 主要定义为 **Orchestrator** 使用的工具。
- **调用者限制**：文档提到“编排者通过此工具同时召唤多个 Specialist”，并未明确 Specialist 是否也能持有或调用此工具。
- **任务定义**：`tasks` 数组中的项包含 `specialist` 类型（Explorer, Implementer, Tester, Architect）和 `task` 描述。

### 2.2 技术可行性（支持程度：中等）
从底层 `AgentOS.spawn_batch` 的能力来看，嵌套并行在技术上是可行的，因为每个 Specialist 也是一个独立的 `Process`。
- **理论路径**：如果将 `ParallelDispatch` 工具也注入到 Specialist（如 Architect 或 Implementer）的工具集中，它们理论上可以发起二层甚至多层并行。
- **文档缺失**：当前文档**缺乏**对递归调用、深度限制或跨层上下文传递的描述。

---

## 3. 潜在风险评估

### 3.1 令牌/成本爆炸 (Token & Cost Explosion)
嵌套并行会导致调用量呈指数级增长。
- **风险描述**：Orchestrator 发起 4 个并行任务，若每个 Specialist 又发起 4 个并行任务，单次决策将瞬间产生 20+ 个 LLM 调用。
- **严重程度**：高

### 3.2 上下文膨胀与一致性 (Context Bloating)
- **风险描述**：子任务的输出需要逐层向上汇总。如果嵌套过深，Orchestrator 最终接收到的聚合结果（`ParallelDispatch` 返回的对象）可能超出其上下文窗口限制。
- **状态同步**：多个层级的并行任务同时修改同一个工作空间（如文件系统）时，缺乏加锁或冲突解决机制。

### 3.3 递归死循环 (Recursive Deadlock/Loop)
- **风险描述**：如果专家 A 派发任务给专家 B，专家 B 又因逻辑判断派发回专家 A（或自身），在没有深度限制的情况下会导致系统资源耗尽。
- **严重程度**：中

### 3.4 调试与可观测性挑战 (Observability)
- **风险描述**：UI/UX 章节（第 4 节）仅描述了单层并行卡片。对于嵌套并行，层叠的进度条和流式输出会导致 UI 极度混乱，用户难以追踪逻辑链条。

---

## 4. 改进建议

### 4.1 引入深度限制 (Max Depth Guard)
建议在 `AgentOS` 层级或工具定义中增加 `depth` 参数。
- **提议**：`ParallelDispatch` 默认只允许 Orchestrator 调用（Depth 0）。若允许专家调用，需强制限制 `max_depth <= 2`。

### 4.2 上下文裁剪策略 (Context Pruning)
- **建议**：在嵌套返回时，子任务必须通过 `Summary` 机制压缩输出，而不是返回全量 `internal_monologue`。

### 4.3 显式权限管控 (Role-based Tool Access)
- **建议**：明确定义哪些专家有权使用 `ParallelDispatch`。通常建议仅 `Architect` 在进行大规模模块拆解时有权使用，而 `Implementer` 或 `Explorer` 应保持线性执行以确保操作原子性。

### 4.4 资源配额 (Resource Quota)
- **建议**：为单次顶层任务设置“总进程配额”，例如一个 Orchestrator 及其所有子孙节点总共不能开启超过 10 个并行进程。

## 5. 结论
当前 `orchestrator_parallel_dispatch.md` **尚未对嵌套并行做出显式设计支持**。虽然底层架构可能允许，但直接开放会导致严重的成本和一致性风险。建议在文档中增加“嵌套约束”章节，明确限制调用深度和资源配额。
