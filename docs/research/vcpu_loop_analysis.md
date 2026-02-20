# VCPU 死循环问题分析与优化方案

## 1. 问题背景 (Problem Background)
在 Nimbus 框架的运行过程中，观察到当 LLM (大语言模型) 产生纯文本回复（如问候、解释或不需要工具调用的回答）时，系统往往会陷入死循环或强制要求工具调用。即使用户的问题已经得到了逻辑上的回答，VCPU 仍会尝试继续执行，直到触发 `max_consecutive_thoughts` 限制。

## 2. 根源分析 (Root Cause Analysis)
经过对 `InstructionDecoder` 和 `VCPU` 核心逻辑的审计，发现以下核心问题：

### 2.1 语义映射模糊
`InstructionDecoder` 将所有不包含工具调用的 LLM 输出统一映射为 `THOUGHT` 模式。在 VCPU 的执行逻辑中，`THOUGHT` 被视为任务处理的中转状态，而非终点。

### 2.2 循环阈值过高
默认配置中的 `max_consecutive_thoughts` 设置通常大于 1。这意味着当 LLM 产生一个“想法”（纯文本）后，VCPU 会认为任务尚未结束，并再次请求 LLM 进行下一步行动。如果 LLM 认为自己已经说完话了，它可能会重复之前的内容或陷入无意义的循环。

### 2.3 缺乏对话出口 (Exit Strategy)
系统目前的架构强依赖于工具调用（Tool Use）作为进度标志。对于“纯对话”类型的交互，缺乏一种自动将 `THOUGHT` 提升为 `RETURN` (或 `FINAL_ANSWER`) 的机制。

## 3. 评审建议与优化路径 (Recommendations)

### 3.1 立即修复（配置层面）
- **按需调整阈值**：将 `VCPUConfig.max_consecutive_thoughts` 设置为 **1**。
- **适用场景**：主要用于对话密集型（Chat-heavy）任务。对于需要复杂推理（CoT）的任务，建议保持为 2 并配合显式协议。

### 3.2 中期改进（通信协议）
- **引入显式终端协议 (Explicit Termination Protocol)**：
    - 在 System Prompt 中要求 LLM 在回答完毕且无需进一步行动时，必须以 `[DONE]` 结尾或调用 `task_complete` 工具。
- **确定性语义出口**：Decoder 优先识别这些显式标记，将其映射为 `ActionKind.RETURN`，从而 100% 避免 VCPU 的误判。

### 3.3 长期优化（架构层面）
- **区分内部思考与外部对话**：
    - 引入 `<thought>` 标签机制。Decoder 将标签内的内容识别为 `THOUGHT`（允许循环），将标签外的内容自动识别为 `RETURN`（直接返回用户）。
- **智能探测器**：在 `InstructionDecoder` 中增加轻量级的语义检查，识别无后续行动意向的陈述句。

## 4. 结论
单纯降低阈值是缓解症状的“止痛药”，而引入**显式终端协议**才是根治死循环、提升系统确定性的“手术方案”。建议在后续版本中优先实现 `task_complete` 工具的自动注入。
