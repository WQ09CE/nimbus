# Nimbus Web UI - 使用指南

## 🎯 完成内容

今晚已完成：
1. ✅ 将 nimbus-web 移植到 `web-ui/` 目录
2. ✅ API 对接到 Nimbus `/api/v1/*` 端点
3. ✅ UI 改造为 Pi TUI 风格
4. ✅ 实现流式对话功能
5. ✅ Tool call 可视化

## 🎨 设计特点

参考 Pi TUI 的设计理念：

- 🌑 **深色终端主题** - 黑色背景 + 等宽字体
- ⚡ **流式打字效果** - 实时显示 LLM 响应
- 🔧 **Tool 可折叠** - 默认隐藏，点击展开
- 🎯 **单栏布局** - 专注对话，无干扰
- 💬 **极简输入框** - 底部固定，Shift+Enter 换行

## 🚀 快速启动

### 方式 1: 使用启动脚本（推荐）

```bash
cd ~/sourcecode/agent/agent-framework/nimbus
./run-web.sh
```

这会同时启动：
- Nimbus Server @ :4096
- Web UI @ :3030

### 方式 2: 分别启动

**终端 1 - 启动 Nimbus Server:**
```bash
cd ~/sourcecode/agent/agent-framework/nimbus
nimbus serve --port 4096
```

**终端 2 - 启动 Web UI:**
```bash
cd ~/sourcecode/agent/agent-framework/nimbus/web-ui
npm run dev
```

### 访问

打开浏览器访问: **http://localhost:3030**

## 📦 文件结构

```
nimbus/
├── web-ui/                    # Web 前端
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx       # 主页面
│   │   │   └── layout.tsx     # 全局布局
│   │   ├── components/
│   │   │   └── chat/
│   │   │       ├── ChatMessage.tsx   # 消息组件
│   │   │       └── ChatInput.tsx     # 输入框
│   │   ├── lib/
│   │   │   └── api/
│   │   │       ├── client.ts   # API 客户端
│   │   │       ├── sessions.ts # Session API
│   │   │       └── chat.ts     # Chat API
│   │   └── stores/
│   │       └── chat-store.ts   # Zustand 状态管理
│   ├── .env.local              # 配置 (API_URL)
│   └── package.json
└── run-web.sh                  # 一键启动脚本
```

## 🔌 API 端点

Web UI 使用以下 Nimbus API：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/health` | GET | 健康检查 |
| `/api/v1/sessions` | POST | 创建会话 |
| `/api/v1/sessions` | GET | 列出会话 |
| `/api/v1/sessions/{id}` | GET | 获取会话详情 |
| `/api/v1/sessions/{id}` | DELETE | 删除会话 |
| `/api/v1/sessions/{id}/chat` | POST | 发送消息（SSE 流） |

## 🎬 SSE 事件类型

Nimbus 通过 SSE 流式推送事件：

```
connected          # 连接建立
message_start      # 消息开始
planning           # 规划中
dag_created        # DAG 创建
task_start         # 任务开始
tool_call          # 工具调用
tool_result        # 工具结果
task_done          # 任务完成
task_failed        # 任务失败
permission_request # 权限请求
dag_complete       # DAG 完成
message            # 文本内容
error              # 错误
heartbeat          # 心跳
```

## 🛠️ 开发

### 安装依赖

```bash
cd web-ui
npm install
```

### 开发模式

```bash
npm run dev
```

### 生产构建

```bash
npm run build
npm run start
```

### 代码检查

```bash
npm run lint
```

## 🎯 功能演示

### 1. 创建新会话
- 打开 Web UI 自动创建会话
- 点击右上角 "New Session" 创建新会话

### 2. 发送消息
- 在底部输入框输入消息
- 按 Enter 发送
- 按 Shift+Enter 换行

### 3. 查看 Tool Calls
- 当 Assistant 调用工具时，会显示折叠的 tool calls
- 点击 "▶ N tool calls" 展开查看
- 显示工具名称、参数和结果

### 4. 流式响应
- 消息实时逐字显示
- 有蓝色脉冲光标指示正在输出
- Tool calls 也会实时更新

## 🔧 配置

编辑 `web-ui/.env.local`:

```env
# Nimbus Server API URL
NEXT_PUBLIC_API_URL=http://localhost:4096
```

## 🐛 故障排查

### Server 启动失败

检查依赖版本：
```bash
pip install "pydantic>=2.0" "anyio>=4.5" --upgrade
```

### Web UI 连接失败

1. 检查 Server 是否运行：
```bash
curl http://localhost:4096/api/v1/health
```

2. 检查 .env.local 配置是否正确

3. 清除 Next.js 缓存：
```bash
rm -rf .next
npm run dev
```

### 流式响应不显示

打开浏览器开发者工具 (F12) → Network 标签，检查：
- SSE 连接是否建立
- 事件是否正常接收

## 📊 与 Pi TUI 的对比

| 特性 | Pi TUI | Nimbus Web UI |
|------|--------|---------------|
| **界面** | 终端 | 浏览器 |
| **部署** | 本地命令行 | Web 服务 |
| **样式** | Blessed + Ink | Next.js + Tailwind |
| **LLM Core** | Pi AI SDK | Pi AI SDK (通过 Nimbus) |
| **Agent 能力** | Pi Extension | Nimbus Core (MMU + DAG) |
| **多用户** | ❌ | ✅ (通过 Session) |
| **远程访问** | ❌ | ✅ (通过 HTTP) |

## 🎉 下一步

可以继续改进的方向：

1. **历史记录** - 显示之前的对话历史
2. **会话列表** - 左侧 sidebar 管理多个会话
3. **Markdown 渲染** - 支持代码高亮、表格等
4. **快捷键** - Cmd+K 快速搜索、Cmd+N 新会话等
5. **主题切换** - 亮色/暗色主题
6. **语音输入** - 支持语音转文字
7. **导出对话** - 导出为 Markdown/PDF

## 💡 技术栈

- **Framework**: Next.js 14 (App Router)
- **State**: Zustand
- **Styling**: Tailwind CSS
- **Transport**: Server-Sent Events (SSE)
- **Backend**: Nimbus FastAPI
- **LLM**: Pi AI SDK

---

**Happy Chatting! 🐒☁️**
