# Nimbus Parallel Subagent & Graceful Scavenging Design

## 1. 核心目标
将 Nimbus 从顺序执行升级为支持并行 Subagent 调度，并引入“超时抢救”机制，确保在任务超时或中断时能够最大程度保留中间产物。

## 2. 并行分发机制 (Parallel Dispatch)
### 2.1 `spawn_batch` 接口
在 `AgentOS` 中新增 `spawn_batch` 方法，允许 Brain 一次性提交多个任务。
- **输入**: `List[SubagentTask]`
- **行为**: 
  - 为每个任务创建一个独立的 `Process`。
  - 将这些进程 ID 关联到一个 `BatchGroupID`。
  - 立即返回一个 `BatchPromise` 对象，而不阻塞 Brain 的后续思考。

### 2.2 Brain 指令扩展
扩展 Brain 的 Action 空间，支持 `ParallelAction`：
```json
{
  "action": "parallel_run",
  "tasks": [
    {"task": "分析代码 A", "model": "flash"},
    {"task": "分析代码 B", "model": "flash"}
  ],
  "aggregation": "wait_all"
}
```

## 3. 异步聚合器 (Aggregator)
设计一个 `ResultAggregator` 逻辑，支持多种等待策略：
- **Wait All**: 等待所有 Subagent 完成。
- **Wait Threshold**: 只要有 60% 的任务完成即向 Brain 汇报，其余转入后台。
- **Race/Any**: 只要第一个结果出来就返回（适用于多模型竞争同一任务）。

## 4. 超时抢救机制 (Graceful Scavenging)
当 Subagent 触发 `timeout` 时，系统不再直接丢弃进程，而是执行以下“抢救”步骤：

### 4.1 `scavenge_last_thought()`
在强杀 `VCPU` 之前，`AgentOS` 会快照当前进程的内存帧：
- **Internal Monologue**: 提取最后一段“内心独白”，了解模型死掉前在想什么。
- **Partial Artifacts**: 提取已生成但未提交的临时文件、代码片段或搜索结果。

### 4.2 `PartialResult` 数据结构
```python
@dataclass
class PartialResult:
    session_id: str
    is_partial: bool = True
    recovered_content: str  # 抢救出的最后文本
    artifacts: List[Dict]   # 抢救出的中间产物
    reason: str = "timeout" # 中断原因
```

## 5. 并发锁优化 (NimFS RWLock)
- **共享读**: 所有并行的 Subagent 默认持有 NimFS 的 `ReadLock`。
- **写锁排队**: 当多个 Subagent 尝试写入时，Heart 的 `MemoryManager` 会进行排队调度。
- **冲突退让**: 如果 Brain 突然被用户唤醒，所有并行 Subagent 的写操作立即挂起，优先保证用户交互。

## 6. UI/UX 反馈
- **多进度条**: 在前端展示一个“任务组”卡片，每个子任务有独立的进度条。
- **状态标记**: 
  - ✅ 完成
  - ⚠️ 部分回收 (Partial)
  - ❌ 失败
- **交互**: 用户可以点击“部分回收”的结果，查看 Agent 在超时前已经做到了哪一步。
