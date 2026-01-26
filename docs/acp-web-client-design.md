# ACP Web Client Design (v2)

> **Status**: Proposed
> **Version**: 2.0
> **Date**: 2025-01-24
> **Author**: @mind (Architect)

---

## Summary

设计一个基于 **Next.js + assistant-ui + Shadcn/ui** 的 Web UI 客户端，通过 **Vercel AI SDK Data Protocol** 与 Nimbus Server 通信。相比 v1 的 WebSocket Bridge 方案，本设计直接在 Nimbus FastAPI 中新增 `/api/chat` 端点，返回 AI SDK 格式的流式响应，大幅简化架构。

---

## 1. Thinking Process

### 1.1 问题理解

**核心问题**: 如何以最低成本构建一个现代化的 AI Agent Web UI？

**约束条件**:
1. Nimbus 已有 FastAPI 服务器基础设施
2. 需要流式响应以提供良好的用户体验
3. 需要支持工具调用的可视化展示
4. 支持响应式布局和深色模式

**v1 方案痛点**:
- WebSocket Bridge 增加了部署复杂度
- 需要额外维护 Node.js 中间层
- 调试不便（WebSocket 协议在 DevTools 中不如 HTTP 直观）

### 1.2 方案探索

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A: WebSocket Bridge (v1) | 独立 Bridge 服务转换 stdio | 通用性高，支持任意 ACP Agent | 需要额外服务、复杂度高 |
| B: Vercel AI SDK (v2) | Nimbus 直接返回 AI SDK 格式流 | 简洁、标准化、生态成熟 | 仅支持实现该协议的 Agent |
| C: 混合模式 | 同时支持两种协议 | 兼容性好 | 维护成本高 |

### 1.3 决策推导

基于以下理由，选择 **方案 B: Vercel AI SDK**：

1. **开发成本大幅降低**: 不需要 Bridge 中间层，直接在 Nimbus 中添加端点
2. **生态成熟**: Vercel AI SDK 是 AI 应用的事实标准，前端组件库丰富
3. **调试方便**: HTTP Streaming 在 Chrome DevTools 中可直接查看数据流
4. **用户体验开箱即用**: assistant-ui 提供响应式布局、深色模式、流式渲染

---

## 2. Design

### 2.1 架构概述

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 ACP Web Client (Next.js + assistant-ui)                      │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                          UI Components                                │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐ │   │
│  │  │ Thread      │ │ FileTree    │ │ EditorPanel │ │ TerminalPanel   │ │   │
│  │  │(assistant-ui)│ │(shadcn/ui) │ │ (Monaco)    │ │ (xterm.js)      │ │   │
│  │  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └────────┬────────┘ │   │
│  └─────────┼───────────────┼───────────────┼────────────────┼───────────┘   │
│            │               │               │                │               │
│  ┌─────────▼───────────────▼───────────────▼────────────────▼───────────┐   │
│  │                       useChat Hook (Vercel AI SDK)                    │   │
│  │  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐               │   │
│  │  │ messages      │ │ toolCalls     │ │ isLoading     │               │   │
│  │  └───────────────┘ └───────────────┘ └───────────────┘               │   │
│  └───────────────────────────────┬──────────────────────────────────────┘   │
└──────────────────────────────────┼──────────────────────────────────────────┘
                                   │ HTTP Streaming
                                   │ (Vercel AI SDK Data Protocol)
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Nimbus Server (FastAPI)                               │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         API Routes                                     │  │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────────┐  │  │
│  │  │ /api/chat       │ │ /sessions       │ │ /permissions            │  │  │
│  │  │ (AI SDK format) │ │ (existing)      │ │ (existing)              │  │  │
│  │  └────────┬────────┘ └─────────────────┘ └─────────────────────────┘  │  │
│  └───────────┼───────────────────────────────────────────────────────────┘  │
│              │                                                               │
│  ┌───────────▼───────────────────────────────────────────────────────────┐  │
│  │                         Nimbus Agent                                   │  │
│  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────────┐  │  │
│  │  │ DAG Planner     │ │ Skill Executor  │ │ Tiered Memory           │  │  │
│  │  └─────────────────┘ └─────────────────┘ └─────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件

#### 2.2.1 前端项目结构

