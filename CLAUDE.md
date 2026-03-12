# Nimbus Developer Context

## Quick Reference

- **Version**: 0.2.0 (Nimbus Next)
- **Python**: 3.13+ | **Node**: 18+
- **Test**: `pytest tests/core --tb=short`
- **Lint**: `ruff check src/ tests/`
- **Server**: `nimbus serve` (port 8000)
- **Web UI**: `cd web-ui && npm run dev` (port 3000)

---

## Architecture: AgentOS

`AgentOS` is a facade orchestrating specialized components (inspired by OS kernel design):

| Component | File | Lines | Role |
|-----------|------|-------|------|
| **AgentOS** | `core/agent.py` | 367 | Facade: wires VCPU + MMU + Gate + Loop |
| **VCPU** | `core/vcpu.py` | 326 | FSM engine: IDLE → THINKING → ACTING → OBSERVING → COMPRESSING → ERROR → DEAD |
| **MMU** | `core/mmu.py` | 486 | Context management: Pinned anchors + dynamic stream + compression |
| **KernelGate** | `core/gate.py` | 202 | Tool execution: permissions, timeout, SIGKILL isolation |
| **RuntimeLoop** | `core/loop.py` | 593 | Drives VCPU steps, manages SteeringQueue and FollowUpQueue |
| **InstructionDecoder** | `core/decoder.py` | 154 | Validates/decodes LLM output into ActionIR |
| **Protocol** | `core/protocol.py` | 149 | Event / ActionIR / ToolResult / Fault types |

### ALU / Adapter — Three LLM Channels

1. **Anthropic Native (OAuth)** — Direct SDK with stealth headers (Claude Code identity)
2. **OpenAI Codex (OAuth)** — Direct SDK via ChatGPT subscription credentials
3. **LiteLLM (default)** — Fallback for Gemini, OpenAI API key, others

Auto-selected based on model name and available credentials.

---

## Server Layer

- **FastAPI** server (`nimbus serve`) with SSE streaming (`/api/v1/sessions/{id}/events`)
- **SessionManagerV2** (`server/session.py`): Per-session `AgentOS` instances, cached between turns
- **SSEHub** (`server/sse.py`): Fan-out event broadcasting to connected web clients
- **PermissionManager** (`server/permission.py`): Runtime tool permission rules
- **LogHub** (`server/log_hub.py`): Real-time log streaming to UI

---

## Tool System

Registered via `ToolRegistry` (`core/tools/registry.py`). Built-in tools:

| Tool | File | Key Detail |
|------|------|------------|
| `bash` | `tools/bash.py` | Streaming output, 60s timeout, auto-truncation (50KB / 2000 lines) |
| `read` | `tools/read.py` | Line-based offset/limit, byte truncation |
| `write` | `tools/write.py` | Auto-creates parent dirs |
| `edit` | `tools/edit.py` | Exact match + fuzzy fallback |
| `grep` | `tools/grep.py` | Regex with glob filter, per-line truncation |
| `spawn_agent` | `tools/spawn_agent.py` | **421 lines** — Full multi-agent orchestration (see below) |
| `submit_result` | `tools/submit_result.py` | Sub-agent result delivery in contract mode |

---

## spawn_agent — Multi-Agent System

**Status: Fully implemented** (not a stub).

### How it works:
1. Parent calls `spawn_agent(role, goal, timeout_seconds)`
2. Framework creates a **new AgentOS instance** with fresh MMU (zero context pollution)
3. Sub-agent runs in `contract_mode=True`: must call `submit_result` to deliver findings
4. Sub-agent writes progress to `.nimbus/sessions/{sub_id}/scratchpad.md`
5. Parent receives structured summary (max 4000 chars), not raw output
6. Role-based tool permissions: `reader` = Read/Grep only; `worker` = full access

### Key implementation details:
- Sub-agents share the parent's LLM adapter but get isolated MMU/VCPU/Gate
- Timeout handled at framework level with `asyncio.wait_for`
- Sub-agents cannot spawn further sub-agents (no nesting)
- Results are structured: `summary`, `findings[]`, `artifacts[]`, `scratchpad_path`

### Verified performance:
22 parallel sub-agents reading 100+ files → only ~20% parent context consumed.

---

## Web UI

- **Next.js 14** (App Router) + **TypeScript** + **Tailwind CSS** + **Zustand**
- 21 `.tsx` components across `app/`, `components/chat/`, `components/session/`
- SSE streaming: `_pendingStreamMsg` rAF buffer (consecutive events build on each other)
- Key components:
  - `ChatMessage.tsx` — Message rendering with tool cards
  - `SpawnAgentCard.tsx` — Sub-agent timeline with expandable nested tool calls
  - `TokenFooter.tsx` — Context window usage indicator (token count + % bar)
  - `MarkdownRenderer.tsx` — Fenced code blocks, tables, syntax highlighting
  - `SessionPanel.tsx` — Session CRUD with path selection
  - `ChatInput.tsx` — Input with interrupt support

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `nimbus serve` | Start FastAPI server (default port 8000) |
| `nimbus run "task"` | One-shot execution mode |
| `nimbus config show` | Show current configuration |
| `nimbus session list` | List active sessions |
| `nimbus acp` | Start as ACP agent over stdio |

---

## Development Conventions

- **Commits**: Semantic, English (`feat:`, `fix:`, `docs:`, `refactor:`, `perf:`, `test:`)
- **Testing**: Always run `pytest tests/core --tb=short` before pushing
- **Linting**: `ruff check src/ tests/`
- **Branching**: Feature branches → merge to `main` (no PR required for solo dev)

---

## Known Issues / Tech Debt

1. **`mmu.py` coupling** (486 lines) — Context management, compression, and pinned store are in one file; should be split into modules
2. **`loop.py` size** (593 lines) — `SteeringHandler` logic should be extracted
3. **Semantic compression degradation** — Silently falls back to Sliding Window when no embedding service; should emit warning log
4. **asyncio teardown warnings** — `Task was destroyed but it is pending!` on session teardown; benign but noisy
5. **async spawn_agent polling** — Background sub-agents lack `wait_agent(pid)` / `kill_agent(pid)` query tools
