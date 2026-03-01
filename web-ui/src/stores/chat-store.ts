/**
 * Chat Store - Zustand state management
 */

import { create } from "zustand";
import {
  type Session,
  type ToolCall,
  type ToolResult,
  type ServerMessage,
  type ChatAttachment,
  type ChatEvent,
  createSession,
  streamChat,
  injectMessage,
  getSessionMessages,
  getSession,
  subscribeToEvents,
} from "@/lib/api";
import { useWorkflowStore } from "./workflow-store";
import { demuxSubToolEvents, routeSubToolCall, routeSubToolResult, routeExecutorStart, routeExecutorDone } from "./MessageDemuxer";
import { reconnectToSession } from "../hooks/useSSEListener";

// Use BroadcastChannel for multi-tab event distribution
const BC_NAME = "nimbus_chat_events";
let broadcastChannel: BroadcastChannel | null = null;
if (typeof window !== "undefined") {
  broadcastChannel = new BroadcastChannel(BC_NAME);
}

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

// SSE event data types (from server)
interface HeartbeatData {
  iteration?: number;
  [key: string]: unknown;
}

export interface ArtifactRef {
  ref: string;
  type: string;
  summary?: string;
  [key: string]: unknown;
}

interface ToolCallData {
  action_id?: string;
  id?: string;
  tool?: string;
  name?: string;
  args?: Record<string, unknown>;
  arguments?: Record<string, unknown>;
  parent_action_id?: string;  // For sub_tool_call: routes to the correct ParallelDispatch parent
  event_id?: string;
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
  fault?: { message: string;[key: string]: unknown };
  parent_action_id?: string;  // For sub_tool_result: routes to the correct ParallelDispatch parent
  event_id?: string;
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
  streamingToolResults: ToolResult[];
  messageQueue: string[]; // Queued user messages
  lastEventId: string | null;

  // Real-time progress indicators
  fsmState: 'THINKING' | 'ACTING' | 'STREAMING' | 'IDLE' | null;     // Unified Agent FSM State
  activeArtifact: ArtifactRef | null; // Currently viewed artifact
  lastHeartbeat: number | null;       // Timestamp of last heartbeat

  // Interrupt state
  isInterrupting: boolean;            // Whether interrupt request is being processed
  streamAbortController: AbortController | null;  // For aborting stream requests

  // UI state
  isLoading: boolean;
  error: string | null;
  errorInfo: { code: string; message: string; retryable: boolean; errorId?: string } | null;
  isCreatingSession: boolean;  // Prevent concurrent session creation
  isReconnecting: boolean;     // Show reconnect indicator

  // Internal
  isLeader: boolean;

  // Actions
  createNewSession: (
    force?: boolean,
    options?: {
      name?: string;
      workspace_path?: string;
      agent_mode?: string;
      llm_config?: Record<string, any>;
    }
  ) => Promise<void>;
  switchSession: (session: Session | null) => Promise<void>;
  loadSession: (sessionId: string) => Promise<void>;
  sendMessage: (content: string, attachments?: ChatAttachment[]) => Promise<void>;
  handleServerEvent: (event: ChatEvent, isForwarded?: boolean) => void;
  retryLastMessage: () => void;
  interruptMessage: () => void;
  closeArtifact: () => void;
  clearError: () => void;
  reset: () => void;
}

const initialState = {
  session: null,
  messages: [],
  isStreaming: false,
  isReconnecting: false,
  streamingContent: "",
  streamingToolCalls: [],
  streamingToolResults: [],
  messageQueue: [],
  lastEventId: null,
  fsmState: null,
  activeArtifact: null,
  lastHeartbeat: null,
  isInterrupting: false,
  streamAbortController: null,
  isLoading: false,
  error: null,
  errorInfo: null,
  isCreatingSession: false,
  isLeader: false,
};