```
acp-web-client/
├── src/
│   ├── app/
│   │   ├── layout.tsx              # 根布局（主题、字体）
│   │   ├── page.tsx                # 主页面
│   │   └── api/
│   │       └── chat/
│   │           └── route.ts        # 代理到 Nimbus（可选）
│   ├── components/
│   │   ├── chat/
│   │   │   ├── ChatThread.tsx      # 对话线程（assistant-ui）
│   │   │   ├── ToolCallCard.tsx    # 工具调用卡片
│   │   │   ├── DiffView.tsx        # Diff 视图
│   │   │   └── TerminalOutput.tsx  # 终端输出
│   │   ├── sidebar/
│   │   │   ├── SessionList.tsx     # 会话列表
│   │   │   └── FileTree.tsx        # 文件树
│   │   └── layout/
│   │       ├── MainLayout.tsx      # 主布局
│   │       └── Header.tsx          # 顶栏
│   ├── lib/
│   │   ├── nimbus-client.ts        # Nimbus API 客户端
│   │   └── utils.ts                # 工具函数
│   └── hooks/
│       ├── useNimbusChat.ts        # 封装 useChat
│       └── useSessions.ts          # 会话管理
├── components.json                  # shadcn/ui 配置
├── tailwind.config.ts
├── next.config.js
└── package.json
```

#### 2.2.2 后端 API 端点（新增）

在 Nimbus Server 中新增 `/api/chat` 端点：

```
nimbus/src/nimbus/server/
├── __init__.py
├── api.py                 # 现有 API 路由
├── api_ai_sdk.py          # 新增：AI SDK 格式端点
├── models.py
├── session.py
└── sse.py
```

### 2.3 Vercel AI SDK Data Protocol

Vercel AI SDK 使用简洁的文本协议进行流式传输：

```
0:"文本内容"\n                                    # 普通文本
9:{"toolCallId":"tc_1","toolName":"read_file","args":{"path":"..."}}\n  # 工具调用开始
a:{"toolCallId":"tc_1","result":"文件内容..."}\n   # 工具调用结果
e:{"finishReason":"stop","usage":{"promptTokens":10,"completionTokens":20}}\n  # 完成
d:{"finishReason":"stop"}\n                       # 流结束
```

**协议字符含义**:

| 字符 | 类型 | 描述 |
|------|------|------|
| `0` | text-delta | 文本增量 |
| `2` | data | 自定义数据（如 DAG 状态） |
| `9` | tool-call | 工具调用开始 |
| `a` | tool-result | 工具调用结果 |
| `b` | tool-call-streaming-start | 工具调用流式开始 |
| `c` | tool-call-delta | 工具调用增量 |
| `e` | finish | 完成信息 |
| `d` | finish_step | 步骤完成 |

### 2.4 Nimbus 事件到 AI SDK 格式映射

| Nimbus 事件 | AI SDK 格式 | 说明 |
|-------------|-------------|------|
| `planning` | `2:{"type":"planning","status":"..."}` | 自定义数据 |
| `dag_created` | `2:{"type":"dag","dagId":"...","nodes":[...]}` | DAG 结构 |
| `task_start` | `9:{"toolCallId":"...","toolName":"...","args":{...}}` | 工具调用开始 |
| `task_done` | `a:{"toolCallId":"...","result":"..."}` | 工具调用结果 |
| `task_failed` | `a:{"toolCallId":"...","result":"Error: ..."}` | 错误结果 |
| `direct` / `complete` | `0:"文本内容"` | 文本输出 |
| `error` | `3:"错误信息"` | 错误 |

### 2.5 数据流

```
User                 WebClient              Nimbus Server
  │                     │                        │
  │── input message ───>│                        │
  │                     │── POST /api/chat ─────>│
  │                     │    (messages array)    │
  │                     │                        │
  │                     │<── 2:{"type":"planning",...}
  │<── show planning ───│                        │
  │                     │                        │
  │                     │<── 9:{"toolCallId":"tc_1","toolName":"read_file",...}
  │<── show tool call ──│                        │
  │                     │                        │
  │                     │<── a:{"toolCallId":"tc_1","result":"..."}
  │<── show result ─────│                        │
  │                     │                        │
  │                     │<── 0:"Here is the analysis..."
  │<── stream text ─────│                        │
  │                     │                        │
  │                     │<── e:{"finishReason":"stop",...}
  │<── complete ────────│                        │
```

### 2.6 技术栈选择

#### 2.6.1 前端技术栈

