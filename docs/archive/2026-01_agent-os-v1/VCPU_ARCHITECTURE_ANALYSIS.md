# vCPU Architecture Analysis Report

> **Purpose**: Help regain control over the vCPU architecture by clarifying concept mappings and identifying areas needing attention.
>
> **Date**: 2026-01-27
> **Analyst**: Mind Avatar (Architect)

---

## Summary

The Nimbus framework implements an "Agent OS" inspired by von Neumann architecture. The mapping is **partially complete but has structural gaps**. This document clarifies what exists, what's ambiguous, and what decisions are needed.

---

## 1. Architecture Current State

### ASCII Architecture Diagram

```
+===========================================================================+
|                        NIMBUS AGENT OS ARCHITECTURE                        |
+===========================================================================+

Layer 2 (Application)
+-----------------------------------------------------------------------+
|  CodeAgent (PID 1 / init)                                             |
|  - Bootstraps system, creates tools, manages subagents                |
|  - File: src/nimbus/core/agent.py                                     |
+-----------------------------------------------------------------------+
         |
         | uses
         v
Layer 1 (Kernel)
+-----------------------------------------------------------------------+
|  AgentOS                                                              |
|  +---------------------------+    +-----------------------------+     |
|  |     ProcessManager        |    |         vCPU Pool           |     |
|  |  - fork/exec/wait/kill    |    |  - Multi-vCPU routing       |     |
|  |  - Process table          |    |  - Affinity binding         |     |
|  +---------------------------+    +-----------------------------+     |
|              |                               |                         |
|              v                               v                         |
|  +---------------------------+    +-----------------------------+     |
|  |     AgentProcess (PCB)    |    |          vCPU               |     |
|  |  - pid, state, memory     |    |  - Think-Act-Observe loop   |     |
|  |  - Resource quotas        |    |  - Context assembly (MMU)   |     |
|  |  - Permissions            |    |  - Error handling (IRQ)     |     |
|  +---------------------------+    +-----------------------------+     |
|                                              |                         |
|                                              | calls                   |
|                                              v                         |
|  +---------------------------+    +-----------------------------+     |
|  |    LLMClient (ALU)        |<-->|    ToolRegistry (ISA)       |     |
|  |  - complete_with_tools()  |    |  - Read, Glob, Grep, Bash   |     |
|  |  - Reasoning/Computation  |    |  - Subagent, Batch          |     |
|  +---------------------------+    +-----------------------------+     |
+-----------------------------------------------------------------------+

Layer 1 (Kernel - Alternative Path)
+-----------------------------------------------------------------------+
|  AsyncRuntime (Scheduler)                                             |
|  +---------------------------+    +-----------------------------+     |
|  |       TaskDAG             |    |    ReplanCoordinator        |     |
|  |  - DAG of TaskNodes       |    |  - Dynamic replanning       |     |
|  |  - Parallel execution     |    |  - Task cancellation        |     |
|  +---------------------------+    +-----------------------------+     |
+-----------------------------------------------------------------------+

CRITICAL OBSERVATION: Two parallel execution models exist
  - Kernel model: AgentOS -> ProcessManager -> vCPU -> AgentProcess
  - DAG model: AsyncRuntime -> TaskDAG -> TaskNode
  - Currently NOT unified (CodeAgent uses SubagentRuntime, not AsyncRuntime)
```

---

## 2. Von Neumann Concept Mapping

### Complete Mapping Table

| Von Neumann Concept | Traditional OS | Nimbus Implementation | File Location | Status |
|---------------------|----------------|----------------------|---------------|--------|
| **CPU** | Processor | vCPU | `kernel/vcpu.py` | Implemented |
| **Control Unit** | CPU component | Think-Act-Observe loop | `vcpu.py:221-329` | Implemented |
| **ALU** | Arithmetic/Logic | LLMClient | `llm/base.py` | Implemented |
| **Registers** | Fast storage | AgentProcess.memory | `proc.py:55` | Partial |
| **RAM** | Main memory | TieredMemory? | `core/memory.py` | Ambiguous |
| **MMU** | Memory mapping | `_assemble_context()` | `vcpu.py:524-543` | Basic |
| **Program Counter** | Current instr | (implicit in loop) | - | Missing |
| **Instruction** | Machine code | Tool call | `tools/*.py` | Implemented |
| **ISA** | Instruction set | ToolRegistry | `tools/base.py` | Implemented |
| **Interrupt Handler** | IRQ handler | Error recovery | `vcpu.py:988-1001` | Basic |
| **Process** | PCB | AgentProcess | `kernel/proc.py` | Implemented |
| **Scheduler** | Process scheduler | ProcessManager + AsyncRuntime | `scheduler.py`, `executor.py` | Dual |
| **init/PID 1** | First process | CodeAgent | `core/agent.py` | Implemented |
| **fork/exec** | Process creation | ProcessManager.fork/exec | `scheduler.py:117-240` | Implemented |
| **wait** | Process sync | ProcessManager.wait | `scheduler.py:241-295` | Implemented |
| **kill** | Process termination | ProcessManager.kill | `scheduler.py:310-345` | Implemented |
| **IPC** | Inter-process comm | IPCMessage, Signal | `kernel/ipc.py` | Partial |

