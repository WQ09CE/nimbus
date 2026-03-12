# Nimbus AgentOS

> **Building Resilient, Long-Horizon AI Agents with Operating System Principles.**

Nimbus is an AI Agent runtime framework inspired by OS kernel design. It treats the LLM as a CPU and builds a complete system abstraction around it — focusing on solving context drift, state management failures, and stability issues that plague agents during complex, long-horizon tasks.

Current version: **v0.2.0 (Nimbus Next)**

---

## 🏗 Core Architecture

```
┌─────────────────────────────────────────────────┐
│                AgentOS  (Facade)                 │
├──────────┬──────────┬──────────┬────────────────┤
│   VCPU   │   MMU    │KernelGate│  ALU / Adapter │
│  FSM引擎 │上下文管理│  工具执行  │   LLM 接口层   │
├──────────┴──────────┴──────────┴────────────────┤
│               RuntimeLoop  (驱动层)              │
│          Think → Act → Observe → Think …        │
└─────────────────────────────────────────────────┘
```

### VCPU — Virtual CPU

FSM-driven Think-Act-Observe loop:

```
IDLE ──→ THINKING ──→ ACTING ──→ OBSERVING
             ↑                       │
             │         (token正常)   │ (token > 85%)
             └───────────────────────┘
                                     │
                                     ↓
                               COMPRESSING
                                /         \
                   (压缩成功)  /           \ (超过 max_compactions)
                              ↓             ↓
                           THINKING        DEAD

THINKING/ACTING ──→ ERROR ──→ (retryable?) ──→ THINKING
                                    └─────────→ DEAD
```

**COMPRESSING triggers (3 paths):**
1. **Proactive**: token usage > 85% detected at loop start
2. **Reactive**: LLM returns `CTX_OVERFLOW` error
3. **Budget**: iteration count exceeds limit (`BUDGET_EXCEEDED`), resets counter

**Safety**: max 3 compressions (`max_compactions`), cooldown of 5 steps between compressions. Exceeding → `DEAD`.

- Max iterations: 200 (context compression is the real resource boundary)
- Real-time Steering injection (user can insert new instructions mid-run)
- Parallel tool calls via `asyncio.gather`

### MMU — Memory Management Unit

- **Pinned Context (Anchors)**: Fixed system rules and core goals, preventing LLM recency bias from causing "forgetting"
- **Dynamic Stream**: Auto-tracks conversation history, triggers compression at 85% token usage
- **Compression strategies**: Sliding Window (default) / Summary Compression / Semantic Relevance filtering (requires embedding service)
- Default context budget: 100k tokens (standard) / 1M tokens (claude-sonnet-4-6)

### KernelGate — Tool Execution Safety Layer

- Permission whitelist/blacklist filtering
- `asyncio.wait_for` timeout + process group `SIGKILL` isolation
- Tool result separation: `output` (LLM-visible) + `ui_detail` (UI-only display)
- Doom loop detection: consecutive identical tool calls are intercepted

### ALU / Adapter — LLM Interface Layer

Three-channel auto-selection:

| Channel | Use Case |
|---------|----------|
| Anthropic Native (OAuth) | Claude models + Pi OAuth credentials |
| OpenAI Codex (OAuth) | Codex models + ChatGPT subscription credentials |
| LiteLLM (default) | Gemini, OpenAI API Key, all other models |

---

## 🤝 Multi-Agent: spawn_agent

Unix philosophy: **sub-agents are subprocesses**, spawned via tool calls.

```python
# Sync: parent agent blocks and waits for result
spawn_agent(role="reader", goal="Read all test files and summarize coverage")

# Async: runs in background, returns PID
spawn_agent(role="worker", goal="Refactor the auth module", mode="async")
```

**Key design:**
- Sub-agents get a **fresh, independent MMU** — zero context pollution to parent
- Parent only receives a **structured summary** (max 4000 chars), not raw output
- Sub-agents operate in `contract_mode`: they MUST call `submit_result` to deliver findings
- Each sub-agent writes to its own scratchpad at `.nimbus/sessions/{sub_id}/scratchpad.md`
- Framework handles timeout, retry, and error isolation — parent just sees success/failure
- **Role-based permissions**: `reader` agents can only Read/Grep; `worker` agents get full tool access

**Context pressure test result:** 22 parallel sub-agents reading 100+ files consumed only ~20% of the parent agent's context window — the rest was contained within sub-agent boundaries.

---

## 🛠 Built-in Tools

| Tool | Description |
|------|-------------|
| `bash` | Execute shell commands with streaming output and auto-truncation |
| `read` | Read file contents with offset/limit support |
| `write` | Write files, auto-creates parent directories |
| `edit` | Exact text replacement with fuzzy fallback |
| `grep` | Regex search across files with glob filtering |
| `spawn_agent` | Delegate complex sub-tasks to independent child agents |
| `submit_result` | Sub-agent result delivery (contract mode) |

