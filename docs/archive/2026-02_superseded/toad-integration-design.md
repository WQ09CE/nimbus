# Toad TUI Integration Design for Nimbus Agent Framework

> **Status**: Proposed
> **Version**: 1.0
> **Date**: 2025-01-24
> **Author**: @mind (Architect)

---

## Summary

本文档设计将 Toad TUI (by Will McGugan) 集成为 Nimbus Agent Framework 的 UI 层的方案。推荐采用 **ACP 协议适配器** 方案，在 Nimbus 侧实现 ACP Agent 接口，使 Toad 可以像管理其他 agent (Claude Code, Gemini CLI 等) 一样管理 Nimbus。

---

## 1. Thinking Process

### 1.1 问题理解

**核心问题**: 如何让 Toad TUI 与 Nimbus 后端通信，使用户通过 Toad 的丰富 UI 组件与 Nimbus Agent 交互？

**约束条件**:
1. Nimbus 已有 OpenCode 兼容 API 层 (HTTP + SSE)
2. Toad 使用 ACP (Agent Client Protocol) 协议
3. 尽量减少对 Toad 核心代码的修改
4. 需要支持 Nimbus 的特殊功能 (DAG 并行执行、分层内存)

**隐含需求**:
- 协议转换层需要处理消息格式差异
- 需要映射 Nimbus 的 SSE 事件到 ACP session/update
- 权限系统需要双向打通
- 会话管理需要同步

### 1.2 方案探索

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A: ACP 适配器 (推荐) | Nimbus 实现 ACP Agent 接口，通过 stdio/JSON-RPC 与 Toad 通信 | 标准协议、Toad 无需修改、复用现有 agent 管理机制 | 需要实现完整 ACP 接口 |
| B: HTTP 代理桥接 | 在 Toad 中添加 Nimbus Provider，通过 HTTP 转发到 Nimbus API | 复用现有 OpenCode 兼容层 | 需要修改 Toad、协议转换复杂 |
| C: 自定义 Widget | 在 Toad 中创建 Nimbus 专用 Widget 集 | 可以展示 DAG 等特殊功能 | 维护成本高、与 Toad 紧耦合 |

### 1.3 决策推导

基于以下理由，推荐 **方案 A: ACP 适配器**：

1. **ACP 是标准协议** - Toad 原生支持 ACP，无需修改
2. **OpenCode 已有成熟实现** - 可参考 `/opencode/src/acp/` 的实现模式
3. **最小侵入性** - Nimbus 新增一个 ACP 模块，Toad 完全不变
4. **生态兼容** - 同时支持 Zed、JetBrains 等其他 ACP 客户端

---

## 2. Design

### 2.1 架构概述

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Toad TUI (Python/Textual)                       │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐             │
│  │   Conversation   │ │   Tool Call      │ │   Diff View      │             │
│  │   Widget         │ │   Widget         │ │   Widget         │             │
│  └────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘             │
│           │                    │                    │                        │
│  ┌────────▼────────────────────▼────────────────────▼─────────┐             │
│  │                    ACP Client Layer                         │             │
│  │              (JSON-RPC over stdio / HTTP+SSE)               │             │
│  └────────────────────────────┬────────────────────────────────┘             │
└───────────────────────────────┼──────────────────────────────────────────────┘
                                │ ACP Protocol
                                │ (stdio JSON-RPC / HTTP)
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Nimbus ACP Server (NEW)                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        ACP Protocol Layer                            │   │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────┐ │   │
│  │  │ Agent        │ │ Session      │ │ Permission   │ │ Event       │ │   │
│  │  │ Interface    │ │ Manager      │ │ Handler      │ │ Emitter     │ │   │
│  │  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬──────┘ │   │
│  └─────────┼────────────────┼────────────────┼────────────────┼────────┘   │
│            │                │                │                │             │
│  ┌─────────▼────────────────▼────────────────▼────────────────▼────────┐   │
│  │                      Nimbus Core Adapter                             │   │
│  │            (Maps ACP operations to Nimbus internals)                 │   │
│  └─────────────────────────────┬───────────────────────────────────────┘   │
│                                │                                            │
│  ┌─────────────────────────────▼───────────────────────────────────────┐   │
│  │                        Existing Nimbus Core                          │   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────────────┐ │   │
│  │  │ CodeAgent │  │ DAG       │  │ Tiered    │  │ Session/Storage   │ │   │
│  │  │           │  │ Planner   │  │ Memory    │  │                   │ │   │
│  │  └───────────┘  └───────────┘  └───────────┘  └───────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件