// Tools that spawn sub-agents and can contain nested sub_tool_call/sub_tool_result
const META_TOOLS = new Set(["Dispatch", "Explore", "Implement", "Design", "Test", "ParallelDispatch"]);

// Human-readable labels for meta-tools in activity status
const META_TOOL_LABELS: Record<string, string> = {
  Dispatch: "Executor",
  Explore: "Explorer",
  Implement: "Implementer",
  Design: "Architect",
  Test: "Tester",
  ParallelDispatch: "并行调度",
};

// Map server-side specialist names to frontend tool names for DispatchCard rendering
const SPECIALIST_TO_TOOL: Record<string, string> = {
  Explorer: "Explore",
  Implementer: "Implement",
  Architect: "Design",
  Tester: "Test",
};

export const useChatStore = create<ChatState>((set, get) => ({
  ...initialState,

  createNewSession: async (force = false, options?: {
    name?: string;
    workspace_path?: string;
    agent_mode?: string;
    llm_config?: Record<string, any>;
  }) => {
    // Abort any in-flight stream from previous session
    const { streamAbortController: prevController } = get();
    if (prevController) {
      prevController.abort();
    }

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

      // Inherit model from current session, fall back to server default
      const currentSession = get().session;
      const inheritedLlmConfig = currentSession?.llm_config && currentSession.llm_config.model_id && currentSession.llm_config.model_id !== "default"
        ? { provider: currentSession.llm_config.provider || "", model_id: currentSession.llm_config.model_id }
        : undefined;

      const newSession = await createSession({
        // Default to dual_agent unless specified otherwise
        agent_mode: options?.agent_mode || "dual_agent",
        llm_config: options?.llm_config || inheritedLlmConfig,
        ...options,
      });
      console.log("[Store] Session created:", newSession.id);

      // Fix: Persist session ID immediately
      if (typeof window !== "undefined") {
        localStorage.setItem("nimbus_session_id", newSession.id);
      }

      set({ session: newSession, messages: [], isLoading: false, isCreatingSession: false });

      // Process queue if any messages arrived during creation
      const { messageQueue } = get();
      if (messageQueue.length > 0) {
        console.log("[Store] Processing queued message after session creation");
        const next = messageQueue[0];
        set({ messageQueue: messageQueue.slice(1) });
        setTimeout(() => get().sendMessage(next), 0);
      }
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to create session",
        isLoading: false,
        isCreatingSession: false,
      });
    }
  },

  switchSession: async (session: Session | null) => {
    // Abort any in-flight stream from previous session
    const { streamAbortController } = get();
    if (streamAbortController) {
      streamAbortController.abort();
    }
    useWorkflowStore.getState().reset();

    // Handle null session (e.g., when deleting current session)
    if (!session) {
      set({
        session: null,
        messages: [],
        isStreaming: false,
        streamingContent: "",
        streamingToolCalls: [],
        streamingToolResults: [],
        fsmState: null,
        activeArtifact: null,
        error: null,
        isLoading: false,
      });
      if (typeof window !== "undefined") {
        localStorage.removeItem("nimbus_session_id");
      }
      return;
    }

    // Switch to an existing session and load its messages
    const currentSession = get().session;
    const currentMessages = get().messages;
    const isSameSession = currentSession?.id === session.id;

    set({
      isLoading: !isSameSession, // ✨ FIX: Don't show loading spinner for background refetches
      session,
      // Only reset messages if we are actually switching to a different session.
      // If it's the same session (e.g. background sync), preserve current messages to avoid flicker.
      messages: isSameSession ? currentMessages : [],
      // Do not force wipe streaming state here if we are just refetching the SAME session
      // `sendMessage` manages its own stream state teardown gracefully now.
      ...(isSameSession ? {} : {
        isStreaming: false,
        streamingContent: "",
        streamingToolCalls: [],
        streamingToolResults: [],
      }),
      error: null,
    });

    // Persist session ID to localStorage
    if (typeof window !== "undefined") {
      localStorage.setItem("nimbus_session_id", session.id);
    }

    // Load messages from server
    try {
      const serverMessages = await getSessionMessages(session.id);
      console.log("[Store] Raw server messages:", serverMessages);

      // First pass: extract all messages and build maps of tool results and sub-tool events
      const toolResultsMap = new Map<string, ToolResult>();
      const subEventsMap = new Map<string, { subCalls: ToolCall[]; subResults: ToolResult[] }>();

      for (let m of serverMessages) {
        // Try to parse JSON-serialized multimodal content (e.g. "[{\"type\":\"image\",...}]")
        if (typeof m.content === 'string' && m.content.startsWith('[')) {
          try {
            const parsed = JSON.parse(m.content);
            if (Array.isArray(parsed)) {
              m.content = parsed;  // restore as array for further processing below
            }
          } catch {
            // not JSON, keep as string
          }
        }

        // Normalize content if it's an array (e.g. multimodal list of blocks)
        if (Array.isArray(m.content)) {
          // Extract image blocks as attachments
          const imageBlocks = m.content.filter((b: any) => b?.type === 'image' || b?.type === 'image_url');
          if (imageBlocks.length > 0 && m.role === 'user') {
            (m as any)._parsedAttachments = imageBlocks.map((b: any) => {
              // Anthropic format: { type: 'image', source: { type: 'base64', media_type, data } }
              if (b.source?.type === 'base64') {
                return {
                  type: 'image' as const,
                  url: `data:${b.source.media_type};base64,${b.source.data}`,
                  name: 'image',
                };
              }
              // URL format
              if (b.image_url?.url) {
                return { type: 'image' as const, url: b.image_url.url, name: 'image' };
              }
              return null;
            }).filter(Boolean);
          }
          // Extract text content
          m.content = m.content.map((b: any) => typeof b === 'string' ? b : b?.text || '').join('\n').trim();
        } else if (typeof m.content === 'object' && m.content !== null) {
          m.content = JSON.stringify(m.content);
        } else {
          m.content = String(m.content || '');
        }

        if (m.role === 'tool' && m.artifacts) {
          // First, find the tool_call_id for this tool message
          let toolCallId: string | null = null;

          for (const artifact of m.artifacts) {
            if (artifact && typeof artifact === 'object') {
              const art = artifact as Record<string, unknown>;
              if (art.type === 'tool_result' && art.tool_call_id) {
                toolCallId = String(art.tool_call_id);
                toolResultsMap.set(toolCallId, {
                  id: toolCallId,
                  name: String(art.name || ''),
                  result: m.content,
                });
              }
            }
          }

          // Then, extract sub_tool_events if present (attached to Dispatch tool results)
          if (toolCallId) {
            // Get tool name from the tool_result artifact to determine grouping strategy
            let toolName = '';
            for (const artifact of m.artifacts) {
              if (artifact && typeof artifact === 'object') {
                const a = artifact as Record<string, unknown>;
                if (a.type === 'tool_result' && a.name) {
                  toolName = String(a.name);
                  break;
                }
              }
            }

            for (const artifact of m.artifacts) {
              if (artifact && typeof artifact === 'object') {
                const art = artifact as Record<string, unknown>;
                if (art.type === 'sub_tool_events' && Array.isArray(art.events)) {
                  const evts = art.events as Array<{ type: string; data: Record<string, unknown> }>;

                  if (toolName && evts) {
                    demuxSubToolEvents(toolCallId, toolName, evts, subEventsMap, toolResultsMap);
                  }
                }
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
                const parsedCalls = (art.tool_calls as Array<{
                  id?: string;
                  function?: { name?: string; arguments?: string };
                }>).map(tc => {
                  const call: ToolCall = {
                    id: tc.id || '',
                    name: tc.function?.name || '',
                    arguments: tc.function?.arguments
                      ? (typeof tc.function.arguments === 'string'
                        ? (() => { try { return JSON.parse(tc.function.arguments as string); } catch { return {}; } })()
                        : tc.function.arguments as Record<string, unknown>)
                      : {},
                  };

                  // Restore sub-agent tool calls/results for meta-tools (Dispatch, etc.)
                  console.log('[DEBUG] checking call.id:', call.id, 'subEventsMap size:', subEventsMap.size, 'has?', subEventsMap.has(call.id || ''));
                  if (call.id && subEventsMap.has(call.id)) {
                    console.log('[DEBUG] injecting subCalls for call.id:', call.id);
                    const sub = subEventsMap.get(call.id)!;
                    if (sub.subCalls.length > 0) call.subCalls = sub.subCalls;
                    if (sub.subResults.length > 0) call.subResults = sub.subResults;
                  }

                  return call;
                });

                toolCalls = toolCalls ? [...toolCalls, ...parsedCalls] : parsedCalls;

                // Match tool results from the map
                const parsedResults = parsedCalls
                  .filter(tc => tc.id)
                  .map(tc => toolResultsMap.get(tc.id!))
                  .filter((r): r is ToolResult => r !== undefined);

                toolResults = toolResults ? [...toolResults, ...parsedResults] : parsedResults;
              }
            }
          }
        }

        // Detect injected messages: server stores them with "[Intervention] " prefix
        const rawContent = m.content || "";
        const interventionPrefix = "[Intervention] ";
        const isInjection = m.role === "user" && rawContent.startsWith(interventionPrefix);
        const content = isInjection ? rawContent.slice(interventionPrefix.length) : rawContent;

        messages.push({
          id: m.id,
          role: m.role as 'user' | 'assistant' | 'system',
          content, // Ensure content is never null
          toolCalls,
          toolResults: toolResults && toolResults.length > 0 ? toolResults : undefined,
          timestamp: (() => {
            // Defensive UTC parsing: if the server timestamp lacks a timezone
            // indicator (e.g. "2024-02-24 12:00:00"), treat it as UTC by
            // appending "Z". Strings already ending with Z, +00:00, etc. are
            // parsed correctly by Date as-is.
            let raw = m.created_at || "";
            if (raw && !/[Zz]$/.test(raw) && !/[+-]\d{2}:\d{2}$/.test(raw)) {
              raw = raw.replace(" ", "T") + "Z";
            }
            const ts = new Date(raw).getTime();
            return !isNaN(ts) ? ts : Date.now();
          })(),
          ...(isInjection ? { isInjection: true } : {}),
          ...((m as any)._parsedAttachments?.length > 0 ? { attachments: (m as any)._parsedAttachments as ChatAttachment[] } : {}),
        });
      }

      // Sort messages by timestamp to ensure correct order
      messages.sort((a, b) => a.timestamp - b.timestamp);

      console.log("[Store] Parsed messages:", messages);

      // Guard against stale fetch: discard result if session has already switched
      const currentSession = get().session;
      if (!currentSession || currentSession.id !== session.id) {
        console.log(`[Store] Session switched during fetch (expected ${session.id}, got ${currentSession?.id}), discarding stale messages`);
        return;
      }

      // Merge: 服务器数据为权威，但补全图片 attachments（服务器存储丢失了图片 base64）
      // 同时过滤掉已被服务器数据覆盖的乐观更新 user 消息（id 以 user- 开头）
      const existingMessages = get().messages;
      const optimisticUserMsgs = existingMessages.filter(m =>
        m.id.startsWith('user-') || m.id.startsWith('user-inject-')
      );

      // 对服务器返回的每条 user message，尝试从乐观消息里找到对应的 attachments 补进去
      const mergedMessages: Message[] = messages.map(m => {
        if (m.role !== 'user' || m.attachments) return m;
        // 找内容相同的乐观消息
        const match = optimisticUserMsgs.find(o => {
          const oText = o.content?.trim() || '';
          const mText = m.content?.trim() || '';
          return oText === mText && oText.length > 0;
        });
        if (match?.attachments) {
          return { ...m, attachments: match.attachments };
        }
        return m;
      });

      if (!isSameSession) {
        set({
          messages: mergedMessages,
          isLoading: false,
          error: null,
        });
      } else {
        // ✨ FIX: For same-session refetches, avoid setting isLoading: false if it was never true,
        // and just update the messages array gracefully.
        set({
          messages: mergedMessages,
          error: null,
        });
      } console.log(`[Store] Loaded ${mergedMessages.length} messages for session ${session.id} (merged from server, dropped ${existingMessages.length - mergedMessages.length < 0 ? 0 : existingMessages.length - mergedMessages.length} optimistic duplicates)`);

      // Check if session has an active task (agent still running)
      try {
        const { getSessionStatus } = await import("@/lib/api/sessions");
        const status = await getSessionStatus(session.id);
        if (status.running) {
          console.log("[Store] Session has active task, reconnecting...");
          // Reconnect in background (use setTimeout to let state settle)
          setTimeout(() => {
            const currentState = get();
            // Double-check: avoid reconnect if sendMessage already started streaming
            if (!currentState.isStreaming) {
              reconnectToSession(session.id);
            } else {
              console.log("[Store] Already streaming, skip reconnect");
            }
          }, 100);
        }
      } catch (err) {
        // Status check failed, not critical
        console.warn("[Store] Failed to check session status:", err);
      }
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
    } catch (err: any) {
      console.error("[Store] Failed to load session:", err);

      // Only clear session if it's a 404 (Not Found)
      // For network errors (backend restart), keep the ID so we can retry
      if (typeof window !== "undefined") {
        // Check if error has status 404
        if (err.status === 404) {
          localStorage.removeItem("nimbus_session_id");
        }
      }
      set({ isLoading: false });
    }
  },

  sendMessage: async (content: string, attachments?: ChatAttachment[]) => {
    const { session, messages, isStreaming, messageQueue, isCreatingSession } = get();

    // Handle streaming case: Inject message instead of queuing
    if (isStreaming && session) {
      // Optimistically add to UI (with attachments if present)
      const userMessage: Message = {
        id: `user-inject-${Date.now()}`,
        role: "user",
        content,
        attachments: attachments && attachments.length > 0 ? attachments : undefined,
        timestamp: Date.now(),
        isInjection: true,
      };

      set({
        messages: [...messages, userMessage],
        // Don't change streaming state, just append message
      });

      try {
        // Pass attachments along with the message (multimodal injection support)
        await injectMessage(session.id, content, attachments);
        console.log(`[Store] Injected message into session ${session.id}${attachments && attachments.length > 0 ? ` with ${attachments.length} attachment(s)` : ""}`);
      } catch (err) {
        console.error("[Store] Failed to inject message:", err);
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
      attachments,
      timestamp: Date.now(),
    };

    // Create abort controller for this request
    const abortController = new AbortController();

    set({
      messages: [...messages, userMessage],
      isStreaming: true,
      streamingContent: "",
      streamingToolCalls: [],
      streamingToolResults: [],
      lastHeartbeat: Date.now(),
      streamAbortController: abortController,
      error: null,
      fsmState: "THINKING",
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
      for await (const event of streamChat(currentSession.id, content, attachments, abortController.signal)) {
        const { type, data } = event;

        // Guard: abort if session switched while streaming
        if (get().session?.id !== currentSession.id) {
          console.log('[Store] Session switched during streaming, aborting old stream');
          abortController.abort();
          break;
        }

        switch (type) {
          case "connected":
            set({
              lastHeartbeat: Date.now()
            });
            break;

          case "message_start":
            set({
              lastHeartbeat: Date.now()
            });
            break;

          case "task_start":
            set({
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
                streamingToolResults: [],
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
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "heartbeat":
            if (data && typeof data === "object") {
              const hbData = data as HeartbeatData & { fsm_state?: 'THINKING' | 'ACTING' | 'STREAMING' | 'IDLE' };
              set({
                fsmState: hbData.fsm_state || "THINKING",
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "thinking":
          case "message":
            let newContent = "";
            if (typeof data === "string") {
              newContent = data;
            } else if (typeof data === "object" && data) {
              const d = data as { content?: unknown; chunk?: unknown };
              if ("content" in d && typeof d.content === "string") {
                newContent = d.content;
              } else if ("chunk" in d && typeof d.chunk === "string") {
                newContent = d.chunk;
              }
            }

            if (newContent) {
              assistantContent += newContent;
              const now = Date.now();
              const d = data as { fsm_state?: 'THINKING' | 'ACTING' | 'STREAMING' | 'IDLE', event_id?: string };
              // Throttle updates to avoid flickering
              if (now - lastUpdate > UPDATE_INTERVAL) {
                set({
                  streamingContent: assistantContent,
                  fsmState: d?.fsm_state || "STREAMING",
                  lastEventId: d?.event_id || null,
                  lastHeartbeat: now
                });
                lastUpdate = now;
              }
            }
            break;

          case "tool_call":
            if (data && typeof data === "object") {
              const d = data as ToolCallData & { fsm_state?: 'THINKING' | 'ACTING' | 'STREAMING' | 'IDLE' };
              // Map server format (action_id, tool, args) to frontend format (id, name, arguments)
              const tool: ToolCall = {
                id: d.action_id || d.id || "",
                name: d.tool || d.name || "unknown",
                arguments: d.args || d.arguments || {},
                agentType: "core",
              };
              toolCalls.push(tool);
              // Force sync streamingContent to ensure any thinking content before tool call is visible
              set({
                streamingContent: assistantContent,
                streamingToolCalls: [...toolCalls],
                fsmState: d.fsm_state || "ACTING",
                lastEventId: d.event_id || null,
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "tool_result":
            if (data && typeof data === "object") {
              const d = data as ToolResultData & { fsm_state?: 'THINKING' | 'ACTING' | 'STREAMING' | 'IDLE' };
              const result: ToolResult = {
                id: d.action_id || d.id || "",
                name: d.tool || d.name || "unknown",
                result: d.output !== undefined ? d.output : d.result,
                error: d.status === "ERROR" ? (d.fault ? d.fault.message : "Error") : undefined,
                duration: d.duration_ms,
              };
              toolResults.push(result);
              set({
                streamingToolResults: [...toolResults],
                fsmState: d.fsm_state || "ACTING",
                lastEventId: d.event_id || null,
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "sub_tool_call":
            if (data && typeof data === "object") {
              const d = data as ToolCallData & { fsm_state?: 'THINKING' | 'ACTING' | 'STREAMING' | 'IDLE', event_id?: string };
              const subTool: ToolCall = {
                id: d.action_id || d.id || "",
                name: d.tool || d.name || "unknown",
                arguments: d.args || d.arguments || {},
                agentType: "dispatch",
              };

              routeSubToolCall(subTool, d, toolCalls, (args) => useWorkflowStore.getState().upsertCall(args));

              set({
                streamingToolCalls: [...toolCalls],
                fsmState: d.fsm_state || "ACTING",
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "sub_tool_result":
            if (data && typeof data === "object") {
              const d = data as ToolResultData & { fsm_state?: 'THINKING' | 'ACTING' | 'STREAMING' | 'IDLE', event_id?: string };
              const subResult: ToolResult = {
                id: d.action_id || d.id || "",
                name: d.tool || d.name || "unknown",
                result: d.output !== undefined ? d.output : d.result,
                error: d.status === "ERROR" ? (d.fault ? d.fault.message : "Error") : undefined,
                duration: d.duration_ms,
              };

              routeSubToolResult(subResult, d, toolCalls, (args) => useWorkflowStore.getState().upsertCall(args));

              set({
                streamingToolCalls: [...toolCalls],
                fsmState: d.fsm_state || "ACTING",
                lastEventId: d.event_id || null,
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "executor_start": {
            const esd = data as Record<string, any>;
            routeExecutorStart(esd, toolCalls, (args) => useWorkflowStore.getState().upsertCall(args));
            set({
              streamingToolCalls: [...toolCalls],
              fsmState: esd?.fsm_state || "ACTING",
              lastEventId: esd?.event_id || null,
              lastHeartbeat: Date.now()
            });
            break;
          }

          case "executor_done": {
            const edd = data as Record<string, any>;
            routeExecutorDone(edd, toolCalls, (args) => useWorkflowStore.getState().upsertCall(args));
            set({
              streamingToolCalls: [...toolCalls],
              fsmState: edd?.fsm_state || "ACTING",
              lastEventId: edd?.event_id || null,
              lastHeartbeat: Date.now()
            });
            break;
          }

          case "permission_request": {
            const permData = data as { action?: string; description?: string; fsm_state?: 'THINKING' | 'ACTING' | 'STREAMING' | 'IDLE'; event_id?: string } | undefined;
            const desc = permData?.description || permData?.action || "Permission requested";
            set({
              fsmState: permData?.fsm_state || "IDLE",
              lastEventId: permData?.event_id || null
            });
            // Add system message to notify user
            set(state => ({
              messages: [...state.messages, {
                id: `perm-${Date.now()}`,
                role: "system" as const,
                content: `⚠️ Agent requests permission: ${desc}`,
                timestamp: Date.now(),
              }]
            }));
            break;
          }

          case "dag_complete":
            set({
              fsmState: "IDLE",
              lastHeartbeat: Date.now()
            });
            // Auto-refresh session to pick up auto-generated title (first 3 rounds)
            if (messages.length <= 6) {
              setTimeout(async () => {
                try {
                  const { getSession } = await import("@/lib/api/sessions");
                  const updated = await getSession(currentSession.id);
                  set({ session: updated });
                } catch { }
              }, 5000);
            }
            // Stream completed successfully, exit loop
            shouldContinue = false;
            break;

          case "error": {
            const errData = data as { code?: string; message?: string; retryable?: boolean; error_id?: string } | string;
            const code = typeof errData === "object" ? (errData.code ?? "stream_error") : "stream_error";
            const message = typeof errData === "object"
              ? (errData.message ?? "Stream error")
              : (typeof errData === "string" ? errData : "Stream error");
            const retryable = typeof errData === "object" ? (errData.retryable ?? false) : false;
            const errorId = typeof errData === "object" ? errData.error_id : undefined;
            const typedErr = new Error(message) as Error & { errorCode: string; retryable: boolean; errorId?: string };
            typedErr.errorCode = code;
            typedErr.retryable = retryable;
            if (errorId) typedErr.errorId = errorId;
            throw typedErr;
          }
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
        // ✨ FIX: Do not immediately drop streaming state if we are about to fetch!
        // We will let the `switchSession` call clear the streaming state later, 
        // to prevent UI flicker when the "cloud background" shows through the empty ChatList.
        streamingContent: "",
        streamingToolCalls: [],
        streamingToolResults: [],
        fsmState: null,
        activeArtifact: null,
        lastHeartbeat: null,
        // Wait to clear these until after session refresh:
        // isStreaming: false, 
        // streamAbortController: null,
        isInterrupting: false,
      });

      // Reload session from server to replace any optimistic injection messages
      // with the real persisted ones (server strips [Intervention] prefix automatically)
      const sessionAfterDag = get().session;
      if (sessionAfterDag) {
        try {
          const { getSessionMessages } = await import("@/lib/api/sessions");
          const serverMsgs = await getSessionMessages(sessionAfterDag.id);
          if (serverMsgs.length > 0) {
            await get().switchSession(sessionAfterDag);
          }
        } catch {
          // Non-critical: local state is still usable
        }
      }

      // Now it's safe to drop the streaming states because the fresh DOM is ready
      set({
        isStreaming: false,
        streamAbortController: null,
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
          streamingToolResults: [],
          lastHeartbeat: null,
          streamAbortController: null,
          isInterrupting: false,
          error: null, // Don't set error for user cancellation
        });
      } else if (
        err instanceof TypeError &&
        (err.message.includes("Load failed") ||
          err.message.includes("Failed to fetch") ||
          err.message.includes("network"))
      ) {
        // Network error (e.g., iOS Safari kills fetch when switching apps)
        // Don't show error — visibility change handler will recover
        console.info("[Store] Network disconnected, agent continues in background");
        set({
          isStreaming: false,
          streamingContent: "",
          streamingToolCalls: [],
          streamingToolResults: [],
          lastHeartbeat: null,
          streamAbortController: null,
          isInterrupting: false,
          error: null,
        });
      } else {
        // Real error occurred - extract structured error info if available
        const errorCode = (err as any).errorCode as string | undefined;
        const isRetryable = (err as any).retryable as boolean | undefined;
        const errorId = (err as any).errorId as string | undefined;
        const rawMessage = err instanceof Error ? err.message : "Failed to send message";

        // Map code to user-friendly display message with icon hint
        let errorMessage: string;
        switch (errorCode) {
          case "llm_rate_limit": errorMessage = rawMessage; break;
          case "resource_timeout": errorMessage = rawMessage; break;
          case "llm_ctx_overflow": errorMessage = rawMessage; break;
          case "auth_error": errorMessage = rawMessage; break;
          case "agent_error":
          case "kernel_system_error": errorMessage = rawMessage; break;
          default: errorMessage = rawMessage;
        }

        const errorInfo = errorCode
          ? { code: errorCode, message: rawMessage, retryable: isRetryable ?? false, errorId }
          : null;

        set({
          error: errorMessage,
          errorInfo,
          isStreaming: false,
          streamingContent: "",
          streamingToolCalls: [],
          streamingToolResults: [],
          lastHeartbeat: null,
          streamAbortController: null,
          isInterrupting: false,
        });
      }
    }
  },

  retryLastMessage: () => {
    const state = get();
    if (state.isStreaming) return;

    // Find last user message
    const lastUserIdx = [...state.messages].reverse().findIndex(m => m.role === 'user');
    if (lastUserIdx === -1) return;

    const actualIdx = state.messages.length - 1 - lastUserIdx;
    const lastUserMsg = state.messages[actualIdx];

    // Remove messages after the last user message
    set({ messages: state.messages.slice(0, actualIdx), error: null });

    // Re-send
    get().sendMessage(lastUserMsg.content, lastUserMsg.attachments);
  },

  clearError: () => {
    set({ error: null, errorInfo: null });
  },

  // UI action for closing artifact viewer
  closeArtifact: () => {
    set({ activeArtifact: null });
  },

  interruptMessage: () => {
    const { streamAbortController, isStreaming, session } = get();

    if (isStreaming && streamAbortController) {
      set({ isInterrupting: true });

      // Call server-side interrupt to cancel the agent task
      if (session) {
        import("@/lib/api/sessions").then(({ interruptSession }) => {
          interruptSession(session.id).catch(err => {
            console.warn("[Store] Server-side interrupt failed:", err);
          });
        });
      }

      // Abort the client-side SSE stream
      streamAbortController.abort();
    }
  },

  handleServerEvent: (event: ChatEvent, isForwarded?: boolean) => {
    // This action can be used to manually inject events into the store's stream logic
    // or handle events broadcasted from other tabs.
    // Currently, streamChat handles events internally, but this is required by ChatState.
    console.debug("[Store] handleServerEvent", event, isForwarded);
  },

  reset: () => { useWorkflowStore.getState().reset(); set(initialState); },
}));
