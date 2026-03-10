# TODO

## 🐛 虚拟滚动 scroll-to-bottom 到不了最底端

**文件**: `web-ui/src/components/chat/ChatList.tsx`

### 问题描述

引入 `@tanstack/react-virtual` 虚拟滚动后，点击 "New messages" 按钮或自动滚动到底部时，
无法滚动到最后一条消息的真实底端，总是差一段距离。

### 已尝试的方案（均无效）

1. `el.scrollTo({ top: el.scrollHeight })` — `scrollHeight` 基于 `getTotalSize()` 估算，偏小
2. `virtualizer.scrollToIndex(lastIndex, { align: 'end' })` — 在动态高度场景下，高度未测量完时位置不准
3. 两次滚动（立即 + 80ms 延迟二次） — 仍然不够，高度测量是异步的，时机难以把控
4. `paddingEnd: 48` + `estimateSize: 300` + `mb-8` 纳入测量 — 部分改善但根本问题未解决

### 根因分析

`@tanstack/react-virtual` 的动态高度测量（`measureElement`）是异步的：
- item 渲染 → `ResizeObserver` 回调 → 更新 virtualizer 内部高度 → `getTotalSize()` 变化
- 在这个过程完成之前调用任何滚动方法，目标位置都是基于旧的估算高度，必然偏小
- 消息高度差异极大（纯文字 ~100px，带多个 tool card 的消息可能 >1000px），估算误差大

### 建议解决方向

**方案 A（推荐）**: 监听 `virtualizer.getTotalSize()` 变化，在其稳定后再执行滚动

```tsx
const totalSize = virtualizer.getTotalSize();
const prevTotalSize = useRef(totalSize);
useEffect(() => {
  if (shouldScrollToBottom.current && totalSize !== prevTotalSize.current) {
    prevTotalSize.current = totalSize;
    el.scrollTop = el.scrollHeight;
  }
}, [totalSize]);
```

**方案 B**: 不用虚拟滚动，改用 `content-visibility: auto` CSS 属性
浏览器原生跳过不在视口内元素的渲染，不改变 DOM 结构，scroll 行为完全正常。
```css
.chat-message {
  content-visibility: auto;
  contain-intrinsic-size: 0 300px; /* 估算高度 */
}
```

**方案 C**: 用 `react-window` 或 `react-virtuoso` 替换 `@tanstack/react-virtual`
`react-virtuoso` 对动态高度 + 自动滚动到底部有开箱即用的支持（`followOutput` prop）。

### 相关 commit

- `fb89bf4` — 引入虚拟滚动
- `890649b` — 改用 scrollToIndex
- `0f9decc` — 改回 scrollTop + 二次滚动 + paddingEnd（仍未解决）

---

## 🐛 SSE 流式渲染时 tool card 显示不完整

**文件**: `web-ui/src/stores/chat-store.ts`

### 问题描述

流式渲染过程中，tool call card 有时显示不完整（result 丢失、args 缺失等）。
刷新页面后从服务端拉取完整历史，tool card 渲染正常。

### 已尝试的方案（均无效）

1. rAF buffer（`_pendingStreamMsg`）— 解决了同帧内 `message` chunk 互相覆盖的问题，但 tool card 仍有问题
2. `tool_call` 处理时检查 `toolResults` 数组提前 attach result — 解决了部分 replay race，但仍不稳定

### 根因分析

`tool_call` 和 `tool_result` 事件的到达顺序在流式场景下不保证：
- rAF buffer 的批处理机制让同一帧内的事件可以叠加，但跨帧的顺序问题仍存在
- `_attachToRunningSession`（reconnect path）replay 历史事件时，高速连续的事件流
  可能导致 `tool_result` 在 `tool_call` 的 rAF 还未 flush 时就已经处理完毕
- `parts` 数组的 find-by-id 逻辑在事件乱序时容易 `matchIdx === -1`

### 建议解决方向

**方案 A（推荐）**: 将 tool card 的状态管理从 `parts` 数组移到独立的 `Map<tcId, ToolCardState>`
不再依赖 `parts` 数组的顺序和 find 逻辑，tool_call 和 tool_result 各自写入 Map，
渲染时再合并，天然解决乱序问题。

**方案 B**: 服务端保证 `tool_call` 和 `tool_result` 合并成单个 SSE 事件推送
在 `tool_result` 时同时携带对应的 `tool_call` 信息，前端只需处理一个事件。

### 相关 commit

- `4a4ed17` — rAF buffer 修复（解决 message chunk 覆盖）
- `48b8cf4` — tool_call 处理时提前 attach result（部分修复，仍不稳定）