### Status Legend
- **Implemented**: Clear mapping, working code
- **Partial**: Concept exists but incomplete
- **Basic**: Minimal implementation
- **Ambiguous**: Unclear how it fits
- **Missing**: Not explicitly modeled

---

## 3. Identified Ambiguities

### 3.1 Memory Hierarchy Confusion

**Problem**: The von Neumann "Registers vs RAM" distinction is unclear.

```
Von Neumann:
  Registers (tiny, fast) -> RAM (larger, slower) -> Disk (huge, slowest)

Current Nimbus mapping in vcpu.py:
  AgentProcess.memory = "Registers" (context window)

But TieredMemoryManager has:
  Pinned (1K tokens)  -> "BIOS/ROM"?
  Working (4K tokens) -> "L1 Cache"?
  Episodic (8K tokens) -> "RAM"?
  Semantic (4K tokens) -> "Disk/Swap"?

UNCLEAR: Where does AgentProcess.memory map relative to TieredMemory?
```

**Evidence**: `vcpu.py:524-526`
```python
def _assemble_context(self, process: AgentProcess) -> List[Dict[str, Any]]:
    """Assemble context window for LLM (MMU - Registers)."""
    return process.memory.copy()
```

The comment says "MMU - Registers" but returns full memory, not a subset.

### 3.2 Dual Execution Models

**Problem**: Two parallel systems exist for task execution.

| Aspect | Kernel Model | DAG Model |
|--------|--------------|-----------|
| Entry point | AgentOS.spawn() | AsyncRuntime.execute_dag() |
| Task unit | AgentProcess | TaskNode |
| Execution | vCPU.execute() | _execute_task() |
| State tracking | ProcessState enum | TaskStatus enum |
| Dependencies | Parent-child tree | DAG edges |

**Evidence**: `agent.py:451-481` (CodeAgent uses SubagentRuntime, not kernel)
```python
async def _run_task_mode(...):
    from .task import TaskPlanner, SubagentRuntime, SubagentRuntimeConfig
    planner = TaskPlanner(self.llm_client)
    dag = await planner.plan(goal=user_input, context=context)
    runtime = SubagentRuntime(...)
    async for event in runtime.execute_stream(dag, parent_context=context):
        yield event
```

### 3.3 Missing Program Counter

**Problem**: No explicit "current instruction" concept.

In real CPUs, PC points to current instruction. In vCPU:
- The "Think-Act-Observe" loop is implicit
- No explicit "instruction pointer" tracking which step we're on
- `iteration` counter exists but doesn't map to PC semantics

**Evidence**: `vcpu.py:269-324`
```python
iteration = 0
while iteration < self.config.max_iterations:
    # Step 1: Assemble context (MMU)
    # Step 2: Think (Control Unit -> ALU)
    # Step 3: Check stop condition
    # Step 4: Act (Control Unit -> Tools)
    # Step 5: Observe (Update memory)
    iteration += 1
```

### 3.4 Process vs Task Semantic Gap

**Problem**: AgentProcess and TaskNode serve similar purposes but at different levels.

| Attribute | AgentProcess | TaskNode |
|-----------|--------------|----------|
| ID | pid (string) | id (string) |
| State | ProcessState (8 states) | TaskStatus (5 states) |
| Dependencies | parent_pid (tree) | depends_on (DAG) |
| Execution | vCPU.execute() | AsyncRuntime._execute_task() |
| Memory | process.memory | (none, uses params) |
| Resources | token_budget, max_turns | timeout constraint |

**Key Question**: Is TaskNode a "lightweight process" or a "function call"?

---

## 4. Design Decisions Needed

### Decision 1: Unify or Separate Execution Models?

**Options**:

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| A. Unify | Make TaskNode wrap AgentProcess | Single mental model | Refactoring cost |
| B. Separate | Keep both, clear boundaries | No refactoring | Two systems to maintain |
| C. Deprecate DAG | Use only kernel model | Simpler | Lose DAG parallelism |

**Recommendation**: Option B (Separate with clear boundaries)
- Kernel model for long-running subagents (eye, body, mind...)
- DAG model for short-lived tool orchestration
- Document the boundary explicitly

### Decision 2: Clarify Memory Hierarchy

**Options**:

