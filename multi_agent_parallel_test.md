# Orchestrator 多 Subagent 并行测试方案

本方案旨在演示如何通过 Orchestrator 同时启动两个子 Agent（Explorer 和 Architect），并验证 Web UI 在多 Agent 并发任务下的渲染表现。

## 1. 测试目标
- **并行性验证**: 确保 Orchestrator 的 `spawn_batch` 或 `Scheduler` 能同时启动多个任务。
- **状态追踪**: 在 Web UI 中观察多个子任务的实时状态（Pending -> Running -> Success）。
- **资源隔离**: 验证两个 Agent 在不同的上下文（MMU）中运行，且产物能正确归集。

## 2. 环境准备
- **Nimbus 版本**: v0.2.0+ (支持并行分发)
- **依赖**: 确保 `AgentOS` 和 `Heart` 服务已启动。
- **模型配置**: 建议使用轻量级模型（如 `gpt-4o-mini` 或 `flash`）以降低延迟。

## 3. 测试用例设计

我们将创建一个名为 `MultiAgentParallelTest` 的演习脚本，模拟以下场景：
1. **Explorer Agent**: 负责扫描项目结构并列出关键文件。
2. **Architect Agent**: 负责读取特定文件的内容并提出改进建议。

### 3.1 测试脚本 (`tests/reproduction/parallel_ui_demo.py`)

```python
import asyncio
import os
from nimbus.agentos import AgentOS

async def run_parallel_demo():
    # 初始化环境
    os.environ["NIMBUS_WORKSPACE_ROOT"] = os.getcwd()
    agent_os = AgentOS()
    await agent_os.start()

    print("🚀 发起并行多 Agent 任务...")

    # 定义两个不同角色的子任务
    tasks = [
        {
            "id": "explorer_task",
            "goal": "扫描 ./src 目录并列出前 5 个最重要的 Python 文件，简述其职责。",
            "profile": "explorer",  # 假设系统中已定义此角色
            "llm_client": "flash"
        },
        {
            "id": "architect_task",
            "goal": "读取 README.md，分析其项目定位，并提出 3 条文档优化建议。",
            "profile": "architect",
            "llm_client": "flash"
        }
    ]

    # 通过 spawn_batch 启动并行执行
    # 注意：这会在 Web UI 中触发两个并列的任务卡片
    results = await agent_os.spawn_batch(
        tasks=tasks,
        timeout=60.0,
        strategy="wait_all"
    )

    print("\n✅ 任务执行完毕，结果汇总：")
    for res in results:
        print(f"Task ID: {res.task_id} | Status: {res.status}")

    await agent_os.stop()

if __name__ == "__main__":
    asyncio.run(run_parallel_demo())
```

## 4. Web UI 观察点

在运行上述脚本时，请打开浏览器访问 `http://localhost:3000`，重点关注以下 UI 表现：

| 观察维度 | 预期表现 |
| :--- | :--- |
| **任务面板 (Dashboard)** | 应该同时出现两个任务条目，分别标记为 `explorer_task` 和 `architect_task`。 |
| **实时日志 (Streaming)** | 两个任务的思考过程（Internal Monologue）应该交替或并行刷新，而不是串行。 |
| **进度条 (Progress)** | 两个任务各自拥有独立的进度指示，一个任务的阻塞不应影响另一个。 |
| **产物展示 (Artifacts)** | Explorer 生成的文件列表和 Architect 生成的建议文档应分别出现在各自的任务产物栏中。 |

## 5. 执行步骤
1. **启动服务**: 运行 `./nimbus start` 确保内核和 Web UI 正常运行。
2. **运行测试**: 执行 `python tests/reproduction/parallel_ui_demo.py`。
3. **观察 UI**: 切换到 Web UI 窗口，观察任务的并行创建和执行。
4. **验证产物**: 在任务完成后，检查 NimFS 中是否正确存储了两个 Agent 的输出结果。

## 6. 异常处理与降级
- **冲突检测**: 若两个 Agent 尝试同时写入同一个文件，观察 NimFS 的 `RWLock` 是否能正确排队（UI 上表现为其中一个任务短暂 Wait）。
- **超时处理**: 若其中一个任务超时，验证 UI 是否能正确显示“Partial Result”（部分抢救的结果）。