| 类别 | 选择 | 理由 |
|------|------|------|
| **框架** | Next.js 14+ (App Router) | 现代 React 框架、流式支持 |
| **AI Chat** | assistant-ui + useChat | Vercel AI SDK 生态、开箱即用 |
| **UI 组件** | shadcn/ui + Radix | 可定制、无样式锁定 |
| **样式** | Tailwind CSS | utility-first、响应式 |
| **代码编辑** | Monaco Editor | VS Code 同款、Diff 支持 |
| **终端** | xterm.js | 工业标准终端模拟 |
| **状态管理** | React Context + useChat | 简单场景无需 Zustand |

#### 2.6.2 为什么选择 assistant-ui + useChat

| 特性 | assistant-ui | 自己实现 |
|------|--------------|----------|
| 流式消息 | 内置支持（useChat） | 需要自己处理 |
| 工具调用 UI | 内置 ToolCall 组件 | 需要设计实现 |
| Markdown 渲染 | 内置支持 | 需要集成库 |
| 代码高亮 | 内置 Shiki | 需要集成库 |
| 响应式布局 | 开箱即用 | 需要自己实现 |
| 深色模式 | 开箱即用 | 需要自己实现 |
| 维护成本 | 低 | 高 |

### 2.7 前端代码示例

#### 2.7.1 Chat 页面

```tsx
// src/app/page.tsx
"use client";

import { Thread } from "@assistant-ui/react";
import { useChat } from "ai/react";
import { ToolCallCard } from "@/components/chat/ToolCallCard";
import { DiffView } from "@/components/chat/DiffView";
import { TerminalOutput } from "@/components/chat/TerminalOutput";

export default function ChatPage() {
  const chat = useChat({
    api: process.env.NEXT_PUBLIC_NIMBUS_URL + "/api/chat",
    // 或者使用 Next.js API 代理: api: "/api/chat"
  });

  return (
    <div className="h-screen flex">
      <aside className="w-64 border-r">
        {/* Session List */}
      </aside>

      <main className="flex-1">
        <Thread
          messages={chat.messages}
          isLoading={chat.isLoading}
          onSubmit={(message) => chat.append({ role: "user", content: message })}
          assistantMessage={{
            components: {
              ToolCall: ({ toolName, args, result, status }) => {
                // 根据工具类型渲染不同组件
                if (toolName === "edit_file" && result) {
                  return <DiffView diff={result} />;
                }
                if (toolName === "bash" && result) {
                  return <TerminalOutput output={result} />;
                }
                return (
                  <ToolCallCard
                    toolName={toolName}
                    args={args}
                    result={result}
                    status={status}
                  />
                );
              }
            }
          }}
        />
      </main>
    </div>
  );
}
```

#### 2.7.2 工具调用卡片

```tsx
// src/components/chat/ToolCallCard.tsx
import { Card, CardHeader, CardContent } from "@/components/ui/card";
import { Loader2, Check, X } from "lucide-react";

interface ToolCallCardProps {
  toolName: string;
  args: Record<string, unknown>;
  result?: string;
  status: "pending" | "running" | "completed" | "failed";
}

export function ToolCallCard({ toolName, args, result, status }: ToolCallCardProps) {
  return (
    <Card className="my-2">
      <CardHeader className="flex flex-row items-center gap-2 py-2">
        {status === "running" && <Loader2 className="w-4 h-4 animate-spin" />}
        {status === "completed" && <Check className="w-4 h-4 text-green-500" />}
        {status === "failed" && <X className="w-4 h-4 text-red-500" />}
        <span className="font-mono text-sm">{toolName}</span>
      </CardHeader>

      {args && Object.keys(args).length > 0 && (
        <CardContent className="py-2">
          <pre className="text-xs bg-muted p-2 rounded">
            {JSON.stringify(args, null, 2)}
          </pre>
        </CardContent>
      )}

      {result && (
        <CardContent className="py-2 border-t">
          <pre className="text-xs whitespace-pre-wrap max-h-40 overflow-auto">
            {result}
          </pre>
        </CardContent>
      )}
    </Card>
  );
}
```

### 2.8 后端 API 实现

#### 2.8.1 新增 API 端点

