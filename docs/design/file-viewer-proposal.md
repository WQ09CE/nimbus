# Nimbus Web-UI 文件查看功能方案（File Viewer Proposal）

## 1. 功能概述（用户可以做什么）

目标是在 Nimbus Web 界面中提供“像 VS Code 一样”的基础文件浏览与查看能力（先不强调编辑），让用户在一个会话（session）的 workspace 内：

- 浏览目录树（已具备基础能力）
- 点击文件后在右侧/下方查看文件内容
- 支持大文件分段加载（offset/limit）
- 支持基础状态提示（加载中、读取失败、二进制文件不可预览）
- 可在文件树与聊天区联动（例如点击工具结果中的文件路径可跳转打开）

当前代码中已经有：
- `FileExplorer.tsx`：可展开目录并列文件
- `/sessions/{session_id}/files`：可列出目录
- 工具结果展示里已有 `FileRead` 组件（但它是“工具调用结果视图”，不是“通用文件浏览器”）

因此本功能属于“在现有基础上补齐 read API + Viewer UI + 状态管理联动”。

---

## 2. 现状架构分析（关键结论）

### 2.1 前端

- API 聚合入口：`web-ui/src/lib/api/index.ts`
  - 当前导出 `client/sessions/chat/filesystem`
- 文件树组件：`web-ui/src/components/chat/FileExplorer.tsx`
  - 已调用 `listFiles(sessionId, path)`
  - 仅支持目录展开，不支持文件内容面板
- 工具渲染分发：`web-ui/src/components/chat/tools/ToolDisplay.tsx`
  - 按 tool name 分发到 `FileRead/FileDiff/Bash/DefaultTool`
  - 可作为未来“点击 tool 中路径 => 打开 viewer”的接入点
- API 层已存在 `filesystem.ts`（但当前只用于 `/fs/complete`）

### 2.2 后端

- 主路由文件：`src/nimbus/server/api.py`
  - 已实现：`GET /sessions/{session_id}/files`（列目录）
  - 已实现：`GET /fs/complete`（路径补全）
  - **未实现**：专门的“读取 workspace 文件内容”HTTP API

虽然 Agent 工具层有 `Read` 工具（`src/nimbus/tools/read.py`），但 Web UI 需要一个稳定、可控、可分页、可鉴权的 REST 端点，不应直接复用 agent 工具输出格式。

---

## 3. 前端需要新增/修改的组件

## 3.1 API 层

建议新增 `web-ui/src/lib/api/file-viewer.ts`（或扩展 `sessions.ts`）：

- `getFileContent(sessionId, path, offset?, limit?)`
- `getFileMeta(sessionId, path)`（可选；也可合并在 content 响应内）

并在 `web-ui/src/lib/api/index.ts` 中导出。

建议响应类型（TS）：

- `path`
- `encoding`（如 `utf-8`）
- `content`
- `truncated`（是否还有剩余）
- `next_offset`
- `size`
- `mime_type`
- `is_binary`

## 3.2 组件层

### A. 改造 `FileExplorer.tsx`

当前点击文件无行为。建议：

- 增加 `onFileSelect(path: string)` 回调 prop
- 目录点击保持展开/收起
- 文件点击触发选中状态 + 回调
- 增加“当前选中文件高亮”

### B. 新增 `FileViewer.tsx`

建议位置：`web-ui/src/components/chat/FileViewer.tsx`

职责：
- 接收 `sessionId + filePath`
- 调用 `getFileContent`
- 渲染内容（先用 `<pre>`，后续可接语法高亮）
- 处理加载态、错误态、二进制提示
- 提供“加载更多”按钮（当 `truncated=true`）

### C. 新增容器布局组件（可选）

根据当前页面结构（聊天主区 + 侧栏），建议：
- 将 `FileExplorer + FileViewer` 组成 `WorkspacePanel`
- 左树右内容（或上下）分栏

### D. Tool 联动增强（可选增强）

在 `ToolDisplay.tsx` 对 `Read` 工具结果增加“在文件查看器中打开”按钮事件（通过全局 store 或回调）。

## 3.3 状态管理（store）

建议在 `chat-store.ts` 新增 viewer 状态：

- `selectedFilePath`
- `fileContentCache: Record<string, FileContentResponse>`
- `openFile(path)`
- `loadFile(path)`

优点：
- 减少重复请求
- 支持从不同入口打开同一文件（文件树、工具结果、后续搜索结果）

---

## 4. 后端需要新增的 API 端点

在 `src/nimbus/server/api.py` 新增：

