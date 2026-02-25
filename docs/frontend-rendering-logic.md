# Nimbus Web UI 前端显示逻辑整理

## 1. 架构总览

Nimbus Web UI 基于 **Next.js App Router** + **Zustand** 状态管理 + **SSE 流式通信** 构建。所有前端代码位于 `web-ui/src/` 下。

```
┌─────────────────────────────────────────────────────────────────┐
│                        page.tsx (主页面)                         │
│  ┌──────────┐  ┌──────────────────────────┐  ┌───────────────┐ │
│  │SessionPanel│  │       ChatList           │  │ FileExplorer  │ │
│  │(左侧面板) │  │  ┌────────────────────┐  │  │(右侧文件浏览)│ │
│  │           │  │  │HistoricalRow (虚拟化)│  │  │               │ │
│  │           │  │  │  ├─ user msg        │  │  │               │ │
│  │           │  │  │  ├─ agent_group     │  │  │               │ │
│  │           │  │  │  │  ├─AgentProcess  │  │  │               │ │
│  │           │  │  │  │  └─ChatMessage   │  │  │               │ │
│  │           │  │  │  └─ system msg      │  │  │               │ │
│  │           │  │  ├────────────────────┤  │  │               │ │
│  │           │  │  │StreamingTail (实时) │  │  │               │ │
│  │           │  │  ├────────────────────┤  │  │               │ │
│  │           │  │  │InjectionTail       │  │  │               │ │
│  │           │  │  └────────────────────┘  │  │               │ │
│  │           │  │  ┌────────────────────┐  │  │               │ │
│  │           │  │  │    ChatInput        │  │  │               │ │
│  │           │  │  └────────────────────┘  │  │               │ │
│  └──────────┘  └──────────────────────────┘  └───────────────┘ │
│                                                                 │
│                    SpecialistDrawer (覆盖式右侧抽屉)              │
└─────────────────────────────────────────────────────────────────┘
```

### 文件结构

```
web-ui/src/
├── app/
│   ├── page.tsx              # 主页面，控制全局面板状态 (SessionPanel/FileExplorer)
│   ├── globals.css
│   └── layout.tsx
├── components/
│   ├── chat/
│   │   ├── ChatList.tsx      # 消息列表（虚拟滚动 + 分组 + 尾部隔离）
│   │   ├── ChatMessage.tsx   # 单条消息渲染（用户/助手/系统 + 工具展平）
│   │   ├── AgentProcess.tsx  # Agent 过程步骤渲染（时间轴 + 气泡）
│   │   ├── ChatInput.tsx     # 输入框
│   │   ├── MarkdownRenderer.tsx  # Markdown 渲染
│   │   ├── StreamingScroller.tsx # 流式滚动控制
│   │   ├── WorkingIndicator.tsx  # 工作状态指示器
│   │   └── tools/
│   │       ├── ToolCard.tsx        # 工具卡片主入口（元工具 vs 普通工具 分流）
│   │       ├── ToolDisplay.tsx     # 二级路由（按工具名分发具体渲染组件）
│   │       ├── DispatchCard.tsx    # 元工具/专家任务卡片（带主题色）
│   │       ├── SpecialistDrawer.tsx # 右侧详情抽屉面板
│   │       ├── FileRead.tsx        # Read 工具结果渲染
│   │       ├── FileDiff.tsx        # Write/Edit 工具 Diff 渲染
│   │       ├── Bash.tsx            # Bash 工具终端渲染
│   │       └── DefaultTool.tsx     # 兜底默认渲染
│   ├── session/              # 会话管理面板
│   └── debug/                # 调试面板
├── stores/
│   └── chat-store.ts         # Zustand 全局状态（消息、流式、工具调用）
├── lib/api/
│   └── chat.ts               # 后端 API + SSE 事件类型定义
└── types/
    └── index.ts              # TypeScript 核心类型
```

---

## 2. 核心类型系统

