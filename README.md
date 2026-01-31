# Nimbus Agent Framework

> 🚀 Production-ready AI Agent framework with vCPU-based process model and multi-provider LLM support.

## ✨ Features

- **v2 AgentOS Architecture** - vCPU + Process model for robust agent execution
- **pi-ai Integration** - Unified LLM API via HTTP service (supports 10+ providers)
- **Web UI** - Modern React chat interface with SSE streaming
- **DAG-based Task Planning** - Parallel execution of independent tasks
- **Tiered Memory Management** - Pinned, Working, Episodic, Semantic layers
- **Doom Loop Detection** - Graceful termination when agent gets stuck
- **OpenCode TUI Compatible** - Drop-in replacement for OpenCode backend

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Interfaces                               │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │  Web UI  │  │  HTTP API    │  │    CLI       │               │
│  │ :3000    │  │  :4096       │  │  nimbus      │               │
│  └────┬─────┘  └──────┬───────┘  └──────────────┘               │
│       │               │                                          │
├───────┴───────────────┴─────────────────────────────────────────┤
│                       v2 AgentOS                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                      vCPU                                │    │
│  │  ┌──────────┐  ┌──────────────┐  ┌─────────────────┐   │    │
│  │  │  Fetch   │  │    Decode    │  │     Execute     │   │    │
│  │  │  (LLM)   │  │  (Actions)   │  │    (Tools)      │   │    │
│  │  └──────────┘  └──────────────┘  └─────────────────┘   │    │
│  └─────────────────────────────────────────────────────────┘    │
│                           │                                      │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                     Process                              │    │
│  │  ┌──────────┐  ┌──────────────┐  ┌─────────────────┐   │    │
│  │  │ Messages │  │    Context   │  │     Gates       │   │    │
│  │  │  (PCB)   │  │   (Memory)   │  │   (Syscalls)    │   │    │
│  │  └──────────┘  └──────────────┘  └─────────────────┘   │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                        LLM Layer                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   pi-ai HTTP Server                      │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │    │
│  │  │Anthropic │  │  OpenAI  │  │  Google  │  │ Others │  │    │
│  │  └──────────┘  └──────────┘  └──────────┘  └────────┘  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                           :3031                                  │
└──────────────────────────────────────────────────────────────────┘
```

## 🚀 Quick Start

### 1. Install

```bash
pip install -e ".[all]"
cd bridge && npm install
```

### 2. Start Services

```bash
# Terminal 1: Start pi-ai HTTP server (LLM backend)
./scripts/start-pi-ai.sh --daemon

# Terminal 2: Start nimbus server
uv run nimbus serve

# Terminal 3: Start Web UI (optional)
cd web-ui && npm run dev
```

### 3. Verify

```bash
curl http://localhost:3031/health  # pi-ai server
curl http://localhost:4096/health  # nimbus server
open http://localhost:3000         # Web UI
```

## 📦 Components

### v2 AgentOS (`src/nimbus/v2/`)

The new architecture uses an OS-like process model:

| Component | Description |
|-----------|-------------|
| **vCPU** | Fetch-Decode-Execute cycle for LLM interactions |
| **Process** | Encapsulates agent state (messages, context, gates) |
| **Gates** | Permission system for tool execution |
| **MMU** | Memory management with tiered storage |

```python
from nimbus.v2.agentos import create_agent_os

# Create AgentOS instance
agent_os = create_agent_os(
    llm_client=llm,
    tools={"Read": read_tool, "Write": write_tool},
    max_processes=5,
)

# Run a task
result = await agent_os.run("Find all Python files")
```

### pi-ai HTTP Server (`bridge/pi-ai-server.ts`)

Unified LLM API supporting multiple providers:

| Provider | Models |
|----------|--------|
| Anthropic | claude-sonnet-4, claude-3.5-sonnet |
| OpenAI | gpt-4o, gpt-4-turbo |
| Google | gemini-2.0-flash, gemini-pro |
| Mistral | mistral-large |
| Groq | llama-3.1-70b |
| Bedrock | claude-3-sonnet |
| GitHub Copilot | gpt-4o (via OAuth) |

```bash
# Endpoints
POST /v1/chat/completions  # OpenAI-compatible
POST /v1/stream            # SSE streaming
GET  /v1/models            # List available models
GET  /health               # Health check
```

### Web UI (`web-ui/`)

Modern React chat interface:

- SSE streaming responses
- Tool call visualization
- Markdown rendering
- Dark mode support

```bash
cd web-ui
npm install
npm run dev  # http://localhost:3000
```

## 🛠️ Tools

Built-in tools for code exploration and editing:

| Tool | Description |
|------|-------------|
| `Read` | Read file contents |
| `Write` | Write/create files |
| `Edit` | Surgical text replacement |
| `Glob` | Find files by pattern |
| `Grep` | Search file contents |
| `Bash` | Execute shell commands |
| `Kill` | Terminate running processes |

## 📡 API Endpoints

### Nimbus API (`:4096`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/session` | POST | Create session |
| `/session/{id}/message` | POST | Send message (SSE) |
| `/api/v1/sessions` | POST | Create session (v1) |
| `/api/v1/sessions/{id}/chat` | POST | Chat (SSE) |

### SSE Event Types

```
event.start      # Conversation started
content.delta    # Text chunk
tool.start       # Tool call initiated
tool.done        # Tool call completed
event.done       # Conversation complete
event.error      # Error occurred
```

## 🧪 Testing

```bash
# Unit tests
pytest tests/ -v

# E2E tests (requires running servers)
python tests/e2e_tool_call.py

# Test pi-ai HTTP client
pytest tests/test_pi_ai_http.py -v
```

## 📁 Project Structure

```
nimbus/
├── src/nimbus/
│   ├── v2/                    # v2 AgentOS architecture
│   │   ├── agentos.py         # Main entry point
│   │   ├── core/
│   │   │   └── runtime/
│   │   │       └── vcpu.py    # vCPU implementation
│   │   ├── adapters/
│   │   │   └── pi_adapter.py  # LLM adapter
│   │   └── tools/             # Built-in tools
│   ├── server/                # HTTP server
│   └── core/                  # Legacy v1 (deprecated)
├── bridge/
│   └── pi-ai-server.ts        # pi-ai HTTP wrapper
├── web-ui/                    # React frontend
├── scripts/
│   └── start-pi-ai.sh         # Launcher script
└── docs/                      # Documentation
```

## 🔧 Configuration

### Environment Variables

```bash
# LLM Provider
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...

# Ports
NIMBUS_PORT=4096
PI_AI_PORT=3031
```

### LLM Configuration (`llm.yaml`)

```yaml
default_provider: anthropic

providers:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-sonnet-4-20250514

  openai:
    api_key: ${OPENAI_API_KEY}
    model: gpt-4o
```

## 📜 License

MIT
