# Nimbus Agent Framework

A notebook-style AI assistant framework with DAG planning and tiered memory management.

## Features

- **DAG-based Task Planning** - Parallel execution of independent tasks
- **Tiered Memory Management** - Pinned, Working, Episodic, Semantic layers
- **RESTful API with SSE Streaming** - Real-time response streaming
- **OpenCode TUI Compatible** - Drop-in replacement for OpenCode backend
- **Flexible LLM Support** - Anthropic, OpenAI, Gemini, Ollama adapters
- **Permission System** - Tool execution control with user approval
- **SQLite Persistence** - Session, Message, DAG, Memory checkpoints

## Installation

```bash
pip install -e .

# With LLM providers
pip install -e ".[llm]"

# Full installation
pip install -e ".[all]"
```

## Quick Start

### Start the Server

```bash
# Default port 4096 (OpenCode compatible)
nimbus serve

# Custom port
nimbus serve --port 8080
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | Health check |
| `/api/v1/sessions` | POST | Create session |
| `/api/v1/sessions/{id}/chat` | POST | Chat (SSE stream) |
| `/session` | POST | Create session (OpenCode) |
| `/session/{id}/message` | POST | Send message (OpenCode SSE) |

### CLI Commands

```bash
# Session management
nimbus session list
nimbus session create --name "my-project"
nimbus session delete <session_id>