```typescript
// 消息
interface Message {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  toolCalls?: ToolCall[]
  toolResults?: ToolResult[]
  timestamp: number
  attachments?: ChatAttachment[]
}

// 工具调用
interface ToolCall {
  id?: string
  name: string
  arguments: Record<string, unknown>
  agentType?: "core" | "dispatch"   // 标识是否为子 Agent 调度
  subCalls?: ToolCall[]              // 嵌套子工具调用
  subResults?: ToolResult[]          // 嵌套子工具结果
}

// 工具结果
interface ToolResult {
  id?: string
  name: string
  result: unknown
  error?: string
  duration?: number                  // 执行耗时 (ms)
}

// 用于 UI 展示的扩展类型
interface SubCallWithStatus extends ToolCall, ToolResult {
  status: "running" | "completed" | "failed"
}
```

---

## 3. 数据流：从 SSE 到渲染

```
后端 /api/chat (SSE Stream)
        │
        ▼
   chat-store.ts (Zustand)
   ┌──────────────────────────────────────────┐
   │  SSE Event Handler:                      │
   │                                          │
   │  "text"           → streamingContent     │
   │  "tool_call"      → streamingToolCalls   │
   │  "sub_tool_call"  → 路由到父节点的        │
   │                     subCalls (via         │
   │                     parent_action_id)     │
   │  "tool_result"    → streamingToolResults  │
   │  "sub_tool_result"→ 路由到父节点的        │
   │                     subResults            │
   │  "heartbeat"      → thinkingIteration    │
   │  "activity"       → currentActivity      │
   │  "done"           → 聚合为完整 Message    │
   │                     推入 messages[]       │
   └──────────────────────────────────────────┘
        │
        ▼
   ChatList.tsx (消费 messages + streaming 状态)
        │
        ├─ 历史消息 → HistoricalRow (虚拟化，memo 隔离)
        ├─ 流式尾部 → StreamingTail (直接订阅 store，高频更新)
        └─ 注入尾部 → InjectionTail (context injection 消息)
```

### 关键设计：流式尾部隔离

**问题**：流式输出时每个字符变化都会触发 re-render，如果历史消息也参与重绘，性能极差。

**方案**：
- `HistoricalRow`：只渲染已完成的消息，用 `React.memo` 包裹，数据不变就不重绘
- `StreamingTail`：独立于虚拟列表，直接订阅 store 中的 `streamingContent` / `streamingToolCalls`
- 流式结束后，聚合为完整 Message 推入 `messages[]`，从 StreamingTail 迁移到 HistoricalRow

---

## 4. 消息分组策略（ChatList 核心逻辑）

ChatList 将原始 messages 数组转换为 `VirtualItem[]`，有 3 种类型：

| 类型 | 触发条件 | 渲染组件 |
|------|---------|---------|
| `user` | `role === "user"` | `ChatMessage` (蓝色气泡) |
| `system` | `role === "system"` | `ChatMessage` (居中 Pill) |
| `agent_group` | 连续的 `role === "assistant"` | `AgentProcess` + `ChatMessage` |

### agent_group 的内部拆分

一个 `agent_group` 包含连续的多条 assistant 消息，内部进一步拆分为：

```
agent_group
├── processSteps[]     → AgentProcess 渲染（时间轴）
│   ├── step 1: 思考文本
│   ├── step 2: 调用 Read + Bash
│   └── step 3: 思考文本
│
└── resultMsg          → ChatMessage 渲染（气泡 + Grid）
    ├── 最终回复文本
    └── 包含 Explore / Implement / ParallelDispatch 等元工具
```

### 并行工具识别

```typescript
const PARALLEL_TOOLS = new Set([
  "Dispatch", "Explore", "Implement", "Design", "Test", "ParallelDispatch"
])
```

**规则**：如果一条 assistant 消息包含 `PARALLEL_TOOLS` 中的工具调用，它会被强制归为 `resultMsg`，通过 `ChatMessage` 以 **Grid 布局** 展示（而非折叠到 AgentProcess 时间轴中）。

---

## 5. 消息渲染分支（ChatMessage）

