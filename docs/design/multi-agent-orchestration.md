# Multi-Agent Orchestration Design: From Dispatch to Specialist Agents

> **Version**: 2.0.0
> **Status**: Proposed
> **Author**: Architect
> **Date**: 2026-02-19
> **Supersedes**: `docs/design/multi-agent-architecture.md` (v1.1.0)

---

## Thinking Process

### Problem Understanding

- **Core Problem**: The current Core->Dispatch architecture has fundamental design flaws that cause context loss, duplicated reasoning, and wasted compute. We need a multi-agent orchestration model where a Core Orchestrator coordinates specialized agents (Explorer, Implementer, Architect, Tester) without doing the work itself.
- **Key Constraints**:
  1. Must reuse existing infrastructure: VCPU, ExecutionState, MMU, KernelGate, ToolRegistry
  2. Must solve the goal summarization compression problem (5280->104 chars)
  3. Must support tool permission isolation per agent role
  4. Must support both parallel and serial execution patterns
  5. Must be backward-compatible with single-agent (standard) mode

### Current Architecture Problems (Evidence-Based)

| Problem | Evidence | Impact |
|---------|----------|--------|
| Goal over-compression | `vcpu.py:1639-1696` - `_prepare_goal_for_pinning` compresses to 100 chars | Executor loses critical context |
| Duplicated reasoning | Core reasons full solution (63s), then Executor re-reasons from scratch | 2x compute waste |
| No role differentiation | `dispatch_tool.py:209` - All Executors get same role="executor" | No specialization |
| Empty response loops | `vcpu.py:641-682` - `consecutive_empty_responses` handling | Executor gets stuck |
| Single Dispatch mechanism | `tools.py:22-61` - One tool for all task types | Cannot optimize per-task-type |
| Context not shared efficiently | `dispatch_tool.py:152-164` - Only file diffs injected as context | Reasoning context lost |

### Solution Exploration

| Solution | Description | Pros | Cons |
|----------|-------------|------|------|
| A: Evolve Dispatch Tool | Add role param to existing Dispatch, improve context passing | Minimal code change (~200 LOC), low risk | Still single-tool bottleneck, limited parallelism, Core still does its own reasoning |
| B: Orchestrator Layer + Specialist Profiles | New orchestration layer above AgentOS, VCPU stays as generic worker | Clean separation, full control over routing, reuses all existing infra | Medium complexity (~800 LOC), new orchestration module |
| C: Agent Protocol / Message Passing | Formal agent protocol with typed messages, async mailboxes, supervisor trees | Most flexible, production-grade | High complexity (~2000+ LOC), over-engineering for current scale |

### Decision Derivation

**Recommend Solution B** (Orchestrator Layer + Specialist Profiles) because:

1. It directly addresses all 6 identified problems without over-engineering
2. It maximally reuses existing `AgentOS.spawn()` / `AgentOS.wait()` / `AgentOS.wait_all()` infrastructure
3. The existing `AgentProfile` system (`core/profile.py`) already has the right shape for specialist definitions
4. The existing `ToolRegistry` role-based filtering (`tools/base.py:530-562`) already supports tool permission isolation
5. It creates a clean upgrade path from the current Dispatch architecture

---

## Summary

Replace the single `Dispatch` tool with a multi-tool specialist system. The Core Agent becomes a pure Orchestrator that delegates to typed specialists (Explorer, Implementer, Architect, Tester) via new meta-tools. Each specialist gets a purpose-built AgentProfile with restricted tool access, tailored system prompts, and appropriate iteration budgets. Context is passed via structured goal documents instead of compressed summaries.

---

## Design

### Architecture Overview

