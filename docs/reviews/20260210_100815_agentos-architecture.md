# AI Review Committee: agentos-architecture

- **Date**: 2026-02-10 10:08:15
- **Focus**: architecture
- **Reviewers**: 3
- **Total Time**: 81.8s

---

## Review by `anthropic/claude-opus-4-6`

# Architecture Review: Nimbus v2 AgentOS Integration Layer

**Reviewer:** anthropic/claude-opus-4-6
**Focus:** Architecture
**Artifact:** `src/nimbus/agentos.py` (~1450 lines)

---

## 1. Overall Assessment

**Score: 6/10** — A coherent and ambitious OS-inspired agent architecture with strong conceptual foundations, undermined by a God-class integration layer that conflates orchestration, lifecycle management, memory, sessions, and tool registration into a single 1450-line file with several complexity hotspots and missing operational guardrails.

---

## 2. Strengths

### 2.1 Sound Conceptual Model
The OS metaphor (VCPU, MMU, Gate, Scheduler, Process) is not just cosmetic — it maps cleanly to real concerns in agentic systems. Processes encapsulate execution state, the MMU manages context windows, the Gate enforces permissions. This is a genuinely useful abstraction taxonomy that aids reasoning about the system.

### 2.2 Role-Based Tool Filtering (RBAC)
The `ToolRegistry` with role-scoped visibility (`roles=["core", "chat"]`) is a well-considered security boundary for the dual-agent architecture. Making executor agents unable to even *see* orchestration tools (Dispatch, Verify, ReviewCommittee) is a defense-in-depth approach that prevents prompt injection from escalating privileges through tool discovery.

### 2.3 Dual-Agent Separation of Privilege
Core Agent (read-only + dispatch) vs. Executor Agent (read-write) is an excellent architectural decision. This is the principle of least privilege applied to agent systems. The spawn-and-wait communication pattern creates a clear audit boundary between planning and execution.

### 2.4 Compaction as a First-Class Concern
Treating context compaction as an explicit MMU-level operation with configurable budgets (`compact_on_limit=True` for infinite context) rather than silently truncating is architecturally mature. Most agent frameworks ignore this problem entirely.

### 2.5 Stream-Based Observability
`run_stream()` yielding events, combined with the `EventStream` and `_emit_event` pattern, provides a clean observability seam. This is the right primitive for building UIs, logging, and debugging on top of the system.

---

## 3. Issues Found

### 🔴 Critical

**C1: No Process Cleanup / GC — Unbounded Memory Growth**
- **Location:** `AgentOS._processes` dict; no cleanup path visible
- **Description:** Completed processes (and their associated MMU state, pinned context, message histories, memo stores) are never removed from `_processes`. In a long-running server scenario (e.g., a chat service spawning processes per-request), this is a memory leak that will eventually OOM the host. The `max_processes` config limits concurrent spawns but doesn't address completed process retention.
- **Suggestion:** Implement a process lifecycle state machine (`CREATED → RUNNING → COMPLETED → ARCHIVED`) with configurable TTL-based eviction for completed processes. At minimum, add a `reap()` method that clears completed process state, and call it from `wait()` after result extraction. Consider weak references for the process map or an LRU eviction policy.

**C2: No Isolation Between Process Failures**
- **Location:** `_run_process` (main execution loop)
- **Description:** If a process's VCPU or Gate throws an unhandled exception, the review material doesn't show any fault isolation preventing this from poisoning the `AgentOS` instance's shared state (scheduler, event stream, tool registry). A single misbehaving tool could corrupt the registry. There's no process-level exception boundary that guarantees the parent OS remains healthy.
- **Suggestion:** Wrap `_run_process` in a structured error boundary that catches all exceptions, transitions the process to a `FAULTED` state, emits a fault event, and ensures all process-local resources are cleaned up. Consider `asyncio.TaskGroup` (3.11+) or equivalent for structured concurrency guarantees.

### 🟡 Major

**M1: God Class — AgentOS Violates Single Responsibility**
- **Location:** `AgentOS` class (entire file)
- **Description:** `AgentOS` is simultaneously a process manager, session manager, tool registry facade, compaction coordinator, skill loader, and chat endpoint. The 1450-line single class with ~25 public/private methods is a maintainability and testability bottleneck. Every new feature (logging policy, new agent role, alternative compaction strategy) requires modifying this class.
- **Suggestion:** Extract into focused subsystems with clear interfaces:
  - `ProcessManager` — spawn, wait, wait_all, reap, list, interrupt
  - `SessionController` — chat, restore_session, new_session, checkpoint
  - `CompactionCoordinator` — compact, _compaction_for_process
  - `ToolFacade` — register_tool, unregister_tool, list_tools, reload_skills
  - `AgentOS` becomes a thin composition root that delegates to these