```python
# nimbus/src/nimbus/server/api_ai_sdk.py
"""Vercel AI SDK compatible API endpoint."""

import json
from typing import AsyncIterator
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter()


class Message(BaseModel):
    """Chat message."""
    role: str  # user | assistant | system
    content: str
    toolCallId: Optional[str] = None
    name: Optional[str] = None  # tool name for tool results


class ChatRequest(BaseModel):
    """AI SDK chat request format."""
    messages: List[Message]
    sessionId: Optional[str] = None


def format_text(text: str) -> str:
    """Format text chunk for AI SDK protocol."""
    # Escape special characters in JSON string
    escaped = json.dumps(text)
    return f'0:{escaped}\n'


def format_tool_call(tool_call_id: str, tool_name: str, args: dict) -> str:
    """Format tool call for AI SDK protocol."""
    data = {
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "args": args,
    }
    return f'9:{json.dumps(data)}\n'


def format_tool_result(tool_call_id: str, result: str) -> str:
    """Format tool result for AI SDK protocol."""
    data = {
        "toolCallId": tool_call_id,
        "result": result,
    }
    return f'a:{json.dumps(data)}\n'


def format_data(data: dict) -> str:
    """Format custom data for AI SDK protocol."""
    return f'2:{json.dumps([data])}\n'


def format_finish(finish_reason: str = "stop") -> str:
    """Format finish message for AI SDK protocol."""
    data = {
        "finishReason": finish_reason,
    }
    return f'e:{json.dumps(data)}\nd:{json.dumps({"finishReason": finish_reason})}\n'


def format_error(message: str) -> str:
    """Format error for AI SDK protocol."""
    return f'3:{json.dumps(message)}\n'


@router.post("/api/chat")
async def chat(
    data: ChatRequest,
    request: Request,
):
    """
    AI SDK compatible chat endpoint.

    Returns streaming response in Vercel AI SDK Data Protocol format.
    """
    session_manager = request.app.state.session_manager
    storage = request.app.state.storage

    # Get or create session
    session_id = data.sessionId
    if not session_id:
        # Create a new session
        session = await session_manager.create_session()
        session_id = session["id"]
    else:
        session = await session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

    # Extract the latest user message
    user_message = None
    for msg in reversed(data.messages):
        if msg.role == "user":
            user_message = msg.content
            break

    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found")

    async def stream_generator() -> AsyncIterator[str]:
        """Generate AI SDK formatted stream."""
        try:
            # Get or create agent
            agent = await session_manager.get_or_create_agent(session_id)

            tool_call_counter = 0

            async for status in agent.run_stream(user_message):
                status_type = status.get("type", "unknown")

                if status_type == "planning":
                    # Send as custom data
                    yield format_data({
                        "type": "planning",
                        "status": status.get("content", "creating_plan"),
                    })

                elif status_type == "dag_created":
                    # Send DAG info as custom data
                    yield format_data({
                        "type": "dag",
                        "dagId": status.get("dag_id", ""),
                        "goal": status.get("goal", ""),
                        "totalTasks": status.get("total_tasks", 0),
                    })

                elif status_type == "task_start":
                    # Map to tool call
                    tool_call_counter += 1
                    task_id = status.get("task_id", f"tc_{tool_call_counter}")
                    skill = status.get("skill", "unknown")
                    params = status.get("params", {})
                    yield format_tool_call(task_id, skill, params)

                elif status_type == "task_done":
                    # Map to tool result
                    task_id = status.get("task_id", "")
                    result = str(status.get("result", ""))[:2000]  # Truncate
                    yield format_tool_result(task_id, result)

                elif status_type == "task_failed":
                    # Map to tool result with error
                    task_id = status.get("task_id", "")
                    error = status.get("error", "Unknown error")
                    yield format_tool_result(task_id, f"Error: {error}")

                elif status_type == "direct":
                    # Direct text response
                    content = status.get("content", "")
                    if content:
                        yield format_text(content)

                elif status_type == "complete":
                    # Final response
                    content = status.get("content", "")
                    if content:
                        yield format_text(content)

            # Send finish message
            yield format_finish("stop")

        except Exception as e:
            yield format_error(str(e))
            yield format_finish("error")

    return StreamingResponse(
        stream_generator(),
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # CORS headers for cross-origin requests
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
    )
```

#### 2.8.2 注册路由

```python
# 在 nimbus/src/nimbus/server/app.py 中添加

from .api_ai_sdk import router as ai_sdk_router

def create_app(...):
    app = FastAPI(...)

    # 现有路由
    app.include_router(router, prefix="/api")

    # 新增 AI SDK 路由
    app.include_router(ai_sdk_router)

    return app
```

