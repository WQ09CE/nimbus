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

// SSE event data types (from server)
interface HeartbeatData {
  iteration?: number;
  [key: string]: unknown;
}

interface ToolCallData {
  action_id?: string;
  id?: string;
  tool?: string;
  name?: string;
  args?: Record<string, unknown>;
  arguments?: Record<string, unknown>;
  [key: string]: unknown;
}

interface ToolResultData {
  action_id?: string;
  id?: string;
  tool?: string;
  name?: string;
  output?: unknown;
  result?: unknown;
  error?: string;
  duration_ms?: number;
  status?: string;
  fault?: { message: string; [key: string]: unknown };
  [key: string]: unknown;
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
  
  // Real-time progress indicators
  thinkingIteration: number | null;  // Current thinking iteration
  currentActivity: string | null;     // Current activity description
  lastHeartbeat: number | null;       // Timestamp of last heartbeat
  
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
  thinkingIteration: null,
  currentActivity: null,
  lastHeartbeat: null,
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
      thinkingIteration: null,
      currentActivity: "连接中...",
      lastHeartbeat: Date.now(),
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
          case "connected":
            set({ 
              currentActivity: "已连接",
              lastHeartbeat: Date.now() 
            });
            break;

          case "message_start":
            set({ 
              currentActivity: "开始生成回复...",
              lastHeartbeat: Date.now() 
            });
            break;

          case "task_start":
            set({ 
              currentActivity: "开始执行任务...",
              lastHeartbeat: Date.now() 
            });
            break;

          case "heartbeat":
            // Update thinking iteration if present
            if (data && typeof data === "object") {
              const hbData = data as HeartbeatData;
              if ("iteration" in hbData && typeof hbData.iteration === "number") {
                const iter = hbData.iteration;
                set({ 
                  thinkingIteration: iter,
                  currentActivity: `正在思考 (第 ${iter + 1} 轮)...`,
                  lastHeartbeat: Date.now() 
                });
              } else if ("kind" in hbData && hbData.kind === "THOUGHT") {
                set({ 
                  currentActivity: "正在思考...",
                  lastHeartbeat: Date.now() 
                });
              } else if ("reason" in hbData) {
                // Thought completed
                set({ 
                  currentActivity: "思考完成，生成回复...",
                  lastHeartbeat: Date.now() 
                });
              }
            }
            break;

          case "message":
            if (typeof data === "string") {
              assistantContent += data;
              set({ 
                streamingContent: assistantContent,
                currentActivity: "生成回复中...",
                lastHeartbeat: Date.now()
              });
            } else if (typeof data === "object" && data && "content" in data) {
              const content = (data as { content?: unknown }).content;
              if (typeof content === "string") {
                assistantContent += content;
                set({ 
                  streamingContent: assistantContent,
                  currentActivity: "生成回复中...",
                  lastHeartbeat: Date.now()
                });
              }
            }
            break;

          case "tool_call":
            if (data && typeof data === "object") {
              const d = data as ToolCallData;
              // Map server format (action_id, tool, args) to frontend format (id, name, arguments)
              const tool: ToolCall = {
                id: d.action_id || d.id || "",
                name: d.tool || d.name || "unknown",
                arguments: d.args || d.arguments || {},
              };
              toolCalls.push(tool);
              set({ 
                streamingToolCalls: [...toolCalls],
                currentActivity: `执行工具: ${tool.name}`,
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "tool_result":
            if (data && typeof data === "object") {
              const d = data as ToolResultData;
              const result: ToolResult = {
                id: d.action_id || d.id || "",
                name: d.tool || d.name || "unknown",
                result: d.output !== undefined ? d.output : d.result,
                error: d.status === "ERROR" ? (d.fault ? d.fault.message : "Error") : undefined,
                duration: d.duration_ms,
              };
              toolResults.push(result);
              set({ 
                currentActivity: "工具执行完成",
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "dag_complete":
            set({ 
              currentActivity: "完成",
              lastHeartbeat: Date.now()
            });
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
        thinkingIteration: null,
        currentActivity: null,
        lastHeartbeat: null,
      });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to send message",
        isStreaming: false,
        streamingContent: "",
        streamingToolCalls: [],
        thinkingIteration: null,
        currentActivity: null,
        lastHeartbeat: null,
      });
    }
  },

  clearError: () => set({ error: null }),
  
  reset: () => set(initialState),
}));
