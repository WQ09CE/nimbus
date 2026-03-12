import { create } from "zustand";
import {
  type Session,
  type ToolCall,
  type ToolResult,
  type ChatAttachment,
  createSession,
  streamChat,
  injectMessage,
  getSessionMessages,
  getSession,
  getSessionStatus,
  subscribeToEvents,
} from "@/lib/api";

export type MessagePart =
  | { type: "text"; content: string }
  | { type: "tool"; toolCall: ToolCall; toolResult?: ToolResult };

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  parts: MessagePart[];
  toolResults?: ToolResult[];
  toolCallsMap?: Record<string, ToolCall>;
  toolResultsMap?: Record<string, ToolResult>;
  attachments?: ChatAttachment[];
  timestamp: number;
  isInjection?: boolean;
  /** Monotonic revision counter — incremented on every SSE update to force React re-render. */
  _rev: number;
}

export interface TokenUsageData {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  total: number;
  cost: {
    input: number;
    output: number;
    cache_read: number;
    cache_write: number;
    total: number;
  };
  context_window?: {
    current: number;
    maximum: number;
  };
}

interface ChatState {
  session: Session | null;
  messages: Message[];
  isStreaming: boolean;
  messageQueue: string[];
  isLoading: boolean;
  error: string | null;
  isCreatingSession: boolean;
  streamAbortController: AbortController | null;
  tokenUsage: TokenUsageData | null;

  fsmState: string | null;
  activeArtifact: any | null;
  isReconnecting: boolean;
  errorInfo: any | null;

  createNewSession: (
    force?: boolean,
    options?: Record<string, any>
  ) => Promise<void>;
  switchSession: (session: Session | null) => Promise<void>;
  loadSession: (sessionId: string) => Promise<void>;
  sendMessage: (content: string, attachments?: ChatAttachment[]) => Promise<void>;
  retryLastMessage: () => void;
  interruptMessage: () => void;
  clearError: () => void;
  closeArtifact: () => void;
  reset: () => void;
  _attachToRunningSession: (sessionId: string) => void;
}

const initialState = {
  session: null,
  messages: [],
  isStreaming: false,
  messageQueue: [],
  isLoading: false,
  error: null,
  isCreatingSession: false,
  streamAbortController: null,
  tokenUsage: null,
  fsmState: null,
  activeArtifact: null,
  isReconnecting: false,
  errorInfo: null,
};

// --- rAF batching for streaming updates ---
// Instead of calling set() on every SSE chunk, buffer the latest message
// and flush at most once per animation frame (~60fps).
let _pendingStreamMsg: Message | null = null;
let _rafHandle: number = 0;

type SetFn = (partial: Partial<ChatState> | ((state: ChatState) => Partial<ChatState>)) => void;
type GetFn = () => ChatState;

function flushStreamUpdate(set: SetFn, get: GetFn, streamingId: string) {
  _rafHandle = 0;
  const msg = _pendingStreamMsg;
  if (!msg) return;
  _pendingStreamMsg = null;
  const currentMsgs = get().messages;
  const idx = currentMsgs.findIndex((m: Message) => m.id === streamingId);
  if (idx === -1) return;
  const next = [...currentMsgs];
  next[idx] = msg;
  set({ messages: next });
}

function scheduleStreamUpdate(set: SetFn, get: GetFn, streamingId: string, msg: Message) {
  _pendingStreamMsg = msg;
  if (!_rafHandle) {
    _rafHandle = requestAnimationFrame(() => flushStreamUpdate(set, get, streamingId));
  }
}

function flushPendingSync(set: SetFn, get: GetFn, streamingId: string) {
  // Force-flush any pending buffered update (used before finalization)
  if (_rafHandle) {
    cancelAnimationFrame(_rafHandle);
    _rafHandle = 0;
  }
  if (_pendingStreamMsg) {
    const msg = _pendingStreamMsg;
    _pendingStreamMsg = null;
    const currentMsgs = get().messages;
    const idx = currentMsgs.findIndex((m: Message) => m.id === streamingId);
    if (idx !== -1) {
      const next = [...currentMsgs];
      next[idx] = msg;
      set({ messages: next });
    }
  }
}