#### 2.2.1 ACP Protocol Layer (`src/nimbus/acp/`)

| 组件 | 文件 | 职责 |
|------|------|------|
| **ACPAgent** | `agent.py` | 实现 ACP Agent 接口，处理 initialize/prompt/cancel |
| **ACPSessionManager** | `session.py` | 管理 ACP Session 状态，映射到 Nimbus Session |
| **ACPPermissionHandler** | `permission.py` | 处理权限请求/响应的双向转换 |
| **ACPEventEmitter** | `events.py` | 将 Nimbus SSE 事件转换为 ACP session/update |
| **ACPServer** | `server.py` | JSON-RPC over stdio 服务启动和生命周期管理 |
| **ACPTypes** | `types.py` | ACP 协议的 Python 类型定义 |

#### 2.2.2 Nimbus Core Adapter (`src/nimbus/acp/adapter.py`)

负责将 ACP 操作映射到 Nimbus 内部：

```python
class NimbusACPAdapter:
    """Maps ACP operations to Nimbus internals."""

    async def create_session(self, cwd: str, mcp_servers: List[McpServer]) -> ACPSessionState:
        """Create Nimbus session from ACP request."""

    async def prompt(self, session_id: str, content: List[ContentBlock]) -> AsyncIterator[ACPUpdate]:
        """Process prompt and yield ACP updates."""

    async def cancel(self, session_id: str) -> None:
        """Cancel ongoing operation."""
```

### 2.3 数据流

#### 2.3.1 Session 创建流程

```
Toad                     Nimbus ACP Server              Nimbus Core
  │                            │                            │
  │─── session/new ───────────>│                            │
  │    {cwd, mcpServers}       │                            │
  │                            │─── create_session() ──────>│
  │                            │    {name, workspace_path}  │
  │                            │                            │
  │                            │<── session_id, status ─────│
  │                            │                            │
  │<── {sessionId, models,     │                            │
  │     modes, _meta} ─────────│                            │
```

#### 2.3.2 Prompt 处理流程 (核心)

```
Toad                     Nimbus ACP Server              Nimbus Core
  │                            │                            │
  │─── session/prompt ────────>│                            │
  │    {sessionId, prompt[]}   │                            │
  │                            │─── run_stream() ──────────>│
  │                            │                            │
  │                            │<── planning event ─────────│
  │<── session/update:         │                            │
  │    agent_thought_chunk ────│                            │
  │                            │                            │
  │                            │<── dag_created ────────────│
  │<── session/update:         │                            │
  │    plan (DAG as entries) ──│                            │
  │                            │                            │
  │                            │<── task_start ─────────────│
  │<── session/update:         │                            │
  │    tool_call (pending) ────│                            │
  │                            │                            │
  │                            │<── task_done ──────────────│
  │<── session/update:         │                            │
  │    tool_call_update ───────│                            │
  │    (completed)             │                            │
  │                            │                            │
  │                            │<── complete ───────────────│
  │<── session/update:         │                            │
  │    agent_message_chunk ────│                            │
  │                            │                            │
  │<── {stopReason: end_turn} ─│                            │
```

#### 2.3.3 权限请求流程

```
Nimbus Core              Nimbus ACP Server              Toad
  │                            │                            │
  │── permission_request ─────>│                            │
  │   {tool, args}             │                            │
  │                            │─── requestPermission() ───>│
  │                            │    {toolCall, options}     │
  │                            │                            │
  │                            │<── {outcome, optionId} ────│
  │                            │                            │
  │<── resolve_permission() ───│                            │
  │    {allow/deny}            │                            │
```

### 2.4 ACP 协议映射

#### 2.4.1 ACP 方法实现