Custom tools via `@tool` decorator:

```python
from nimbus.core.tools.registry import tool

@tool
async def my_tool(param: str) -> str:
    return f"result: {param}"
```

---

## 🌐 Web UI

Real-time conversational interface built with **Next.js 14 + TypeScript + Tailwind CSS**.

- SSE real-time streaming (`_pendingStreamMsg` rAF buffer — solves same-frame event overwrite)
- Session management with create/delete/switch
- Tool execution visualization with expandable cards
- Sub-agent spawn timeline with nested tool call display
- Context window usage indicator (token count + percentage bar)
- Model selector with multi-provider support
- Port: `3000` (production) / `3001` (staging)

---

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/WQ09CE/nimbus.git
cd nimbus

# Install (uv recommended)
pip install -e ".[llm]"

# Start server
nimbus serve

# Start Web UI (separate terminal)
cd web-ui && npm install && npm run dev
```

Environment variables (pick one):

```bash
# API Key mode
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...

# Or Pi OAuth (auto-reads from ~/.pi/agent/auth.json)
```

---

## 🧪 Testing

```bash
# Core unit tests
pytest tests/core --tb=short

# Full test suite
pytest tests/ --tb=short
```

---

## 📁 Project Structure

```
nimbus/
├── src/nimbus/
│   ├── core/
│   │   ├── agent.py          # AgentOS facade (367 lines)
│   │   ├── vcpu.py           # FSM execution engine (326 lines)
│   │   ├── mmu.py            # Context management (486 lines)
│   │   ├── gate.py           # KernelGate safety layer (202 lines)
│   │   ├── loop.py           # RuntimeLoop driver (593 lines)
│   │   ├── decoder.py        # InstructionDecoder
│   │   ├── protocol.py       # Event / ActionIR / ToolResult protocol
│   │   ├── storage.py        # Conversation persistence
│   │   ├── models/           # Model registry & manifests
│   │   └── tools/            # Built-in tool implementations
│   │       ├── bash.py, read.py, write.py, edit.py, grep.py
│   │       ├── spawn_agent.py   # Multi-agent orchestration (421 lines)
│   │       ├── submit_result.py # Sub-agent result delivery
│   │       └── registry.py     # Tool registration & schema
│   ├── adapters/
│   │   ├── direct_adapter.py # Three-channel LLM adapter
│   │   ├── llm_factory.py    # Model factory
│   │   └── types.py          # LLM adapter type definitions
│   ├── server/
│   │   ├── api.py            # FastAPI routes
│   │   ├── session.py        # SessionManagerV2
│   │   ├── sse.py            # SSE Hub
│   │   ├── permission.py     # Runtime permission rules
│   │   └── log_hub.py        # Real-time log streaming
│   └── cli/                  # nimbus CLI (serve, run, config, session, acp)
├── web-ui/                   # Next.js frontend (21 components)
├── tests/                    # Test suite (33 files)
├── docs/                     # Architecture & design documents
└── skills/                   # Pluggable skill packs
```

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [Architecture Overview](docs/architecture_overview.md) | High-level framework design |
| [Actual Architecture](docs/actual_architecture.md) | Implementation reality vs. design |
| [Toolchain Design](docs/toolchain_design.md) | Tool system philosophy |
| [Multi-Agent Collaboration](docs/multi_agent_collaboration.md) | spawn_agent design rationale |
| [State & Context Management](docs/state_and_context_management.md) | MMU and context strategies |
| [System Rules & Prompting](docs/system_rules_and_prompting.md) | Prompt engineering approach |
| [Key Technologies](docs/actual_key_tech.md) | Core technical innovations |
| [Pi Coding Agent Study](docs/pi-coding-agent-article.md) | Research on Pi's architecture |
| [Pi Robustness Study](docs/pi-robustness-study.md) | Robustness analysis |
| [Sub-agent Orchestration](docs/subagent-orchestration-design.md) | Detailed orchestration design |

---

## 📈 Roadmap

- [x] **spawn_agent real implementation** — Nested `AgentOS` instances with contract mode
- [x] **Context window usage UI** — Token count + percentage indicator in footer
- [x] **Sub-agent timeline visualization** — Expandable tool call cards in Web UI
- [ ] **async spawn polling API** — `wait_agent(pid)` / `kill_agent(pid)` tools
- [ ] **MMU modularization** — Split into `context_manager` / `compressor` / `pinned_store`
- [ ] **Process tree UI** — Visualize parent-child agent relationships
- [ ] **Semantic Compression** — Integrate embedding service for relevance-based filtering

---

*Nimbus — Giving LLMs true system-level execution capabilities.*
