# Nimbus Memory System: The Minimalist (V3) Refactor Plan

Based on the philosophy of "Keep It Simple" and treating the LLM as a highly capable, self-managing agent, we must strip away the over-engineered heuristics present in the current MMU and NimFS architectures. We will abandon complex "Smart Drops" and black-box Vector DB retrievals in favor of a deterministic **"Top-and-Tail" + "File-System-as-Memory"** approach.

## 1. MMU Simplification: The "Top-and-Tail" Context

The current MMU spends too much CPU and complexity "guessing" what the LLM needs via `archive_and_reset`, `drop_oldest_non_essential`, and token estimation heuristics. We will replace this with a brutal but effective truncation strategy.

### The Scratchpad (The "Top")
*   **Goal**: Offload "remembering progress" from the Python framework directly to the LLM.
*   **Implementation**: 
    1.  Introduce a new `UpdateScratchpad(text)` tool.
    2.  The VCPU `StateInit` perpetually injects the current contents of this Scratchpad directly into the System Rules (the "Anchor").
    3.  If the LLM solves a sub-problem, it calls `UpdateScratchpad` to write down the solution before proceeding.

### Hard Truncation (The "Tail")
*   **Goal**: Remove unpredictable token budgeting and summarize-on-the-fly logic.
*   **Implementation**:
    1.  Delete `token_budget.py` and its wild estimation heuristics. Token budgeting should be a simple `max_history_turns` integer (e.g., keep the last 10 messages).
    2.  When `len(messages) > max_history_turns`, slice the array: `messages = messages[-max_history_turns:]`.
    3.  Delete `_global_summary` generation using the LLM inline. Rely *entirely* on the Scratchpad to persist state across the slice boundary.

---

## 2. NimFS Simplification: "Unix File System as Memory"

The current `nimfs/manager.py` attempts to build a pseudo-database (`index.json`, `l0.abstract`, `l1.overview.md`) on top of the file system. It relies on a fragile glob-based `search_memory` tool.

### Strip the Database Abstraction
*   **Goal**: Let the LLM use the file system natively.
*   **Implementation**:
    1.  Remove the `MemoryCategory` and `l0/l1/l2` auto-generation folders.
    2.  When an Agent wants to "remember" an architecture rule, it shouldn't call a black-box `NimFSWriteMemory` tool that hides the path. It should simply write a file to `.nimbus/memory/architecture_rules.md` using standard file writing tools.

### Deprecate `search_memory`
*   **Goal**: Stop guessing relevance. Let the LLM grep what it needs.
*   **Implementation**:
    1.  Remove `NimFSSearchMemory`.
    2.  Ensure tools like `Bash` or `GrepSearch` have full access to the `.nimbus/memory/` directory. If the LLM needs to know about database configs, it runs `grep "postgres" .nimbus/memory/`.

---

## 3. Workflow & Architecture-as-a-Document

For long tasks or multi-agent workflows, context maintenance should not be implicit.

### Explicit `PLAN.md` Enforcement
*   **Implementation**: Before embarking on a complex sub-agent flow or heavy coding task, the Orchestrator agent should be explicitly prompted to generate a `PLAN.md` file in the workspace containing checkboxes.
*   As the task progresses through the "Tail" truncation boundaries, the LLM reads `PLAN.md` to reorient itself, checks off completed items using file-edit tools, and continues.

---

## Execution Phasing

1.  **Phase 1: MMU Truncation**: Replace `archive_and_reset` and `token_budget.py` with the rigid integer-based History Slice strategy. Add the `UpdateScratchpad` tool.
2.  **Phase 2: NimFS Diet**: Deprecate `search_memory` and L0/L1 layer generation. Transition memory writing strictly to raw file writes in `.nimbus/memory/`.
3.  **Phase 3: Prompting Update**: Adjust the Orchestrator prompt (`prompts.py`) to heavily emphasize using `UpdateScratchpad` and `PLAN.md` over relying on infinite context memory.