```
                         USER
                          |
                          v
+=========================================================+
|                    Core Orchestrator                      |
|  Role: Coordinate, Verify, Communicate                   |
|  Tools: Explore, Implement, Design, Test,                |
|         Verify, Read, Bash, Memo                         |
|  Does NOT: Write code, explore extensively               |
+=========================================================+
      |           |           |           |
      v           v           v           v
+-----------+ +-----------+ +-----------+ +-----------+
| Explorer  | |Implementer| | Architect | |  Tester   |
| Read-only | |Full tools | |Read+Write | |Read+Bash  |
| Glob,Grep | |Write,Edit | |(.md only) | |Glob only  |
| Read      | |Bash,Read  | |Glob,Grep  | |           |
+-----------+ +-----------+ +-----------+ +-----------+
      ^           ^           ^           ^
      |           |           |           |
+=========================================================+
|                    AgentOS Kernel                         |
|  spawn() / wait() / wait_all()                           |
|  VCPU + MMU + KernelGate + ToolRegistry                  |
+=========================================================+
```

### Core Components

#### 1. **Specialist Profiles** (extend `core/profile.py`)

Each specialist is defined as an `AgentProfile` with:
- `role`: unique identifier for tool filtering
- `allowed_tools`: whitelist of permitted tools
- `system_prompt`: role-specific instructions
- `max_iterations`: budget appropriate to the task type
- `max_consecutive_thoughts`: termination behavior

```
+------------------+-------------------------------------------+----------+--------+
| Specialist       | allowed_tools                             | max_iter | cost   |
+------------------+-------------------------------------------+----------+--------+
| Explorer         | Read, Glob, Grep                          | 20       | CHEAP  |
| Implementer      | Read, Write, Edit, Bash, Glob, Grep       | 30       | EXPENSIVE |
| Architect        | Read, Write(.md), Glob, Grep              | 15       | EXPENSIVE |
| Tester           | Read, Bash, Glob                          | 20       | MEDIUM |
+------------------+-------------------------------------------+----------+--------+
```

#### 2. **Specialist Meta-Tools** (new: `orchestration/specialist_tools.py`)

Replace the single `Dispatch` tool with multiple typed tools:

- **Explore(task, context?)** -> spawns Explorer agent, returns findings
- **Implement(task, context?)** -> spawns Implementer agent, returns diff summary
- **Design(task, context?)** -> spawns Architect agent, returns design doc
- **Test(task, context?)** -> spawns Tester agent, returns test results

Each tool:
1. Constructs a structured goal document (not a compressed summary)
2. Spawns a process with the appropriate `AgentProfile`
3. Waits for completion (with timeout)
4. Returns a structured result

#### 3. **Structured Context Protocol** (new: `orchestration/context_protocol.py`)

Solves the goal compression problem by passing structured documents:

```
## Goal Document Format

### Mission
{task} -- The specific task, uncompressed, verbatim from Orchestrator

### Context
{context} -- Relevant file contents, code snippets, prior findings

### Constraints
- Tool budget: {max_iterations} iterations
- Workspace: {workspace_path}
- Role: {role_name}

### Expected Output
{output_format} -- What the Orchestrator expects back
```

Key design decision: The goal document is **not summarized by LLM**. It is composed programmatically from the Orchestrator's tool call arguments. This eliminates the 5280->104 char compression problem entirely.

#### 4. **Orchestrator Profile** (extend `core/profile.py`)

The Core Agent becomes a pure Orchestrator:

```
Orchestrator Profile:
  role: "orchestrator"
  allowed_tools: [Explore, Implement, Design, Test, Verify, Read, Bash, Memo]
  system_prompt: "You are the Orchestrator. You coordinate, verify, and communicate.
                  You do NOT write code or explore extensively yourself.
                  For exploration: use Explore tool.
                  For implementation: use Implement tool.
                  For architecture: use Design tool.
                  For testing: use Test tool.
                  For quick checks: use Read or Bash directly."
  max_iterations: 50
  compact_on_limit: true
```

#### 5. **Parallel Execution Controller** (reuse `AgentOS.wait_all()`)

The Orchestrator can dispatch multiple specialists simultaneously:

