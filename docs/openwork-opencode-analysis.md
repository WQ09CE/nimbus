# OpenWork / OpenCode 架构分析

> **分析目标**: 理解 OpenWork 如何基于 OpenCode 构建，以及与你的 Agent Framework 设计的对比

---

## 1. 整体定位

```
┌─────────────────────────────────────────────────────────────────┐
│                         OpenWork                                │
│                   (Tauri 桌面应用 / GUI)                        │
│  - 工作区管理                                                   │
│  - Skill 管理器                                                 │
│  - 模板系统                                                     │
│  - 权限审批 UI                                                  │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP + SSE
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                         OpenCode                                │
│                   (AI Coding Agent 内核)                        │
│  - Session 管理                                                 │
│  - Tool 执行 (bash, file, search...)                           │
│  - LLM 调用 (多 Provider)                                       │
│  - MCP 协议支持                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**关键洞察**：OpenWork 不是一个 Agent Framework，它是 OpenCode 的 **GUI 封装层**。真正的 Agent 逻辑在 OpenCode 里。

---

## 2. OpenCode 核心架构

### 2.1 客户端-服务器分离

```
┌─────────────┐          ┌─────────────────────────────────┐
│   Client    │   HTTP   │           Server                │
│  (TUI/GUI)  │ ◄──────► │  opencode serve --port 8080     │
└─────────────┘   SSE    └─────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
       ┌───────────┐   ┌───────────┐   ┌───────────┐
       │  Session  │   │   Tools   │   │    LLM    │
       │  Manager  │   │  Executor │   │  Provider │
       └───────────┘   └───────────┘   └───────────┘
```

**设计亮点**：
- TUI 和 Server 是分离的，TUI 只是一个客户端
- 任何客户端（CLI、GUI、IDE 插件）都可以通过 HTTP API 接入
- SSE 实现实时事件推送

### 2.2 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| **Server** | Bun (TypeScript) | HTTP Server + 业务逻辑 |
| **TUI** | Go (Bubble Tea) | 终端 UI |
| **SDK** | TypeScript | 由 OpenAPI 自动生成 |
| **存储** | SQLite | Session、对话历史 |
| **工具** | MCP Protocol | 标准化工具接口 |

### 2.3 核心 API

```typescript
// OpenCode SDK 主要接口
const client = createOpencodeClient({ baseUrl: "http://localhost:8080" })

// Session 管理
await client.session.create({ body: { name: "fix-bug" } })
await client.session.list()
await client.session.get({ path: { id: "xxx" } })

// 发送 Prompt
await client.session.chat({ 
  path: { id: "xxx" },
  body: { content: "修复这个 bug" }
})

// 订阅事件 (SSE)
const events = client.event.subscribe({ path: { sessionId: "xxx" } })
for await (const event of events) {
  // message | tool_call | tool_result | permission_request | ...
}

// 权限响应
await client.permission.respond({
  path: { id: "xxx" },
  body: { decision: "allow_once" }  // allow_once | allow_always | deny
})
```

---

## 3. OpenWork 做了什么

### 3.1 架构层次

```
┌─────────────────────────────────────────────────────────────────┐
│                      OpenWork (Tauri App)                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    React Frontend                         │  │
│  │  - WorkspacePicker    - SessionView    - SkillManager    │  │
│  │  - TemplateManager    - PermissionDialog                 │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                  │
│                    Tauri IPC │                                  │
│                              ▼                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    Rust Backend                           │  │
│  │  - 启动/管理 opencode serve 进程                          │  │
│  │  - 文件系统访问                                           │  │
│  │  - 本地配置存储                                           │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP (localhost:port)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OpenCode Server                              │
│              (子进程，由 Tauri 管理)                            │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 OpenWork 增加的功能

| 功能 | 说明 | OpenCode 原生支持？ |
|------|------|-------------------|
| **Workspace 管理** | 切换不同项目目录 | ❌ 需要重启 |
| **Skill 管理器** | 安装/管理 .opencode/skill | ⚠️ 需要手动编辑 |
| **模板系统** | 保存/复用常用 Prompt | ❌ |
| **权限审批 UI** | 可视化权限请求 | ⚠️ TUI 有，但不直观 |
| **执行计划可视化** | 展示 TODO 列表为时间线 | ⚠️ TUI 有 |
| **Host/Client 模式** | 本地运行或连接远程 | ✅ |

### 3.3 Skill 机制

```
.opencode/
├── skill/
│   ├── data-analysis/
│   │   └── SKILL.md       # Skill 描述和 Prompt
│   ├── report-writer/
│   │   └── SKILL.md
│   └── ...
├── agent/
│   └── custom-agent.md    # 自定义 Agent
├── command/
│   └── my-command.md      # 自定义命令
└── opencode.json          # 配置文件
```

**Skill 本质**：就是预定义的 Prompt 模板 + 工具配置，不是代码插件。

---

## 4. 与你的 Agent Framework 对比

### 4.1 架构对比

| 维度 | 你的设计 | OpenCode | 差异分析 |
|------|---------|----------|---------|
| **核心模式** | DAG + Planner | ReAct Loop | OpenCode 更简单 |
| **Memory** | 分层压缩 | SQLite Session | 你的更复杂但更灵活 |
| **任务规划** | 预先规划 DAG | 每步 LLM 决策 | OpenCode 更动态 |
| **工具系统** | SkillRegistry | MCP Protocol | MCP 是行业标准 |
| **持久化** | SurrealDB | SQLite | OpenCode 更简单 |
| **并行执行** | asyncio DAG | 无 (串行) | 你的更强 |

