# Specialist 信息流展示重构方案

## 1. 现状问题

当前 Specialist（子 Agent）的展示分三层，信息重复且交互笨重：

```
现状信息流：

  AgentProcess 时间轴
  ├── 思考: "让我并行探索..."
  └── ToolCard → DispatchCard (默认全展开, 很大)
       ├── Header: ✓ 🔍 EXPLORER
       ├── 📋 Task: "列出主要文件..."        ← 占空间，用户不太关心
       ├── ✅ 4 tools  ⏱ 31s               ← 过程信息
       ├── 📁 Files Changed                  ← 有用但不是最重要的
       └── 🔍 查看执行细节 (点击弹 Drawer)   ← 结果藏在这里面！
                                                  ↓
                               SpecialistDrawer (全屏遮罩抽屉)
                               ├── SUMMARY REPORT  ← 用户最想看的
                               └── EXECUTION DETAILS
```

**核心矛盾**：用户最想看的**结果**（Summary）藏在最深处，要额外点击；而默认展开的**过程信息**（Task、进度条）占据最多空间。

---

## 2. 设计目标

| 原则 | 说明 |
|------|------|
| **结果优先** | Summary 默认可见，不需要额外点击 |
| **渐进展开** | 紧凑态 → 展开结果 → 深入过程，层层递进 |
| **紧凑默认** | 默认占用空间尽量小 |
| **不遮挡** | 取消全屏遮罩 Drawer，保持上下文可见 |
| **并行友好** | 多个子任务并排时视觉不拥挤 |

---

## 3. 新设计：三态卡片

用 **一个卡片三种状态** 替换现有的 DispatchCard + SpecialistDrawer 双组件架构：

### 状态 A：紧凑态（Collapsed）— 一行摘要

执行中或折叠后的默认样式，仅一行：

```
┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐
│ ✓  🔍 Explorer  "列出主要文件和目录结构"  4 tools · 31s          ∨  │
└─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┘
  [状态] [类型]    [task 截断 ~60字]        [统计]    [耗时]   [展开]
```

- 高度：~40px，一行搞定
- 左侧色条标识 specialist 类型
- 执行中：⏳ spinner + "Explorer processing…"
- **点击整行** → 展开到状态 B

### 状态 B：结果态（Expanded）— 默认完成后

执行完成后自动切到此状态，展示最重要的信息——**结果**：

```
┌──────────────────────────────────────────────────────────────────────┐
│ ✓  🔍 Explorer  "列出主要文件和目录结构"  4 tools · 31s        ∧  │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Nimbus 是一个受 OS 原理启发的 AI Agent 框架，核心包含 vCPU /       │
│  MMU / NimFS。目录分 core/、agents/、tools/、os/ 等模块...         │
│                                                                      │
│  📁 Files Changed: chat.html (+)                                    │
│                                                                      │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  │
│  ▶ 查看执行过程 (4 calls)                                           │
└──────────────────────────────────────────────────────────────────────┘
```

- Header 和状态 A 一样（点击可折叠回 A）
- **Summary Report 直接 Markdown 渲染**，不需要再点击
- File Changes 紧跟其后（如果有）
- 底部一行"查看执行过程"折叠入口 → 展开到状态 C

### 状态 C：详情态（Full）— 按需展开

在状态 B 的基础上，展开底部的过程细节（**原地展开，不弹 Drawer**）：

```
┌──────────────────────────────────────────────────────────────────────┐
│ ✓  🔍 Explorer  "列出主要文件和目录结构"  4 tools · 31s        ∧  │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Nimbus 是一个受 OS 原理启发的 AI Agent 框架...                     │
│                                                                      │
│  📁 Files Changed: chat.html (+)                                    │
│                                                                      │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  │
│  ▼ 执行过程 (4 calls)                                               │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ 🟢 1. Bash  / ls -F /Users/.../nimbus               30ms  ∨ │  │
│  │ 🟢 2. Read  / README.md :1-100                       2ms  ∨ │  │
│  │ 🟢 3. Bash  / ls -F src/ agent/ nimbus_harbor/      30ms  ∨ │  │
│  │ 🟢 4. Bash  / ls -F src/nimbus/                     31ms  ∨ │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

- 子工具列表原地展开（accordion），每行可再展开看输入/输出
- **不弹 Drawer，不遮挡聊天上下文**
- 已经在当前卡片内完成所有信息的层级浏览

---

## 4. 状态流转

```
                    ┌──────────┐
                    │ Running  │  ← 执行中，显示状态 A（一行 + spinner）
                    └────┬─────┘
                         │ (完成)
                         ▼
                    ┌──────────┐
                    │ State B  │  ← 自动展开到结果态（Summary 可见）
                    │ Expanded │
                    └────┬─────┘
                    ↕    │
              点击 Header │ 点击 "查看执行过程"
              折叠/展开    │
                    ↕    ▼
              ┌──────────┐  ┌──────────┐
              │ State A  │  │ State C  │
              │Collapsed │  │  Full    │
              └──────────┘  └──────────┘
                                ↕ 点击收起过程
```

---

## 5. 并行场景的 Grid 优化

当前：多个 DispatchCard 在 Grid 中各自默认展开，占满屏幕。

### 新方案：Grid 中统一用紧凑态 + 点击单独展开

```
并行完成后默认视图（全部状态 A 紧凑行）：

┌──────────────────────────────────────────────────────────────────────┐
│ ✓ 🔍 Explorer  "分析结构"        4 tools · 25s                  ∨ │
│ ✓ 🔍 Explorer  "搜索模式"        3 tools · 18s                  ∨ │
│ ✓ 🔧 Implementer "修改代码"      6 tools · 45s                  ∨ │
│ ✓ 🧪 Tester   "运行测试"         2 tools · 12s                  ∨ │
└──────────────────────────────────────────────────────────────────────┘

