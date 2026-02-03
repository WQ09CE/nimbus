/**
 * Chat Store - Zustand state management
 */

import { create } from "zustand";
import {
  type Session,
  type ToolCall,
  type ToolResult,
  type ServerMessage,
  createSession,
  streamChat,
  injectMessage, // New import
  getSessionMessages,
  getSession,
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
  messageQueue: string[]; // Queued user messages

  // Real-time progress indicators
  thinkingIteration: number | null;  // Current thinking iteration
  currentActivity: string | null;     // Current activity description
  lastHeartbeat: number | null;       // Timestamp of last heartbeat

  // Interrupt state
  isInterrupting: boolean;            // Whether interrupt request is being processed
  streamAbortController: AbortController | null;  // For aborting stream requests

  // UI state
  isLoading: boolean;
  error: string | null;
  isCreatingSession: boolean;  // Prevent concurrent session creation

  // Actions
  createNewSession: (force?: boolean) => Promise<void>;
  switchSession: (session: Session | null) => Promise<void>;
  loadSession: (sessionId: string) => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
  interruptMessage: () => void;
  clearError: () => void;
  reset: () => void;
}

const initialState = {
  session: null,
  messages: [],
  isStreaming: false,
  streamingContent: "",
  streamingToolCalls: [],
  messageQueue: [],
  thinkingIteration: null,
  currentActivity: null,
  lastHeartbeat: null,
  isInterrupting: false,
  streamAbortController: null,
  isLoading: false,
  error: null,
  isCreatingSession: false,
};