```
ChatMessage
├── role === "system"
│   └── 居中 Pill 标签（灰色小字）
│
├── role === "user"
│   └── 右侧蓝色气泡
│       ├── 文本内容 (whitespace-pre-wrap)
│       ├── 附件列表 (图片预览 / 文件图标)
│       └── "插入消息" 标记 (如有)
│
└── role === "assistant"
    └── 左侧深色气泡
        ├── MarkdownRenderer (流式 Markdown 渲染)
        │
        └── 工具调用区域
            │
            ├── [包含元工具/并行工具]
            │   └── flattenTools() 展平
            │       → 响应式 Grid 布局
            │       → 每个 slot 渲染为 DispatchCard
            │       → slot 索引保持 React Key 稳定
            │
            └── [仅普通工具]
                └── "Used X Tools" 折叠按钮
                    → 点击展开垂直列表
                    → 每个渲染为 ToolCard
```

### flattenTools 的 Key 稳定性

并行调度（如 `ParallelDispatch`）在流式过程中，子任务是逐个返回的。`flattenTools` 通过 **slot 索引** 而非动态 id 来生成 React Key，确保从"占位符"→"真实调用"的过渡不会导致组件卸载重建。

---

## 6. AgentProcess 过程渲染

Agent 的中间执行步骤通过 `AgentProcess` 组件以时间轴形式渲染：

```
┌─ ● 思考步骤 ────────────────────────┐
│    "我需要先看一下项目结构..."        │
│    (浅色文字，MarkdownRenderer)       │
└──────────────────────────────────────┘
│  (连接线)
┌─ ● 工具调用步骤 ─────────────────────┐
│    ┌──────────┐  ┌──────────┐        │
│    │ToolCard  │  │ToolCard  │        │
│    │ Read     │  │ Bash     │        │
│    │ file.py  │  │ ls -la   │        │
│    └──────────┘  └──────────┘        │
└──────────────────────────────────────┘
│  (连接线)
┌─ ● 思考步骤 ────────────────────────┐
│    "根据分析结果..."                  │
└──────────────────────────────────────┘
```

**步骤类型判断**：
- `step.toolCalls` 存在且 length > 0 → 工具调用步骤
- 否则 → 思考步骤（文字颜色更浅 `text-gray-300`）

**状态流转**：`running` → `completed` / `failed`（通过匹配 `toolResults` 判断）

---

## 7. 工具卡片分流（ToolCard → ToolDisplay）

这是显示逻辑中最复杂的分支之一，采用**两级分发**架构：

```
ToolCard (一级分流)
│
├── 工具名 ∈ META_TOOLS ?
│   │
│   ├── YES → DispatchCard
│   │         ├── 主题色（Explorer=蓝, Implementer=绿, Architect=紫, Tester=橙）
│   │         ├── 状态图标 + specialist 类型标签
│   │         ├── Summary 摘要
│   │         └── 点击 → 打开 SpecialistDrawer
│   │
│   └── NO → 标准折叠卡片
│             ├── Header: 状态图标 + 工具名 + 参数摘要
│             │   (自动提取: 文件路径、命令、查询等)
│             ├── 折叠/展开控制
│             └── 展开时 → ToolDisplay (二级分发)
│
ToolDisplay (二级分发)
│
├── "Read"        → FileRead   (代码高亮显示)
├── "Edit"/"Write"→ FileDiff   (Diff 对比显示)
├── "Bash"        → Bash       (终端风格显示)
└── 其他           → DefaultTool (JSON 格式展示)
```

### META_TOOLS 集合

```typescript
const META_TOOLS = new Set([
  "Dispatch", "Explore", "Implement", "Design", "Test", "ParallelDispatch"
])
```

这些工具代表**子 Agent 调度**，不使用普通的折叠卡片，而是用 `DispatchCard` 以更醒目的方式展示。

---

## 8. 右侧详情面板（SpecialistDrawer）

```
┌────────────────────────┐  ┌─────────────────────────────┐
│                        │  │  ✓  🟢 EXPLORER    4 tools X │
│     主聊天区域          │  │                             │
│                        │  │  📋 SUMMARY REPORT          │
│   ┌──────────────┐     │  │  ┌─────────────────────┐    │
│   │DispatchCard  │─────┼──│  │ Explorer Result      │    │
│   │ 🟢 Explorer  │click│  │  │ (Markdown 渲染)      │    │
│   │ 4 tools      │     │  │  └─────────────────────┘    │
│   └──────────────┘     │  │                             │
│                        │  │  ⚙ EXECUTION DETAILS        │
│                        │  │  ┌─────────────────────┐    │
│                        │  │  │ 1. Bash / ls -F ... │    │
│                        │  │  │ 2. Read / README.md │    │
│                        │  │  │ 3. Bash / ls src/   │    │
│                        │  │  │ 4. Bash / ls ...    │    │
│                        │  │  └─────────────────────┘    │
└────────────────────────┘  └─────────────────────────────┘
                             SpecialistDrawer (fixed, overlay)
```

