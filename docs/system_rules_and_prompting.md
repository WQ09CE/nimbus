# 提示词与角色约束 (System Rules & Prompting)

## 1. 现状概述

在基于 LLM 的 Agent 框架中，提示词（Prompting）不仅仅是激发模型能力的手段，更是构建多智能体系统边界和规则的核心。Nimbus 的 System Rules 被设计为一种**强约束机制**，用以划分 Agent 角色、规范行为模式并保障系统的稳定运行。

## 2. 角色职能隔离 (Role Separation)

Nimbus 严格区分 Orchestrator（协调者）和 Worker/Reader（执行者）的 System Rules，确保各自专注于特定领域：

### 2.1 Orchestrator 规则
- **定位**：最高层的任务拆解和状态管理器。
- **约束**：
  - **强制计划 (Plan First)**：必须在 scratchpad 中编写和更新 TODO 列表，再进行复杂操作。
  - **高频更新 (Update Frequently)**：每完成一个子任务，必须将发现、错误和打钩的 TODO 写入 scratchpad。
  - **协作优先 (Agent Collaboration)**：偏向于并行派发 (`spawn_agent`) 子任务，除非是极简单的验证（如单次 `Read` 或 `Bash`），否则严禁亲自动手修改系统。
  - **状态恢复 (State Recovery)**：失去进度或子节点超时，必须 `Read` scratchpad 恢复。

### 2.2 Sub-Agent (Worker/Reader) 规则
- **定位**：专注解决单一目标的执行单元。
- **约束**：
  - **目标专注**：严格按照 Orchestrator 分配的 Goal 运行，忽略不相关的上下文。
  - **无嵌套 (No Nesting)**：严禁使用 `spawn_agent` 拉起新的子节点，防止无限递归。
  - **角色能力**：
    - `Reader`: 仅分析、阅读代码和日志（`Read`, `Grep`）。
    - `Worker`: 负责精确执行修改或测试（`Write`, `Edit`, `Bash`）。

## 3. 并发派发规则 (Parallel Spawning Rules)

Orchestrator 在派发任务时受到严格的 System Rules 约束：
1. **默认并行 (Parallel by default)**：独立的子任务必须在同一个 function call 块中被派发。
2. **角色匹配 (Right Role)**：必须根据任务类型选择正确的角色（`reader` 用于分析，`worker` 用于变动）。
3. **丰富上下文 (Rich Context)**：每次 `spawn_agent` 必须包含明确的 Goal、相关的代码文件路径和期望输出的格式。
4. **事后验证 (Verify After Delegate)**：Worker 任务完成后，Orchestrator 必须验证结果（如执行 `Read` 或跑测试脚本）。

## 4. 强约束落地策略 (Enforcement Strategy)

1. **System Rules 前置**：规则作为 LLM 的 System Message 置于对话的最顶层，拥有最高的注意力权重。
2. **格式化输出**：强制模型遵循特定的工具调用格式（例如 JSON 或特定的标记）。
3. **Scratchpad 校验机制**：Orchestrator 对自身和子 Agent 的 scratchpad 拥有最终解释权，它是状态流转和规则执行的实体证据。
