# Nimbus Agent Framework

> Production-ready AI Agent framework with OS-like architecture.

## Overview

**Nimbus** is a modular AI Agent framework (v0.2.0 Alpha) featuring a von Neumann-inspired architecture. It treats Agent execution like an operating system: vCPU executes Think-Act-Observe cycles, MMU manages context memory, and Gate provides permission-isolated tool access.

**Core Capabilities:**
- 🖥️ OS-like architecture (vCPU / MMU / Gate / Process)
- 🧠 Context Stack with automatic refinement
- 📊 DAG-based parallel task scheduling
- 🔒 Permission-isolated subagent system
- 🔌 Multi-protocol support (REST / OpenCode / AI SDK v6)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      AgentOS                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   vCPU      │  │    MMU      │  │       Gate          │  │
│  │ Think-Act-  │  │ Context     │  │ Permission-isolated │  │
│  │ Observe     │◄─┤ Stack       │  │ Tool Dispatch       │  │
│  │ Loop        │  │ Management  │  │                     │  │
│  └──────┬──────┘  └─────────────┘  └──────────┬──────────┘  │
│         │                                      │            │
│         ▼                                      ▼            │
│  ┌─────────────┐                      ┌─────────────────┐   │
│  │  Scheduler  │                      │     Tools       │   │
│  │ DAG-based   │                      │ Read/Write/Edit │   │
│  │ Parallel    │                      │ Bash/ReadArchive│   │
│  └─────────────┘                      └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     HTTP Server                             │
│  /api/v1/*  │  /session/*  │  /v1/chat/completions         │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| **AgentOS** | `agentos.py` | 1220 | Main orchestrator, process management |
| **vCPU** | `core/runtime/vcpu.py` | 1540 | Think-Act-Observe execution loop |
| **MMU** | `core/memory/mmu.py` | 910 | Context stack, memory management |
| **Gate** | `os/gate.py` | 409 | Permission-isolated tool dispatch |
| **Scheduler** | `core/scheduler.py` | 963 | DAG task scheduling, parallel execution |
| **Decoder** | `core/runtime/decoder.py` | 202 | LLM response → ActionIR parsing |
| **PiAdapter** | `adapters/pi_adapter.py` | 320 | pi-ai LLM integration |

## Project Structure

```
src/nimbus/
├── agentos.py              # AgentOS main entry
├── adapters/               # LLM adapters
│   └── pi_adapter.py       # pi-ai integration
├── bridge/                 # External service bridges
│   └── pi_ai_http.py       # pi-ai HTTP client
├── core/
│   ├── runtime/
│   │   ├── vcpu.py         # vCPU execution engine
│   │   └── decoder.py      # Instruction decoder
│   ├── memory/
│   │   ├── mmu.py          # Memory management unit
│   │   └── context.py      # Context types
│   ├── scheduler.py        # DAG scheduler
│   ├── session.py          # Session management
│   └── types.py            # Core data types
├── os/
│   └── gate.py             # System call interface
├── server/                 # HTTP API server
│   ├── app.py              # FastAPI app
│   ├── api.py              # REST endpoints
│   └── compat/opencode.py  # OpenCode compatibility
├── tools/                  # Built-in tools
│   ├── read.py, edit.py, grep.py, sandbox.py
│   └── ...
└── cli/                    # Command-line interface
    └── main.py
```

## Quick Start

### Installation

```bash
# Basic installation
pip install -e .

# Full installation (with all dependencies)
pip install -e ".[all]"
```

### Running the Server

```bash
# Start server (default port 4096)
./nimbus start

# Or with custom port
nimbus serve --port 8080
```

### Running Tests

```bash
# All tests (454 test cases)
pytest tests/ -v

# Quick tests (skip slow/integration)
pytest tests/ -v -m "not slow"
```

## Key Concepts

### vCPU Execution Loop

```
┌─────────────────────────────────────────┐
│              vCPU Cycle                 │
│                                         │
│   ┌─────────┐                           │
│   │  THINK  │ ── LLM generates plan     │
│   └────┬────┘                           │
│        ▼                                │
│   ┌─────────┐                           │
│   │   ACT   │ ── Execute tool calls     │
│   └────┬────┘                           │
│        ▼                                │
│   ┌─────────┐                           │
│   │ OBSERVE │ ── Collect results        │
│   └────┬────┘                           │
│        │                                │
│        ▼                                │
│   Continue or Return                    │
└─────────────────────────────────────────┘
```

### Memory Management (MMU)

The MMU implements a **Hybrid Memory Architecture** designed for infinite session duration:

**1. Memory Tiers:**

| Tier | Purpose | Behavior |
|------|---------|----------|
| **Pinned** | System rules, Workspace info, **Env State** | Never compressed, always visible |
| **Stack** | Conversation history | Auto-archived to disk when full |
| **Frame** | Current task context | Refined on pop (removes noise) |

**2. Infinite Context Strategy (Rolling Summary):**
When the context window fills up (e.g., >200k tokens), the MMU performs a **"Distill & Archive"** operation:
1.  **Distill**: An LLM generates an *Execution Summary* of the current context (Goals, Completed Steps, Next Actions).
2.  **Archive**: The full raw message history is written to a file (e.g., `~/.nimbus/sessions/<id>/archive/part_timestamp.md`).
3.  **Reset**: The active memory is cleared and replaced with:
    *   A **Pointer** to the archive file.
    *   The **Execution Summary** to maintain cognitive continuity.

**3. Tooling Safety Net:**
If the Agent needs to recall specific details from the deep past, it can use the `ReadArchive` tool to access historical files referenced by the pointers.

### Process Roles (Permission Isolation)

| Role | Allowed Tools | Use Case |
|------|---------------|----------|
| `eye` | Read, ReadArchive, Glob, Grep | Code exploration |
| `body` | Read, ReadArchive, Write, Edit, Bash | Implementation |
| `mind` | Read, ReadArchive, Glob, Grep | Architecture design |
| `tongue` | Read, Glob, Bash | Testing |
| `nose` | Read, Glob, Grep | Code review |

### Doom Loop Detection

Prevents infinite loops by detecting repeated tool calls:

```python
DOOM_LOOP_THRESHOLD = 3  # Same params 3x = abort
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | Health check |
| `/api/v1/sessions` | POST | Create session |
| `/api/v1/sessions/{id}/chat` | POST | Chat (SSE stream) |
| `/session` | POST | Create session (OpenCode) |
| `/session/{id}/message` | POST | Send message (OpenCode) |
| `/v1/chat/completions` | POST | Chat completions (AI SDK v6) |

## Configuration

### LLM Configuration

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
```

### Environment Variables

```bash
export ANTHROPIC_API_KEY="sk-..."
export NIMBUS_LOG_LEVEL=DEBUG  # Enable debug logging
```

## Development

### Code Style

- **Formatter**: ruff (line-length=100)
- **Type Checker**: mypy (strict mode)
- **Python**: 3.10+ required

```bash
# Format code
ruff format src/ tests/

# Check types
mypy src/nimbus/
```

### Adding a New Tool

```python
# src/nimbus/tools/my_tool.py
from nimbus.tools import tool

@tool(
    name="MyTool",
    description="Does something useful",
    parameters={
        "param1": {"type": "string", "description": "First param"},
    }
)
async def my_tool(param1: str, workspace: Path) -> str:
    return f"Result: {param1}"
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Unknown tool" | Check tool registration in AgentOS |
| Context overflow | MMU auto-compresses, check logs |
| Timeout errors | Adjust `RuntimeConfig.default_timeout` |
| Doom loop abort | Review tool call patterns |

### Debug Logging

```bash
export NIMBUS_LOG_LEVEL=DEBUG
./nimbus start
```

## License

MIT
