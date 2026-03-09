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
- **Architecture**: `AgentOS` is a facade orchestrating specialized components:
    - **VCPU**: FSM-based execution engine (Think-Act-Observe).
    - **MMU**: Context & state management (Message compression, Pinned context).
    - **KernelGate**: Tool execution with safety, timeouts, and process-group abort.
    - **ALU/Adapter**: LLM interface (OpenAI/Anthropic).
    - **RuntimeLoop**: Drives the VCPU and handles message queues (Steering/Follow-up).
- **Tooling**: Registered via `ToolRegistry`. Built-in tools: `read`, `write`, `edit`, `bash`, `grep`.
- **Advanced Features**: Supports real-time steering, streaming tool outputs, and clean interruptions.