```
Orchestrator calls:
  Explore(task="Find all test files")       -- background
  Explore(task="Read package.json")         -- background

AgentOS.wait_all([pid1, pid2])              -- parallel execution

Results returned to Orchestrator simultaneously
```

### Data Flow

```
User: "Add error handling to the API routes"
  |
  v
[Orchestrator] receives goal
  |
  | Step 1: Explore (parallel, cheap)
  +---> Explore(task="Find all API route files in src/")
  +---> Explore(task="Find existing error handling patterns")
  |
  | <--- Explorer 1: "Found routes in src/api/routes/*.py"
  | <--- Explorer 2: "Error patterns use try/except with HTTPException"
  |
  | Step 2: Plan (Orchestrator thinks)
  | "I need to add error handling to 5 route files using HTTPException pattern"
  |
  | Step 3: Implement (serial, expensive)
  +---> Implement(task="Add error handling to src/api/routes/users.py
  |              using HTTPException pattern. Wrap each endpoint in
  |              try/except. Return 400 for validation, 500 for server.",
  |              context="<existing code from Explorer>")
  |
  | <--- Implementer: "Modified users.py, added 3 try/except blocks"
  |
  | Step 4: Test (serial, after implementation)
  +---> Test(task="Run pytest on src/api/routes/test_users.py")
  |
  | <--- Tester: "4/4 tests pass"
  |
  | Step 5: Report to user
  v
[Orchestrator]: "Done. Added error handling to users.py, all tests pass."
```

### Tool Permission Isolation

The existing `ToolRegistry.get_definitions(role=...)` mechanism is sufficient. Each specialist process is spawned with its profile's `role`, and the `ToolRegistry` filters tool definitions by that role.

**Implementation approach**: Register specialist-specific tools with role restrictions in `ToolDefinition.roles`.

For the Architect's "Write .md only" constraint, a new `WriteMarkdown` tool variant or a sandbox rule in the `Write` tool itself is needed. Two options:

- **Option A (Recommended)**: Add a `file_extension_filter` parameter to `AgentProfile`. The KernelGate checks this filter before executing Write/Edit operations. This is ~20 LOC in `gate.py`.
- **Option B**: Create a separate `WriteDoc` tool that only writes `.md` files. Requires a new tool definition but no Gate changes.

### Execution Modes

#### Parallel Execution (Cheap Specialists)

Explorers can run in parallel because they are read-only and cannot conflict:

```python
# Orchestrator dispatches two Explorers simultaneously
pid1 = agent_os.spawn(goal=goal_1, profile=explorer_profile)
pid2 = agent_os.spawn(goal=goal_2, profile=explorer_profile)
results = await agent_os.wait_all([pid1, pid2], timeout=60.0)
```

#### Serial Execution (Expensive / Stateful Specialists)

Implementers and Testers must run serially when operating on the same files:

```python
# Implement first, then test
impl_pid = agent_os.spawn(goal=impl_goal, profile=implementer_profile)
impl_result = await agent_os.wait(impl_pid, timeout=120.0)

test_pid = agent_os.spawn(goal=test_goal, profile=tester_profile)
test_result = await agent_os.wait(test_pid, timeout=60.0)
```

#### Dependency Rules

| Specialist A | Can Run Parallel With | Must Run Serial With |
|-------------|----------------------|---------------------|
| Explorer    | Explorer, Architect  | -- (read-only, always safe) |
| Implementer | Explorer             | Implementer, Tester (file conflicts) |
| Architect   | Explorer             | Implementer (design before implement) |
| Tester      | Explorer             | Implementer (test after implement) |

The Orchestrator (LLM) decides the execution order. The framework does not enforce dependency ordering -- that is the Orchestrator's responsibility, following the "kernel provides mechanism, application decides policy" principle.

---

## Decisions

### ADR-1: Replace Single Dispatch with Typed Specialist Tools