### 2.9 UI 布局设计

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Header: Logo | Session Name | Theme Toggle | Settings                    │
├──────────────────┬──────────────────────────────────────────────────────┤
│                  │                                                       │
│   Sidebar        │                  Main Area                            │
│   (collapsible)  │                                                       │
│                  │  ┌─────────────────────────────────────────────────┐ │
│  ┌────────────┐  │  │                                                  │ │
│  │ Sessions   │  │  │                Chat Thread                       │ │
│  │ + New      │  │  │                (assistant-ui)                    │ │
│  │ - session1 │  │  │                                                  │ │
│  │ - session2 │  │  │  ┌─────────────────────────────────────────────┐│ │
│  │            │  │  │  │ User: Please analyze config.py              ││ │
│  ├────────────┤  │  │  └─────────────────────────────────────────────┘│ │
│  │            │  │  │                                                  │ │
│  │ Quick      │  │  │  ┌─────────────────────────────────────────────┐│ │
│  │ Actions    │  │  │  │ Assistant:                                  ││ │
│  │            │  │  │  │  ┌─────────────────────────────────────┐   ││ │
│  │ [Explore]  │  │  │  │  │ 🔧 read_file                         │   ││ │
│  │ [Test]     │  │  │  │  │    path: /path/to/config.py         │   ││ │
│  │ [Review]   │  │  │  │  │    ✓ 128 lines read                  │   ││ │
│  │            │  │  │  │  └─────────────────────────────────────┘   ││ │
│  └────────────┘  │  │  │                                             ││ │
│                  │  │  │  Here's my analysis of config.py...          ││ │
│                  │  │  └─────────────────────────────────────────────┘│ │
│                  │  │                                                  │ │
│                  │  │  ┌─────────────────────────────────────────────┐│ │
│                  │  │  │ [Message input]                    [Send]  ││ │
│                  │  │  └─────────────────────────────────────────────┘│ │
│                  │  └─────────────────────────────────────────────────┘ │
└──────────────────┴───────────────────────────────────────────────────────┘
```

---

## 3. Decisions

### Decision 1: 采用 Vercel AI SDK Data Protocol

- **决策**: 使用 Vercel AI SDK Data Protocol 替代 WebSocket
- **理由**:
  - 开发成本大幅降低：无需 Bridge 中间层
  - 生态成熟：Vercel AI SDK 是事实标准
  - 调试方便：HTTP Streaming 在 DevTools 中直接可见
  - 前端开箱即用：assistant-ui 原生支持
- **备选方案**: WebSocket Bridge (v1 方案)
- **风险**: 仅支持实现该协议的 Agent

### Decision 2: 直接在 Nimbus 中添加 /api/chat 端点

- **决策**: 在 Nimbus FastAPI 中新增端点，而非独立服务
- **理由**:
  - 复用现有基础设施（SessionManager、Storage 等）
  - 减少部署复杂度
  - 一个服务支持多种协议（SSE、AI SDK）
- **备选方案**: 独立的 AI SDK 适配服务
- **风险**: Nimbus 服务职责增加

### Decision 3: 使用 Next.js App Router

- **决策**: 前端使用 Next.js 14+ App Router
- **理由**:
  - 服务端组件支持
  - 流式渲染原生支持
  - 可选的 API 路由代理
  - 现代 React 最佳实践
- **备选方案**: Vite + React
- **风险**: 学习曲线略高

### Decision 4: assistant-ui 作为核心 Chat 组件

- **决策**: 使用 assistant-ui 的 Thread 组件
- **理由**:
  - 专为 Vercel AI SDK 设计
  - 工具调用 UI 内置支持
  - 响应式 + 深色模式开箱即用
  - 高度可定制
- **备选方案**: 自己实现 Chat UI
- **风险**: 依赖第三方库

---

## 4. Tradeoffs

1. **简洁性 vs 通用性**
   - 选择 Vercel AI SDK，放弃 WebSocket Bridge 的通用性
   - 但获得了更简洁的架构和更低的开发成本
   - 如果未来需要支持其他 Agent，可以让它们实现 AI SDK 协议

2. **前端框架选择**
   - 选择 Next.js 而非 Vite，增加了一些复杂度
   - 但获得了更好的流式支持和服务端渲染能力
   - 可以作为 API 代理解决 CORS 问题

3. **功能完整性 vs 开发速度**
   - 优先实现核心 Chat 功能
   - 文件树、编辑器、终端等作为后续迭代
   - 使用 assistant-ui 加速核心功能开发

---

## 5. Constraints

### 技术约束
- Node.js >= 18 (Next.js 14 要求)
- Python >= 3.10 (Nimbus 要求)
- 现代浏览器支持 (Chrome, Firefox, Safari, Edge 最新版)

### 协议约束
- Vercel AI SDK Data Protocol
- HTTP/1.1 或 HTTP/2 Streaming

### 部署约束
- Nimbus Server 必须支持 CORS 或使用 Next.js API 代理
- 支持 HTTPS 生产部署

---

## 6. Risks

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| AI SDK 协议变更 | 低 | 中 | 关注 Vercel 更新，使用稳定版本 |
| assistant-ui 停止维护 | 低 | 中 | 使用 Headless 设计，可替换 |
| CORS 问题 | 中 | 低 | 使用 Next.js API 代理 |
| 流式响应中断 | 中 | 中 | 实现重试和恢复机制 |
| Nimbus Agent 事件格式变化 | 中 | 中 | 适配层隔离变化 |

---

## 7. Evidence

### Sources
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/server/api.py:214-355` - 现有 SSE 流式实现
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/server/sse.py:1-416` - SSE 事件定义
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/server/models.py:1-284` - 数据模型定义
- Vercel AI SDK 官方文档: https://sdk.vercel.ai/docs
- assistant-ui 官方文档: https://www.assistant-ui.com/docs