| ACP Method | Nimbus 实现 |
|------------|-------------|
| `initialize` | 返回 Nimbus 能力 (agentCapabilities) |
| `session/new` | 调用 SessionManager.create_session() |
| `session/load` | 调用 SessionManager.get_session() + 重放历史 |
| `session/prompt` | 调用 CodeAgent.run_stream() |
| `session/cancel` | 调用 abort 逻辑 (待实现) |
| `session/setModel` | 设置 LLM 模型 |
| `session/setMode` | 暂不支持 (Nimbus 单 agent) |

#### 2.4.2 事件映射表

| Nimbus SSE Event | ACP session/update Type |
|------------------|-------------------------|
| `planning` | `agent_thought_chunk` |
| `dag_created` | `plan` (entries) |
| `task_start` | `tool_call` (status: pending) |
| `task_done` | `tool_call_update` (status: completed) |
| `task_failed` | `tool_call_update` (status: failed) |
| `content.delta` | `agent_message_chunk` |
| `permission_request` | `requestPermission()` 回调 |
| `error` | 抛出 RequestError |

#### 2.4.3 Tool Kind 映射

| Nimbus Skill | ACP ToolKind |
|--------------|--------------|
| read_file / Read | `read` |
| grep_content / Grep | `search` |
| glob_files / Glob | `search` |
| bash / Bash | `execute` |
| Edit / Write | `edit` |
| chat | `other` |
| search (web) | `fetch` |

### 2.5 接口设计

#### 2.5.1 ACP Agent Interface (Python)

```python
# src/nimbus/acp/agent.py

from abc import ABC, abstractmethod
from typing import AsyncIterator, List, Optional
from .types import (
    InitializeRequest, InitializeResponse,
    NewSessionRequest, NewSessionResponse,
    LoadSessionRequest, LoadSessionResponse,
    PromptRequest, PromptResponse,
    CancelNotification,
    SessionUpdate,
    PermissionRequest, PermissionResponse,
)


class ACPAgent(ABC):
    """Abstract base class for ACP Agent implementation."""

    @abstractmethod
    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        """Handle ACP initialize request."""
        pass

    @abstractmethod
    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        """Create new ACP session."""
        pass

    @abstractmethod
    async def load_session(self, params: LoadSessionRequest) -> LoadSessionResponse:
        """Load existing ACP session."""
        pass

    @abstractmethod
    async def prompt(self, params: PromptRequest) -> PromptResponse:
        """Process user prompt."""
        pass

    @abstractmethod
    async def cancel(self, params: CancelNotification) -> None:
        """Cancel ongoing operation."""
        pass


class NimbusACPAgent(ACPAgent):
    """Nimbus implementation of ACP Agent."""

    def __init__(self, config: ACPConfig):
        self.config = config
        self.session_manager = ACPSessionManager()
        self.adapter = NimbusACPAdapter()
        self.connection: Optional[AgentSideConnection] = None

    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=1,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                mcp_capabilities=MCPCapabilities(http=True, sse=True),
                prompt_capabilities=PromptCapabilities(
                    embedded_context=True,
                    image=False,  # Nimbus 暂不支持图片
                ),
                session_capabilities=SessionCapabilities(
                    fork={},
                    list={},
                    resume={},
                ),
            ),
            agent_info=AgentInfo(
                name="Nimbus",
                version=__version__,
            ),
        )

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        session = self.session_manager.get(params.session_id)

        # Convert ACP content blocks to Nimbus message
        content = self._extract_text_content(params.prompt)

        # Run agent with streaming, emit ACP updates
        async for status in self.adapter.run_stream(session.nimbus_session_id, content):
            update = self._convert_to_acp_update(params.session_id, status)
            await self.connection.session_update(update)

        return PromptResponse(stop_reason="end_turn")
```

#### 2.5.2 ACP Types (Python)

