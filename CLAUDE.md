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
- ~~Treat `AgentOS` and `vCPU` as legacy God Classes scheduled for decomposition.~~ (*Status: Decomposed in Phase 10/11!*) AgentOS is now a facade, and VCPU tool execution is cleanly extracted. Any new sub-agents or processes should be orchestrated via the `ProcessManager`.
