# 评估报告：运行中注入多模态数据对 vCPU 内存压力的影响

## 1. 背景概述
Nimbus 架构将 LLM 视为中央处理器（vCPU），并通过 MMU（内存管理单元）管理上下文。当前系统支持在执行循环（Think-Act-Observe）中动态注入多模态数据（如图片、长文本 Artifact 引用）。本报告旨在评估这种“运行中注入”模式对 vCPU 内存（即 LLM 上下文窗口）造成的压力及其潜在风险。

## 2. 核心架构与内存模型分析

### 2.1 Anchor & Stream 机制
- **Anchor (锚点)**: 存储系统规则、工作空间信息、能力描述。这部分在上下文窗口中是**永久固定**且置顶的。
- **Stream (执行流)**: 存储消息历史（Messages），采用栈帧（StackFrame）管理。
- **注入点**: 多模态数据主要通过 `Message` 对象进入 Stream。

### 2.2 内存压力计算因子 (Token Estimation)
根据 `src/nimbus/core/memory/context.py` 的定义：
- **图片压力**: 每张图片固定估算为 **1500 tokens**（`IMAGE_TOKEN_ESTIMATE`）。
- **文本压力**: 
    - 英文: ~4 字符/token
    - 中文: ~1.5 字符/token
    - 代码: ~3 字符/token
- **结构开销**: 每条消息有 4 tokens 固定开销，工具调用有 10 tokens 开销。

## 3. 内存压力风险评估

### 3.1 瞬时压力风险 (Peak Pressure)
当 Agent 调用 `Read` 或 `NimFSReadArtifact` 获取大量数据时，系统会尝试将内容直接注入 Stream。
- **风险点**: 
    - 如果读取一个 100KB 的纯文本文件（约 30k-40k tokens），会瞬间吃掉 context 窗口的很大比例。
    - 连续的多模态注入（如多张高分辨率图片）可能导致 context 迅速耗尽。

### 3.2 长期压力与压缩失效 (Context Drift)
Nimbus 拥有 `Compaction`（压缩）机制，但多模态数据的注入会带来以下挑战：
- **摘要损失**: 压缩时会将旧消息总结为摘要（Summary）。对于图片或结构化 Artifact，摘要可能丢失关键细节，导致 Agent 在后续循环中“失忆”。
- **压缩频率**: 高 token 消耗的多模态数据会频繁触发 `threshold_ratio`（默认 0.85），导致系统频繁进入 Compaction 流程，增加 LLM 开销和延迟。

### 3.3 注入路径的确定性 (State Managament)
- `StateManager` 追踪“客观现实”（文件修改、命令状态），这部分是确定性的且占用空间较小。
- 风险在于，注入的多模态数据（Observation）是**非结构化**的。如果 Observation 过大，MMU 的 `Smart Drop` 策略可能会丢弃掉重要的前置 Tool Calls，导致 vCPU 逻辑链条断裂。

## 4. 关键指标评估 (Estimated)

| 数据类型 | 典型大小/数量 | Token 占用 (估计) | 对 128k 窗口占比 |
| :--- | :--- | :--- | :--- |
| 系统 Anchor | 2000 字符 | ~1000 | < 1% |
| 单张图片 | 1 张 | 1500 | ~1.2% |
| 代码 Artifact | 500 行 (15KB) | ~5000 | ~4% |
| 外部文档注入 | 50KB | ~15000 - 20000 | ~15% |
| 10 轮对话历史 | 10 pairs | ~8000 (不含注入) | ~6% |

**结论**: 单次注入压力尚可控，但**累积注入**和**超大文本注入**是主要威胁。

## 5. 设计改进建议

1.  **分级注入策略 (Tiered Injection)**:
    - 对于超大 Artifact，不直接注入全量文本，而是注入“采样片段”+“元数据”，并提供辅助工具让 Agent 按需检索。
2.  **多模态感知压缩 (Multimodal-Aware Compaction)**:
    - 在压缩时，对图片等非文本数据保留其 OCR 结果或关键特征描述，而非简单的“Image discarded”。
3.  **动态 Token 预算管理 (Dynamic Quota)**:
    - 为 `Observation` 设置单次注入上限。在 `context.py` 中已实现 `token_estimate_view(max_tool_chars=10000)`，应强制执行此截断，防止单次工具输出撑爆内存。
4.  **显存级换入换出 (NimFS Paging)**:
    - 利用 `NimFS` 存储原始多模态数据，context 中仅保留 `nimfs://` 引用。只有当 vCPU 显式需要“看”某个引用时，才通过临时窗口载入。

## 6. 总结
当前“运行中注入”架构在灵活性上有显著优势，但在应对大规模多模态数据时存在 **Context Window 暴涨** 和 **压缩后细节丢失** 的风险。建议通过强化 `MMU` 的截断机制和引入“按需加载”模式来缓解 vCPU 内存压力。