```python
# src/nimbus/acp/types.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime


class ToolKind(str, Enum):
    READ = "read"
    EDIT = "edit"
    EXECUTE = "execute"
    SEARCH = "search"
    FETCH = "fetch"
    OTHER = "other"


class ToolCallStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ContentBlock:
    """Base class for ACP content blocks."""
    type: str


@dataclass
class TextContent(ContentBlock):
    type: str = "text"
    text: str = ""


@dataclass
class ToolCallContent:
    type: str  # "content" | "diff"
    content: Optional[Dict] = None
    path: Optional[str] = None
    old_text: Optional[str] = None
    new_text: Optional[str] = None


@dataclass
class ToolCall:
    tool_call_id: str
    title: str
    kind: ToolKind
    status: ToolCallStatus
    locations: List[Dict[str, str]] = field(default_factory=list)
    raw_input: Dict[str, Any] = field(default_factory=dict)
    content: List[ToolCallContent] = field(default_factory=list)


@dataclass
class PlanEntry:
    content: str
    status: str  # "pending" | "in_progress" | "completed"
    priority: str = "medium"


@dataclass
class SessionUpdate:
    session_id: str
    update: Dict[str, Any]


@dataclass
class AgentCapabilities:
    load_session: bool = False
    mcp_capabilities: Optional[Dict] = None
    prompt_capabilities: Optional[Dict] = None
    session_capabilities: Optional[Dict] = None


@dataclass
class InitializeResponse:
    protocol_version: int
    agent_capabilities: AgentCapabilities
    agent_info: Dict[str, str]
    auth_methods: List[Dict] = field(default_factory=list)


@dataclass
class ACPSessionState:
    id: str  # ACP session ID
    nimbus_session_id: str  # Nimbus internal session ID
    cwd: str
    mcp_servers: List[Dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    model: Optional[Dict[str, str]] = None
    mode_id: Optional[str] = None
```

#### 2.5.3 ACP Server Entry Point

```python
# src/nimbus/acp/server.py

import asyncio
import json
import sys
from typing import Optional

from .agent import NimbusACPAgent
from .types import ACPConfig


class ACPServer:
    """ACP JSON-RPC server over stdio."""

    def __init__(self, config: Optional[ACPConfig] = None):
        self.config = config or ACPConfig()
        self.agent = NimbusACPAgent(self.config)
        self._running = False

    async def start(self):
        """Start the ACP server, reading from stdin and writing to stdout."""
        self._running = True

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown)

        # Main JSON-RPC loop
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        writer_transport, writer_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, loop)

        self.agent.connection = StdioConnection(writer)

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    break

                request = json.loads(line.decode())
                response = await self._handle_request(request)

                if response:
                    await self._write_response(writer, response)

            except Exception as e:
                await self._write_error(writer, str(e))

    async def _handle_request(self, request: dict) -> Optional[dict]:
        """Route JSON-RPC request to appropriate handler."""
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")

        handlers = {
            "initialize": self.agent.initialize,
            "session/new": self.agent.new_session,
            "session/load": self.agent.load_session,
            "session/prompt": self.agent.prompt,
            "session/cancel": self.agent.cancel,
            "session/setModel": self.agent.set_model,
            "session/setMode": self.agent.set_mode,
        }

        handler = handlers.get(method)
        if not handler:
            return self._method_not_found(request_id, method)

        try:
            result = await handler(params)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(e)},
            }

    def _shutdown(self):
        """Handle graceful shutdown."""
        self._running = False


# CLI entry point
def main():
    """nimbus acp command entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Nimbus ACP Server")
    parser.add_argument("--cwd", default=None, help="Working directory")
    args = parser.parse_args()

    config = ACPConfig(cwd=args.cwd)
    server = ACPServer(config)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()
```

### 2.6 Toad 配置

#### 2.6.1 Toad 配置文件

用户只需在 Toad 配置中添加 Nimbus 作为 ACP agent：

```toml
# ~/.config/toad/config.toml

[agents.nimbus]
name = "Nimbus"
command = "nimbus"
args = ["acp"]
```

或通过环境变量：

```bash
TOAD_AGENT_NIMBUS_COMMAND="nimbus acp" toad
```

#### 2.6.2 Toad Agent Store (可选)

如果 Toad 支持 agent store，可以发布 Nimbus 配置：

```yaml
# nimbus-agent.yaml
name: nimbus
display_name: Nimbus Agent
description: DAG-parallel agent with tiered memory
command: nimbus
args: ["acp"]
homepage: https://github.com/your-org/nimbus
tags: ["parallel", "dag", "memory"]
```

