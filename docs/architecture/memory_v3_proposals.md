# Nimbus Memory System: Current State & V3 Upgrade Proposals

Based on a deep-dive analysis of the Nimbus `core.memory` (MMU) and `core.nimfs` modules, here is a breakdown of the current architecture and a strategic roadmap for a "Memory V3" upgrade.

## 1. Current State (V2) Architecture

Nimbus currently employs a pragmatic, text-based "Anchor & Stream" architecture backed by a local filesystem hierarchy.

### The MMU (Memory Management Unit)
*   **Anchor & Stream Pipeline:** `mmu.py` separates immutable constraints (System Rules, Goal, Global Summary) from the mutable "Stream" (StackFrames of LLM messages).
*   **Token Budgeting:** Uses rough heuristics (`chars / 4` in `token_budget.py`) to manage context limits.
*   **Context Assembly:** `context_assembler.py` handles the construction of the final LLM prompt. It features "Lazy Expansion" (inline substitution of NimFS references if budget allows) and naive image downgrading (dropping duplicate or excess images).
*   **Smart Drop & Compaction:** When approaching token limits, the MMU drops failed tool calls first, then oldest messages. If that fails, `archive_and_reset` triggers an inline LLM summarization of the history to compress it.

### NimFS (Nimbus File System)
*   **Dual-Partition Storage:** `manager.py` splits data into `artifacts/` (short-lived IPC tool outputs) and `memory/` (long-term knowledge).
*   **L0/L1/L2 Hierarchy:** Memories are saved on disk with varying levels of detail (`l0.abstract`, `l1.overview.md`, `l2.content.md`), allowing the system to inject lightweight `l0` summaries into the prompt without blowing up the context.
*   **Search Limitations:** `search_memory` relies entirely on static keyword substring and glob matching against titles, tags, and L0 abstracts.

---

## 2. Weaknesses in V2

1.  **Semantic Blindness:** NimFS keyword search is fragile. If the agent saves a memory about "Database Connection Spikes," searching for "Postgres Latency" might yield zero results.
2.  **Synchronous Compaction:** Compressing the MMU stream (`archive_and_reset`) blocks the active conversation loop, causing latency spikes for the user when the context window fills up.
3.  **Binary Context:** A message is either fully in the context window, or permanently summarized away. There is no fluid semantic paging.
4.  **Opaque Image Management:** Images that exceed token budgets are simply replaced with `[Image Omitted]`, permanently losing the information density of that interaction for future turns.

---

## 3. "Memory V3" Upgrade Proposals

To elevate Nimbus from a competent conversational agent to a system with true, persistent episodic and semantic memory, I propose the following core pillars for V3:

### Pillar 1: Semantic Vector DB & Hybrid Search
Replace or augment the `search_memory` keyword globbing with a local, embedded Vector Database (e.g., `ChromaDB`, `lancedb`, or `sqlite` with `pgvector-lite`).
*   **How it works:** When NimFS writes an L1/L2 memory, it automatically generates an embedding vector.
*   **Impact:** The agent can inject highly relevant historical context purely based on semantic intent, drastically reducing hallucinations across long-running projects. Tool calls like `NimFSSearchMemory` become much more powerful.

### Pillar 2: Asynchronous Memory Consolidation (The "Sleep" Cycle)
Remove inline summarization from the critical path of the VCPU.
*   **How it works:** Implement a low-priority background daemon (or run it immediately after a session completes). This "Dream / Consolidation" VCPU scans recent NimFS entries and chat logs, deduplicates overlapping L0 summaries, extracts recurring patterns into global `Preferences`, and builds a connected Knowledge Graph (GraphRAG).
*   **Impact:** Zero-latency chat for the user, while the LLM continuously gets smarter and more organized "overnight."

### Pillar 3: Semantic "Paging" and Working Memory
Evolve the MMU from a linear array to a Tree/Graph structure.
*   **How it works:** Instead of dropping old messages entirely, the MMU "folds" historical conversation branches into compact XML/JSON stubs in the context window (e.g., `<ArchivedConversation topic="Debugging the Auth Flow" tokens=1200 id="arch-123" />`).
*   **Impact:** The LLM knows *exactly* what information it has archived. If the user asks a follow-up question about the auth flow days later, the LLM can use a tool like `ExpandMemory(id="arch-123")` to proactively swap that branch back into its hot working memory.

### Pillar 4: Multi-Modal Context Preservation
Never lose information when dropping images from the hot context.
*   **How it works:** When `context_assembler.py` detects that the `max_image_tokens` budget is exceeded, it triggers an async task to generate a dense, textual description of the image (e.g., "Screenshot showing a React stack trace indicating a Null Reference Exception in UserAvatar.tsx").
*   **Impact:** The image is dropped to save tokens, but its *semantic value* is permanently bolted into the conversation history text.

### Pillar 5: Temporal "Fading"
*   **How it works:** Give memories "weight" strings based on time decay. A memory from 5 minutes ago carries total weight, while a memory from 3 weeks ago fades unless it is heavily queried by the Vector DB.
*   **Impact:** Prevents the LLM from fixating on outdated architecture decisions or old user states.