export const useChatStore = create<ChatState>((set, get) => ({
  ...initialState,

  createNewSession: async (force = false) => {
    const { isCreatingSession, session } = get();
    
    // Prevent concurrent session creation
    if (isCreatingSession) {
      console.log("[Store] Session creation already in progress, skipping");
      return;
    }
    
    // Don't create if we already have a session (unless force=true from UI button)
    if (session && !force) {
      console.log("[Store] Session already exists:", session.id);
      return;
    }
    
    try {
      set({ isLoading: true, isCreatingSession: true, error: null });
      const newSession = await createSession();
      console.log("[Store] Session created:", newSession.id);
      set({ session: newSession, messages: [], isLoading: false, isCreatingSession: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to create session",
        isLoading: false,
        isCreatingSession: false,
      });
    }
  },

  switchSession: async (session: Session | null) => {
    // Handle null session (e.g., when deleting current session)
    if (!session) {
      set({
        session: null,
        messages: [],
        isStreaming: false,
        streamingContent: "",
        streamingToolCalls: [],
        thinkingIteration: null,
        currentActivity: null,
        error: null,
        isLoading: false,
      });
      if (typeof window !== "undefined") {
        localStorage.removeItem("nimbus_session_id");
      }
      return;
    }

    // Switch to an existing session and load its messages
    set({
      session,
      messages: [],
      isStreaming: false,
      streamingContent: "",
      streamingToolCalls: [],
      thinkingIteration: null,
      currentActivity: null,
      error: null,
      isLoading: true,
    });

    // Persist session ID to localStorage
    if (typeof window !== "undefined") {
      localStorage.setItem("nimbus_session_id", session.id);
    }

    // Load messages from server
    try {
      const serverMessages = await getSessionMessages(session.id);
      
      // First pass: extract all messages and build a map of tool results
      const toolResultsMap = new Map<string, ToolResult>();
      
      for (const m of serverMessages) {
        if (m.role === 'tool' && m.artifacts) {
          for (const artifact of m.artifacts) {
            if (artifact && typeof artifact === 'object') {
              const art = artifact as Record<string, unknown>;
              if (art.type === 'tool_result' && art.tool_call_id) {
                toolResultsMap.set(String(art.tool_call_id), {
                  id: String(art.tool_call_id),
                  name: String(art.name || ''),
                  result: m.content,
                });
              }
            }
          }
        }
      }
      
      // Second pass: build messages, skipping tool messages and merging results into assistant messages
      const messages: Message[] = [];
      
      for (const m of serverMessages) {
        // Skip tool messages - their content is merged into assistant messages
        if (m.role === 'tool') continue;
        
        // Skip system messages that are just task completion markers
        if (m.role === 'system' && m.content?.startsWith('✓ Task completed')) continue;
        
        let toolCalls: ToolCall[] | undefined;
        let toolResults: ToolResult[] | undefined;
        
        if (m.artifacts && Array.isArray(m.artifacts)) {
          for (const artifact of m.artifacts) {
            if (artifact && typeof artifact === 'object') {
              const art = artifact as Record<string, unknown>;
              
              // Handle tool_calls artifact
              if (art.type === 'tool_calls' && Array.isArray(art.tool_calls)) {
                toolCalls = (art.tool_calls as Array<{
                  id?: string;
                  function?: { name?: string; arguments?: string };
                }>).map(tc => ({
                  id: tc.id || '',
                  name: tc.function?.name || '',
                  arguments: tc.function?.arguments 
                    ? (typeof tc.function.arguments === 'string' 
                        ? JSON.parse(tc.function.arguments) 
                        : tc.function.arguments)
                    : {},
                }));
                
                // Match tool results from the map
                toolResults = toolCalls
                  .filter(tc => tc.id)
                  .map(tc => toolResultsMap.get(tc.id!))
                  .filter((r): r is ToolResult => r !== undefined);
              }
            }
          }
        }
        
        messages.push({
          id: m.id,
          role: m.role as 'user' | 'assistant' | 'system',
          content: m.content || "", // Ensure content is never null
          toolCalls,
          toolResults: toolResults && toolResults.length > 0 ? toolResults : undefined,
          timestamp: !isNaN(new Date(m.created_at).getTime()) ? new Date(m.created_at).getTime() : Date.now(),
        });
      }
      
      // Sort messages by timestamp to ensure correct order
      messages.sort((a, b) => a.timestamp - b.timestamp);
      
      set({ messages, isLoading: false });
      console.log(`[Store] Loaded ${messages.length} messages for session ${session.id}`);
    } catch (err) {
      console.error("[Store] Failed to load messages:", err);
      set({ isLoading: false });
    }
  },

  loadSession: async (sessionId: string) => {
    // Load a session by ID (used on page refresh)
    try {
      set({ isLoading: true });
      const session = await getSession(sessionId);
      if (session) {
        await get().switchSession(session);
      } else {
        // Session not found, clear localStorage
        if (typeof window !== "undefined") {
          localStorage.removeItem("nimbus_session_id");
        }
        set({ isLoading: false });
      }
    } catch (err) {
      console.error("[Store] Failed to load session:", err);
      if (typeof window !== "undefined") {
        localStorage.removeItem("nimbus_session_id");
      }
      set({ isLoading: false });
    }
  },

  sendMessage: async (content: string) => {
    const { session, messages, isStreaming, messageQueue, isCreatingSession } = get();
    
    // Handle streaming case: Inject message instead of queuing
    if (isStreaming && session) {
        // Optimistically add to UI
        const userMessage: Message = {
          id: `user-inject-${Date.now()}`,
          role: "user",
          content: `[追加指令] ${content}`,
          timestamp: Date.now(),
        };
        
        set({ 
            messages: [...messages, userMessage],
            // Don't change streaming state, just append message
        });
        
        try {
            await injectMessage(session.id, content);
            console.log(`[Store] Injected message into session ${session.id}`);
        } catch (err) {
            console.error("[Store] Failed to inject message:", err);
            // Maybe show a toast or error status?
            // For now, keep it simple
        }
        return;
    }
    
    // Wait for session creation if in progress
    if (isCreatingSession) {
      console.log("[Store] Waiting for session creation...");
      set({ messageQueue: [...messageQueue, content] });
      return;
    }
    
    // Must have a session to send messages
    const currentSession = session;
    if (!currentSession) {
      console.error("[Store] No session available, cannot send message");
      set({ error: "请先创建一个 Session" });
      return;
    }

    // Add user message
    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      content,
      timestamp: Date.now(),
    };

    // Create abort controller for this request
    const abortController = new AbortController();

    set({
      messages: [...messages, userMessage],
      isStreaming: true,
      streamingContent: "",
      streamingToolCalls: [],
      thinkingIteration: null,
      currentActivity: "连接中...",
      lastHeartbeat: Date.now(),
      streamAbortController: abortController,
      error: null,
    });

    try {
      let assistantContent = "";
      const toolCalls: ToolCall[] = [];
      const toolResults: ToolResult[] = [];
      let shouldContinue = true;
      
      // Throttling state
      let lastUpdate = 0;
      const UPDATE_INTERVAL = 50; // ms

      // Stream response
      for await (const event of streamChat(currentSession.id, content, abortController.signal)) {
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

          case "step_start":
            // New turn detected - commit previous content if any
            if (assistantContent || toolCalls.length > 0) {
              const stepMessage: Message = {
                id: `assistant-${Date.now()}`,
                role: "assistant",
                content: assistantContent,
                toolCalls: toolCalls.length > 0 ? [...toolCalls] : undefined,
                toolResults: toolResults.length > 0 ? [...toolResults] : undefined,
                timestamp: Date.now(),
              };

              set(state => ({
                messages: [...state.messages, stepMessage],
                streamingContent: "",
                streamingToolCalls: [],
              }));

              // Reset accumulators
              assistantContent = "";
              toolCalls.length = 0;
              toolResults.length = 0;
              // Reset throttle
              lastUpdate = 0;
            }

            // Update iteration info
            if (data && typeof data === "object" && "iteration" in data) {
                const iter = (data as any).iteration;
                set({ 
                  thinkingIteration: iter,
                  currentActivity: `思考中 (第 ${iter} 轮)...`,
                  lastHeartbeat: Date.now() 
                });
            }
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
            let newContent = "";
            if (typeof data === "string") {
              newContent = data;
            } else if (typeof data === "object" && data && "content" in data) {
              const c = (data as { content?: unknown }).content;
              if (typeof c === "string") {
                newContent = c;
              }
            }
            
            if (newContent) {
                assistantContent += newContent;
                const now = Date.now();
                // Throttle updates to avoid flickering
                if (now - lastUpdate > UPDATE_INTERVAL) {
                    set({ 
                        streamingContent: assistantContent,
                        currentActivity: "生成回复中...",
                        lastHeartbeat: now
                    });
                    lastUpdate = now;
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
        streamAbortController: null,
        isInterrupting: false,
      });

      // Process next message in queue
      const { messageQueue: queue } = get();
      if (queue.length > 0) {
          const next = queue[0];
          set({ messageQueue: queue.slice(1) });
          // Use setTimeout to allow state update to propagate
          setTimeout(() => get().sendMessage(next), 0);
      }
    } catch (err) {
      // Handle user cancellation differently from errors
      if (err instanceof Error && err.name === 'AbortError') {
        // User cancelled - add a gentle message instead of error
        const cancelMessage: Message = {
          id: `cancel-${Date.now()}`,
          role: "system",
          content: "已取消对话",
          timestamp: Date.now(),
        };

        set({
          messages: [...get().messages, cancelMessage],
          isStreaming: false,
          streamingContent: "",
          streamingToolCalls: [],
          thinkingIteration: null,
          currentActivity: null,
          lastHeartbeat: null,
          streamAbortController: null,
          isInterrupting: false,
          error: null, // Don't set error for user cancellation
        });
      } else {
        // Real error occurred
        const errorMessage = err instanceof Error ? err.message : "Failed to send message";
        set({
          error: errorMessage,
          isStreaming: false,
          streamingContent: "",
          streamingToolCalls: [],
          thinkingIteration: null,
          currentActivity: null,
          lastHeartbeat: null,
          streamAbortController: null,
          isInterrupting: false,
        });
      }
    }
  },

  clearError: () => set({ error: null }),

  interruptMessage: () => {
      const { streamAbortController, isStreaming } = get();

      if (isStreaming && streamAbortController) {
        set({ isInterrupting: true });
        streamAbortController.abort();
      }
    },

    reset: () => set(initialState),
}));