---

## 3. Decisions

### Decision 1: 采用 ACP 协议而非自定义协议

- **决策**: 实现标准 ACP Agent 接口
- **理由**:
  - ACP 是开放标准，被 Zed、JetBrains、Avante.nvim 等采用
  - Toad 原生支持 ACP，无需修改 Toad 代码
  - OpenCode 已有成熟的 ACP 实现可参考
- **备选方案**: 自定义 HTTP 协议、WebSocket
- **风险**: ACP 协议可能演进，需要跟进版本更新

### Decision 2: 使用 stdio JSON-RPC 而非 HTTP+SSE

- **决策**: 主要通信方式为 stdio JSON-RPC (Toad 启动 `nimbus acp` 子进程)
- **理由**:
  - 这是 ACP 的标准通信方式
  - 无需额外端口管理
  - 子进程生命周期与 UI 绑定，自动清理
- **备选方案**: HTTP server mode (作为补充)
- **风险**: Windows 平台 stdio 可能有兼容性问题

### Decision 3: DAG 执行进度映射为 ACP Plan

- **决策**: 将 Nimbus DAG 节点映射为 ACP 的 `plan` entries
- **理由**:
  - ACP 原生支持 plan 展示
  - 用户可以在 Toad 中看到任务执行进度
  - 符合 ACP 设计理念
- **备选方案**: 仅展示最终结果
- **风险**: DAG 结构比线性 plan 复杂，可能丢失依赖关系信息

### Decision 4: 权限系统双向打通

- **决策**: Nimbus 权限请求通过 ACP `requestPermission()` 转发给 Toad
- **理由**:
  - 用户可以在 Toad UI 中审批权限
  - 与 Nimbus 原有权限系统保持一致
- **备选方案**: Nimbus 独立弹窗请求权限
- **风险**: Toad 权限 UI 可能与 Nimbus 预期不完全匹配

---

## 4. Tradeoffs

1. **标准化 vs 特性完整性**
   - 选择 ACP 标准协议意味着某些 Nimbus 特有功能 (如 DAG 可视化、分层内存统计) 可能无法完全展示
   - 缓解: 通过 `_meta` 扩展字段传递额外信息

2. **开发成本 vs 维护成本**
   - 方案 A (ACP 适配器) 前期开发成本较高
   - 但长期维护成本低，且可复用于其他 ACP 客户端

3. **实时性 vs 复杂度**
   - 选择流式事件转换，增加实现复杂度
   - 但用户体验更好，可以看到实时执行进度

---

## 5. Constraints

### 技术约束
- Python 版本: >= 3.11 (async 特性)
- Nimbus 核心模块不应依赖 ACP 模块 (单向依赖)
- ACP 模块应该是可选安装 (`pip install nimbus[acp]`)

### 协议约束
- ACP 协议版本: v1
- JSON-RPC 版本: 2.0
- 消息编码: UTF-8

### 兼容性约束
- 需要与现有 OpenCode 兼容层并存
- 不应破坏现有 HTTP API

---

## 6. Risks

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| ACP 协议版本升级 | 中 | 中 | 关注 ACP 官方更新，版本协商 |
| Toad 未公开发布 | 中 | 高 | 设计时基于公开 ACP 规范，与 Toad 解耦 |
| DAG 信息丢失 | 低 | 低 | 使用 _meta 扩展字段传递 |
| Windows stdio 兼容性 | 中 | 中 | 提供 HTTP server 作为备选 |
| 权限系统不匹配 | 低 | 中 | 设计权限选项映射表 |

---

## 7. Evidence

### Sources
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/docs/opencode-tui-integration.md` - Nimbus 现有 OpenCode 兼容层文档
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/server/compat/opencode.py:1-867` - OpenCode 兼容 API 实现
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/agent.py:1-823` - CodeAgent 核心实现
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/server/sse.py:1-416` - SSE 事件系统
- `/Users/wangqing/sourcecode/agent/agent-framework/opencode/packages/opencode/src/acp/README.md` - OpenCode ACP 实现说明
- `/Users/wangqing/sourcecode/agent/agent-framework/opencode/packages/opencode/src/acp/agent.ts:1-1437` - OpenCode ACP Agent 实现 (TypeScript)
- `/Users/wangqing/sourcecode/agent/agent-framework/opencode/packages/opencode/src/acp/session.ts:1-106` - OpenCode ACP Session 管理
- `/Users/wangqing/sourcecode/agent/agent-framework/opencode/packages/opencode/src/acp/types.ts:1-23` - OpenCode ACP 类型定义

