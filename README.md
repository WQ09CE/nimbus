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
