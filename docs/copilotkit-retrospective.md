# CopilotKit POC 回顾 & Legacy Web-UI 回归方案

> 分支: `feature/copilotkit-frontend` → 回归 `main` (web-ui-legacy)
> 日期: 2026-02-25

## 1. 背景

在 `feature/copilotkit-frontend` 分支上，我们尝试用 CopilotKit + AG-UI 协议替代原有的 legacy web-ui。经过完整 POC 开发后，对比两套方案发现 CopilotKit 在 Nimbus 场景下是净负面。

## 2. CopilotKit POC 结论

### 2.1 胶水代码税

为了让 CopilotKit 接入 Nimbus 后端，额外产生了 ~774 行胶水代码：

| 文件 | 行数 | 作用 |
|------|------|------|
| `api_agui.py` | 564 | Python SSE → AG-UI 协议翻译器 |
| `route.ts` | 55 | CopilotRuntime + HttpAgent 代理 |
| `SessionHistoryLoader.tsx` | 65 | 黑入 `useCopilotChatInternal` 加载历史 |
| `NimbusMessages.tsx` | 61 | 薄包装层（inject 显示） |
| `next.config.mjs` 额外配置 | ~10 | external packages workaround |

### 2.2 数据链路对比

```
CopilotKit 路径 (6 跳):
  用户 → CopilotChat → Next.js route → CopilotRuntime → HttpAgent
  → api_agui.py (NimbusToAGUI 翻译) → session_manager → SSE
  → 翻译回 AG-UI → CopilotKit 渲染

Legacy 路径 (3 跳):
  用户 → Next.js → SSE 直连 :4096 → chat-store → UI
```

### 2.3 功能差距

Legacy 有而 CopilotKit 版缺失的功能：

- 专用工具视图（FileRead / FileDiff / Bash）
- 虚拟滚动（@tanstack/react-virtual）
- 完整 Session 管理（搜索、恢复中断、状态标签）
- 每 session 独立 Model 选择（持久化到后端）
- 文本文件 + PDF 上传
- 错误分类（rate_limit / auth_error / ctx_overflow + 重试）
- 文件浏览器
- ASCII 图表渲染 + CJK 字体栈
- 移动端适配
- 键盘快捷键（Cmd+K）
- 测试套件（Jest + Playwright E2E）

### 2.4 风险点

- `useCopilotChatInternal`：未公开的内部 API，任何 CopilotKit 版本升级都可能挂
- `@copilotkit/*` 1.51.4 锁定 React 18，升级 React 19 需要 `--legacy-peer-deps`
- AG-UI 适配层需要随 Nimbus SSE 事件变更同步维护

### 2.5 CopilotKit 的价值

- 验证了 AG-UI 协议可以接入 Nimbus（备用集成路径）
- SubAgentCard 的独立流式渲染思路值得借鉴
- `api_agui.py` 可保留为外部集成端点（不耦合主 UI）

## 3. Chainlit 评估

同期也评估了 Chainlit 作为替代方案，结论：**不适合 Nimbus**。

- Chainlit 用 Socket.IO（WebSocket），无法直接消费 Nimbus SSE 流
- 自定义组件在沙盒中运行，只允许 react/shadcn/lucide，无法使用 Zustand 等
- 母公司 LiteralAI 已解散（2025.05），转为社区维护
- 多 Agent 支持不足（GitHub Issue #2591 承认"落后于时代"）

## 4. 从 CopilotKit 借鉴的唯一设计：独立流式 SubAgent 卡片

### 4.1 问题

Legacy 的 `ParallelDispatch` 渲染用 CSS Grid 分列布局，整个 `Message.toolCalls[]` 每次更新都触发全量重渲染。CopilotKit 版本中，每个 SubAgentCard 是独立 React 组件，各自订阅 Zustand store，只更新自己。

### 4.2 CopilotKit 的实现机制

```
CopilotKit 框架层:
  每个 tool_call → 独立 React render → useCopilotAction({ name: "*" })
                                        ↓
                                    ChildActionTracker (useEffect → upsertCall)
                                        ↓
                                    WorkflowStore (Zustand, flat Record<callId, WorkflowCall>)
                                        ↓
                                    SubAgentCard (useWorkflowStore → selectChildren)
```

关键：`WorkflowCall.parentId` 链接父子关系，`selectChildren(calls, parentId)` 筛选子任务。每个卡片只因自己的子任务变化而重渲染。

### 4.3 移植到 Legacy 的方案（~100 行改动）

#### Step 1: 复制 `workflow-store.ts` 到 legacy

文件来源: `web-ui/src/stores/workflow-store.ts`（127 行，零外部依赖，纯 Zustand）

