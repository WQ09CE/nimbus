# Nimbus Developer Context

This project uses an external AI Context Hub for maintaining cross-tool architectural knowledge.

Before making significant architectural changes or assumptions, **MUST READ** the living documents in the Context Hub:
`../ai-context-hub/projects/nimbus/`

Key files to check:
1. `STATUS.md`: Current project health, technical debt, and pending roadmap.
2. `ARCHITECTURE.md`: Subagent/Specialist boundaries, Component breakdown.
3. `DECISIONS.md`: ADRs and conventions.
4. `GOTCHAS.md`: Known edge cases, LLM hallucination firewalls, and model quirks.
5. `STACK.md`: Exact frameworks and tools in use.

**Important Instructions**:
- Always run `pytest tests/core --tb=short` before pushing commits manually.
- Use `ruff` for linting/formatting.

---

## Architecture: AgentOS (Nimbus Next)

`AgentOS` is a facade orchestrating specialized components (inspired by OS kernel design):

- **VCPU**: FSM-based execution engine (`IDLE → THINKING → ACTING → OBSERVING → COMPRESSING → ERROR → DEAD`). Drives the Think-Act-Observe loop.
- **MMU**: Context & state management. Handles message compression (sliding window / summary), Pinned context, and token budget enforcement.
- **KernelGate**: Tool execution with safety timeouts, process-group abort (`SIGKILL`), and result auditing.
- **ALU / Adapter**: LLM interface layer. Three channels:
  1. **Anthropic Native (OAuth)** — Direct Anthropic SDK with stealth headers (Claude Code identity).
  2. **OpenAI Codex (OAuth)** — Direct OpenAI SDK via ChatGPT subscription credentials.
  3. **LiteLLM (default)** — Fallback for Gemini, OpenAI API key, and others.
- **RuntimeLoop**: Drives the VCPU, manages `SteeringQueue` (real-time message injection) and `FollowUpQueue`.
- **InstructionDecoder**: Validates and decodes raw LLM output into structured `ToolCall` / text actions.

## Server Layer

- **FastAPI** server (`nimbus serve`) with SSE streaming (`/api/v1/sessions/{id}/events`).
- **SessionManagerV2**: Per-session `AgentOS` instances, cached between turns, with pre-warming.
- **SSEHub**: Fan-out event broadcasting to connected web clients.
- **PermissionManager**: Runtime tool permission rules.

## Tooling

Registered via `ToolRegistry`. Built-in tools (all in `src/nimbus/core/tools/`):
- `read`, `write`, `edit`, `bash`, `grep` — filesystem & shell
- `spawn_agent` — Multi-agent subprocess delegation (see below)

## spawn_agent (Multi-Agent)

Unix-philosophy multi-agent design: sub-agents are **subprocesses**, spawned via tool call.

```python
spawn_agent(role="Test Engineer", task="...", mode="sync")   # blocking
spawn_agent(role="Security Scanner", task="...", mode="async")  # background, returns PID
```

- Sub-agents get a **fresh MMU** (no context pollution from parent).
- They operate inside the same process but use isolated `AgentOS` loops and models.
- **Output isolation**: Large sub-agent returns are truncated (max 4000 chars) in the parent's tool result. Full details must be read from their disk `scratchpad`.

## Web UI

- **Next.js 14** (App Router) + **TypeScript** + **Tailwind CSS**.
- Served on port `3000` (deploy) / `3001` (staging).
- Real-time SSE rendering via `chat-store.ts` (Zustand).
- **Key fix (2026-03-10)**: `_pendingStreamMsg` rAF buffer — consecutive SSE events within the same animation frame now build on each other instead of overwriting stale store state.

## Known Issues / Tech Debt

- `mmu.py` (744 lines) — context management, compression, pinned store all coupled; needs splitting.
- `loop.py` (680 lines) — `RuntimeLoop` has god-class tendencies; `SteeringHandler` should be extracted.
- Sub-agent massive outputs were previously leaking into the parent Context Window, causing Token bloat (now truncated with a scratchpad redirect).
- `async` spawn_agent background polling is missing.
- Semantic Relevance compression requires external embedding service; silently degrades to Sliding Window (should emit explicit warning log).
- `Task was destroyed but it is pending!` asyncio warnings on session teardown — benign but noisy.
