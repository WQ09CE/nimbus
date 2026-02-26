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
  createSession,
  streamChat,
  injectMessage,
  getSessionMessages,
  getSession,
} from "@/lib/api";
import { useWorkflowStore } from "./workflow-store";

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

interface ToolCallData {
  action_id?: string;
  id?: string;
  tool?: string;
  name?: string;
  args?: Record<string, unknown>;
  arguments?: Record<string, unknown>;
  parent_action_id?: string;  // For sub_tool_call: routes to the correct ParallelDispatch parent
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
  errorInfo: { code: string; message: string; retryable: boolean; errorId?: string } | null;
  isCreatingSession: boolean;  // Prevent concurrent session creation

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
  reconnectToSession: (sessionId: string) => Promise<void>;
  retryLastMessage: () => void;
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
  streamingToolResults: [],
  messageQueue: [],
  thinkingIteration: null,
  currentActivity: null,
  lastHeartbeat: null,
  isInterrupting: false,
  streamAbortController: null,
  isLoading: false,
  error: null,
  errorInfo: null,
  isCreatingSession: false,
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

      const newSession = await createSession({
        // Default to dual_agent unless specified otherwise
        agent_mode: options?.agent_mode || "dual_agent",
        llm_config: options?.llm_config || {
          provider: "",
          model_id: "default",
        },
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
    // NOTE: Do NOT clear messages here to avoid white-screen flash.
    // Messages will be replaced atomically after fetch completes.
    set({
      session,
      isStreaming: false,
      streamingContent: "",
      streamingToolCalls: [],
      streamingToolResults: [],
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

                  if (toolName === 'ParallelDispatch') {
                    // ── ParallelDispatch: group sub-events by batch_slot_index to reconstruct specialist slots
                    const SPEC_TO_TOOL: Record<string, string> = {
                      Explorer: 'Explore', Implementer: 'Implement', Architect: 'Design', Tester: 'Test',
                    };
                    const slotMap = new Map<number, { specialist: string; subCalls: ToolCall[]; subResults: ToolResult[] }>();
                    const actionIdToSlot = new Map<string, number>();
                    let autoSlot = 0;

                    for (const evt of evts) {
                      if (evt.type === 'sub_tool_call' && evt.data) {
                        const slotIdx = typeof evt.data.batch_slot_index === 'number'
                          ? evt.data.batch_slot_index
                          : autoSlot++;
                        const specialist = String(evt.data.specialist || '');
                        const actionId = String(evt.data.action_id || evt.data.id || '');

                        if (!slotMap.has(slotIdx)) {
                          slotMap.set(slotIdx, { specialist, subCalls: [], subResults: [] });
                        }
                        const slot = slotMap.get(slotIdx)!;
                        slot.subCalls.push({
                          id: actionId,
                          name: String(evt.data.tool || evt.data.name || 'unknown'),
                          arguments: (evt.data.args || evt.data.arguments || {}) as Record<string, unknown>,
                          agentType: 'dispatch',
                        });
                        if (actionId) actionIdToSlot.set(actionId, slotIdx);

                      } else if (evt.type === 'sub_tool_result' && evt.data) {
                        const actionId = String(evt.data.action_id || evt.data.id || '');
                        const fault = evt.data.fault as { message: string } | undefined;
                        const subRes: ToolResult = {
                          id: actionId,
                          name: String(evt.data.tool || evt.data.name || 'unknown'),
                          result: evt.data.output !== undefined ? evt.data.output : evt.data.result,
                          error: evt.data.status === 'ERROR' ? (fault ? fault.message : 'Error') : undefined,
                          duration: evt.data.duration_ms as number | undefined,
                        };

                        // 1. Add to the slot for rendering hierarchy
                        const slotIdx = actionIdToSlot.get(actionId);
                        if (slotIdx !== undefined && slotMap.has(slotIdx)) {
                          slotMap.get(slotIdx)!.subResults.push(subRes);
                        }

                        // 2. IMPORTANT: Also flatten to toolResultsMap so the second pass can find it by action_id
                        if (actionId) {
                          toolResultsMap.set(actionId, subRes);
                        }
                      }
                    }

                    if (slotMap.size > 0) {
                      const sortedSlots = Array.from(slotMap.entries()).sort((a, b) => a[0] - b[0]);
                      const specialistCalls: ToolCall[] = sortedSlots.map(([slotIdx, slot]) => {
                        const sName = SPEC_TO_TOOL[slot.specialist] || slot.specialist || 'Dispatch';
                        return {
                          id: `${toolCallId}-slot-${slotIdx}`,
                          name: sName,
                          arguments: {},
                          agentType: 'dispatch' as const,
                          subCalls: slot.subCalls,
                          subResults: slot.subResults,
                        };
                      });
                      subEventsMap.set(toolCallId, { subCalls: specialistCalls, subResults: [] });
                    }

                  } else {
                    // ── Regular specialist tool (Explore/Implement/etc.): flat list of sub-tool-calls
                    const subCalls: ToolCall[] = [];
                    const subResults: ToolResult[] = [];

                    for (const evt of evts) {
                      if (evt.type === 'sub_tool_call' && evt.data) {
                        subCalls.push({
                          id: String(evt.data.action_id || evt.data.id || ''),
                          name: String(evt.data.tool || evt.data.name || 'unknown'),
                          arguments: (evt.data.args || evt.data.arguments || {}) as Record<string, unknown>,
                          agentType: 'dispatch',
                        });
                      } else if (evt.type === 'sub_tool_result' && evt.data) {
                        const fault = evt.data.fault as { message: string } | undefined;
                        subResults.push({
                          id: String(evt.data.action_id || evt.data.id || ''),
                          name: String(evt.data.tool || evt.data.name || 'unknown'),
                          result: evt.data.output !== undefined ? evt.data.output : evt.data.result,
                          error: evt.data.status === 'ERROR' ? (fault ? fault.message : 'Error') : undefined,
                          duration: evt.data.duration_ms as number | undefined,
                        });
                      }
                    }

                    if (subCalls.length > 0 || subResults.length > 0) {
                      console.log('[DEBUG] subEventsMap.set (Regular)', toolCallId, 'subCalls:', subCalls.length, 'subResults:', subResults.length);
                      subEventsMap.set(toolCallId, { subCalls, subResults });
                    }
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

      set({ messages: mergedMessages, isLoading: false });
      console.log(`[Store] Loaded ${mergedMessages.length} messages for session ${session.id} (merged from server, dropped ${existingMessages.length - mergedMessages.length < 0 ? 0 : existingMessages.length - mergedMessages.length} optimistic duplicates)`);

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
              get().reconnectToSession(session.id);
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
                agentType: "core",
              };
              toolCalls.push(tool);
              // Force sync streamingContent to ensure any thinking content before tool call is visible
              set({
                streamingContent: assistantContent,
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
                streamingToolResults: [...toolResults],
                currentActivity: "工具执行完成",
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "sub_tool_call":
            if (data && typeof data === "object") {
              const d = data as ToolCallData;
              const subTool: ToolCall = {
                id: d.action_id || d.id || "",
                name: d.tool || d.name || "unknown",
                arguments: d.args || d.arguments || {},
                agentType: "dispatch",
              };

              // Batch routing: parent_action_id + batch_slot_index -> specialist's subCalls
              const parentId = d.parent_action_id;
              const slotIdx = (d as any).batch_slot_index;
              if (parentId && slotIdx !== undefined) {
                const metaIdx = toolCalls.findIndex(tc => tc.id === parentId);
                if (metaIdx >= 0) {
                  const meta = toolCalls[metaIdx];
                  const specialistSlot = meta.subCalls?.[slotIdx];
                  if (specialistSlot) {
                    if (!specialistSlot.subCalls) specialistSlot.subCalls = [];
                    specialistSlot.subCalls.push(subTool);
                    const label = META_TOOL_LABELS[specialistSlot.name] || specialistSlot.name;
                    useWorkflowStore.getState().upsertCall({
                      callId: subTool.id || "",
                      name: subTool.name,
                      parentId: specialistSlot.id,
                      status: "running",
                      args: subTool.arguments as Record<string, unknown>,
                    });
                    set({
                      streamingToolCalls: [...toolCalls],
                      currentActivity: `${label}: ${subTool.name}`,
                      lastHeartbeat: Date.now()
                    });
                    break;
                  }
                }
              }

              // Legacy routing (no batch metadata): use parent_action_id or last meta-tool
              let targetMetaIdx = -1;
              if (parentId) {
                targetMetaIdx = toolCalls.findIndex(tc => tc.id === parentId);
              }
              // Fallback: last meta-tool
              if (targetMetaIdx < 0) {
                targetMetaIdx = toolCalls.reduce(
                  (last, tc, i) => META_TOOLS.has(tc.name) ? i : last, -1
                );
              }
              if (targetMetaIdx >= 0) {
                const metaTool = toolCalls[targetMetaIdx];
                if (!metaTool.subCalls) metaTool.subCalls = [];
                metaTool.subCalls.push(subTool);
              }
              const metaLabel = targetMetaIdx >= 0
                ? META_TOOL_LABELS[toolCalls[targetMetaIdx].name] || toolCalls[targetMetaIdx].name
                : "Executor";
              if (targetMetaIdx >= 0) {
                useWorkflowStore.getState().upsertCall({
                  callId: subTool.id || "",
                  name: subTool.name,
                  parentId: toolCalls[targetMetaIdx].id,
                  status: "running",
                  args: subTool.arguments as Record<string, unknown>,
                });
              }
              set({
                streamingToolCalls: [...toolCalls],
                currentActivity: `${metaLabel}: ${subTool.name}`,
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "sub_tool_result":
            if (data && typeof data === "object") {
              const d = data as ToolResultData;
              const subResult: ToolResult = {
                id: d.action_id || d.id || "",
                name: d.tool || d.name || "unknown",
                result: d.output !== undefined ? d.output : d.result,
                error: d.status === "ERROR" ? (d.fault ? d.fault.message : "Error") : undefined,
                duration: d.duration_ms,
              };

              // Batch routing: parent_action_id + batch_slot_index -> specialist's subResults
              const parentIdForResult = d.parent_action_id;
              const slotIdxForResult = (d as any).batch_slot_index;
              if (parentIdForResult && slotIdxForResult !== undefined) {
                const metaIdx = toolCalls.findIndex(tc => tc.id === parentIdForResult);
                if (metaIdx >= 0) {
                  const meta = toolCalls[metaIdx];
                  const specialistSlot = meta.subCalls?.[slotIdxForResult];
                  if (specialistSlot) {
                    if (!specialistSlot.subResults) specialistSlot.subResults = [];
                    specialistSlot.subResults.push(subResult);
                    const label = META_TOOL_LABELS[specialistSlot.name] || specialistSlot.name;
                    useWorkflowStore.getState().upsertCall({
                      callId: subResult.id || "",
                      name: subResult.name,
                      parentId: specialistSlot.id,
                      status: subResult.error ? "failed" : "completed",
                      result: subResult.result,
                    });
                    set({
                      streamingToolCalls: [...toolCalls],
                      currentActivity: `${label}: ${subResult.name} done`,
                      lastHeartbeat: Date.now()
                    });
                    break;
                  }
                }
              }

              // Legacy routing
              let targetMetaIdxForResult = -1;
              if (parentIdForResult) {
                targetMetaIdxForResult = toolCalls.findIndex(tc => tc.id === parentIdForResult);
              }
              // Fallback: find meta-tool that owns the sub-call with matching id
              if (targetMetaIdxForResult < 0 && subResult.id) {
                targetMetaIdxForResult = toolCalls.findIndex(
                  tc => META_TOOLS.has(tc.name) && tc.subCalls?.some(sc => sc.id === subResult.id)
                );
              }
              // Last resort: last meta-tool
              if (targetMetaIdxForResult < 0) {
                targetMetaIdxForResult = toolCalls.reduce(
                  (last, tc, i) => META_TOOLS.has(tc.name) ? i : last, -1
                );
              }
              if (targetMetaIdxForResult >= 0) {
                const metaTool = toolCalls[targetMetaIdxForResult];
                if (!metaTool.subResults) metaTool.subResults = [];
                metaTool.subResults.push(subResult);
              }
              const metaResultLabel = targetMetaIdxForResult >= 0
                ? META_TOOL_LABELS[toolCalls[targetMetaIdxForResult].name] || toolCalls[targetMetaIdxForResult].name
                : "Executor";
              if (targetMetaIdxForResult >= 0) {
                useWorkflowStore.getState().upsertCall({
                  callId: subResult.id || "",
                  name: subResult.name,
                  parentId: toolCalls[targetMetaIdxForResult].id,
                  status: subResult.error ? "failed" : "completed",
                  result: subResult.result,
                });
              }
              set({
                streamingToolCalls: [...toolCalls],
                currentActivity: `${metaResultLabel}: ${d.tool || d.name} done`,
                lastHeartbeat: Date.now()
              });
            }
            break;

          case "executor_start": {
            const esd = data as Record<string, any>;
            const esParentId = esd?.parent_action_id;
            const esSlotIdx = esd?.batch_slot_index;
            const esSpecialist = esd?.specialist;
            const esPid = esd?._executor_pid;

            if (esParentId && esSlotIdx !== undefined && esSpecialist) {
              // ParallelDispatch batch: create virtual specialist entry in subCalls
              const metaIdx = toolCalls.findIndex(tc => tc.id === esParentId);
              if (metaIdx >= 0) {
                const meta = toolCalls[metaIdx];
                if (!meta.subCalls) meta.subCalls = [];
                const toolName = SPECIALIST_TO_TOOL[esSpecialist] || esSpecialist;
                // Pull task description from ParallelDispatch args
                const pdTasks = meta.arguments?.tasks;
                const taskDesc = Array.isArray(pdTasks) && esSlotIdx < pdTasks.length
                  ? ((pdTasks[esSlotIdx] as any)?.task || (pdTasks[esSlotIdx] as any)?.context || "")
                  : "";
                const contextDesc = Array.isArray(pdTasks) && esSlotIdx < pdTasks.length
                  ? ((pdTasks[esSlotIdx] as any)?.context || "")
                  : "";
                const esGoal = esd?.goal || "";
                meta.subCalls[esSlotIdx] = {
                  id: esPid || `slot-${esSlotIdx}`,
                  name: toolName,
                  arguments: { task: taskDesc, context: contextDesc, goal: esGoal, model: esd?.model || "" },
                  agentType: "dispatch" as const,
                  subCalls: [],
                  subResults: [],
                };
                useWorkflowStore.getState().upsertCall({
                  callId: esPid || `slot-${esSlotIdx}`,
                  name: toolName,
                  parentId: esParentId,
                  specialist: esSpecialist,
                  batchSlotIndex: esSlotIdx,
                  status: "running",
                  args: { task: taskDesc, context: contextDesc, goal: esGoal, model: esd?.model || "" },
                });
                set({
                  streamingToolCalls: [...toolCalls],
                  currentActivity: `⚡ ${esSpecialist} 已启动...`,
                  lastHeartbeat: Date.now()
                });
                break;
              }
            }
            // Fallback: non-batch executor — propagate resolved model to parent tool
            if (esd?.model_full || esd?.model) {
              const resolvedModel = esd.model_full || esd.model || "";
              // Find the running specialist tool call and inject model into its args
              const runningSpecialist = [...toolCalls].reverse().find(
                tc => META_TOOLS.has(tc.name)
              );
              if (runningSpecialist && runningSpecialist.arguments) {
                (runningSpecialist.arguments as Record<string, unknown>).model = resolvedModel;
              }
            }
            set({
              streamingToolCalls: [...toolCalls],
              currentActivity: "⚡ Executor 已启动...",
              lastHeartbeat: Date.now()
            });
            break;
          }

          case "executor_done": {
            const edd = data as Record<string, any>;
            const edParentId = edd?.parent_action_id;
            const edSlotIdx = edd?.batch_slot_index;

            if (edParentId && edSlotIdx !== undefined) {
              // Mark specialist slot as completed by adding a synthetic result
              const metaIdx = toolCalls.findIndex(tc => tc.id === edParentId);
              if (metaIdx >= 0) {
                const meta = toolCalls[metaIdx];
                const specialistSlot = meta.subCalls?.[edSlotIdx];
                if (specialistSlot) {
                  if (!meta.subResults) meta.subResults = [];
                  meta.subResults.push({
                    id: specialistSlot.id || `slot-${edSlotIdx}`,
                    name: specialistSlot.name,
                    result: edd?.result || "Completed",
                  });
                  useWorkflowStore.getState().upsertCall({
                    callId: specialistSlot.id || `slot-${edSlotIdx}`,
                    name: specialistSlot.name,
                    parentId: edParentId,
                    status: "completed",
                    result: edd?.result,
                  });
                  set({
                    streamingToolCalls: [...toolCalls],
                    currentActivity: `⚡ ${specialistSlot.name} 已完成`,
                    lastHeartbeat: Date.now()
                  });
                  break;
                }
              }
            }
            set({
              currentActivity: "⚡ Executor 已完成",
              lastHeartbeat: Date.now()
            });
            break;
          }

          case "permission_request": {
            const permData = data as { action?: string; description?: string } | undefined;
            const desc = permData?.description || permData?.action || "Permission requested";
            set({ currentActivity: `⚠️ ${desc}` });
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
              currentActivity: "完成",
              lastHeartbeat: Date.now()
            });
            // Auto-refresh session to pick up auto-generated title (first 3 rounds)
            if (messages.length <= 6) {
              setTimeout(async () => {
                try {
                  const { getSession } = await import("@/lib/api/sessions");
                  const updated = await getSession(currentSession.id);
                  set({ session: updated });
                } catch {}
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
        isStreaming: false,
        streamingContent: "",
        streamingToolCalls: [],
        streamingToolResults: [],
        thinkingIteration: null,
        currentActivity: null,
        lastHeartbeat: null,
        streamAbortController: null,
        isInterrupting: false,
      });

      // Reload session from server to replace any optimistic injection messages
      // with the real persisted ones (server strips [Intervention] prefix automatically)
      const sessionAfterDag = get().session;
      if (sessionAfterDag) {
        try {
          await get().switchSession(sessionAfterDag);
        } catch {
          // Non-critical: local state is still usable
        }
      }

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
          thinkingIteration: null,
          currentActivity: null,
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
          thinkingIteration: null,
          currentActivity: null,
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
          case "llm_rate_limit":      errorMessage = rawMessage; break;
          case "resource_timeout":    errorMessage = rawMessage; break;
          case "llm_ctx_overflow":    errorMessage = rawMessage; break;
          case "auth_error":          errorMessage = rawMessage; break;
          case "agent_error":
          case "kernel_system_error": errorMessage = rawMessage; break;
          default:                    errorMessage = rawMessage;
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
          thinkingIteration: null,
          currentActivity: null,
          lastHeartbeat: null,
          streamAbortController: null,
          isInterrupting: false,
        });
      }
    }
  },

  reconnectToSession: async (sessionId: string) => {
    const { session, isStreaming, streamAbortController } = get();
    if (!session || session.id !== sessionId) return;

    // 防重入：如果已有 stream 在跑，先 abort 旧的
    if (isStreaming && streamAbortController) {
      console.log("[Store] Aborting existing stream before reconnect");
      streamAbortController.abort();
    }

    const abortController = new AbortController();

    set({
      isStreaming: true,
      streamingContent: "",
      streamingToolCalls: [],
      streamingToolResults: [],
      currentActivity: "重新连接中...",
      lastHeartbeat: Date.now(),
      streamAbortController: abortController,
    });

    try {
      const { subscribeToEvents } = await import("@/lib/api/chat");
      let assistantContent = "";
      const toolCalls: ToolCall[] = [];
      const toolResults: ToolResult[] = [];

      for await (const event of subscribeToEvents(sessionId, abortController.signal)) {
        const { type, data } = event;

        switch (type) {
          case "connected":
            set({ currentActivity: "已重新连接", lastHeartbeat: Date.now() });
            break;

          case "message": {
            let newContent = "";
            if (typeof data === "string") {
              newContent = data;
            } else if (typeof data === "object" && data && "content" in data) {
              const c = (data as { content?: unknown }).content;
              if (typeof c === "string") newContent = c;
            }
            if (newContent) {
              assistantContent += newContent;
              set({
                streamingContent: assistantContent,
                currentActivity: "生成回复中...",
                lastHeartbeat: Date.now(),
              });
            }
            break;
          }

          case "tool_call":
            if (data && typeof data === "object") {
              const d = data as Record<string, unknown>;
              toolCalls.push({
                id: (d.action_id || d.id || "") as string,
                name: (d.tool || d.name || "unknown") as string,
                arguments: (d.args || d.arguments || {}) as Record<string, unknown>,
                agentType: "core",
              });
              set({
                streamingContent: assistantContent,
                streamingToolCalls: [...toolCalls],
                currentActivity: `执行工具: ${d.tool || d.name}`,
                lastHeartbeat: Date.now(),
              });
            }
            break;

          case "tool_result":
            if (data && typeof data === "object") {
              const d = data as Record<string, unknown>;
              const fault = d.fault as { message: string } | undefined;
              toolResults.push({
                id: (d.action_id || d.id || "") as string,
                name: (d.tool || d.name || "unknown") as string,
                result: d.output !== undefined ? d.output : d.result,
                error: d.status === "ERROR" ? (fault ? fault.message : "Error") : undefined,
                duration: d.duration_ms as number | undefined,
              });
              set({
                streamingToolResults: [...toolResults],
                currentActivity: "工具执行完成",
                lastHeartbeat: Date.now(),
              });
            }
            break;

          case "sub_tool_call":
            if (data && typeof data === "object") {
              const d = data as Record<string, unknown>;
              const subTool: ToolCall = {
                id: (d.action_id || d.id || "") as string,
                name: (d.tool || d.name || "unknown") as string,
                arguments: (d.args || d.arguments || {}) as Record<string, unknown>,
                agentType: "dispatch",
              };

              // Batch routing: parent_action_id + batch_slot_index -> specialist's subCalls
              const parentId = d.parent_action_id as string | undefined;
              const slotIdx = (d as any).batch_slot_index;
              if (parentId && slotIdx !== undefined) {
                const metaIdx = toolCalls.findIndex(tc => tc.id === parentId);
                if (metaIdx >= 0) {
                  const meta = toolCalls[metaIdx];
                  const specialistSlot = meta.subCalls?.[slotIdx];
                  if (specialistSlot) {
                    if (!specialistSlot.subCalls) specialistSlot.subCalls = [];
                    specialistSlot.subCalls.push(subTool);
                    const label = META_TOOL_LABELS[specialistSlot.name] || specialistSlot.name;
                    useWorkflowStore.getState().upsertCall({
                      callId: subTool.id || "",
                      name: subTool.name,
                      parentId: specialistSlot.id,
                      status: "running",
                      args: subTool.arguments as Record<string, unknown>,
                    });
                    set({
                      streamingToolCalls: [...toolCalls],
                      currentActivity: `${label}: ${subTool.name}`,
                      lastHeartbeat: Date.now(),
                    });
                    break;
                  }
                }
              }

              // Legacy routing: parent_action_id or last meta-tool
              let targetMetaIdx = -1;
              if (parentId) {
                targetMetaIdx = toolCalls.findIndex(tc => tc.id === parentId);
              }
              if (targetMetaIdx < 0) {
                targetMetaIdx = toolCalls.reduce(
                  (last, tc, i) => META_TOOLS.has(tc.name) ? i : last, -1
                );
              }
              if (targetMetaIdx >= 0) {
                const metaTool = toolCalls[targetMetaIdx];
                if (!metaTool.subCalls) metaTool.subCalls = [];
                metaTool.subCalls.push(subTool);
              }
              const metaLabel = targetMetaIdx >= 0
                ? META_TOOL_LABELS[toolCalls[targetMetaIdx].name] || toolCalls[targetMetaIdx].name
                : "Executor";
              if (targetMetaIdx >= 0) {
                useWorkflowStore.getState().upsertCall({
                  callId: subTool.id || "",
                  name: subTool.name,
                  parentId: toolCalls[targetMetaIdx].id,
                  status: "running",
                  args: subTool.arguments as Record<string, unknown>,
                });
              }
              set({
                streamingToolCalls: [...toolCalls],
                currentActivity: `${metaLabel}: ${subTool.name}`,
                lastHeartbeat: Date.now(),
              });
            }
            break;

          case "sub_tool_result":
            if (data && typeof data === "object") {
              const d = data as Record<string, unknown>;
              const fault = d.fault as { message: string } | undefined;
              const subResult: ToolResult = {
                id: (d.action_id || d.id || "") as string,
                name: (d.tool || d.name || "unknown") as string,
                result: d.output !== undefined ? d.output : d.result,
                error: d.status === "ERROR" ? (fault ? fault.message : "Error") : undefined,
                duration: d.duration_ms as number | undefined,
              };

              // Batch routing
              const parentIdForResult = d.parent_action_id as string | undefined;
              const slotIdxForResult = (d as any).batch_slot_index;
              if (parentIdForResult && slotIdxForResult !== undefined) {
                const metaIdx = toolCalls.findIndex(tc => tc.id === parentIdForResult);
                if (metaIdx >= 0) {
                  const meta = toolCalls[metaIdx];
                  const specialistSlot = meta.subCalls?.[slotIdxForResult];
                  if (specialistSlot) {
                    if (!specialistSlot.subResults) specialistSlot.subResults = [];
                    specialistSlot.subResults.push(subResult);
                    const label = META_TOOL_LABELS[specialistSlot.name] || specialistSlot.name;
                    useWorkflowStore.getState().upsertCall({
                      callId: subResult.id || "",
                      name: subResult.name,
                      parentId: specialistSlot.id,
                      status: subResult.error ? "failed" : "completed",
                      result: subResult.result,
                    });
                    set({
                      streamingToolCalls: [...toolCalls],
                      currentActivity: `${label}: ${subResult.name} done`,
                      lastHeartbeat: Date.now(),
                    });
                    break;
                  }
                }
              }

              // Legacy routing
              let targetMetaIdxForResult = -1;
              if (parentIdForResult) {
                targetMetaIdxForResult = toolCalls.findIndex(tc => tc.id === parentIdForResult);
              }
              if (targetMetaIdxForResult < 0 && subResult.id) {
                targetMetaIdxForResult = toolCalls.findIndex(
                  tc => META_TOOLS.has(tc.name) && tc.subCalls?.some(sc => sc.id === subResult.id)
                );
              }
              if (targetMetaIdxForResult < 0) {
                targetMetaIdxForResult = toolCalls.reduce(
                  (last, tc, i) => META_TOOLS.has(tc.name) ? i : last, -1
                );
              }
              if (targetMetaIdxForResult >= 0) {
                const metaTool = toolCalls[targetMetaIdxForResult];
                if (!metaTool.subResults) metaTool.subResults = [];
                metaTool.subResults.push(subResult);
              }
              const metaResultLabel = targetMetaIdxForResult >= 0
                ? META_TOOL_LABELS[toolCalls[targetMetaIdxForResult].name] || toolCalls[targetMetaIdxForResult].name
                : "Executor";
              if (targetMetaIdxForResult >= 0) {
                useWorkflowStore.getState().upsertCall({
                  callId: subResult.id || "",
                  name: subResult.name,
                  parentId: toolCalls[targetMetaIdxForResult].id,
                  status: subResult.error ? "failed" : "completed",
                  result: subResult.result,
                });
              }
              set({
                streamingToolCalls: [...toolCalls],
                currentActivity: `${metaResultLabel}: ${d.tool || d.name} done`,
                lastHeartbeat: Date.now(),
              });
            }
            break;

          case "executor_start": {
            const esd = data as Record<string, any>;
            const esParentId = esd?.parent_action_id;
            const esSlotIdx = esd?.batch_slot_index;
            const esSpecialist = esd?.specialist;
            const esPid = esd?._executor_pid;

            if (esParentId && esSlotIdx !== undefined && esSpecialist) {
              // ParallelDispatch batch: create virtual specialist entry in subCalls
              const metaIdx = toolCalls.findIndex(tc => tc.id === esParentId);
              if (metaIdx >= 0) {
                const meta = toolCalls[metaIdx];
                if (!meta.subCalls) meta.subCalls = [];
                const toolName = SPECIALIST_TO_TOOL[esSpecialist] || esSpecialist;
                // Pull task description from ParallelDispatch args
                const pdTasks = meta.arguments?.tasks;
                const taskDesc = Array.isArray(pdTasks) && esSlotIdx < pdTasks.length
                  ? ((pdTasks[esSlotIdx] as any)?.task || (pdTasks[esSlotIdx] as any)?.context || "")
                  : "";
                const contextDesc = Array.isArray(pdTasks) && esSlotIdx < pdTasks.length
                  ? ((pdTasks[esSlotIdx] as any)?.context || "")
                  : "";
                const esGoal = esd?.goal || "";
                meta.subCalls[esSlotIdx] = {
                  id: esPid || `slot-${esSlotIdx}`,
                  name: toolName,
                  arguments: { task: taskDesc, context: contextDesc, goal: esGoal, model: esd?.model || "" },
                  agentType: "dispatch" as const,
                  subCalls: [],
                  subResults: [],
                };
                useWorkflowStore.getState().upsertCall({
                  callId: esPid || `slot-${esSlotIdx}`,
                  name: toolName,
                  parentId: esParentId,
                  specialist: esSpecialist,
                  batchSlotIndex: esSlotIdx,
                  status: "running",
                  args: { task: taskDesc, context: contextDesc, goal: esGoal, model: esd?.model || "" },
                });
                set({
                  streamingToolCalls: [...toolCalls],
                  currentActivity: `⚡ ${esSpecialist} 已启动...`,
                  lastHeartbeat: Date.now(),
                });
                break;
              }
            }
            // Fallback: non-batch executor — propagate resolved model to parent tool
            if (esd?.model_full || esd?.model) {
              const resolvedModel = esd.model_full || esd.model || "";
              const runningSpecialist = [...toolCalls].reverse().find(
                tc => META_TOOLS.has(tc.name)
              );
              if (runningSpecialist && runningSpecialist.arguments) {
                (runningSpecialist.arguments as Record<string, unknown>).model = resolvedModel;
              }
            }
            set({
              streamingToolCalls: [...toolCalls],
              currentActivity: "⚡ Executor 已启动...",
              lastHeartbeat: Date.now(),
            });
            break;
          }

          case "executor_done": {
            const edd = data as Record<string, any>;
            const edParentId = edd?.parent_action_id;
            const edSlotIdx = edd?.batch_slot_index;

            if (edParentId && edSlotIdx !== undefined) {
              // Mark specialist slot as completed by adding a synthetic result
              const metaIdx = toolCalls.findIndex(tc => tc.id === edParentId);
              if (metaIdx >= 0) {
                const meta = toolCalls[metaIdx];
                const specialistSlot = meta.subCalls?.[edSlotIdx];
                if (specialistSlot) {
                  if (!meta.subResults) meta.subResults = [];
                  meta.subResults.push({
                    id: specialistSlot.id || `slot-${edSlotIdx}`,
                    name: specialistSlot.name,
                    result: edd?.result || "Completed",
                  });
                  useWorkflowStore.getState().upsertCall({
                    callId: specialistSlot.id || `slot-${edSlotIdx}`,
                    name: specialistSlot.name,
                    parentId: edParentId,
                    status: "completed",
                    result: edd?.result,
                  });
                  set({
                    streamingToolCalls: [...toolCalls],
                    currentActivity: `⚡ ${specialistSlot.name} 已完成`,
                    lastHeartbeat: Date.now(),
                  });
                  break;
                }
              }
            }
            set({
              currentActivity: "⚡ Executor 已完成",
              lastHeartbeat: Date.now(),
            });
            break;
          }

          case "step_start":
            if (assistantContent || toolCalls.length > 0) {
              const stepMsg: Message = {
                id: `assistant-reconnect-${Date.now()}`,
                role: "assistant",
                content: assistantContent,
                toolCalls: toolCalls.length > 0 ? [...toolCalls] : undefined,
                toolResults: toolResults.length > 0 ? [...toolResults] : undefined,
                timestamp: Date.now(),
              };
              set(state => ({
                messages: [...state.messages, stepMsg],
                streamingContent: "",
                streamingToolCalls: [],
                streamingToolResults: [],
              }));
              assistantContent = "";
              toolCalls.length = 0;
              toolResults.length = 0;
            }
            break;

          case "heartbeat":
            set({ lastHeartbeat: Date.now(), currentActivity: "正在思考..." });
            break;

          case "dag_complete":
            // Agent finished - reload messages directly without calling switchSession
            // (switchSession would abort+reconnect, causing cascading side effects in multi-tab)
            try {
              const { getSessionMessages } = await import("@/lib/api/sessions");
              const serverMessages = await getSessionMessages(sessionId);
              // Use current session from store (not stale closure reference)
              const currentSessionForDag = get().session;
              if (serverMessages.length > 0 && currentSessionForDag && currentSessionForDag.id === sessionId) {
                // Re-use switchSession to parse messages, but it will guard stale writes via session ID check
                await get().switchSession(currentSessionForDag);
              }
            } catch {
              // Fallback: just finalize what we have
            }
            set({
              isStreaming: false,
              streamingContent: "",
              streamingToolCalls: [],
              streamingToolResults: [],
              currentActivity: null,
              lastHeartbeat: null,
              streamAbortController: null,
            });
            return;

          case "error":
            set({
              isStreaming: false,
              streamingContent: "",
              streamingToolCalls: [],
              streamingToolResults: [],
              currentActivity: null,
              error: typeof data === "string" ? data : "Stream error",
              streamAbortController: null,
            });
            return;
        }
      }

      // Stream ended without dag_complete (agent finished while we were connecting)
      // Reload messages from DB (use current session from store, not stale closure)
      const endSession = get().session;
      if (endSession && endSession.id === sessionId) {
        await get().switchSession(endSession);
      }
      set({
        isStreaming: false,
        streamingContent: "",
        streamingToolCalls: [],
        streamingToolResults: [],
        currentActivity: null,
        streamAbortController: null,
      });
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        set({
          isStreaming: false,
          currentActivity: null,
          streamAbortController: null,
        });
      } else {
        // Reconnect failed - not critical, user can refresh
        console.warn("[Store] Reconnect failed:", err);
        set({
          isStreaming: false,
          currentActivity: null,
          streamAbortController: null,
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

  clearError: () => set({ error: null, errorInfo: null }),

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

  reset: () => { useWorkflowStore.getState().reset(); set(initialState); },
}));
