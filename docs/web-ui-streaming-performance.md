# Web UI Streaming 渲染性能优化方案

> 问题：stream 输出时鼠标点击、打字卡顿，UI 响应迟钝
>
> 日期：2026-02-07  |  优先级：P0  |  预计改动：6 个文件，约 120 行

---

## 1. 问题现象

- AI streaming 回复过程中，用户鼠标点击、文本框打字出现明显卡顿
- Gemini 改版后 UI 更漂亮，但引入了更多 GPU 密集型 CSS，性能下降

## 2. 根因分析

### 2.1 核心瓶颈：Streaming 内容变化引发全树重渲染

```
streamingContent 每 50ms 更新一次 (chat-store.ts UPDATE_INTERVAL=50)
    ↓
ChatList.useMemo 依赖 [messages, isStreaming, streamingContent, streamingToolCalls]
    ↓ streamingContent 一变，useMemo 全部重算
整个消息列表重新分组 + 重新渲染
    ↓ 包括所有历史消息（ChatMessage 没有 React.memo）
每条 ChatMessage → MarkdownRenderer → ReactMarkdown 重新解析 AST
    ↓ 如果内容有代码块
SyntaxHighlighter (Prism) 对每个代码块重新执行语法高亮
    ↓
💥 主线程阻塞 200-500ms → 输入/点击无响应
```

### 2.2 副瓶颈：backdrop-blur 大量使用

5 处使用了 `backdrop-blur-md/xl`，都是 GPU 合成层操作：

| 位置 | 文件 | CSS |
|------|------|-----|
| Header | `page.tsx` | `backdrop-blur-xl` |
| AI 消息气泡 | `ChatMessage.tsx` | `backdrop-blur-md` |
| AgentProcess 气泡 | `AgentProcess.tsx` | `backdrop-blur-md` |
| Working Indicator | `page.tsx` | `backdrop-blur-md` |
| ChatInput 输入框 | `ChatInput.tsx` | `backdrop-blur-xl` |

DOM 高频更新时，浏览器需要反复重绘这些合成层。

### 2.3 副瓶颈：自动滚动过于频繁

`page.tsx` 中 streaming 滚动 effect 依赖 `streamingContent`，每 50ms 变化就触发一次 scroll，每次 scroll 都造成 layout reflow。

---

## 3. 修复方案

### Fix 1: 拆分 ChatList 的 streaming 渲染路径 [P0]

**文件**: `src/components/chat/ChatList.tsx`

**问题**: `useMemo` 依赖 `streamingContent`，每次更新都重算所有历史消息分组。

**方案**: 历史消息和 streaming 消息分离渲染。

```tsx
// ❌ 当前：streamingContent 变化 → 整个 groups 重算
const groups = useMemo(() => {
  // ... 分组逻辑
  if (isStreaming) {
    const streamingMsg = { id: "streaming", content: streamingContent, ... };
    currentAgentGroup.push(streamingMsg);  // 混在历史消息分组里
  }
  flushAgentGroup();
  return result;
}, [messages, isStreaming, streamingContent, streamingToolCalls]);

// ✅ 修复后：分离历史与 streaming
const groups = useMemo(() => {
  // ... 历史消息分组逻辑（不包含 streaming）
  flushAgentGroup();
  return result;
}, [messages]); // ← 只依赖 messages！

// streaming 消息单独渲染
const streamingElement = useMemo(() => {
  if (!isStreaming) return null;
  const streamingMsg: Message = {
    id: "streaming",
    role: "assistant",
    content: streamingContent,
    toolCalls: streamingToolCalls.length > 0 ? streamingToolCalls : undefined,
    timestamp: Date.now(),
  };
  return <ChatMessage message={streamingMsg} isStreaming={true} />;
}, [isStreaming, streamingContent, streamingToolCalls]);

return (
  <div className="space-y-6 max-w-4xl mx-auto">
    {groups.map((group, i) => /* 历史消息，不会因 streaming 重渲染 */)}
    {streamingElement}
  </div>
);
```

### Fix 2: 给 ChatMessage 加 React.memo [P0]

**文件**: `src/components/chat/ChatMessage.tsx`

**问题**: 没有 memo，父组件重渲染时所有历史消息都跟着重渲染。

```tsx
// ✅ 修复后
export const ChatMessage = React.memo(function ChatMessage({
  message, isStreaming
}: ChatMessageProps) {
  // ... 组件内容不变
}, (prevProps, nextProps) => {
  return (
    prevProps.message.id === nextProps.message.id &&
    prevProps.message.content === nextProps.message.content &&
    prevProps.isStreaming === nextProps.isStreaming &&
    prevProps.message.toolCalls === nextProps.message.toolCalls &&
    prevProps.message.toolResults === nextProps.message.toolResults
  );
});
```

### Fix 3: Streaming 期间用轻量渲染 [P0]

**文件**: `src/components/chat/MarkdownRenderer.tsx`

**问题**: `ReactMarkdown` + `Prism` 每 50ms 重新解析整个内容，开销极大。

**方案**: streaming 时用 `<pre>` 纯文本显示（零解析开销），结束后再做 Markdown 渲染。