### Assumptions
- **Toad 使用标准 ACP 协议**: 基于 Toad 的公开描述 (GitHub repo) 和 Will McGugan 的背景，假设其遵循 ACP 规范
- **Toad 支持外部 agent 配置**: 假设可以通过配置文件或环境变量添加自定义 agent

---

## 8. Next Steps

### Phase 1: ACP 核心实现 (Week 1-2)

1. [ ] 创建 `src/nimbus/acp/` 模块结构
2. [ ] 实现 ACP types (`types.py`)
3. [ ] 实现 ACPAgent 基类和 NimbusACPAgent
4. [ ] 实现 stdio JSON-RPC server (`server.py`)
5. [ ] 添加 `nimbus acp` CLI 命令

### Phase 2: 事件转换 (Week 2-3)

1. [ ] 实现 Nimbus SSE → ACP session/update 转换
2. [ ] 实现 DAG → Plan entries 映射
3. [ ] 实现 Tool → ToolCall 映射
4. [ ] 实现权限请求转发

### Phase 3: Session 管理 (Week 3-4)

1. [ ] 实现 session/new 和 session/load
2. [ ] 实现会话历史重放
3. [ ] 实现 model/mode 切换 (如适用)

### Phase 4: 测试和文档 (Week 4-5)

1. [ ] 单元测试 (ACP 消息解析、事件转换)
2. [ ] 集成测试 (完整对话流程)
3. [ ] 与 Toad 实际集成测试
4. [ ] 编写用户文档

### Phase 5: 发布 (Week 5)

1. [ ] 添加 `nimbus[acp]` 可选依赖
2. [ ] 更新 README 和文档
3. [ ] 发布新版本

---

## Appendix A: 文件结构

```
src/nimbus/
├── acp/                          # NEW: ACP 模块
│   ├── __init__.py
│   ├── agent.py                  # ACPAgent 实现
│   ├── adapter.py                # Nimbus Core 适配器
│   ├── events.py                 # 事件转换
│   ├── permission.py             # 权限处理
│   ├── server.py                 # JSON-RPC server
│   ├── session.py                # Session 管理
│   └── types.py                  # 类型定义
├── core/
│   ├── agent.py                  # 现有 CodeAgent
│   └── ...
├── server/
│   ├── api.py                    # 现有 HTTP API
│   ├── compat/
│   │   └── opencode.py           # 现有 OpenCode 兼容层
│   └── ...
└── cli/
    ├── __init__.py
    └── commands/
        └── acp.py                # NEW: nimbus acp 命令
```

---

## Appendix B: ACP 消息示例

### Initialize

Request:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": 1,
    "clientCapabilities": {}
  }
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": 1,
    "agentCapabilities": {
      "loadSession": true,
      "mcpCapabilities": {"http": true, "sse": true},
      "promptCapabilities": {"embeddedContext": true}
    },
    "agentInfo": {
      "name": "Nimbus",
      "version": "0.2.0"
    }
  }
}
```

### Session Update (Tool Call)

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "sess_abc123",
    "update": {
      "sessionUpdate": "tool_call",
      "toolCallId": "task_001",
      "title": "read_file",
      "kind": "read",
      "status": "pending",
      "locations": [{"path": "/path/to/file.py"}],
      "rawInput": {"path": "/path/to/file.py"}
    }
  }
}
```

### Session Update (Plan - DAG)

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "sess_abc123",
    "update": {
      "sessionUpdate": "plan",
      "entries": [
        {"content": "Read file: config.py", "status": "completed", "priority": "high"},
        {"content": "Search for imports", "status": "in_progress", "priority": "medium"},
        {"content": "Analyze dependencies", "status": "pending", "priority": "medium"}
      ]
    }
  }
}
```

---

*Document End*
