/**
 * Chat Store - Zustand state management
 */

import { create } from "zustand";
import {
  type Session,
  type ToolCall,
  type ToolResult,
  createSession,
  streamChat,
} from "@/lib/api";

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  toolCalls?: ToolCall[];
  toolResults?: ToolResult[];
  timestamp: number;
}

interface ChatState {
  // Session
  session: Session | null;
  
  // Messages
  messages: Message[];
  
  // Streaming state
  isStreaming: boolean;
  streamingContent: string;
  streamingToolCalls: ToolCall[];
  
  // UI state
  isLoading: boolean;
  error: string | null;
  
  // Actions
  createNewSession: () => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
  clearError: () => void;
  reset: () => void;
}

const initialState = {
  session: null,
  messages: [],
  isStreaming: false,
  streamingContent: "",
  streamingToolCalls: [],
  isLoading: false,
  error: null,
};

export const useChatStore = create<ChatState>((set, get) => ({
  ...initialState,

  createNewSession: async () => {
    try {
      set({ isLoading: true, error: null });
      const session = await createSession();
      set({ session, messages: [], isLoading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to create session",
        isLoading: false,
      });
    }
  },

  sendMessage: async (content: string) => {
    const { session, messages } = get();
    
    // Create session if needed
    let currentSession = session;
    if (!currentSession) {
      try {
        currentSession = await createSession();
        set({ session: currentSession });
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : "Failed to create session",
        });
        return;
      }
    }

    // Add user message
    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      content,
      timestamp: Date.now(),
    };

    set({
      messages: [...messages, userMessage],
      isStreaming: true,
      streamingContent: "",
      streamingToolCalls: [],
      error: null,
    });

    try {
      let assistantContent = "";
      const toolCalls: ToolCall[] = [];
      const toolResults: ToolResult[] = [];
      let shouldContinue = true;

      // Stream response
      for await (const event of streamChat(currentSession.id, content)) {
        const { type, data } = event;

        switch (type) {
          case "message":
            if (typeof data === "string") {
              assistantContent += data;
              set({ streamingContent: assistantContent });
            } else if (typeof data === "object" && data && "content" in data) {
              const content = (data as { content?: unknown }).content;
              if (typeof content === "string") {
                assistantContent += content;
                set({ streamingContent: assistantContent });
              }
            }
            break;

          case "tool_call":
            if (data && typeof data === "object") {
              toolCalls.push(data as ToolCall);
              set({ streamingToolCalls: [...toolCalls] });
            }
            break;

          case "tool_result":
            if (data && typeof data === "object") {
              toolResults.push(data as ToolResult);
            }
            break;

          case "dag_complete":
            // Stream completed successfully, exit loop
            shouldContinue = false;
            break;

          case "error":
            throw new Error(typeof data === "string" ? data : "Stream error");
        }

        // Exit loop if dag_complete received
        if (!shouldContinue) {
          break;
        }
      }

      // Finalize assistant message
      const assistantMessage: Message = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: assistantContent,
        toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
        toolResults: toolResults.length > 0 ? toolResults : undefined,
        timestamp: Date.now(),
      };

      set({
        messages: [...get().messages, assistantMessage],
        isStreaming: false,
        streamingContent: "",
        streamingToolCalls: [],
      });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to send message",
        isStreaming: false,
        streamingContent: "",
        streamingToolCalls: [],
      });
    }
  },

  clearError: () => set({ error: null }),
  
  reset: () => set(initialState),
}));
