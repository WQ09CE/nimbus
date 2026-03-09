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
} from "@/lib/api";

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  toolCalls?: ToolCall[];
  toolResults?: ToolResult[];
  attachments?: ChatAttachment[];
  timestamp: number;
  isInjection?: boolean;
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
  fsmState: null,
  activeArtifact: null,
  isReconnecting: false,
  errorInfo: null,
};

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
        localStorage.setItem("nimbus_session_id", newSession.id);
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
      if (typeof window !== "undefined") localStorage.removeItem("nimbus_session_id");
      return;
    }

    const isSameSession = get().session?.id === session.id;
    set({
      session,
      isLoading: !isSameSession,
      error: null,
      messages: isSameSession ? get().messages : [],
    });

    if (typeof window !== "undefined") localStorage.setItem("nimbus_session_id", session.id);

    try {
      const serverMessages = await getSessionMessages(session.id);

      const parsedMessages: Message[] = serverMessages.map(m => {
        let content = m.content || "";
        if (Array.isArray(content)) {
          content = content.map((b: any) => typeof b === 'string' ? b : b?.text || '').join('\n').trim();
        } else if (typeof content === 'object') {
          content = JSON.stringify(content);
        }

        return {
          id: m.id,
          role: m.role as "user" | "assistant" | "system",
          content: String(content),
          timestamp: m.created_at ? new Date(m.created_at.replace(" ", "T") + (m.created_at.includes("Z") ? "" : "Z")).getTime() : Date.now(),
        };
      }).filter(m => (m as any).role !== 'tool');

      parsedMessages.sort((a, b) => a.timestamp - b.timestamp);

      // Prevent stale updates
      if (get().session?.id === session.id) {
        // Sync server history but keep current optimistic messages to avoid flickering
        const currentOptimistic = get().messages.filter(m => m.id.startsWith("user-") || m.id === "streaming-assistant");
        set({ messages: [...parsedMessages, ...currentOptimistic], isLoading: false });
      }

    } catch (err) {
      console.error("[Store] Load messages failed", err);
      set({ isLoading: false });
    }
  },

  loadSession: async (sessionId: string) => {
    try {
      set({ isLoading: true });
      const session = await getSession(sessionId);
      if (session) {
        await get().switchSession(session);
      } else {
        if (typeof window !== "undefined") localStorage.removeItem("nimbus_session_id");
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
        attachments,
        timestamp: Date.now(),
        isInjection: true,
      };
      set({ messages: [...get().messages, userMessage] });
      try {
        await injectMessage(session.id, content, attachments);
      } catch (err) {
        console.error("Injection failed", err);
      }
      return;
    }

    // Standard send
    const userMessage: Message = { id: `user-${Date.now()}`, role: "user", content, attachments, timestamp: Date.now() };
    const abortController = new AbortController();

    // Start streaming: prepare an empty assistant message
    const STREAMING_ID = "streaming-assistant";
    const initialAssistantMsg: Message = { id: STREAMING_ID, role: "assistant", content: "", timestamp: Date.now(), toolCalls: [], toolResults: [] };

    set({
      messages: [...get().messages, userMessage, initialAssistantMsg],
      isStreaming: true,
      streamAbortController: abortController,
      error: null
    });

    try {
      for await (const event of streamChat(session.id, content, attachments, abortController.signal)) {
        if (get().session?.id !== session.id) {
          abortController.abort();
          break;
        }

        const { type, data } = event;
        const currentMsgs = get().messages;
        const targetIdx = currentMsgs.findIndex(m => m.id === STREAMING_ID);
        if (targetIdx === -1) continue;

        const targetMsg = { ...currentMsgs[targetIdx] };
        let updated = false;

        switch (type) {
          case "message":
            const chunk = typeof data === "string" ? data : (data as any)?.content || (data as any)?.chunk || "";
            if (chunk) {
              targetMsg.content += chunk;
              updated = true;
            }
            break;
          case "tool_call":
            if (data && typeof data === "object") {
              const d = data as any;
              const tc: ToolCall = {
                id: d.action_id || d.id || `tc-${Date.now()}`,
                name: d.tool || d.name || "unknown",
                arguments: d.args || d.arguments || {},
              };
              targetMsg.toolCalls = [...(targetMsg.toolCalls || []), tc];
              updated = true;
            }
            break;
          case "tool_result":
            if (data && typeof data === "object") {
              const d = data as any;
              const tcId = d.action_id || d.id;
              const tr: ToolResult = {
                id: tcId || "",
                name: d.tool || d.name || "unknown",
                result: d.output !== undefined ? d.output : d.result,
                error: d.status === "ERROR" ? (d.fault?.message || "Error") : undefined,
              };
              targetMsg.toolResults = [...(targetMsg.toolResults || []), tr];
              updated = true;
            }
            break;
          case "error":
            throw new Error(typeof data === "string" ? data : (data as any)?.message || "Stream error");
        }

        if (updated) {
          const nextMsgs = [...currentMsgs];
          nextMsgs[targetIdx] = targetMsg;
          set({ messages: nextMsgs });
        }
      }

      // Stream Finished
      const finalMsgs = [...get().messages];
      const streamingIdx = finalMsgs.findIndex(m => m.id === STREAMING_ID);
      if (streamingIdx !== -1) {
        finalMsgs[streamingIdx].id = `assistant-${Date.now()}`; // Lock the ID
      }
      set({ messages: finalMsgs, isStreaming: false, streamAbortController: null });

    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        // Graceful cancel
      } else {
        set({ error: err instanceof Error ? err.message : "Stream failed" });
      }
      set({ isStreaming: false, streamAbortController: null });
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
    const { streamAbortController, session } = get();
    if (streamAbortController) {
      if (session) {
        try {
          const { interruptSession } = await import("@/lib/api/sessions");
          await interruptSession(session.id);
        } catch { }
      }
      streamAbortController.abort();
    }
  },

  reset: () => set(initialState),
}));
