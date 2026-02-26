# Web UI Architecture: DispatchCard & Client-Side Rendering

## 1. DispatchCard 渲染逻辑

`DispatchCard` 是 Nimbus 前端最核心的组件，用于展示 Orchestrator 对子任务的并行调度 (Parallel Dispatch)。

### 状态机 (State Management)
组件内部维护了一个响应式的状态机：
- **`running`**: 子任务正在执行，显示加载动画和实时思考日志。
- **`completed`**: 子任务已成功调用 `SubmitResult` 并返回，显示结果摘要和统计信息。
- **`failed`**: 子任务异常退出或超时。

### 子任务槽位 (Slots) 管理
`DispatchCard` 通过管理多个并发执行的子任务槽位来支持并行。
- **动态渲染**: 根据后端推送的 `sub_tool_events` 实时创建或更新对应的任务槽 (Slot)。
- **解析逻辑**: 每个槽位负责解析其对应的 `sub_tool_events`：
  - **`thought:`**: 提取为内部思考日志，并应用过滤逻辑。
  - **`call:`**: 展示当前调用的工具名 (例如 `Read`, `Write`) 及其参数。
  - **`done:`**: 标记槽位完成，并存储返回的 `result` 内容。

## 2. UI & UX 优化细节

为了提升 Agent 协同的透明度和可读性，前端引入了多项细粒度优化：

- **视觉装饰**: 在任务槽位的左侧增加了**渐变色装饰条 (Gradient Accent Bars)**，用于区分不同类型的 Specialist (例如 Explorer 使用蓝色，Implementer 使用紫色)。
- **Summary 高度控制**: 对于任务执行结果的 Summary 部分，应用了 `line-clamp` 及最大高度限制，防止超长输出撑爆页面布局。
- **Thought 日志过滤**: 
  - **去噪**: 自动过滤掉后端冗余的内部状态日志。
  - **聚合**: 连续的思考内容会被聚合显示，减少布局跳动。

## 3. 会话恢复 (Rehydration)

`chat-store.ts` 负责整个前端状态的持久化与恢复：

- **序列化**: 每当有新的 `Message` 或 `Event` 到达时，自动同步至浏览器的 IndexedDB。
- **Rehydration 策略**:
  - 用户刷新页面后，`chat-store` 会重新加载历史消息。
  - **状态重置**: 恢复历史消息时，原本处于 `running` 状态的 `DispatchCard` 会被标记为 `interrupted` 或 `stale`，除非后端能提供当前的运行快照。
  - **资源映射**: 自动恢复与 `nimfs://` 相关的 Artifact 预览和下载链接。