- **Status**: Proposed
- **Decision**: Replace the single `Dispatch(task, context, model)` tool with typed tools: `Explore(task, context)`, `Implement(task, context)`, `Design(task, context)`, `Test(task, context)`.
- **Rationale**:
  1. Typed tools give the LLM clearer affordances for when to use what
  2. Each tool can have different timeout, iteration budget, and tool permissions
  3. Eliminates the "Core reasons everything then Dispatch re-reasons" problem because the Orchestrator knows it should delegate early
  4. The `DispatchTool` class can be refactored into a `SpecialistTool` base class that handles spawn/wait/diff logic
- **Consequences**:
  - Positive: Clearer task routing, better resource utilization, type safety
  - Negative: More tool definitions to maintain (4 vs 1), Orchestrator prompt must describe when to use each
- **Alternatives**:
  - Keep single Dispatch with `role` parameter: Simpler but LLM must decide role in addition to task
  - Free-form agent names: Too flexible, LLM may hallucinate invalid roles

### ADR-2: Structured Goal Documents Instead of LLM Summarization

- **Status**: Proposed
- **Decision**: Pass task context to specialists as structured goal documents composed programmatically, not summarized by LLM.
- **Rationale**:
  1. Current `_prepare_goal_for_pinning` (`vcpu.py:1639-1696`) compresses 5280 chars to 104 chars, losing critical details
  2. LLM summarization is non-deterministic and expensive (one extra LLM call per dispatch)
  3. The Orchestrator already has the relevant context in its tool call arguments -- just pass it through
  4. Structured documents are predictable, debuggable, and never lose information
- **Consequences**:
  - Positive: Zero information loss, deterministic, no extra LLM call
  - Negative: Goal documents may be longer, consuming more of the specialist's context budget
- **Alternatives**:
  - Improved LLM summarization: Still lossy, still expensive
  - File-based context sharing: Write context to file, tell specialist to read it -- adds latency

### ADR-3: Reuse AgentOS.spawn() Instead of Building New Orchestration Layer

- **Status**: Proposed
- **Decision**: Build specialist tools on top of existing `AgentOS.spawn()` + `AgentOS.wait()` + `AgentOS.wait_all()` API. Do NOT create a new orchestration kernel module.
- **Rationale**:
  1. `AgentOS.spawn()` (`agentos.py:368-489`) already supports `role`, `system_rules`, `max_iterations`, `llm_client`, `tools_override`, and `profile` parameters -- everything we need
  2. `AgentOS.wait_all()` (`agentos.py:551-580`) already supports parallel execution with timeout
  3. `Process` dataclass (`agentos.py:100-113`) already tracks state, VCPU, MMU, Gate
  4. Building a new layer would duplicate this infrastructure
- **Consequences**:
  - Positive: Minimal new code, battle-tested spawn/wait mechanism, inherits compaction/checkpoint support
  - Negative: Specialist tools are tightly coupled to AgentOS internals
- **Alternatives**:
  - New Orchestrator class wrapping AgentOS: Cleaner interface but more indirection
  - Subprocess-based agents: True isolation but massive overhead

### ADR-4: Gate-Level File Extension Filter for Architect

- **Status**: Proposed
- **Decision**: Add an optional `write_filter` field to `AgentProfile` that restricts which file extensions Write/Edit can operate on. Enforced in `KernelGate.syscall_tool()`.
- **Rationale**:
  1. The Architect should only write `.md` files, not source code
  2. This is a security boundary, not just a convention
  3. Gate-level enforcement means even a hallucinating LLM cannot bypass it
  4. Minimal change: ~20 LOC in `gate.py`, one new field in `AgentProfile`
- **Consequences**:
  - Positive: Hard security boundary, no new tools needed
  - Negative: Slightly more complex Gate logic
- **Alternatives**:
  - Separate `WriteDoc` tool: More tools to maintain, but no Gate changes
  - Trust the LLM prompt: Unreliable, LLM can hallucinate

### ADR-5: Orchestrator Decides Execution Order, Not Framework