### 触发链路

1. `ChatMessage` 检测到包含 META_TOOLS 的 toolCalls
2. 通过 `flattenTools()` 展平为子任务
3. 每个子任务渲染为 `DispatchCard`
4. 点击 `DispatchCard` → 设置 `isDrawerOpen = true`
5. `SpecialistDrawer` 从右侧滑出（fixed 定位 + 半透明遮罩）

### 面板内容

- **Header**: specialist 类型标签 + 工具数量 + 关闭按钮
- **Summary Report**: 子 Agent 的最终输出（Markdown 渲染）
- **Execution Details**: 子 Agent 执行过程中调用的所有子工具列表
  - 每行显示：序号 + 状态点 + 工具名 + 参数摘要 + 耗时
  - 点击可展开查看完整的 Arguments 和 Result

### 数据来源

面板数据来自 `mergedTool.subCalls` 和 `mergedTool.subResults`，这些在 `ChatMessage` 组件中预处理完成。

---

## 9. 并行调度的特殊处理（ParallelDispatch）

并行调度是最复杂的场景，一个 `ParallelDispatch` 工具调用可能包含多个子 Agent 同时执行：

```
ChatMessage (assistant)
├── 文本: "好的，我来并行探索..."
└── 工具调用:
    └── ParallelDispatch
        ├── slot[0]: Explore (task: "分析结构")   → DispatchCard 🔵
        ├── slot[1]: Explore (task: "搜索模式")   → DispatchCard 🔵
        ├── slot[2]: Implement (task: "修改代码") → DispatchCard 🟢
        └── slot[3]: Test (task: "运行测试")      → DispatchCard 🟠
```

**Grid 布局展示**：多个 DispatchCard 以响应式网格排列（非垂直列表），视觉上更紧凑。

**流式阶段**：子任务按到达顺序逐个填充 slot，未完成的 slot 显示为 loading 占位符。

---

## 10. 复杂度总结

当前前端显示逻辑的复杂度主要来自以下几个设计决策：

| 复杂点 | 原因 | 涉及组件 |
|--------|------|---------|
| **消息分组** | 连续 assistant 消息要拆分为"过程"和"结果" | ChatList |
| **两级工具分发** | 元工具（子Agent）和普通工具走完全不同的渲染路径 | ToolCard, ToolDisplay, DispatchCard |
| **流式尾部隔离** | 避免流式更新导致历史消息重绘 | ChatList (HistoricalRow + StreamingTail) |
| **嵌套工具调用** | sub_tool_call/sub_tool_result 需要路由到正确父节点 | chat-store.ts |
| **并行调度展平** | ParallelDispatch 的 slot 管理和 Key 稳定性 | ChatMessage (flattenTools) |
| **详情面板** | DispatchCard 局部状态控制 Drawer，非全局状态 | DispatchCard + SpecialistDrawer |

### 组件调用关系总图

```
page.tsx
└── ChatList
    ├── HistoricalRow (memo 包裹)
    │   ├── ChatMessage [user]     → 蓝色气泡
    │   ├── ChatMessage [system]   → 居中 Pill
    │   └── agent_group
    │       ├── AgentProcess
    │       │   └── ToolCard
    │       │       ├── DispatchCard → SpecialistDrawer (覆盖式)
    │       │       └── 折叠卡片 → ToolDisplay
    │       │           ├── FileRead
    │       │           ├── FileDiff
    │       │           ├── Bash
    │       │           └── DefaultTool
    │       └── ChatMessage [assistant]
    │           ├── MarkdownRenderer
    │           └── Grid[DispatchCard...] (元工具)
    │              或 折叠列表[ToolCard...] (普通工具)
    │
    ├── StreamingTail (非虚拟化，直接订阅 store)
    └── InjectionTail
```