**M2: `__init__` Does Too Much — 120 Lines of Eager Initialization**
- **Location:** `AgentOS.__init__`
- **Description:** Skill loading, tool registration, prompt assembly, session directory creation, compaction engine setup — all happening eagerly in the constructor. This makes the class impossible to partially initialize for testing, creates ordering dependencies between initialization steps, and means any failure in skill loading prevents the entire OS from starting.
- **Suggestion:** Use a builder pattern or phased initialization:
  ```python
  os = AgentOS(llm_client, config)  # Minimal: just store config
  os.initialize_tools(tools)         # Tool registry setup
  os.load_skills()                   # Skill loading (can fail gracefully)
  os.start()                         # Session setup, scheduler start
  ```
  Or use a factory: `AgentOS.create(llm_client, tools, config)` that handles the full setup sequence.

**M3: Duplicate Process Creation Logic**
- **Location:** `chat()`, `spawn()`, `restore_session()`
- **Description:** All three methods independently construct VCPU/MMU/Gate/Process assemblies with slightly different parameters. This is a classic "shotgun surgery" smell — adding a new per-process concern (e.g., telemetry, rate limiting) requires changes in three places, with high risk of inconsistency.
- **Suggestion:** Extract a `_create_process(goal, role, system_rules, llm_client, tools_override) -> Process` factory method that all three call. Session restoration adds its checkpoint overlay *after* base process creation.

**M4: `_compaction_for_process` — Nested Async Closure Complexity**
- **Location:** `_compaction_for_process` (~100 lines)
- **Description:** This method is described as having nested async closures, which in Python creates issues with: (a) debuggability (stack traces through closures are opaque), (b) testability (inner closures can't be independently tested), (c) resource lifecycle (closures capturing mutable process state create subtle bugs if the process is modified concurrently).
- **Suggestion:** Decompose into a `CompactionPipeline` class:
  ```python
  class CompactionPipeline:
      async def extract_context(self, mmu: MMU) -> CompactionInput: ...
      async def summarize(self, input: CompactionInput) -> CompactionOutput: ...
      async def apply(self, mmu: MMU, output: CompactionOutput) -> bool: ...
  ```
  Each step is testable, the pipeline is inspectable, and there's no closure-based state capture.

**M5: Chat Session State Machine is Implicit**
- **Location:** `chat()` method
- **Description:** `chat()` creates a full `Process` on first call, then on subsequent calls injects messages into the existing process's inbox. The state transitions (no session → active session → compacted session → interrupted session → restored session) are implicit in conditional logic scattered across `chat()`, `interrupt()`, `restore_session()`, and `compact()`. This is a bug factory.
- **Suggestion:** Model session state explicitly:
  ```python
  class ChatSession:
      state: Literal["idle", "active", "compacting", "interrupted", "terminated"]
      def receive_message(self, msg) -> None: ...  # validates state transition
      def interrupt(self) -> None: ...
      def compact(self) -> None: ...
  ```

### 🔵 Minor

**m1: spawn() Creates MMU/Memo for Pure-Reasoning Processes**
- **Location:** `spawn()` with `tools_override=[]`
- **Description:** Even when spawning a process with no tools (pure reasoning), a full MMU with pinned context and a Memo tool are created. This is wasteful for short-lived reasoning tasks.
- **Suggestion:** Lazy initialization of MMU and Memo — only create when first accessed. Or accept a `lightweight=True` flag for reasoning-only processes.

**m2: `max_processes` Only Limits Concurrent Count, Not Total**
- **Location:** `AgentOSConfig.max_processes`
- **Description:** This seems to limit simultaneous processes but doesn't bound the total number of processes ever created (since there's no cleanup). The naming is misleading.
- **Suggestion:** Rename to `max_concurrent_processes` and add `max_retained_processes` for the history/GC concern.

