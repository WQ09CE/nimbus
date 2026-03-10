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