### Assumptions
- **Nimbus Agent run_stream 接口稳定**: 假设事件类型和格式不会有重大变化
- **前端可以直接访问 Nimbus API**: 或通过 Next.js API 代理

---

## 8. Next Steps

### Phase 1: 后端 API (Week 1)

1. [ ] 创建 `api_ai_sdk.py` 实现 AI SDK 格式端点
2. [ ] 实现 Nimbus 事件到 AI SDK 格式的转换
3. [ ] 添加 CORS 支持
4. [ ] 测试流式响应

### Phase 2: 前端基础 (Week 1-2)

1. [ ] 创建 Next.js 项目，配置 shadcn/ui
2. [ ] 集成 assistant-ui 和 useChat
3. [ ] 实现基础 Chat Thread
4. [ ] 实现 ToolCallCard 组件

### Phase 3: 富 UI (Week 2-3)

1. [ ] 实现 DiffView 组件
2. [ ] 实现 TerminalOutput 组件
3. [ ] 添加会话管理侧边栏
4. [ ] 实现响应式布局

### Phase 4: 打磨 (Week 3-4)

1. [ ] 深色/亮色主题切换
2. [ ] 错误处理和重试
3. [ ] 性能优化
4. [ ] E2E 测试

---

## Appendix A: 与 v1 架构对比

| 方面 | v1 (WebSocket Bridge) | v2 (AI SDK) |
|------|----------------------|-------------|
| 架构层数 | 3 层（Client - Bridge - Agent） | 2 层（Client - Nimbus） |
| 部署复杂度 | 高（需要额外 Bridge 服务） | 低（仅 Nimbus + 前端） |
| 调试方便性 | 低（WebSocket 协议） | 高（HTTP Streaming） |
| 开发成本 | 高（需要实现 Bridge） | 低（使用现成 SDK） |
| 通用性 | 高（支持任意 ACP Agent） | 中（需要实现 AI SDK 协议） |
| 前端生态 | 需要适配 | 原生支持 |

---

## Appendix B: API 请求示例

### 发送消息

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Please analyze the project structure"}
    ],
    "sessionId": "session_123"
  }'
```

### 响应流示例

```
2:[{"type":"planning","status":"analyzing_request"}]
9:{"toolCallId":"tc_1","toolName":"glob","args":{"pattern":"**/*.py"}}
a:{"toolCallId":"tc_1","result":"Found 15 Python files..."}
0:"Based on my analysis of the project structure, I found:\n\n"
0:"1. **Core modules**: Located in `src/`\n"
0:"2. **Tests**: Located in `tests/`\n"
e:{"finishReason":"stop"}
d:{"finishReason":"stop"}
```

---

*Document End*