- **Status**: Proposed
- **Decision**: The framework does not enforce task dependency ordering. The Orchestrator LLM decides what runs when based on its understanding of the task.
- **Rationale**:
  1. Following the "kernel provides mechanism, application decides policy" principle from the prior design (`multi-agent-architecture.md:11-13`)
  2. Task dependencies are context-dependent and hard to specify declaratively
  3. The LLM is capable of reasoning about what needs to happen first
  4. The existing `Scheduler` DAG infrastructure (`core/scheduler.py`) exists but is not used in practice -- adding automatic dependency resolution would be premature complexity
- **Consequences**:
  - Positive: Simple framework, flexible orchestration
  - Negative: If the LLM orders things wrong, results may be incorrect
- **Alternatives**:
  - Declarative dependency graph: More reliable but harder to express
  - Hardcoded patterns (always explore->implement->test): Too rigid

---

## Tradeoffs

### 1. Multiple Tools vs Single Dispatch

**Chose**: Multiple typed specialist tools

- **Sacrificed**: Simplicity of a single Dispatch tool
- **Gained**: Better type safety, clearer LLM affordances, per-specialist optimization
- **Why**: The single Dispatch tool was the root cause of "Core reasons everything then re-dispatches" -- typed tools make it obvious that the Orchestrator should delegate early and specifically

### 2. Structured Goals vs LLM Summarization

**Chose**: Structured goal documents (programmatic composition)

- **Sacrificed**: Potentially more compact representations via summarization
- **Gained**: Zero information loss, determinism, one fewer LLM call
- **Why**: The 5280->104 char compression was the #1 reported problem. Lossless is worth the extra tokens.

### 3. Reuse vs Rebuild

**Chose**: Build on existing AgentOS.spawn() infrastructure

- **Sacrificed**: Cleaner abstraction boundaries
- **Gained**: ~800 LOC less to write, inherited compaction/checkpoint/session support
- **Why**: The existing infrastructure already handles 90% of what we need. The remaining 10% (tool filtering, context protocol) is additive.

### 4. LLM-Driven Ordering vs Framework-Driven DAG

**Chose**: LLM decides execution order

- **Sacrificed**: Guaranteed correct ordering via declarative DAG
- **Gained**: Flexibility, simplicity, no DAG specification overhead
- **Why**: For the Orchestrator use case, the LLM has enough context to make ordering decisions. If this proves unreliable, we can add DAG support later via the existing `Scheduler`.

---

## Constraints

### Technical Constraints

- Single `asyncio` event loop -- all parallelism is cooperative
- Each specialist process gets its own VCPU+MMU+Gate stack -- memory overhead ~O(N) for N concurrent specialists
- LLM context window limits specialist goal document size (~8K tokens practical max)
- `ToolRegistry` role filtering is string-based, no hierarchical roles

### Business Constraints

- Must not break existing single-agent (standard) mode
- Must not break existing Dispatch-based (core) mode during migration
- LLM API cost should not increase more than 2x for equivalent tasks
- Latency should not increase more than 1.5x for equivalent tasks

### Operational Constraints

- Each specialist run generates its own trace logs (via `TraceManager`)
- Workspace diff tracking must still work for Implementer
- Memo persistence must be accessible to Orchestrator but not specialists (to prevent cross-contamination)

---

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Orchestrator over-delegates (calls Explore for trivial reads) | High | Low | System prompt: "For quick checks, use Read/Bash directly" |
| Specialist context budget exceeded by large goal documents | Medium | Medium | Cap goal document at 8K tokens, truncate with `[truncated]` marker |
| Orchestrator under-delegates (does the work itself) | Medium | High | System prompt enforcement + monitoring iteration counts |
| Parallel Explorers return conflicting/redundant info | Medium | Low | Orchestrator synthesizes; redundancy is acceptable |
| Implementer modifies files that Tester then can't find | Low | Medium | Workspace diff tracking (already exists in `workspace_diff.py`) |
| Migration breaks existing Dispatch users | Medium | Medium | Phase 1 keeps Dispatch working, Phase 2 deprecates |
| LLM cost increases due to multiple specialist calls | Medium | Medium | Explorer/Tester use cheaper models; budget monitoring |

