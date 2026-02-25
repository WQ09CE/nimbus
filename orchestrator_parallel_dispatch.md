# Orchestrator Parallel Dispatch Design

## 1. 核心目标
使 Orchestrator（编排者）能够直接通过单次工具调用发起多个并行的专家任务，充分利用 `AgentOS.spawn_batch` 的底层能力，提升复杂任务的执行效率。

## 2. 工具定义 (Tool Schema)

### `ParallelDispatch`
编排者通过此工具同时召唤多个 Specialist 智能体。

**参数定义 (JSON Schema):**
```json
{
  "name": "ParallelDispatch",
  "description": "同时派发多个并行的专家任务。适用于需要多维度分析、多模块重构或并行测试的场景。",
  "parameters": {
    "type": "object",
    "properties": {
      "tasks": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "specialist": { "enum": ["Explorer", "Implementer", "Tester", "Architect"], "description": "专家类型" },
            "task": { "type": "string", "description": "具体的子任务描述" },
            "model": { "type": "string", "description": "指定模型别名 (如 sonnet, flash)" },
            "context": { "type": "string", "description": "该子任务所需的特定上下文" }
          },
          "required": ["specialist", "task"]
        }
      },
      "strategy": {
        "enum": ["wait_all", "wait_any", "wait_threshold"],
        "default": "wait_all",
        "description": "并行聚合策略"
      },
      "threshold": { "type": "number", "description": "当策略为 wait_threshold 时的完成比例 (0.0-1.0)" }
    },
    "required": ["tasks"]
  }
}
```

## 3. 内部映射逻辑

### 3.1 专家到进程的转换
当 `ParallelDispatch` 被调用时，Orchestrator 内部逻辑会将其转换为 `AgentOS.spawn_batch` 所需的格式：
- **Explorer** -> 映射为加载了 `ExplorerInstruction` 的 `Process`。
- **Implementer** -> 映射为具备读写权限的 `Process`。
- **Tester** -> 映射为受限的 Bash 执行环境。

### 3.2 结果聚合 (Response Handling)
`ParallelDispatch` 返回一个聚合后的结果对象：
```json
{
  "status": "COMPLETED",
  "results": [
    { "id": "task_1", "specialist": "Explorer", "output": "分析报告...", "is_partial": false },
    { "id": "task_2", "specialist": "Implementer", "output": "重构完成...", "is_partial": true }
  ],
  "summary": "2个任务已完成，其中1个为部分回收。"
}
```

## 4. UI/UX 展现
- **并行卡片**：在 Chat 界面显示一个 `Parallel Operation` 容器。
- **动态进度条**：容器内为每个子任务展示独立的 `Specialist Badge` 和 `Progress Bar`。
- **即时流式输出**：支持多个子任务的 `internal_monologue` 同时在各自的子区域内流式展示。

## 5. 错误处理与抢救
- 继承 `AgentOS` 的 **Graceful Scavenging** 机制。
- 如果其中一个子任务失败或超时，`ParallelDispatch` 会在 `results` 数组中标记该项为 `is_partial: true`，并附带抢救出的中间结论，确保 Orchestrator 不会因为局部失败而中断全局决策。
