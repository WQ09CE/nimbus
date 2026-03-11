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
          };
        });

      parsedMessages.sort((a, b) => a.timestamp - b.timestamp);

      // Prevent stale updates
      if (get().session?.id === session.id) {
        // Only keep the in-flight streaming message (if any).
        // Do NOT keep "user-*" messages — they are already in server history.
        // Merging them causes duplicates when reloading the same session.
        const streaming = get().messages.filter(m => m.id === "streaming-assistant");
        set({ messages: [...parsedMessages, ...streaming], isLoading: false });
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
                if (!chunk) break;

                // 1. Always write to Map
                const map = targetMsg.toolResultsMap || {};
                if (map[tcId]) {
                  map[tcId] = { ...map[tcId], result: (map[tcId].result || "") + chunk };
                } else {
                  map[tcId] = { id: tcId, name: d.tool || "unknown", result: chunk };
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
                set({ tokenUsage: d.cumulative_usage || null });
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

    // In-flight streaming injection
    if (isStreaming) {
      const userMessage: Message = {
        id: `user-inject-${Date.now()}`,
        role: "user",
        content,
        parts: [{ type: "text", content }],
        attachments,
        timestamp: Date.now(),
        isInjection: true,
      };
      // Insert BEFORE the streaming-assistant message so it doesn't float
      // on top of tool cards. The streaming message should always be last.
      const msgs = [...get().messages];
      const streamIdx = msgs.findIndex(m => m.id === "streaming-assistant");
      if (streamIdx !== -1) {
        msgs.splice(streamIdx, 0, userMessage);
      } else {
        msgs.push(userMessage);
      }
      set({ messages: msgs });
      try {
        await injectMessage(session.id, content, attachments);
      } catch (err) {
        console.error("Injection failed", err);
      }
      return;
    }

    // Standard send
    const userMessage: Message = { id: `user-${Date.now()}`, role: "user", content, parts: [{ type: "text", content }], attachments, timestamp: Date.now() };
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
    const initialAssistantMsg: Message = { id: STREAMING_ID, role: "assistant", content: "", parts: [], timestamp: Date.now(), toolCallsMap: {}, toolResults: [], toolResultsMap: {} };

    set({
      messages: [...get().messages, userMessage, initialAssistantMsg],
      isStreaming: true,
      streamAbortController: abortController,
      error: null
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
              if (!chunk) break;

              // 1. Always write to Map — never lose data regardless of parts state
              const map = targetMsg.toolResultsMap || {};
              if (map[tcId]) {
                map[tcId] = { ...map[tcId], result: (map[tcId].result || "") + chunk };
              } else {
                map[tcId] = { id: tcId, name: d.tool || "unknown", result: chunk };
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
              // Merge with any streaming chunks already buffered in the Map
              const existing = targetMsg.toolResultsMap?.[tcId];
              const tr: ToolResult = {
                id: tcId || "",
                name: d.tool || d.name || "unknown",
                result: d.output !== undefined ? d.output : (d.result !== undefined ? d.result : existing?.result),
                error: d.status === "ERROR" ? (d.fault?.message || "Error") : undefined,
                ui_detail: d.ui_detail,
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
          case "done":
            receivedDone = true;
            break;
          case "usage_update": {
            if (data && typeof data === "object") {
              const d = data as any;
              set({ tokenUsage: d.cumulative_usage || null });
            }
            break;
          }
          case "error":
            throw new Error(typeof data === "string" ? data : (data as any)?.message || "Stream error");
        }

        // Exit the for-await loop when stream is done
        if (type === "done") break;

        if (updated) {
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
        // Stream dropped without done — check if agent is still running
        console.warn("[Store] Stream ended without done event, checking agent status...");
        try {
          const status = await getSessionStatus(session.id);
          if (status.running && get().session?.id === session.id) {
            // Agent still running — reload will pick up when it finishes
            console.info("[Store] Agent still running, reloading session to recover...");
            await get().switchSession(session);
            return;
          }
        } catch { /* status check failed */ }
        // Agent done or can't check — reload full results from server
        try {
          await get().switchSession(session);
        } catch {
          // Fallback: just finalize with what we have
          const finalMsgs = [...get().messages];
          const streamingIdx = finalMsgs.findIndex(m => m.id === STREAMING_ID);
          if (streamingIdx !== -1) {
            finalMsgs[streamingIdx].id = `assistant-${Date.now()}`;
          }
          set({ messages: finalMsgs, isStreaming: false, streamAbortController: null });
        }
      }

    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        // Could be user interrupt or watchdog timeout — try to recover
        console.warn("[Store] Stream aborted, checking agent status for recovery...");
        try {
          const status = await getSessionStatus(session.id);
          if (status.running && get().session?.id === session.id) {
            // Agent still running — reload session (switchSession checks status and re-attaches)
            await get().switchSession(session);
            return;
          }
          // Agent done — reload to get complete results
          await get().switchSession(session);
          return;
        } catch { /* status check or reload failed */ }
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

    // Abort the local stream first (unblocks the UI immediately)
    if (streamAbortController) {
      streamAbortController.abort();
    }

    // Then tell the backend to stop the agent
    if (session) {
      try {
        const { interruptSession } = await import("@/lib/api/sessions");
        await interruptSession(session.id);
      } catch (err) {
        console.warn("[Store] interruptSession API call failed:", err);
      }
    }

    // Safety net: if isStreaming is still true after abort (e.g. dead connection),
    // force-finalize so the UI is never stuck in streaming state.
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