```tsx
interface MarkdownRendererProps {
  content: string;
  className?: string;
  isStreaming?: boolean;  // 新增
}

export const MarkdownRenderer = memo(function MarkdownRenderer({
  content, className = "", isStreaming = false
}: MarkdownRendererProps) {
  // Streaming 期间：纯文本（零开销）
  if (isStreaming) {
    return (
      <div className={`markdown-content ${className}`}>
        <pre className="whitespace-pre-wrap text-gray-100 text-[15px] leading-relaxed font-sans">
          {content}
          <span className="animate-pulse">▍</span>
        </pre>
      </div>
    );
  }

  // 非 streaming：完整 Markdown 渲染（只执行一次）
  return (
    <div className={`markdown-content ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{...}}>
        {content}
      </ReactMarkdown>
    </div>
  );
});
```

调用方传递 `isStreaming`：

```tsx
// ChatMessage.tsx
<MarkdownRenderer
  content={message.content}
  isStreaming={isStreaming && message.id === "streaming"}
  className="..."
/>
```

### Fix 4: 消息气泡去掉 backdrop-blur [P1]

**文件**: `ChatMessage.tsx`, `AgentProcess.tsx`

消息气泡区域是 streaming 期间频繁重绘的区域，`backdrop-blur` 在这里开销最大。Header 和 Input 的 blur 可以保留（静态元素）。

```tsx
// ❌ ChatMessage.tsx 消息气泡
"bg-gray-900/60 backdrop-blur-md border border-white/5"

// ✅ 用纯色替代
"bg-gray-900/80 border border-white/5"

// ❌ AgentProcess.tsx 步骤气泡
"bg-gray-900/60 backdrop-blur-md border border-white/5"

// ✅ 同上
"bg-gray-900/80 border border-white/5"
```

保留 `backdrop-blur` 的地方（不频繁重绘）：Header、ChatInput。

### Fix 5: 滚动改为固定间隔 [P1]

**文件**: `src/app/page.tsx`

```tsx
// ❌ 当前：依赖 streamingContent，每 50ms 触发一次 scroll effect
useEffect(() => {
  if (isStreaming && autoScrollEnabled) {
    const timeoutId = setTimeout(() => scrollToBottom(), 150);
    return () => clearTimeout(timeoutId);
  }
}, [streamingContent, isStreaming, autoScrollEnabled, scrollToBottom]);

// ✅ 修复后：用 interval 代替，不依赖 streamingContent
useEffect(() => {
  if (!isStreaming || !autoScrollEnabled) return;
  const interval = setInterval(() => scrollToBottom(), 300);
  return () => clearInterval(interval);
}, [isStreaming, autoScrollEnabled, scrollToBottom]);
```

这样 `streamingContent` 的变化不再触发 scroll effect，减少 reflow。

### Fix 6: 增大 streaming 更新间隔 [P2]

**文件**: `src/stores/chat-store.ts`

```tsx
// 当前 50ms，增大到 100ms，人眼感知差异极小
const UPDATE_INTERVAL = 100; // ms (原 50)
```

配合 Fix 1-3，这个改动让 streaming 更新频率减半，进一步降低渲染压力。

---

## 4. 修改文件清单

| 文件 | 改动 | 优先级 | 工作量 |
|------|------|--------|--------|
| `src/components/chat/ChatList.tsx` | 拆分 streaming 渲染路径 | P0 | 30 行 |
| `src/components/chat/ChatMessage.tsx` | React.memo + 去 backdrop-blur | P0 | 15 行 |
| `src/components/chat/MarkdownRenderer.tsx` | streaming 轻量渲染 + memo | P0 | 20 行 |
| `src/components/chat/AgentProcess.tsx` | 去 backdrop-blur | P1 | 2 行 |
| `src/app/page.tsx` | 滚动改 interval | P1 | 8 行 |
| `src/stores/chat-store.ts` | UPDATE_INTERVAL 50→100 | P2 | 1 行 |

## 5. 预期效果

| 指标 | 修复前 | 修复后（预估） |
|------|--------|---------------|
| Streaming 时每次渲染耗时 | 200-500ms | 10-30ms |
| 历史消息重渲染次数/秒 | ~20 次 | 0 次（memo 跳过） |
| Markdown 解析次数（streaming 中） | ~20 次/秒 | 0 次（纯文本） |
| 用户输入响应延迟 | 200-500ms | <16ms（一帧内） |
| GPU 合成层重绘区域 | 5 个 blur 层 | 2 个（仅 header/input） |

**总体预估：streaming 期间渲染开销降低 70-80%。**

## 6. 验证方法

1. **Chrome DevTools → Performance**：录制 streaming 过程，确认没有 >50ms 的 Long Tasks
2. **实际体感**：streaming 期间在输入框打字，应完全无卡顿
3. **React DevTools → Profiler**：确认历史消息组件在 streaming 期间显示 "Did not render"
4. **视觉回归**：streaming 结束后 Markdown 正常渲染（代码高亮、表格、列表等）

## 7. 注意事项

1. **Fix 3 的 tradeoff**：streaming 期间看不到 Markdown 格式化（纯文本显示），streaming 结束后才渲染格式。如果需要 streaming 期间也有部分格式化，可用更轻量的库（如 `marked` + `DOMPurify`）替代 `react-markdown` + `prism`。

2. **Fix 1 的边界**：改了 ChatList 分组逻辑后，需确保：
   - streaming 结束后消息正确归入历史分组
   - AgentProcess（工具调用折叠）逻辑保持正常
   - step_start 事件产生的中间消息正确 commit

3. **Fix 2 的比较函数**：`React.memo` 的自定义比较函数用了引用比较（`===`），如果 `toolCalls`/`toolResults` 在 store 中被重新创建（即使内容相同），会导致不必要的重渲染。如果发现这种情况，改用深比较或在 store 层面做引用稳定化。

4. **修复顺序建议**：先做 P0 的三个 Fix（1→2→3），验证效果后再做 P1 和 P2。P0 三个 fix 加起来就能解决 80% 的性能问题。