---

## Evidence

### File References

| File | Lines | Finding |
|------|-------|---------|
| `src/nimbus/agentos.py:368-489` | `spawn()` | Already supports `role`, `system_rules`, `max_iterations`, `llm_client`, `tools_override`, `profile` |
| `src/nimbus/agentos.py:551-580` | `wait_all()` | Already supports parallel execution with timeout |
| `src/nimbus/agentos.py:100-113` | `Process` | Tracks state, VCPU, MMU, Gate per process |
| `src/nimbus/core/profile.py:15-72` | `AgentProfile` | Already has `role`, `allowed_tools`, `system_prompt`, `max_iterations` |
| `src/nimbus/tools/base.py:530-562` | `get_definitions(role=)` | Already filters tools by role |
| `src/nimbus/orchestration/dispatch_tool.py:104-275` | `dispatch()` | Current Dispatch mechanism -- to be refactored |
| `src/nimbus/orchestration/prompts.py:31-85` | Role prompts | CORE_INSTRUCTIONS and EXECUTOR_INSTRUCTIONS -- need specialist variants |
| `src/nimbus/orchestration/review_tool.py:84-212` | `ReviewTool` | Example of meta-tool pattern spawning with `tools_override=[]` |
| `src/nimbus/core/runtime/vcpu.py:1639-1696` | `_prepare_goal_for_pinning` | Goal compression problem -- bypassed by structured goal docs |
| `src/nimbus/os/gate.py:47-283` | `KernelGate` | Tool execution with timeout -- where write filter would be added |
| `src/nimbus/orchestration/workspace_diff.py:1-206` | `WorkspaceDiff` | Reusable for Implementer result tracking |
| `src/nimbus/tools/composite.py:1-38` | `CompositeToolRegistry` | Unified tool view -- specialists can use filtered subsets |

### Assumptions

1. **Assumption**: The LLM (Claude Opus / Sonnet) is capable of acting as a pure Orchestrator without writing code itself, given appropriate system prompts.
   - *Validation*: Test with real tasks, measure how often the Orchestrator "escapes" its role.

2. **Assumption**: Structured goal documents of ~4-8K tokens provide sufficient context for specialists.
   - *Validation*: Measure specialist success rate vs goal document size.

3. **Assumption**: Parallel Explorer execution provides a net speedup despite the overhead of spawning two processes.
   - *Validation*: Benchmark parallel vs serial Explorer on representative tasks.

4. **Assumption**: The current `ToolRegistry` role filtering is sufficient for tool permission isolation.
   - *Validation*: Verify that specialists cannot access tools outside their allowlist by testing with adversarial prompts.

---

## Integration Path

### What to Reuse (No Changes)

| Component | File | Reason |
|-----------|------|--------|
| VCPU | `core/runtime/vcpu.py` | Generic execution engine, works for all specialist roles |
| ExecutionState | `core/runtime/execution_state.py` | State tracking is role-agnostic |
| MMU | `core/memory/mmu.py` | Context management is role-agnostic |
| KernelGate | `os/gate.py` | Tool execution gateway (minor addition for write filter) |
| ToolRegistry | `tools/base.py` | Already supports role-based filtering |
| CompositeToolRegistry | `tools/composite.py` | Unified tool view |
| InstructionDecoder | `core/runtime/decoder.py` | Response parsing is role-agnostic |
| WorkspaceDiff | `orchestration/workspace_diff.py` | Reusable for Implementer tracking |
| EventStream | `os/gate.py` | Event emission for observability |
| Scheduler | `core/scheduler.py` | DAG execution (future use for complex workflows) |

### What to Modify (~200 LOC)

