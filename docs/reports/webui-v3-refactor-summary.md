# Nimbus Web UI V3 Refactor Summary

**Date:** March 2026
**Focus:** Simplicity, Efficiency, and Extensibility

## Overview
The V3 refactor transitioned the Nimbus Web UI from a heuristic, content-guessing architecture to a deterministic, rigorous state-machine-driven Next.js application. This update dramatically resolved accumulated technical debt while laying down the foundation for scalable, high-performance agentic interactions.

## 1. Architectural Clean-up & State Management
- **Decoupled `chat-store.ts`:**
  - Extracted SSE connection, reconnection, and error handling logic into a dedicated React hook (`useSSEListener.ts`).
  - Extracted complex sub-event multiplexing logic into `MessageDemuxer.ts`.
  - Focused `chat-store.ts` strictly on Zustand state management.
- **FSM-Driven UI:** Deprecated regular-expression content parsing. UI states (e.g., `WorkingIndicator`, tool execution tracking) now strictly follow Backend FSM state transitions (`THINKING`, `ACTING`, `STREAMING`, `IDLE`).

## 2. Performance Optimizations
- **Virtual Scrolling:** Implemented `@tanstack/react-virtual` for `ChatList` to lazily unmount off-screen messages, dramatically improving DOM performance and scroll smoothness for long (10+ turn) conversations.
- **Debounced Markdown Rendering:** Implemented a 15 FPS throttle for `react-markdown` syntax parsing using `useDeferredValue` and `requestAnimationFrame` to keep rapid text streaming fluid without freezing the main thread.
- **Typewriter Smoothing:** Created an internal queue buffer in `useTypewriter` to yield tokens smoothly to the DOM regardless of network jitter.

## 3. Streaming Stability & Connection Resilience
- **Silent Auto-Reconnect:** Implemented transparent exponential backoff reconnection if the Server-Sent Events stream silently drops context, with an unobtrusive "reconnecting..." UI indicator.
- **Multi-Tab Sync:** Resolved multi-tab fighting loops by allowing multiple concurrent SSE subscriptions without evicting existing clients. Passive tabs catch up automatically.
- **Next.js SSE Proxy:** Fixed cross-origin streaming connection failures by natively buffering and proxying `/api/v1/sessions` through Next.js natively, resolving hostname mixed-content issues.

## 4. Enhanced UI/UX Features
- **Artifact Viewer Integration:** Added dedicated lateral sandbox components to render `NimFS` artifacts (Markdown, JSON, raw Code, and HTML) completely disconnected from the main chat stream.
- **Visual Code Diffs:** Integrated `react-diff-viewer-continued` to elegantly present GitHub-style side-by-side git diffs for automated code modifications.
- **Live Tool Timers:** Included reactive `[0.0s...]` up-counters for actively executing tools to reduce user anxiety.
- **Hierarchical Agent Toolcards:** Replaced a flat history list with nested accordion trees to correctly trace recursive workflows from orchestrators to subagents.
- **Smart Session Auto-Titles:** Added asynchronous background LLM tasks to generate a 3-8 word session title after the first turn, broadcasting the real-time update seamlessly via a `session_updated` SSE event hook.

## 5. Bug Fixes & Automated Verification
- **Parallel Subagent Demultiplexing:** Re-wrote the React pipeline to map sub-tool metrics to their precise `parent_action_id` recursively, preventing concurrent tools from pooling incorrectly into the primary agent's history card.
- **E2E Playwright Suite Hardening (100% Green):**
  - Updated bounding-box selectors to accommodate the new rendering models (like `Bash.tsx`).
  - Rewrote element-count assertions to account for virtual list behavior.
  - Added background mocks for quiet endpoints (`/events`) to extinguish aggressive polling bugs in the test environment.
  - Test suites now pass consistently without race conditions.