### 4.1 获取文件内容

`GET /sessions/{session_id}/file-content`

Query 参数：
- `path`（相对 workspace 路径，必填）
- `offset`（默认 0）
- `limit`（默认 10000，最大比如 200000）

返回示例：

```json
{
  "path": "src/main.py",
  "size": 52340,
  "offset": 0,
  "limit": 10000,
  "content": "...",
  "truncated": true,
  "next_offset": 10000,
  "encoding": "utf-8",
  "mime_type": "text/x-python",
  "is_binary": false,
  "last_modified": "2026-02-10T10:00:00"
}
```

### 4.2 安全与边界处理（必须）

- 路径必须在 session workspace 内（`relative_to(root)` 校验）
- 仅允许读取普通文件，不允许目录
- 对隐藏文件策略可配置（与 list_files 一致）
- 防止超大读取：limit 上限
- 编码容错：优先 utf-8，失败回退替代字符或标记二进制
- 对二进制文件：返回 `is_binary=true` 且不直接下发大块乱码

### 4.3 可选端点

- `GET /sessions/{session_id}/file-meta`（若想拆分元信息）
- `GET /sessions/{session_id}/file-raw`（下载流）

MVP 阶段建议先只做 `file-content`。

---

## 5. 数据流设计

1. 用户在 `FileExplorer` 点击文件
2. 前端更新 `selectedFilePath`
3. `FileViewer` 监听到 path 变化，调用 `getFileContent(sessionId, path, 0, chunkSize)`
4. 后端校验 session 与路径安全后读取内容并返回
5. 前端展示内容
6. 若 `truncated=true`，用户点击“加载更多”
7. 前端以 `next_offset` 继续请求并 append

联动流（可选）：
- 用户在聊天 tool 结果（`Read`）中看到路径 -> 点击“在文件查看器打开” -> 直接触发第 2 步

---

## 6. 技术难点和建议

### 6.1 大文件性能

难点：一次性读取导致前后端卡顿。
建议：
- 后端分段返回（offset/limit）
- 前端虚拟滚动可后续再做，MVP 用“加载更多”

### 6.2 编码与二进制识别

难点：非 utf-8、图片/可执行文件显示异常。
建议：
- 后端先做二进制检测（如空字节比例/`mimetypes`）
- 非文本返回提示，不渲染全文

### 6.3 安全性

难点：路径穿越（`../`）、软链接越界。
建议：
- 统一 `resolve()` 后做 `relative_to(root)`
- 如需更严谨，增加 symlink 策略（MVP 可先禁止跨 root）

### 6.4 与现有 Read 工具职责区分

难点：工具输出和 UI 浏览器重复。
建议：
- 工具 `Read` 保持“Agent 工作流用途”
- 新 API 面向“用户主动浏览文件”
- 两者通过路径联动而非复用同一协议

---

## 7. 实现优先级建议

## P0（必须，先上线最小可用）

1. 后端新增 `/sessions/{session_id}/file-content`
2. 前端新增 `getFileContent` API 方法
3. `FileExplorer` 支持文件点击选中
4. 新增 `FileViewer` 展示文本内容 + 错误态

## P1（增强体验）

1. 分段加载（加载更多）
2. 简单语法高亮（按扩展名）
3. 内容缓存（store）
4. Tool 结果跳转打开文件

## P2（高级能力）

1. 全文搜索/文件内搜索
2. 面包屑与多标签页
3. 图片/Markdown/JSON 专用预览器
4. 与编辑能力（Edit/Write）深度整合，支持“查看-编辑-diff”闭环

---

## 8. 建议修改文件清单（预估）

前端：
- `web-ui/src/lib/api/index.ts`（导出新增 API）
- `web-ui/src/lib/api/sessions.ts` 或新增 `web-ui/src/lib/api/file-viewer.ts`
- `web-ui/src/components/chat/FileExplorer.tsx`（支持文件选中回调）
- 新增 `web-ui/src/components/chat/FileViewer.tsx`
- 可能改动页面组合组件（如 `app/page.tsx` / chat 主容器）
- （可选）`web-ui/src/components/chat/tools/ToolDisplay.tsx` 联动入口
- （可选）`web-ui/src/stores/chat-store.ts` 增加 viewer 状态

后端：
- `src/nimbus/server/api.py`（新增 file-content 路由）
- `src/nimbus/server/models.py`（新增响应模型，若采用 response_model）

---

以上方案基于当前 Nimbus 代码结构，优先复用现有 `session + workspace + files` 架构，以最小侵入方式落地“文件查看”能力。