export const useChatStore = create<ChatState>((set, get) => ({
  ...initialState,

  closeArtifact: () => set({ activeArtifact: null }),

  createNewSession: async (force = false, options) => {
    const { streamAbortController: prevController } = get();
    if (prevController) prevController.abort();

    if (get().isCreatingSession) return;
    if (get().session && !force) return;

    try {
      set({ isLoading: true, isCreatingSession: true, error: null });
      const currentSession = get().session;
      const inheritedLlmConfig = currentSession?.llm_config && currentSession.llm_config.model_id !== "default"
        ? { provider: currentSession.llm_config.provider || "", model_id: currentSession.llm_config.model_id }
        : undefined;

      const newSession = await createSession({
        agent_mode: options?.agent_mode || "dual_agent",
        ...(options?.llm_config || inheritedLlmConfig ? { llm_config: options?.llm_config || inheritedLlmConfig } : {}),
        ...options,
      } as any);

      if (typeof window !== "undefined") {
        sessionStorage.setItem("nimbus_session_id", newSession.id);
      }

      set({ session: newSession, messages: [], isLoading: false, isCreatingSession: false });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "Failed to create session", isLoading: false, isCreatingSession: false });
    }
  },

  switchSession: async (session: Session | null) => {
    const { streamAbortController } = get();
    if (streamAbortController) streamAbortController.abort();

    if (!session) {
      set({ ...initialState });
      if (typeof window !== "undefined") sessionStorage.removeItem("nimbus_session_id");
      return;
    }

    const isSameSession = get().session?.id === session.id;
    set({
      session,
      isLoading: !isSameSession,
      error: null,
      messages: isSameSession ? get().messages : [],
    });

    if (typeof window !== "undefined") sessionStorage.setItem("nimbus_session_id", session.id);

    try {
      const serverMessages = await getSessionMessages(session.id);

      // Build tool result lookup from role='tool' messages
      const toolResultMap = new Map<string, { name: string; content: string }>();
      for (const m of serverMessages) {
        if (m.role === 'tool' && m.tool_call_id) {
          const resultContent = typeof m.content === 'string' ? m.content : JSON.stringify(m.content || '');
          toolResultMap.set(m.tool_call_id, { name: m.name || 'unknown', content: resultContent });
        }
      }

      const parsedMessages: Message[] = serverMessages
        .filter(m => m.role !== 'tool')  // tool results are merged into assistant messages
        .map(m => {
          const rawContent = m.content || "";

          // Extract text and reconstruct image attachments from multimodal content blocks
          let textContent = "";
          const reloadedAttachments: ChatAttachment[] = [];

          if (Array.isArray(rawContent)) {
            for (const block of rawContent as any[]) {
              if (typeof block === 'string') {
                textContent += block;
              } else if (block?.type === 'text') {
                textContent += (textContent ? '\n' : '') + (block.text || '');
              } else if (block?.type === 'image') {
                // Reconstruct image attachment from stored content block
                const mimeType = block.mimeType || block.mime_type || 'image/png';
                const base64 = block.data || block.content || '';
                reloadedAttachments.push({
                  id: `reload-img-${reloadedAttachments.length}-${Date.now()}`,
                  type: 'image',
                  name: block.name || 'image.png',
                  size: base64.length,
                  content: base64,
                  mimeType,
                  // Build a data URL as preview (no blob URL available after reload)
                  preview: base64 ? `data:${mimeType};base64,${base64}` : undefined,
                });
              }
            }
            textContent = textContent.trim();
          } else if (typeof rawContent === 'object') {
            textContent = JSON.stringify(rawContent);
          } else {
            textContent = String(rawContent);
          }

          const timestamp = m.created_at ? new Date(m.created_at.replace(" ", "T") + (m.created_at.includes("Z") ? "" : "Z")).getTime() : Date.now();

          // Reconstruct tool parts for assistant messages with tool_calls
          const parts: MessagePart[] = [];
          const toolCalls: ToolCall[] = [];
          const toolResults: ToolResult[] = [];

          if (m.role === 'assistant' && m.tool_calls && Array.isArray(m.tool_calls)) {
            // Add text part first if there's content before tools
            if (textContent) {
              parts.push({ type: "text" as const, content: textContent });
            }
            // Add tool parts
            for (const tc of m.tool_calls) {
              const fn = tc.function;
              const tcId = tc.id || `tc-${Date.now()}`;
              const tcObj: ToolCall = {
                id: tcId,
                name: fn?.name || tc.name || 'unknown',
                arguments: typeof fn?.arguments === 'string' ? JSON.parse(fn.arguments || '{}') : (fn?.arguments || tc.arguments || {}),
              };
              toolCalls.push(tcObj);

              // Find matching tool result
              const result = toolResultMap.get(tcId);
              const toolPart: MessagePart = { type: "tool" as const, toolCall: tcObj };
              if (result) {
                const tr: ToolResult = {
                  id: tcObj.id,
                  name: result.name,
                  result: result.content,
                };
                toolResults.push(tr);
                (toolPart as any).toolResult = tr;
              }
              parts.push(toolPart);
            }
          } else {
            // Non-tool-call messages: just text
            if (textContent) {
              parts.push({ type: "text" as const, content: textContent });
            }
          }

          return {
            id: m.id,
            role: m.role as "user" | "assistant" | "system",
            content: textContent,
            parts,
            toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
            toolCallsMap: toolCalls.length > 0 ? toolCalls.reduce((acc, tc) => { if (tc.id) acc[tc.id] = tc; return acc; }, {} as Record<string, ToolCall>) : undefined,
            toolResults: toolResults.length > 0 ? toolResults : undefined,
            toolResultsMap: toolResults.length > 0 ? toolResults.reduce((acc, tr) => { if (tr.id) acc[tr.id] = tr; return acc; }, {} as Record<string, ToolResult>) : undefined,
            attachments: reloadedAttachments.length > 0 ? reloadedAttachments : undefined,
            timestamp,
            _rev: 0,
          };
        });

      parsedMessages.sort((a, b) => a.timestamp - b.timestamp);

      // Prevent stale updates
      if (get().session?.id === session.id) {
        // Only keep the in-flight streaming message (if any).
        // Do NOT keep "user-*" messages — they are already in server history.
        // Merging them causes duplicates when reloading the same session.
        const streaming = get().messages.filter(m => m.id === "streaming-assistant");
        // Restore persisted tokenUsage for this session
        let savedUsage: TokenUsageData | null = null;
        try {
          const raw = sessionStorage.getItem(`nimbus_token_usage_${session.id}`);
          if (raw) savedUsage = JSON.parse(raw);
        } catch { /* ignore */ }
        set({ messages: [...parsedMessages, ...streaming], isLoading: false, tokenUsage: savedUsage });
      }

      // Check if the session has a running task — if so, attach to the SSE stream
      // so this client receives real-time events (multi-client observation)
      try {
        const status = await getSessionStatus(session.id);
        if (status.running && get().session?.id === session.id && !get().isStreaming) {
          get()._attachToRunningSession(session.id);
        }
      } catch {
        // Status check failure is non-fatal
      }

    } catch (err) {
      console.error("[Store] Load messages failed", err);
      set({ isLoading: false });
    }
  },

  /** Attach to an already-running session's SSE stream (multi-client / reconnect). */
  _attachToRunningSession: (sessionId: string) => {
    const STREAMING_ID = "streaming-assistant";
    const abortController = new AbortController();

    const initialAssistantMsg: Message = {
      id: STREAMING_ID,
      role: "assistant",
      content: "",
      parts: [],
      timestamp: Date.now(),
      toolCallsMap: {},
      toolResults: [],
      toolResultsMap: {},
      _rev: 0,
    };

    set(s => ({
      isStreaming: true,
      streamAbortController: abortController,
      messages: [...s.messages, initialAssistantMsg],
    }));

    // Safety net: poll status every 5s. If task finished but "done" event was
    // never received (e.g. replay race or proxy buffering issue), force-finalize
    // so the spinner never gets stuck forever.
    const pollTimer = setInterval(async () => {
      if (get().session?.id !== sessionId || !get().isStreaming) {
        clearInterval(pollTimer);
        return;
      }
      try {
        const status = await getSessionStatus(sessionId);
        if (!status.running) {
          clearInterval(pollTimer);
          abortController.abort(); // triggers finally → isStreaming: false
        }
      } catch { /* ignore poll errors */ }
    }, 5000);

    (async () => {
      try {
        for await (const event of subscribeToEvents(sessionId, abortController.signal)) {
          // Bail out if the session has changed
          if (get().session?.id !== sessionId) { abortController.abort(); break; }

          const { type, data } = event;
          const currentMsgs = get().messages;
          const targetIdx = currentMsgs.findIndex(m => m.id === STREAMING_ID);
          if (targetIdx === -1) continue;

          // Use pending rAF buffer if available, so consecutive events
          // within the same frame build on each other instead of overwriting.
          const targetMsg = _pendingStreamMsg
            ? { ..._pendingStreamMsg }
            : { ...currentMsgs[targetIdx] };
          let updated = false;

          switch (type) {
            case "user_message": {
              if (data && typeof data === "object") {
                const content = (data as any)?.content || "";
                if (content) {
                  // Add user message that was sent by another client
                  const userMsg: Message = {
                    id: `user-remote-${Date.now()}`,
                    role: "user",
                    content,
                    parts: [{ type: "text", content }],
                    timestamp: Date.now(),
                    _rev: 0,
                  };
                  // Insert BEFORE the streaming assistant message
                  const msgs = [...get().messages];
                  const streamIdx = msgs.findIndex(m => m.id === STREAMING_ID);
                  if (streamIdx !== -1) {
                    msgs.splice(streamIdx, 0, userMsg);
                  } else {
                    msgs.push(userMsg);
                  }
                  set({ messages: msgs });
                }
              }
              break;
            }
            case "message": {
              const chunk = typeof data === "string" ? data : (data as any)?.content || (data as any)?.chunk || "";
              if (chunk) {
                targetMsg.content += chunk;
                const parts = [...(targetMsg.parts || [])];
                const last = parts[parts.length - 1];
                if (last?.type === "text") {
                  parts[parts.length - 1] = { ...last, content: last.content + chunk };
                } else {
                  parts.push({ type: "text", content: chunk });
                }
                targetMsg.parts = parts;
                updated = true;
              }
              break;
            }
            case "tool_call": {
              if (data && typeof data === "object") {
                const d = data as any;
                const tcId = d.action_id || d.id || `tc-${Date.now()}`;

                const tcMap = targetMsg.toolCallsMap || {};
                const existingTc = tcMap[tcId];
                const tc: ToolCall = {
                  id: tcId,
                  name: d.tool || d.name || existingTc?.name || "unknown",
                  arguments: d.args || d.arguments || existingTc?.arguments || {},
                };

                targetMsg.toolCallsMap = { ...tcMap, [tcId]: tc };

                const parts = [...(targetMsg.parts || [])];
                const matchIdx = parts.findIndex(p => p.type === "tool" && (p as any).toolCall?.id === tcId);
                if (matchIdx === -1) {
                  parts.push({ type: "tool", toolCall: { id: tcId, name: tc.name, arguments: {} } } as any);
                  targetMsg.parts = parts;
                }
                updated = true;
              }
              break;
            }
            case "tool_output_chunk": {
              if (data && typeof data === "object") {
                const d = data as any;
                const tcId = d.action_id || d.id;
                const chunk = d.chunk || "";
                const uiDetail = d.ui_detail;
                if (!chunk && !uiDetail) break;

                // 1. Always write to Map — mark as streaming (not yet completed)
                const map = targetMsg.toolResultsMap || {};
                const existing = map[tcId];
                if (existing) {
                  map[tcId] = { 
                    ...existing, 
                    result: (existing.result || "") + chunk,
                    sub_events: uiDetail ? [...(existing.sub_events || []), uiDetail] : existing.sub_events,
                    _streaming: true,
                  };
                } else {
                  map[tcId] = { 
                    id: tcId, 
                    name: d.tool || "unknown", 
                    result: chunk,
                    sub_events: uiDetail ? [uiDetail] : undefined,
                    _streaming: true,
                  };
                }
                targetMsg.toolResultsMap = map;
                updated = true;
              }
              break;
            }
            case "tool_result": {
              if (data && typeof data === "object") {
                const d = data as any;
                const tcId = d.action_id || d.id;
                // Merge with any streaming chunks already buffered
                const existing = targetMsg.toolResultsMap?.[tcId];
                const tr: ToolResult = {
                  id: tcId || "",
                  name: d.tool || d.name || "unknown",
                  result: d.output !== undefined ? d.output : (d.result !== undefined ? d.result : existing?.result),
                  error: d.status === "ERROR" ? (d.fault?.message || "Error") : undefined,
                  ui_detail: d.ui_detail,
                  sub_events: existing?.sub_events,
                  _streaming: false,
                };
                targetMsg.toolResults = [...(targetMsg.toolResults || []), tr];
                // Always write to Map
                const map = targetMsg.toolResultsMap || {};
                map[tcId] = tr;
                targetMsg.toolResultsMap = map;

                // Also ensure a part placeholder exists in case tool_result beat tool_call
                const parts = [...(targetMsg.parts || [])];
                const matchIdx = parts.findIndex(p => p.type === "tool" && (p as any).toolCall?.id === tcId);
                if (matchIdx === -1) {
                  parts.push({ type: "tool", toolCall: { id: tcId, name: tr.name, arguments: {} } } as any);
                  targetMsg.parts = parts;
                }
                updated = true;
              }
              break;
            }
            case "usage_update": {
              if (data && typeof data === "object") {
                const d = data as any;
                console.debug("[SSE] Usage Update received:", d);
                const usage = d.cumulative_usage || null;
                if (usage && d.context_window) {
                  usage.context_window = d.context_window;
                }
                set({ tokenUsage: usage });
                // Persist to sessionStorage for refresh survival
                const sid = get().session?.id;
                if (sid && usage) {
                  try { sessionStorage.setItem(`nimbus_token_usage_${sid}`, JSON.stringify(usage)); } catch { /* ignore */ }
                }
              }
              break;
            }
            case "done":
            case "error":
              abortController.abort();
              break;
          }

          if (updated) {
            scheduleStreamUpdate(set, get, STREAMING_ID, targetMsg);
          }
        }
      } catch {
        // stream ended or aborted
      } finally {
        clearInterval(pollTimer);
        // Flush any buffered rAF update before finalization
        flushPendingSync(set, get, STREAMING_ID);
        if (get().session?.id === sessionId) {
          const finalMsgs = [...get().messages];
          const idx = finalMsgs.findIndex(m => m.id === STREAMING_ID);
          if (idx !== -1) finalMsgs[idx] = { ...finalMsgs[idx], id: `assistant-${Date.now()}` };
          set({ messages: finalMsgs, isStreaming: false, streamAbortController: null });
        }
      }
    })();
  },

  loadSession: async (sessionId: string) => {
    try {
      set({ isLoading: true });
      const session = await getSession(sessionId);
      if (session) {
        await get().switchSession(session);
      } else {
        if (typeof window !== "undefined") sessionStorage.removeItem("nimbus_session_id");
        set({ isLoading: false });
      }
    } catch {
      set({ isLoading: false });
    }
  },

  sendMessage: async (content: string, attachments?: ChatAttachment[]) => {
    const { session, isStreaming, isCreatingSession, messageQueue } = get();

    if (isCreatingSession) {
      set({ messageQueue: [...messageQueue, content] });
      return;
    }

    if (!session) {
      set({ error: "Session not initialized" });
      return;
    }

    // In-flight streaming injection or Fast Re-prompt
    if (isStreaming) {
      // Stop the current generation completely
      get().interruptMessage();
      
      // Wait for state to settle, then send as a brand new message
      setTimeout(() => {
        get().sendMessage(content, attachments);
      }, 100);
      return;
    }

    // Standard send
    const userMessage: Message = { id: `user-${Date.now()}`, role: "user", content, parts: [{ type: "text", content }], attachments, timestamp: Date.now(), _rev: 0 };
    const abortController = new AbortController();
    let receivedDone = false;
    let lastEventTime = Date.now();

    // Watchdog: if no SSE event (including heartbeats) for 30s, connection is dead
    const watchdog = setInterval(() => {
      if (Date.now() - lastEventTime > 30000) {
        clearInterval(watchdog);
        console.warn("[Store] SSE watchdog: no event for 30s, aborting stream");
        abortController.abort();
      }
    }, 5000);

    // Start streaming: prepare an empty assistant message
    const STREAMING_ID = "streaming-assistant";
    const initialAssistantMsg: Message = { id: STREAMING_ID, role: "assistant", content: "", parts: [], timestamp: Date.now(), toolCallsMap: {}, toolResults: [], toolResultsMap: {}, _rev: 0 };

    set({
      messages: [...get().messages, userMessage, initialAssistantMsg],
      isStreaming: true,
      streamAbortController: abortController,
      error: null,
    });

    try {
      for await (const event of streamChat(session.id, content, attachments, abortController.signal)) {
        lastEventTime = Date.now(); // Reset watchdog on ANY event (including heartbeats)

        if (get().session?.id !== session.id) {
          abortController.abort();
          break;
        }

        const { type, data } = event;
        const currentMsgs = get().messages;
        const targetIdx = currentMsgs.findIndex(m => m.id === STREAMING_ID);
        if (targetIdx === -1) continue;

        // Use pending rAF buffer if available, so consecutive events
        // within the same frame build on each other instead of overwriting.
        const targetMsg = _pendingStreamMsg
          ? { ..._pendingStreamMsg }
          : { ...currentMsgs[targetIdx] };
        let updated = false;

        switch (type) {
          case "user_message":
            // Sender already has the user message — skip
            break;
          case "message": {
            const chunk = typeof data === "string" ? data : (data as any)?.content || (data as any)?.chunk || "";
            if (chunk) {
              targetMsg.content += chunk;
              // Build ordered parts: append to last text part or create new one
              const parts = [...(targetMsg.parts || [])];
              const lastPart = parts[parts.length - 1];
              if (lastPart && lastPart.type === "text") {
                parts[parts.length - 1] = { ...lastPart, content: lastPart.content + chunk };
              } else {
                parts.push({ type: "text", content: chunk });
              }
              targetMsg.parts = parts;
              updated = true;
            }
            break;
          }
          case "tool_call": {
            if (data && typeof data === "object") {
              const d = data as any;
              const tcId = d.action_id || d.id || `tc-${Date.now()}`;

              const tcMap = targetMsg.toolCallsMap || {};
              const existingTc = tcMap[tcId];
              const tc: ToolCall = {
                id: tcId,
                name: d.tool || d.name || existingTc?.name || "unknown",
                arguments: d.args || d.arguments || existingTc?.arguments || {},
              };

              targetMsg.toolCallsMap = { ...tcMap, [tcId]: tc };

              const parts = [...(targetMsg.parts || [])];
              const matchIdx = parts.findIndex(p => p.type === "tool" && (p as any).toolCall?.id === tcId);
              if (matchIdx === -1) {
                parts.push({ type: "tool", toolCall: { id: tcId, name: tc.name, arguments: {} } } as any);
                targetMsg.parts = parts;
              }
              updated = true;
            }
            break;
          }
          case "tool_output_chunk": {
            if (data && typeof data === "object") {
              const d = data as any;
              const tcId = d.action_id || d.id;
              const chunk = d.chunk || "";
              const uiDetail = d.ui_detail;
              if (!chunk && !uiDetail) break;

              // 1. Always write to Map — never lose data regardless of parts state
              // IMPORTANT: Create a new Map reference so React.memo detects the change
              const existing = targetMsg.toolResultsMap?.[tcId];
              const updatedEntry = existing
                ? { 
                    ...existing, 
                    result: (existing.result || "") + chunk,
                    sub_events: uiDetail ? [...(existing.sub_events || []), uiDetail] : existing.sub_events,
                    _streaming: true,
                  }
                : { 
                    id: tcId, 
                    name: d.tool || "unknown", 
                    result: chunk,
                    sub_events: uiDetail ? [uiDetail] : undefined,
                    _streaming: true,
                  };
              targetMsg.toolResultsMap = { ...(targetMsg.toolResultsMap || {}), [tcId]: updatedEntry };
              updated = true;
            }
            break;
          }
          case "tool_result": {
            if (data && typeof data === "object") {
              const d = data as any;
              const tcId = d.action_id || d.id;
              // Merge with any streaming chunks already buffered in the Map
              const existing = targetMsg.toolResultsMap?.[tcId];
              const tr: ToolResult = {
                id: tcId || "",
                name: d.tool || d.name || "unknown",
                result: d.output !== undefined ? d.output : (d.result !== undefined ? d.result : existing?.result),
                error: d.status === "ERROR" ? (d.fault?.message || "Error") : undefined,
                ui_detail: d.ui_detail,
                sub_events: existing?.sub_events,
                _streaming: false,
              };
              targetMsg.toolResults = [...(targetMsg.toolResults || []), tr];
              // IMPORTANT: Create a new Map reference so React.memo detects the change
              targetMsg.toolResultsMap = { ...(targetMsg.toolResultsMap || {}), [tcId]: tr };

              // Also ensure a part placeholder exists in case tool_result beat tool_call
              const parts = [...(targetMsg.parts || [])];
              const matchIdx = parts.findIndex(p => p.type === "tool" && (p as any).toolCall?.id === tcId);
              if (matchIdx === -1) {
                parts.push({ type: "tool", toolCall: { id: tcId, name: tr.name, arguments: {} } } as any);
                targetMsg.parts = parts;
              }
              updated = true;
            }
            break;
          }
          case "done":
            receivedDone = true;
            break;
          case "usage_update": {
            if (data && typeof data === "object") {
              const d = data as any;
              const usage = d.cumulative_usage || null;
              if (usage && d.context_window) {
                usage.context_window = d.context_window;
              }
              set({ tokenUsage: usage });
              // Persist to sessionStorage for refresh survival
              const sid = get().session?.id;
              if (sid && usage) {
                try { sessionStorage.setItem(`nimbus_token_usage_${sid}`, JSON.stringify(usage)); } catch { /* ignore */ }
              }
            }
            break;
          }
          case "error":
            throw new Error(typeof data === "string" ? data : (data as any)?.message || "Stream error");
        }

        // Exit the for-await loop when stream is done
        if (type === "done") break;

        if (updated) {
          targetMsg._rev = (targetMsg._rev || 0) + 1;
          scheduleStreamUpdate(set, get, STREAMING_ID, targetMsg);
        }
      }

      // Flush any buffered rAF update before finalization
      flushPendingSync(set, get, STREAMING_ID);
      abortController.abort();

      if (receivedDone) {
        // Clean completion — finalize message
        const finalMsgs = [...get().messages];
        const streamingIdx = finalMsgs.findIndex(m => m.id === STREAMING_ID);
        if (streamingIdx !== -1) {
          finalMsgs[streamingIdx].id = `assistant-${Date.now()}`;
        }
        set({ messages: finalMsgs, isStreaming: false, streamAbortController: null });
      } else {
        // Stream dropped without done — try recovery
        console.warn("[Store] Stream ended without done event, attempting recovery...");
        let recovered = false;
        try {
          const status = await getSessionStatus(session.id);
          if (status.running && get().session?.id === session.id) {
            console.info("[Store] Agent still running, re-attaching to session...");
            await get().switchSession(session);
            recovered = true;
          } else {
            // Agent done — reload full results
            await get().switchSession(session);
            recovered = true;
          }
        } catch (recoveryErr) {
          console.warn("[Store] Recovery via switchSession failed:", recoveryErr);
        }

        if (!recovered) {
          // Fallback: finalize with what we have + show hint to user
          console.warn("[Store] Recovery failed, finalizing with partial content");
          const finalMsgs = [...get().messages];
          const streamingIdx = finalMsgs.findIndex(m => m.id === STREAMING_ID);
          if (streamingIdx !== -1) {
            finalMsgs[streamingIdx].id = `assistant-${Date.now()}`;
          }
          set({
            messages: finalMsgs,
            isStreaming: false,
            streamAbortController: null,
            error: "连接中断，已保留部分内容。刷新页面可加载完整结果。",
          });
        }
      }

    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        // P3 fix: lightweight recovery — finalize with existing messages
        // instead of calling switchSession which triggers full UI re-render
        console.warn("[Store] Stream aborted, finalizing with existing messages...");
        const finalMsgs = [...get().messages];
        const streamingIdx = finalMsgs.findIndex(m => m.id === STREAMING_ID);
        if (streamingIdx !== -1) {
          const msg = finalMsgs[streamingIdx];
          if (!msg.content && (!msg.parts || msg.parts.length === 0)) {
            finalMsgs.splice(streamingIdx, 1);
          } else {
            finalMsgs[streamingIdx] = { ...msg, id: `assistant-aborted-${Date.now()}` };
          }
        }
        set({
          messages: finalMsgs,
          isStreaming: false,
          streamAbortController: null,
        });


      } else {
        // Find and clean up or finalize the STREAMING_ID message
        const finalMsgs = [...get().messages];
        const streamingIdx = finalMsgs.findIndex(m => m.id === STREAMING_ID);
        if (streamingIdx !== -1) {
          const msg = finalMsgs[streamingIdx];
          if (!msg.content && (!msg.parts || msg.parts.length === 0)) {
            // Remove empty placeholder to prevent identical React Keys on retry
            finalMsgs.splice(streamingIdx, 1);
          } else {
            // Lock ID to prevent collision if it partially generated
            finalMsgs[streamingIdx].id = `assistant-${Date.now()}`;
          }
        }
        set({
          messages: finalMsgs,
          error: err instanceof Error ? err.message : "Stream failed"
        });
      }
      set({ isStreaming: false, streamAbortController: null });
    } finally {
      clearInterval(watchdog);
    }
  },

  retryLastMessage: () => {
    const state = get();
    if (state.isStreaming) return;
    const reversed = [...state.messages].reverse();
    const lastUser = reversed.find(m => m.role === 'user');
    if (!lastUser) return;
    const actualIdx = state.messages.indexOf(lastUser);
    set({ messages: state.messages.slice(0, actualIdx), error: null });
    get().sendMessage(lastUser.content, lastUser.attachments);
  },

  clearError: () => set({ error: null }),

  interruptMessage: async () => {
    const { streamAbortController, session, isStreaming } = get();

    // 1. Abort SSE immediately — instant UI feedback
    if (streamAbortController) {
      streamAbortController.abort();
    }

    // 2. Fire-and-forget: tell backend to stop (don't await — it blocks up to 10s)
    if (session) {
      import("@/lib/api/sessions").then(({ interruptSession }) =>
        interruptSession(session.id).catch(err =>
          console.warn("[Store] interruptSession API call failed:", err)
        )
      );
    }

    // 3. Safety net: force-finalize so the UI is never stuck in streaming state
    if (get().isStreaming) {
      const finalMsgs = [...get().messages];
      const streamingIdx = finalMsgs.findIndex(m => m.id === "streaming-assistant");
      if (streamingIdx !== -1) {
        const msg = finalMsgs[streamingIdx];
        if (!msg.content && (!msg.parts || msg.parts.length === 0)) {
          finalMsgs.splice(streamingIdx, 1);
        } else {
          finalMsgs[streamingIdx] = { ...msg, id: `assistant-interrupted-${Date.now()}` };
        }
      }
      set({ messages: finalMsgs, isStreaming: false, streamAbortController: null });
    }
  },

  reset: () => set(initialState),
}));
