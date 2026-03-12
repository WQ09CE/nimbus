# 状态与上下文管理 (State & Context Management)

## 1. 核心挑战

在基于 LLM 的长周期复杂任务中，如何维持长时间的上下文连贯性和任务焦点是一个艰巨的挑战。由于上下文窗口（Token Limit）的限制和模型记忆能力的衰减，如果所有思考、日志、代码片段都塞入一个 Prompt 对话流中，会导致：
1. **幻觉与指令遗忘 (Hallucination & Forgetting)**：模型容易忘记最初的目标或重复执行同一操作。
2. **上下文污染 (Context Pollution)**：长时间的重试和错误日志会淹没关键线索。
3. **状态丢失 (State Loss)**：系统崩溃或意外中断后，从头开始代价高昂。

Nimbus 提出了一套基于**文件系统持久化**的上下文管理机制：**Scratchpad (草稿本)**。

## 2. Scratchpad 机制设计

### 2.1 核心理念
Scratchpad 是一个独立于对话流的物理文件（例如 `.nimbus/sessions/sess_xxx/scratchpad.md`）。它通过标准的文件工具（Read/Write/Edit）由模型主动维护。

系统强制 Orchestrator 和 Sub-Agents 将**当前目标、TODO 列表、发现、执行结果以及错误日志**外置到 Scratchpad 中。这是一种典型的“**思考外化 (Externalized Thinking)**”和“**状态解耦**”策略。

### 2.2 Orchestrator Scratchpad 的生命周期
1. **初始化 (Initialization)**：Orchestrator 接收任务后，第一步**必须**在 Scratchpad 中创建一个初始的 TODO 列表。
2. **状态流转 (State Transition)**：
   - 每完成一个子任务（或拉起多个并发 Sub-Agent 前后），Orchestrator 必须调用 `Write` 或 `Edit` 更新 Scratchpad。
   - 记录新发现的线索、测试结果以及需要调整的计划。
3. **上下文刷新与恢复 (Context Refresh & Recovery)**：
   - 当对话轮次过长、触发截断或系统重启时，Orchestrator 可以通过单个 `Read` 工具读取 Scratchpad，迅速找回之前的执行状态和未完成的任务。
   - 这赋予了 Nimbus 强大的**断点续传能力 (Resumability)**。

### 2.3 Sub-Agent Scratchpad
每个并发的 Sub-Agent 都有自己**专属**的 Scratchpad。
- 这不仅防止了并发写入的冲突，也实现了任务状态的隔离。
- 当 Sub-Agent 由于死循环、超时或大段报错导致任务失败时，它的 Scratchpad 成为了**黑匣子记录仪**。Orchestrator 可以读取该日志，分析失败原因并接手剩余进度，而不是盲目重启任务。

## 3. 防护与截断机制 (Protection & Truncation)
长文本的处理不仅仅依靠外置存储，还需要防范输入暴涨。Nimbus 在工具链层面集成了强有力的保护措施：
- **大文本读取截断**：通过 `Read` 工具的 `offset` 和 `limit` 参数分片读取超大文件。
- **命令行输出限制**：`Bash` 工具的输出被硬性截断（如限制最后 2000 行或 50KB）。如果发生截断，完整输出会被保存到临时文件中，模型可以通过提示获得路径，按需分片检视，从而避免单次执行日志冲爆 LLM 的上下文。