| Component | File | Change |
|-----------|------|--------|
| AgentProfile | `core/profile.py` | Add `create_explorer()`, `create_implementer()`, `create_architect()`, `create_tester()` factory methods. Add `write_filter` field. |
| KernelGate | `os/gate.py` | Add write filter enforcement (~20 LOC in `syscall_tool()`) |
| PromptManager | `orchestration/prompts.py` | Add specialist system prompts (EXPLORER_INSTRUCTIONS, IMPLEMENTER_INSTRUCTIONS, ARCHITECT_INSTRUCTIONS, TESTER_INSTRUCTIONS) |
| create_agent_os | `agentos.py:1562-1705` | Register specialist meta-tools when `profile="orchestrator"` |

### What to Create (~600 LOC)

| Component | File | Description |
|-----------|------|-------------|
| SpecialistTool | `orchestration/specialist_tools.py` | Base class for specialist meta-tools. Handles spawn/wait/diff/result formatting. Subclassed by Explore, Implement, Design, Test tools. |
| Context Protocol | `orchestration/context_protocol.py` | `GoalDocument` class that composes structured goal documents from task + context + constraints. |
| Tool Definitions | `orchestration/specialist_defs.py` | OpenAI-format tool definitions for Explore, Implement, Design, Test. |
| Orchestrator Profile | `core/profile.py` | `create_orchestrator()` factory method. |

### Migration Plan

#### Phase 1: Add Specialist Infrastructure (Non-Breaking)

1. Create `specialist_tools.py` with SpecialistTool base class
2. Create specialist tool definitions
3. Add specialist AgentProfile factories
4. Add specialist system prompts to PromptManager
5. Register specialist tools alongside existing Dispatch (both available)
6. Add `create_orchestrator()` profile

**Risk**: None. Existing Dispatch continues to work. New tools are additive.

#### Phase 2: Promote Specialist Mode

1. Update `create_agent_os` to register specialist tools when `profile="orchestrator"`
2. Update web-ui to show specialist tool calls distinctly
3. Add cost tracking per specialist type
4. Run A/B comparison: Dispatch mode vs Specialist mode on benchmark tasks

**Risk**: Low. Old mode still available via `profile="core"`.

#### Phase 3: Deprecate Dispatch

1. Mark `Dispatch` tool as deprecated in its description
2. Add migration warning in `DispatchTool.dispatch()` if used
3. Eventually remove `DispatchTool` class

**Risk**: Medium. Any external integrations using Dispatch must migrate.

### Estimated Effort

| Phase | New LOC | Modified LOC | Duration |
|-------|---------|-------------|----------|
| Phase 1 | ~600 | ~200 | 2-3 days |
| Phase 2 | ~100 | ~150 | 1-2 days |
| Phase 3 | -300 (removal) | ~50 | 0.5 day |
| **Total** | **~700 net** | **~400** | **~5 days** |

---

## Next Steps

1. **Implementer**: Create `src/nimbus/orchestration/specialist_tools.py` with `SpecialistTool` base class and `ExploreTool`, `ImplementTool`, `DesignTool`, `TestTool` subclasses
2. **Implementer**: Create `src/nimbus/orchestration/context_protocol.py` with `GoalDocument` builder
3. **Implementer**: Add specialist profiles to `src/nimbus/core/profile.py`
4. **Implementer**: Add specialist prompts to `src/nimbus/orchestration/prompts.py`
5. **Implementer**: Add write filter to `src/nimbus/os/gate.py`
6. **Implementer**: Wire up specialist tools in `create_agent_os()` for `profile="orchestrator"`
7. **Tester**: Benchmark specialist mode vs Dispatch mode on evoeval and terminal-bench tasks
8. **Architect**: Design cost tracking and budget enforcement for multi-specialist workflows

---

## Appendix A: Specialist System Prompt Sketches

### Explorer

