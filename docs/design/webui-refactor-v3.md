# Nimbus Web-UI 重构设计方案 (v3)

**日期**: 2024-03-21  
**状态**: 草案 / 评审中  
**设计者**: Architect Agent

---

## 1. 执行摘要 (Executive Summary)

### 1.1 目标
本次重构的核心目标是提升 Nimbus Web-UI 的**简洁性 (Simplicity)**、**效率 (Efficiency)** 和**可扩展性 (Extensibility)**。通过解决当前前端逻辑中存在的“内容推测”问题，建立一个由状态机驱动的、标准化的前后端通信机制。

### 1.2 核心改进点
- **通信协议**: 从碎片化的消息解析转向标准化的 JSON SSE 流。
- **状态管理**: 前端 `ChatStore` 不再通过正则表达式或字符串匹配来猜测 Agent 行为，而是严格遵循后端 FSM 状态。
- **产物处理**: 深度集成 NimFS Artifacts，提供独立的侧边栏渲染与沙箱预览功能。

---

## 2. SSE 协议标准化 (SSE Protocol Standardization)

定义统一的 JSON 结构，确保前端能够精确解析每一帧增量数据。

### 2.1 事件结构 (Standardized Event Structure)

```json
{
  "fsm_state": "THINKING | ACTING | STREAMING | IDLE",
  "event_id": "uuid-v4",
  "content": "增量文本内容...",
  "tool_call": {
    "name": "read_file",
    "arguments": "{\"path\": \"...\"}",
    "call_id": "call_123"
  },
  "artifact_ref": {
    "ref": "nimfs://artifact/abc-123",
    "type": "code | markdown | diff",
    "summary": "简要描述"
  },
  "metadata": {
    "usage": { "tokens": 120 },
    "latency": "150ms"
  }
}
```

### 2.2 状态映射
- **THINKING**: 对应 Agent 的内省/思考阶段（如 `<thought>` 标签内容）。
- **ACTING**: 工具调用执行中。
- **STREAMING**: 正向用户输出回复。
- **IDLE**: 会话空闲，等待输入。

---

## 3. ChatStore 逻辑简化 (ChatStore Logic Simplification)

从“内容猜测”转向“状态驱动”的 UI 更新逻辑。

### 3.1 现状与痛点
目前前端依赖 `message.content` 的内容来判断是否显示加载动画、工具卡片或代码块。这种耦合导致后端格式微调时前端频繁崩溃。

### 3.2 驱动模型重构
- **响应式映射**: `ChatStore` 维护一个 `currentSessionState`。
- **UI 组件映射表**:
  - `state == 'THINKING'` -> 渲染 `ThoughtBubble` 组件。
  - `state == 'ACTING'` -> 渲染 `ToolExecutionProgress` 组件。
  - `artifact_ref != null` -> 触发侧边栏 `ArtifactViewer` 更新。

---

## 4. Artifact 集成设计 (Artifact Integration)

针对 NimFS 产生的持久化产物进行专门设计。

### 4.1 侧边栏预览 (Side-panel)
- 当 SSE 包含 `artifact_ref` 时，前端自动在右侧开启侧边栏。
- 支持按类型渲染：
  - **Code**: 集成 Monaco Editor 进行语法高亮。
  - **Markdown**: 即时渲染文档。
  - **Diff**: 友好的代码差异对比视图。

### 4.2 沙箱化逻辑
- 产物内容通过 `NimFSReadArtifact` 接口异步获取，不随主消息流传输，减小首屏压力。
- 引入缓存机制，避免重复拉取大体积 Artifact。

---

## 5. 实施路线图 (Implementation Roadmap)

| 阶段 | 目标 | 关键任务 |
| :--- | :--- | :--- |
| **Phase 1: Foundation** | 协议对齐 | 后端切换为标准 JSON SSE 格式；前端实现基础解析器。 |
| **Phase 2: Refactor** | 逻辑迁移 | 重构 `ChatStore`，移除旧的正则匹配逻辑，建立状态订阅机制。 |
| **Phase 3: Features** | 产物增强 | 实现侧边栏组件与 NimFS Artifact 渲染逻辑。 |
| **Phase 4: Optimization** | 性能优化 | 引入虚拟列表处理超长会话，优化大文本渲染。 |

---

## 6. 结论
通过本次重构，Nimbus Web-UI 将从一个简单的聊天界面演变为一个真正的“代理协作工作空间”，为用户提供更清晰的执行反馈和更专业的产物管理能力。