点击第 1 行后：

┌──────────────────────────────────────────────────────────────────────┐
│ ✓ 🔍 Explorer  "分析结构"        4 tools · 25s                  ∧ │
│ ┌────────────────────────────────────────────────────────────────┐  │
│ │ Nimbus 是一个受 OS 原理启发的 AI Agent 框架...                │  │
│ │ 📁 Files Changed: ...                                         │  │
│ │ ▶ 查看执行过程 (4 calls)                                      │  │
│ └────────────────────────────────────────────────────────────────┘  │
│ ✓ 🔍 Explorer  "搜索模式"        3 tools · 18s                  ∨ │
│ ✓ 🔧 Implementer "修改代码"      6 tools · 45s                  ∨ │
│ ✓ 🧪 Tester   "运行测试"         2 tools · 12s                  ∨ │
└──────────────────────────────────────────────────────────────────────┘
```

**关键变化**：
- **不再用 Grid 并排**，改为 **垂直堆叠的 accordion 列表**
- 并行 ≠ 必须并排显示；垂直列表更省空间、更适合阅读
- 每个子任务默认一行，点击展开看结果
- 同时只展开一个（accordion 模式）或允许多个展开（看需求）

### 执行中的并行视图

```
┌──────────────────────────────────────────────────────────────────────┐
│ ⏳ 🔍 Explorer  "分析结构"        2/4 tools  ···                    │
│ ⏳ 🔍 Explorer  "搜索模式"        processing…  ···                  │
│ ✓ 🔧 Implementer "修改代码"      6 tools · 45s                  ∨ │
│ ⏳ 🧪 Tester   "运行测试"         1/2 tools  ···                    │
└──────────────────────────────────────────────────────────────────────┘
```

- 正在执行的显示实时进度（`2/4 tools`）
- 已完成的可以先展开查看
- 视觉上清晰区分完成/进行中

---

## 6. 组件重构方案

### 删除
- **SpecialistDrawer.tsx** — 不再需要覆盖式抽屉

### 重构
- **DispatchCard.tsx** → 重写为三态卡片
  - 状态 A：紧凑行（~40px）
  - 状态 B：展开 Summary + File Changes
  - 状态 C：展开 Execution Details（原 SpecialistDrawer 的 SubCallRow 逻辑内联）
  - 完成后默认状态 B；在 ParallelDispatch 的 Grid 中默认状态 A

### 修改
- **ChatMessage.tsx** — `ParallelToolList` 改为垂直堆叠，去掉 Grid 布局
- **ToolCard.tsx** — 简化，META_TOOLS 分支传 `defaultCollapsed` prop 给 DispatchCard

### 新增 props
```typescript
interface DispatchCardProps {
  tool: { ... };  // 不变
  defaultState?: "collapsed" | "expanded";  // 新增：控制默认态
  // collapsed: 并行场景用
  // expanded: 单独 subagent 用
}
```

---

## 7. 新组件结构

```
ChatList
├── AgentProcess (时间轴)
│   └── ToolCard
│       ├── DispatchCard (三态卡片，自包含)  ← 简化！
│       │   ├── State A: 紧凑行
│       │   ├── State B: + Summary + Files
│       │   └── State C: + Execution Details (inline)
│       └── 折叠卡片 (普通工具)
│           └── ToolDisplay
└── ChatMessage (最终回复)
    └── ParallelToolList
        └── 垂直列表 [DispatchCard(defaultState="collapsed")...]
            （不再用 Grid，改为 accordion 堆叠）
```

**对比旧结构**：
- 删除 SpecialistDrawer（-1 组件）
- DispatchCard 从"大卡片 + 弹抽屉"变成"自包含三态卡片"
- 并行从 Grid 横排变成垂直 accordion

---

## 8. 视觉设计要点

### 紧凑行（状态 A）的设计

```
┌─╴蓝色条╶─ ✓  🔍 Explorer ╌╌╌ "列出主要文件..." ╌╌╌ 4 tools · 31s ─── ∨ ─┐
└───────────────────────────────────────────────────────────────────────────────┘
  2px 色条   状态  类型badge     task 截断           统计      展开按钮
```

- 左侧 2px 色条（蓝/绿/橙/紫/红）标识类型
- 微妙的 hover 效果（bg-white/[0.02]）
- 圆角 8px，border 1px white/8%

### Summary 区域（状态 B）

- Summary 直接用 MarkdownRenderer 渲染
- 最大高度限制 ~300px，超出后 `overflow-y: auto` + 渐变遮罩
- File Changes 保持现有 pill 样式

### 执行过程（状态 C）

- 复用现有 SpecialistDrawer 的 SubCallRow 逻辑
- 每行：序号 + 状态点 + 工具名 + 参数摘要 + 耗时 + 展开箭头
- 展开后显示 Arguments（折叠默认）+ Result（代码块）

---

## 9. 实施计划

| 步骤 | 内容 | 改动文件 |
|------|------|---------|
| 1 | 重写 DispatchCard 为三态卡片 | `DispatchCard.tsx` |
| 2 | 将 SubCallRow 逻辑从 SpecialistDrawer 搬入 DispatchCard | `DispatchCard.tsx` |
| 3 | 删除 SpecialistDrawer | `SpecialistDrawer.tsx` |
| 4 | ParallelToolList 改垂直堆叠 | `ChatMessage.tsx` |
| 5 | ToolCard 传 defaultState prop | `ToolCard.tsx` |
| 6 | 调试并行流式场景 | 全链路测试 |