```
You are the Explorer Agent -- a read-only investigator.

## Your Mission
- Search the codebase to find information requested by the Orchestrator.
- Read files, search patterns, understand structure.
- Report back with specific findings: file paths, line numbers, code snippets.

## Your Toolkit
- Read: Read file contents
- Glob: Find files by pattern
- Grep: Search file contents by regex

## Rules
- You are READ-ONLY. You cannot modify any files.
- Be thorough but concise. Report what you found, not what you think should be done.
- Include exact file paths and line numbers in your findings.
- If you can't find what was requested, say so clearly.
```

### Implementer

```
You are the Implementer Agent -- the hands-on engineer.

## Your Mission
- Execute the specific implementation task given by the Orchestrator.
- Write code, edit files, run commands as instructed.
- Do NOT deviate from the instructions.
- Report back with exactly what files were changed.

## Your Toolkit
- Read, Write, Edit, Bash, Glob, Grep

## Rules
- Action over talk. Just do it.
- Use exact filenames and patterns from the task description.
- If something fails, try to fix it before giving up.
- When done, return a brief summary of changes made.
```

### Tester

```
You are the Tester Agent -- the quality gatekeeper.

## Your Mission
- Run tests and verification commands as instructed.
- Report results clearly: what passed, what failed, with details.

## Your Toolkit
- Read, Bash, Glob

## Rules
- Run the exact commands requested.
- Report full output of test results.
- Do NOT fix failing tests yourself. Report the failures for the Orchestrator to handle.
- If a test command fails to run (not a test failure), explain why.
```

---

## Appendix B: SpecialistTool Base Class Sketch

```python
class SpecialistTool:
    """Base class for specialist meta-tools."""

    def __init__(
        self,
        agent_os: AgentOS,
        profile_factory: Callable[..., AgentProfile],
        workspace: Path,
    ):
        self._agent_os = agent_os
        self._profile_factory = profile_factory
        self._workspace = workspace

    async def execute(self, task: str, context: str = "", **kwargs) -> str:
        # 1. Build structured goal document
        goal = GoalDocument(
            mission=task,
            context=context,
            workspace=str(self._workspace),
        ).render()

        # 2. Create specialist profile
        model = kwargs.get("model", "")
        profile = self._profile_factory(model_id=model or "default")

        # 3. Take workspace snapshot (for Implementer)
        snapshot_before = None
        if profile.role == "implementer":
            snapshot_before = take_snapshot(self._workspace)

        # 4. Spawn specialist process
        pid = self._agent_os.spawn(
            goal=goal,
            profile=profile,
        )

        # 5. Wait for completion
        result = await self._agent_os.wait(pid, timeout=self._timeout)

        # 6. Compute diff (for Implementer)
        diff_summary = ""
        if snapshot_before is not None:
            snapshot_after = take_snapshot(self._workspace)
            diff = diff_snapshots(snapshot_before, snapshot_after)
            diff_summary = diff.summary()

        # 7. Format and return result
        return self._format_result(result, diff_summary)
```

---

## Appendix C: GoalDocument Builder Sketch

```python
@dataclass
class GoalDocument:
    mission: str           # The specific task
    context: str = ""      # Relevant code/findings
    workspace: str = ""    # Workspace path
    constraints: List[str] = field(default_factory=list)
    expected_output: str = ""

    def render(self) -> str:
        parts = [f"## Mission\n{self.mission}"]

        if self.context:
            # Cap context to prevent context overflow
            ctx = self.context
            if len(ctx) > 16000:
                ctx = ctx[:16000] + "\n\n[Context truncated]"
            parts.append(f"## Context\n{ctx}")

        if self.workspace:
            parts.append(f"## Workspace\n{self.workspace}")

        if self.constraints:
            constraints_str = "\n".join(f"- {c}" for c in self.constraints)
            parts.append(f"## Constraints\n{constraints_str}")

        if self.expected_output:
            parts.append(f"## Expected Output\n{self.expected_output}")

        return "\n\n".join(parts)
```