**m3: No Typing for Process Return Channel**
- **Location:** `wait()` returns `ToolResult`
- **Description:** Processes communicate results via `ToolResult`, which appears to be a generic container. There's no type safety on what a specific process role is expected to return, making it easy for callers to misinterpret results.
- **Suggestion:** Consider generic typing: `Process[T]` where `wait()` returns `T`, or at minimum document the expected result schema per role.

---

## 4. Architecture/Design Observations

### 4.1 The OS Metaphor Has a Kernel-Mode Gap
The dual-agent model (Core = kernel, Executor = userspace) is compelling, but the architecture lacks a true syscall boundary. When an Executor calls a tool, it goes through the Gate, but there's no mechanism for the *Gate itself* to invoke kernel-level operations (e.g., "this tool call requires Core approval"). The RBAC is static at spawn time. A real OS has dynamic capability checks — consider whether runtime privilege escalation/de-escalation is needed.

### 4.2 Compaction Is a Correctness Risk
LLM-based summarization for context compaction is inherently lossy. If a compaction discards information that a later tool call depends on, the process will silently produce wrong results. The architecture doesn't appear to have a mechanism for detecting or recovering from bad compactions (e.g., keeping a "compaction log" that can be re-expanded if the agent gets confused).

### 4.3 Concurrency Model Needs Clarity
`wait_all()` parallelizes multiple processes, but the concurrency model isn't explicit. Are processes sharing a single event loop? Can two processes call the same tool concurrently? Is the ToolRegistry thread-safe? Is the LLM client shared across processes (and if so, does it handle concurrent requests)? These questions need answers in the architecture, not just the implementation.

### 4.4 The Scheduler Is Underutilized
There's a `Scheduler` with `DAG` and `Task` primitives, but the primary API surface (`spawn`/`wait`/`run`) seems to bypass structured scheduling in favor of ad-hoc process management. Either the Scheduler should be the primary orchestration mechanism (processes are tasks in a DAG), or it should be removed to reduce conceptual overhead.

### 4.5 Missing Observability Contract
`_emit_event` exists but there's no defined event schema or catalog. Without a documented set of event types and their payloads, consumers of `run_stream()` are coding against an unstable interface. This is especially important for the ReviewCommittee tool, which presumably needs to inspect execution traces.

---

## 5. Actionable Recommendations (Prioritized)

### Priority 1: Implement Process Lifecycle Management (C1 + C2)
**Why first:** This is the only issue that can cause production incidents (OOM, corrupted state). Everything else is maintainability.

- Add process states: `CREATED → RUNNING → COMPLETED | FAULTED → REAPED`
- Add `reap(pid)` and `reap_completed(max_age=timedelta)` methods
- Wrap `_run_process` in a fault boundary that always transitions to a terminal state
- Add a background reaper task or hook reaping into `spawn()` (reap before spawn)
- Write integration tests that spawn 100+ processes and verify memory remains bounded

### Priority 2: Decompose AgentOS (M1 + M2 + M3)
**Why second:** The God-class structure will make Priority 1 harder to implement correctly and will compound every future change.

- Extract `ProcessManager`, `SessionController`, `CompactionCoordinator`, `ToolFacade`
- Unify process creation into a single `_create_process()` factory
- Move `__init__` logic into a builder or `create()` factory with phased initialization
- Target: `AgentOS` should be <200 lines of composition and delegation

### Priority 3: Make the Concurrency Model Explicit (Observation 4.3)
**Why third:** The system will break in subtle ways under concurrent load without this, and it's cheaper to define the model now than to debug race conditions later.

- Document whether processes share an event loop (they likely do given asyncio)
- Add a concurrency test: two processes calling the same Bash tool simultaneously
- Decide if ToolRegistry is append-only (safe) or mutable (needs locking)
- If LLM client is shared, ensure it's concurrent-safe or add per-process client pooling
- Consider whether `inject_message()` needs synchronization with an in-flight `_run_process`

---

**Summary:** The architecture is intellectually sound and the OS metaphor provides genuine structural benefits. The primary risks are operational (no GC, no fault isolation) and maintainability (God class, duplicated logic). Addressing process lifecycle and decomposition will put this on solid footing for production use.

---

## Review by `openai/gpt-5.3-codex`

(Review failed: LLM call failed: Client error '401 Unauthorized' for url 'http://localhost:3031/v1/chat/completions'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401)

---

## Review by `google/gemini-3-pro-high`

(Review failed: LLM call failed: Client error '400 Bad Request' for url 'http://localhost:3031/v1/chat/completions'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400)

---