### 4.2 OpenCode 的简化之处

OpenCode 选择了一条**极简路线**：

```python
# OpenCode 的核心循环（伪代码）
while not finished:
    # 1. 获取上下文（直接从 SQLite 读历史）
    context = session.get_messages()
    
    # 2. 调用 LLM（AI SDK 统一接口）
    response = await llm.chat(context + tools)
    
    # 3. 如果有 tool_call，执行
    if response.tool_calls:
        for call in response.tool_calls:
            result = await execute_tool(call)
            session.add_message(tool_result=result)
    
    # 4. 如果是 finish，结束
    if response.finish:
        break
```

**没有**：
- 没有预先规划（Planner）
- 没有 DAG 依赖分析
- 没有并行执行
- 没有 Re-planning 机制
- 没有 Memory 压缩

**有的**：
- 简单直接的 ReAct Loop
- MCP 标准工具协议
- 良好的 Client-Server 分离
- 完善的 Session 管理

### 4.3 你可以从 OpenCode 学到什么

| 学习点 | 说明 | 建议 |
|--------|------|------|
| **Client-Server 分离** | Agent 逻辑和 UI 完全解耦 | ✅ 采纳 |
| **MCP 协议** | 工具定义的行业标准 | ✅ 考虑支持 |
| **SSE 事件流** | 实时推送执行状态 | ✅ 采纳 |
| **权限系统** | ask/allow_once/allow_always/deny | ✅ 采纳 |
| **SQLite 存储** | 轻量级持久化 | ⚠️ MVP 可用，后期换 |

---

## 5. 如果你要做类似的事情

### 5.1 两种路线

**路线 A：基于 OpenCode 做上层应用（像 OpenWork）**

```
优点：
- 省去 Agent 内核开发
- OpenCode 生态成熟
- 可以专注 UI 和特化

缺点：
- 受限于 OpenCode 的能力
- 无法深度定制 Agent 行为
- 依赖第三方项目
```

**路线 B：自己做 Agent 内核（你原来的设计）**

```
优点：
- 完全控制
- 可以实现 DAG、并行、Re-planning
- 可以深度优化

缺点：
- 工作量大
- 需要重复造轮子（工具系统、Session 管理等）
```

### 5.2 我的建议：混合路线

```
┌─────────────────────────────────────────────────────────────────┐
│                      Your Agent Core                            │
│  - 保留：Memory 分层压缩（比 OpenCode 强）                      │
│  - 保留：DAG Planner（复杂任务需要）                            │
│  - 借鉴：MCP 工具协议（不用自己定义）                           │
│  - 借鉴：Client-Server 分离 + SSE                               │
│  - 借鉴：Permission 系统                                        │
└─────────────────────────────────────────────────────────────────┘
```

具体来说：

| 模块 | 建议 | 理由 |
|------|------|------|
| **工具系统** | 用 MCP 协议 | 行业标准，生态好 |
| **Session** | 参考 OpenCode | 成熟方案 |
| **Memory** | 用你自己的设计 | OpenCode 的太简单 |
| **Planner** | 用你自己的设计 | OpenCode 没有 |
| **Runtime** | 用你自己的设计 | OpenCode 不支持并行 |

---

## 6. 快速验证建议

如果你想快速验证你的 Agent Core 能力，有两个选择：

### 选择 1：Fork OpenWork，替换内核

```
OpenWork (保留 UI)
    │
    ├── 原来：连接 OpenCode Server
    │
    └── 改成：连接你的 Agent Server（实现相同的 API）
```

**工作量**：中等（需要实现 OpenCode 兼容的 API）

### 选择 2：从零做一个简化版

```
你的 Code Agent MVP (5-7 天)
    │
    ├── ReAct Loop（先不用 DAG）
    ├── MCP 工具支持
    ├── 简单 CLI
    │
    └── 验证后再加：DAG Planner、并行、Memory 压缩
```

**工作量**：较小（专注核心）

---

## 7. 总结

| 项目 | 定位 | 核心价值 |
|------|------|---------|
| **OpenCode** | AI Coding Agent 内核 | 简洁的 ReAct + MCP + 良好工程 |
| **OpenWork** | OpenCode 的 GUI 封装 | 非技术用户友好的交互 |
| **你的 Framework** | 更强大的 Agent 内核 | DAG + 并行 + Memory 压缩 |

**我的建议**：

1. **学习 OpenCode** 的 Client-Server 分离和 MCP 协议
2. **保留你的核心设计**（Memory、Planner、Runtime）
3. **先做 MVP 验证**，不要一开始就追求完整
4. **考虑兼容 OpenCode API**，这样可以复用 OpenWork 的 UI

---

## 附录：OpenCode 关键源码位置

如果你想深入研究 OpenCode：

```
opencode/
├── packages/
│   ├── core/              # Agent 核心逻辑
│   │   ├── agent.ts       # Agent Loop
│   │   ├── session.ts     # Session 管理
│   │   └── tool.ts        # 工具定义
│   ├── server/            # HTTP Server
│   │   ├── api.ts         # API 路由
│   │   └── sse.ts         # SSE 事件
│   └── tui/               # Go TUI (单独仓库)
├── tools/                 # 内置工具
│   ├── bash.ts
│   ├── file.ts
│   └── search.ts
└── opencode.json          # 配置 Schema
```

---

*文档结束*
