# TODO

## 🚀 Web-UI Rendering & Architecture Review (Post-Implementation)

The Web-UI has achieved significant stability and features a highly functional streaming architecture. However, as session logs grow larger (especially with Bash and Grep tools dumping massive outputs), the following architectural bottlenecks should be addressed to maintain a butter-smooth 60fps experience:

### 1. React Virtualization Limits in `ChatList.tsx`
**Current State:** `@tanstack/react-virtual` is implemented correctly for *messages*, assigning an `estimateSize: 200` to each chat bubble.
**The Problem:** While the chat *messages* are virtualized, the *content inside* a single message is not. If `Bash` outputs 50,000 lines, the *entire* 50,000-line DOM block is rendered if that specific `ChatMessage` enters the viewport, causing catastrophic layout thrashing and scrolling lag.
**Proposed Fix:** 
- Implement **Nested Virtualization** or **Windowing** specifically inside `Bash.tsx` and `FileRead.tsx`. 
- Only render the visible 100 lines of the terminal buffer at any given scroll position, or implement a hard UI truncation with a "View Full Raw Output" pagination/modal.

### 2. Markdown Parser Thrashing (`MarkdownRenderer.tsx`)
**Current State:** We correctly use `useDeferredValue(content)` and `React.memo` to throttle React updates during fast SSE token streaming.
**The Problem:** `ReactMarkdown` creates a massive AST (Abstract Syntax Tree) on every render cycle. When an Assistant response reaches thousands of tokens, the AST parsing overhead dominates the JS Main Thread, causing typing latency. 
**Proposed Fix:** 
- Migrate from `react-markdown` to a faster streaming-first parser (like `marked` + custom React wrapper), or implement **Incremental Rendering** where only the *newly added* chunks are parsed, rather than re-parsing the entire huge string from index 0 on every delta received.

### 3. Zustand Global State Over-subscription (`page.tsx` & `ChatInput.tsx`)
**Current State:** Components like `ChatInput` subscribe to global primitives (`isStreaming`, `session`).
**The Problem:** When `chat-store.ts` receives highly frequent SSE updates (e.g. updating `messages` array 20 times a second), any component that lacks strictly shallow equality checks or selects too broad a state slice might unintentionally re-render on every token chunk.
**Proposed Fix:** 
- Implement strict atomic selectors: `const isStreaming = useChatStore(useShallow(state => state.isStreaming))`.
- Extract the heavily-mutated `messages` array out of the main Zustand store entirely, using a secondary fast-track pub/sub store specifically for streaming deltas to isolate Re-Renders strictly to the `ChatMessage` component itself.

### 4. DOM Bloat in Tool Blocks (`ToolCard.tsx`)
**Current State:** Tools render multiple layers of nested UI (Card > Header > Toggle > Status > Pre > Code).
**The Problem:** An agent iterating quickly (e.g. 20 consecutive Git modifications or Python runs) generates an enormous total DOM node count, triggering the browser's "Max DOM Nodes" memory constraints over long-running continuous chats.
**Proposed Fix:**
- Implement aggressive "History Condensation". When an agent task finishes, automatically collapse all intermediate `ToolCard`s and perhaps aggressively GC (garbage collect) their raw DOM node trees until the user explicitly expands them again.

## 🛠 Post-Implementation Issues & Testing

### 1. UI Refinements
- [ ] **Fix Sub-agent Timeline layout**: The checkmarks/icons are currently overflowing the ToolCard container.
- [ ] **Fix Timeline visual artifact**: There is a vertical line running through the center of the timeline area that needs to be removed or correctly aligned.

### 2. Interaction & Control
- [ ] **Fix LLM Interruption**: Currently, clicking "Interrupt" (or sending a new message while LLM is generating) does not stop the stream correctly, and the user message sometimes gets sent multiple times in the WebUI. This needs to be bulletproof.

### 3. Core Architecture Testing
- [ ] **Test Multi-agent Steering Message**: Verify the feasibility and stability of injecting steering messages into a session where multiple sub-agents are running concurrently. Ensure the steering reaches the correct active agent and is handled gracefully.
