# DispatchCard.tsx 代码审查

> 文件：`web-ui/src/components/chat/tools/DispatchCard.tsx`
> 审查日期：2025-01

---

## 1. 三态状态机（collapsed / expanded / full）

### 实现方式

```ts
// L~310
const [viewState, setViewState] = useState<"collapsed" | "expanded" | "full">(
    defaultState ?? "collapsed"
);
```

状态转换通过 header 区域的 `onClick` 驱动：

| 当前态 | 点击后 |
|---|---|
| collapsed | expanded |
| expanded | full |
| full | collapsed |

### 评价

✅ 状态定义清晰，三态语义（折叠 / 展开 / 全屏）符合直觉。  
✅ `defaultState` prop 允许调用方控制初始态（并行场景默认折叠、单 agent 默认展开），设计合理。  
⚠️ **状态机无保护**：循环切换逻辑写在 JSX onClick 内联表达式里，没有枚举/reducer 封装。当三态需要扩展或加条件跳转（如 running 态禁止 full）时，改动散落在 JSX 中，可维护性弱。  
**建议**：抽一个 `nextViewState(cur, isRunning)` 纯函数，或用 `useReducer` 管理。

---

## 2. SubCallRow 组件复用性

### 现状

`SubCallRow` 接收三个 props：`sub: SubCallWithStatus`、`index: number`、`theme?: SpecialistTheme`。  
职责：渲染单条工具调用，含状态点、工具名、参数摘要、可展开详情。

### 评价

✅ 组件独立、无外部依赖（除 `buildArgsSummary` helper），可在其他卡片类型中直接复用。  
✅ `theme` 可选，降级到 `DEFAULT_THEME`，兼容性好。  
⚠️ **内联 `useState`**：每个 `SubCallRow` 内部持有 `isExpanded` 本地状态——这意味着 running 阶段列表频繁变化时，新增行的折叠状态会被重置。若父组件重建数组引用导致 key 变化，状态也会丢失。  
⚠️ **`buildArgsSummary` 未 memo**：每次渲染都重新调用，对 `Bash` 命令截 80 字符等逻辑重复计算，低成本但不够干净。  
**建议**：用 `useMemo(() => buildArgsSummary(...), [sub.name, sub.arguments])` 包裹。

---

## 3. Running 态实时进度展示

### 实现

- **顶部色带**：`strip.running = "bg-purple-500 animate-pulse"`，视觉上标识 running。
- **三个跳动点**：header 右侧 3 个 `animate-bounce` span，用 `animationDelay` 错开（0ms / 150ms / 300ms）。
- **SubCallRow 状态点**：running 行显示 `bg-yellow-400 animate-pulse`，完成变 `bg-emerald-400`。
- **进度条**：`<div style={{ width: \`${progress}%\` }} />` 基于 `completedCount / total * 100` 计算。
- **`useEffect` + `setInterval`**：running 时每 300ms 更新 `elapsedSeconds`，用于展示已用时。

### 评价

✅ 多层次视觉反馈（色带 + 跳点 + 进度条 + 计时器），体验完整。  
✅ `useEffect` 清理函数正确 `clearInterval`，无内存泄漏。  
⚠️ **`setInterval` 无条件挂载**：仅在 `status === "running"` 时启动 interval，但 `useEffect` 依赖数组为 `[tool.status, tool.duration]`。若父组件频繁传入新 `tool` 对象（引用变化但 status 不变），effect 不会重新触发，计时可能错位。  
⚠️ **进度计算**：`progress = completedCount / subCalls.length * 100`，当 `subCalls` 为空时得 `NaN`，需加保护（当前代码有 `|| 0` fallback，OK）。

---

## 4. 性能问题

### 已发现的潜在 re-render

| 问题 | 位置 | 影响 |
|---|---|---|
| `SPECIALIST_THEMES` 是模块级常量，每次 render 重新 `Object.values()` 遍历 | 实际无遍历，直接 key 访问，OK | 无 |
| `parseFileChanges` / `parseExecutorReport` 每次 render 重新执行正则 | L~170–190，在 render 内直接调用 | 中等：result 不变时重复解析 |
| `subCallsWithStatus` 数组每次 render 重新 `map` 构建 | L~350 附近 | 低：但 running 期间父组件高频更新时累积明显 |
| `SubCallRow` 无 `React.memo` | SubCallRow 定义处 | 父组件任何 state 变化都会重绘所有子行 |

### 建议

```tsx
// 1. memo 化 SubCallRow
const SubCallRow = React.memo(function SubCallRow(...) { ... });

// 2. useMemo 缓存 subCallsWithStatus
const subCallsWithStatus = useMemo(() =>
    buildSubCallsWithStatus(tool.subCalls, tool.subResults),
    [tool.subCalls, tool.subResults]
);

// 3. useMemo 缓存解析结果
const fileChanges = useMemo(() =>
    resultText ? parseFileChanges(resultText) : [],
    [resultText]
);
```

---

## 总结

| 维度 | 评分 | 主要问题 |
|---|---|---|
| 三态状态机 | ★★★★☆ | 缺 reducer 封装，扩展成本高 |
| SubCallRow 复用性 | ★★★★☆ | 本地 state 在动态列表中有风险 |
| Running 进度展示 | ★★★★★ | 完整，细节到位 |
| 性能 | ★★★☆☆ | 缺 `React.memo` + `useMemo`，running 期间高频更新场景下明显 |

**优先修复**：给 `SubCallRow` 加 `React.memo`，`subCallsWithStatus` 加 `useMemo`——改动最小，收益最大。