直接复制到 `web-ui-legacy/src/stores/workflow-store.ts`，导出 `useWorkflowStore` 和 `selectChildren`。

#### Step 2: `chat-store.ts` — 4 个事件处理器加 `upsertCall` 调用（~80 行）

在现有 SSE 事件处理逻辑旁边，同步写入 WorkflowStore：

```typescript
import { useWorkflowStore } from './workflow-store';
const wfStore = useWorkflowStore.getState();

// executor_start (chat-store.ts ~938)
wfStore.upsertCall({
  callId: executorPid,
  name: specialistName,       // "Explore" / "Implement" / etc.
  parentId: parentActionId,
  batchSlotIndex: slotIdx,
  status: "running",
  args: { task: taskDescription },
});

// sub_tool_call (chat-store.ts ~807)
wfStore.upsertCall({
  callId: subTool.id,
  name: subTool.name,
  parentId: parentActionId,
  status: "running",
  args: subTool.arguments,
});

// sub_tool_result (chat-store.ts ~851)
wfStore.upsertCall({
  callId: subResult.id,
  name: subResult.name,
  parentId: parentActionId,
  status: subResult.error ? "failed" : "completed",
  result: subResult.result,
});

// executor_done (chat-store.ts ~975)
wfStore.upsertCall({
  callId: executorPid,
  name: specialistName,
  status: "completed",
  result: executorSummary,
});
```

**注意**: 保留原有的 `toolCalls[]` 数组变异逻辑不变，store 写入是**附加的**，不替代。这样 session 重载路径（从 `tool.subCalls` 反序列化）仍然正常工作。

#### Step 3: `DispatchCard.tsx` — 改用 store 订阅（~20 行）

```typescript
import { useWorkflowStore, selectChildren } from '@/stores/workflow-store';
import { useShallow } from 'zustand/react/shallow';

// 在 DispatchCard 组件内:
const storeChildren = useWorkflowStore(
  useShallow(s => selectChildren(s.calls, tool.id))
);

// 混合策略: 直播用 store，回看用 props
const subCallsWithStatus = storeChildren.length > 0
  ? storeChildren.map(c => ({
      id: c.callId,
      name: c.name,
      status: c.status,
      args: c.args,
      result: c.result,
      durationMs: c.durationMs,
    }))
  : deriveFromProps(tool.subCalls, tool.subResults);  // 原有逻辑
```

#### Step 4: `ChatMessage.tsx` — 去掉 Grid，改为垂直堆叠（~5 行）

```diff
- <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: '0.5rem' }}>
+ <div className="space-y-3">
    {flatEntries.map(entry => (
      <ToolCard key={entry.stableKey} ... />
    ))}
  </div>
```

#### Step 5: 复制 `specialist-colors.ts`（可选）

CopilotKit 版的颜色映射更简洁（33 行），可替代 legacy 的 `SPECIALIST_THEMES`。

### 4.4 混合策略说明

| 场景 | 数据来源 | 原因 |
|------|----------|------|
| 直播流式 | WorkflowStore（Zustand 订阅） | 实时独立更新 |
| Session 回看 | `tool.subCalls` / `tool.subResults` (props) | 历史数据已序列化在消息中 |
| 过渡态 | Store 优先，props 兜底 | `storeChildren.length > 0` 判断 |

### 4.5 效果

- 3 个 specialist 卡片各自独立出现、各自流式更新子工具
- 不再是 Grid 整体刷新，每个卡片只因自己的子任务变化而重渲染
- 无 AG-UI 胶水层，直接走 Nimbus SSE

## 5. 保留资产

以下文件可从 `feature/copilotkit-frontend` 分支提取复用：

| 文件 | 用途 | 目标 |
|------|------|------|
| `web-ui/src/stores/workflow-store.ts` | Zustand 调用树 store | 直接复制到 legacy |
| `web-ui/src/components/specialist-colors.ts` | Specialist 颜色映射 | 可选替换 legacy SPECIALIST_THEMES |
| `src/nimbus/server/api_agui.py` | AG-UI 端点 | 保留在后端，作为外部集成接口 |

## 6. 行动计划

1. 将本文档提交到 `main` 分支 `docs/` 目录
2. 从 `feature/copilotkit-frontend` cherry-pick `api_agui.py` 到 `main`（保留 AG-UI 端点）
3. 复制 `workflow-store.ts` + `specialist-colors.ts` 到 `web-ui-legacy/src/stores/`
4. 实施 4.3 节的 4 步改动（~100 行）
5. 验证直播流式 + session 回看两个路径
6. 废弃 `feature/copilotkit-frontend` 分支