| Option | Description | Mapping |
|--------|-------------|---------|
| A. Keep Current | process.memory = context window | Registers = context window |
| B. Tiered Integration | Process uses TieredMemory | Registers = Working tier |
| C. Explicit Layers | Model all 3 levels | Registers/RAM/Disk explicit |

**Recommendation**: Option A with documentation
- Current model is pragmatic (context window = what LLM sees)
- Add docstring clarifying "Registers" is a metaphor, not literal
- TieredMemory is a separate concern (session persistence)

### Decision 3: Model Program Counter?

**Options**:

| Option | Description | Benefit |
|--------|-------------|---------|
| A. Keep Implicit | Current loop is sufficient | Simplicity |
| B. Explicit PC | Add PC field to process | Debuggability, save/restore |
| C. Instruction Queue | Model tool calls as instructions | Replayability |

**Recommendation**: Option A for now
- The Think-Act-Observe loop is deterministic enough
- PC would add complexity without clear benefit
- Consider Option C later for "replayable agents"

---

## 5. Clear Boundaries (Current State)

### What vCPU IS:
1. **Control Unit**: Orchestrates Think-Act-Observe loop
2. **MMU (basic)**: Assembles context from process.memory
3. **Interrupt Handler**: Catches errors, handles resource limits
4. **"Talkative LLM" corrector**: Handles format hallucinations

### What vCPU is NOT:
1. **NOT a full CPU emulator**: No instruction set, no pipelines
2. **NOT the scheduler**: ProcessManager/AsyncRuntime do scheduling
3. **NOT memory manager**: TieredMemory is separate from vCPU
4. **NOT the ALU**: LLMClient is the "computation" unit

### What belongs in Kernel vs Application:

| Layer | Components | Responsibility |
|-------|------------|----------------|
| Kernel (Layer 1) | vCPU, ProcessManager, AgentProcess, IPC | Process lifecycle, execution |
| Application (Layer 2) | CodeAgent, PlannerPipeline, Skills | User-facing orchestration |

---

## 6. Recommended Next Steps

### Priority 1: Documentation (Low effort, High value)
1. Add architecture diagram to CLAUDE.md
2. Document the dual-model boundary clearly
3. Clarify "Registers" metaphor in vcpu.py docstring

### Priority 2: Cleanup (Medium effort)
1. Consider deprecating AsyncRuntime in favor of SubagentRuntime
2. Or clearly document when to use which runtime
3. Unify ProcessState and TaskStatus if possible

### Priority 3: Enhancement (High effort, optional)
1. If needed: Model explicit instruction queue for debugging
2. If needed: Integrate TieredMemory with kernel model
3. If needed: Add process checkpointing for long-running agents

---

## 7. Key Questions for User Decision

### Question 1: Runtime Unification

> The codebase has two execution models:
> - **Kernel model** (AgentOS/vCPU/ProcessManager) - used for subagents
> - **DAG model** (AsyncRuntime/TaskDAG) - used for tool orchestration
>
> **Do you want to:**
> A) Keep both and document boundaries
> B) Unify them (significant refactoring)
> C) Deprecate one

### Question 2: Memory Model Formalization

> Currently, `AgentProcess.memory` is called "Registers" but is actually the full conversation history.
>
> **Do you want to:**
> A) Keep the metaphor (it's "good enough")
> B) Rename to avoid confusion (e.g., "context_buffer")
> C) Actually implement register-like semantics (subset of memory)

### Question 3: Architecture Scope

> The von Neumann mapping is currently a **loose analogy**. Some concepts (like PC, cache hierarchy) are not literally implemented.
>
> **How strict should the mapping be:**
> A) Keep it as a mental model / documentation aid
> B) Make it more literal (implement missing concepts)
> C) Abandon the analogy and use simpler terminology

---

## Evidence Sources

All findings based on code analysis:

| Finding | File | Lines |
|---------|------|-------|
| vCPU definition | `kernel/vcpu.py` | 1-33, 171-220 |
| Think-Act-Observe loop | `kernel/vcpu.py` | 221-329 |
| Memory = "Registers" | `kernel/vcpu.py` | 524-543 |
| Process definition | `kernel/proc.py` | 36-120 |
| ProcessManager | `kernel/scheduler.py` | 31-453 |
| AgentOS interface | `kernel/__init__.py` | 55-333 |
| CodeAgent = init | `core/agent.py` | 1-18, 54-78 |
| AsyncRuntime | `core/runtime/executor.py` | 65-306 |
| TaskNode/TaskDAG | `core/types.py` | 265-550 |

---

## Assumptions

The following assumptions were made where code evidence was insufficient:

1. **SubagentRuntime** (referenced in agent.py but not fully read) follows similar patterns to AsyncRuntime
2. **TieredMemoryManager** is used primarily for session persistence, not kernel-level memory management
3. The dual-model situation is **intentional** rather than technical debt (to be confirmed with user)