# Configuration
nimbus config show
nimbus config set default_memory_type tiered
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      CLI / HTTP API                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ nimbus serve│  │  /api/v1/*   │  │ /session/* (OC)   │  │
│  └─────────────┘  └──────────────┘  └───────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                     Server Layer                            │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │SessionManager│ │   SSE Hub    │  │PermissionManager  │  │
│  └─────────────┘  └──────────────┘  └───────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                      Core Layer                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                    CodeAgent                         │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │   │
│  │  │   Planner   │  │   Runtime   │  │   Memory    │  │   │
│  │  │  Pipeline   │  │  (DAG Exec) │  │  (Tiered)   │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│                    Skills & Tools                           │
│  ┌─────────┐  ┌──────┐  ┌──────┐  ┌─────────┐  ┌──────┐   │
│  │  Read   │  │ Glob │  │ Grep │  │  Chat   │  │ ...  │   │
│  └─────────┘  └──────┘  └──────┘  └─────────┘  └──────┘   │
├─────────────────────────────────────────────────────────────┤
│                     Storage Layer                           │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                SQLite (aiosqlite)                    │   │
│  │  Sessions | Messages | DAGs | Memory | Permissions   │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Location | Description |
|-----------|----------|-------------|
| **serve.py** | `cli/commands/serve.py` | Server entry point |
| **app.py** | `server/app.py` | FastAPI application factory |
| **api.py** | `server/api.py` | REST API routes |
| **opencode.py** | `server/compat/opencode.py` | OpenCode compatible API |
| **session.py** | `server/session.py` | Session & Agent pool management |
| **sse.py** | `server/sse.py` | SSE event hub |
| **agent.py** | `core/agent.py` | CodeAgent with streaming |
| **pipeline.py** | `core/planner/pipeline.py` | Planner pipeline (Rule → Context → LLM) |
| **runtime.py** | `core/runtime.py` | Async DAG executor |
| **sqlite.py** | `storage/sqlite.py` | Persistence layer |

### Planner Pipeline

```
User Goal
    │
    ▼
┌─────────────┐
│ RulePlanner │ ──── Fast pattern matching (no LLM)
└─────────────┘
    │
    ▼
┌─────────────────┐
│ContextAnalyzer  │ ──── Detect context-dependent questions
└─────────────────┘
    │
    ▼
┌─────────────┐
│ LLMEnhancer │ ──── Generate/enhance DAG with LLM
└─────────────┘
    │
    ▼
┌─────────────┐
│  Validator  │ ──── Validate and repair DAG
└─────────────┘
    │
    ▼
  TaskDAG
```

### SSE Event Types

**Nimbus API** (`/api/v1/sessions/{id}/chat`):
```
connected, message_start, planning, dag_created,
task_start, tool_call, tool_result, task_done,
task_failed, permission_request, dag_complete,
message, error, heartbeat
```

**OpenCode API** (`/session/{id}/message`):
```
event.start, event.status, content.delta, content.done,
tool.start, tool.done, tool.error, event.done, event.error
```

## LLM Configuration

Create `llm.yaml` in project root:

```yaml
default_provider: anthropic

providers:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-sonnet-4-20250514

  openai:
    api_key: ${OPENAI_API_KEY}
    model: gpt-4o

  gemini:
    api_key: ${GEMINI_API_KEY}
    model: gemini-2.0-flash

  ollama:
    base_url: http://localhost:11434
    model: llama3.2
```

## Testing

```bash
# Unit tests
pytest tests/

# E2E tests (requires server running)
python tests/e2e_readonly_agent.py    # Tool tests (Glob/Read/Grep)
python tests/e2e_context_test.py      # Context understanding
python tests/e2e_tiered_memory.py     # Multi-turn memory
```

## Project Structure

```
src/nimbus/
├── cli/              # CLI commands
│   ├── commands/     # serve, session, config
│   └── main.py
├── core/             # Core components
│   ├── agent.py      # CodeAgent
│   ├── planner/      # Planning pipeline
│   ├── runtime/      # DAG executor
│   ├── memory/       # Tiered memory
│   └── types.py      # Core types
├── server/           # HTTP server
│   ├── app.py        # FastAPI app
│   ├── api.py        # REST routes
│   ├── sse.py        # SSE hub
│   ├── session.py    # Session manager
│   └── compat/       # OpenCode compat
├── storage/          # Persistence
│   └── sqlite.py
├── skills/           # Built-in skills
├── tools/            # Built-in tools
└── llm/              # LLM adapters
```

## License

MIT

## Nimbus 架构概述

根据提供的文件列表，Nimbus 似乎是一个 AI 代理框架，其架构可以概括为以下几个主要部分：

1.  **核心组件 (`src/nimbus/core`)**:
    *   **规划器 (Planner)**: 位于 `src/nimbus/core/planner`，包含 `pipeline.py`（规划流程）、`rule_planner.py`（规则规划）、`llm_enhancer.py`（LLM增强）、`validator.py`（验证器）等，负责任务分解、策略制定和验证。
    *   **运行时 (Runtime)**: `src/nimbus/core/runtime/executor.py` 表明存在一个执行器，负责执行规划器生成的任务。
    *   **代理 (Agent)**: `src/nimbus/core/agent.py` 可能是代理的核心协调逻辑。
    *   **工厂 (Factory)**: `src/nimbus/core/factory.py` 用于创建和管理不同组件的实例。
    *   **类型 (Types)**: `src/nimbus/core/types.py` 定义了系统中的数据结构。

2.  **服务器组件 (`src/nimbus/server`)**:
    *   **API**: `src/nimbus/server/api.py` 和 `src/nimbus/server/api_ai_sdk.py` 提供对外接口，可能用于与其他系统或前端交互。
    *   **应用**: `src/nimbus/server/app.py` 是服务器的主应用入口。
    *   **会话与权限**: `src/nimbus/server/session.py` 和 `src/nimbus/server/permission.py` 处理用户会话和访问权限。

3.  **技能组件 (`src/nimbus/skills`)**:
    *   `src/nimbus/skills/synthesize.py` 和 `src/nimbus/skills/delegation.py` 表明 Nimbus 具备信息综合、任务委托等特定技能。

4.  **工具组件 (`src/nimbus/tools`)**:
    *   提供了一系列工具供代理执行操作，例如：
        *   文件系统操作: `read.py`, `write.py`, `edit.py`, `glob.py`
        *   搜索与网络: `search.py`, `websearch.py`, `webfetch.py`
        *   命令行执行: `bash.py`

此外，`examples/` 目录下包含使用示例，而 `tests/` 目录下的测试文件（如 `tests/capabilities/` 中的 `test_task_decomposition.py`, `test_code_search.py`, `test_repo_understanding.py` 等）进一步揭示了 Nimbus 在任务分解、代码理解和修改、上下文理解等方面的能力。

总结来说，Nimbus 架构围绕一个核心代理构建，该代理通过规划器制定执行计划，利用各种工具与环境交互，并通过服务器提供服务接口，并具备多种特定技能。

