import { useEffect } from 'react';
import { useChatStore } from '../stores/chat-store';
import { useWorkflowStore } from '../stores/workflow-store';
import { subscribeToEvents, ToolCall, ToolResult } from '@/lib/api';
import { Message } from '@/types';
import { routeSubToolCall, routeSubToolResult, routeExecutorStart, routeExecutorDone } from '../stores/MessageDemuxer';

export const reconnectToSession = async (sessionId: string, attempt: number = 0) => {
    const state = useChatStore.getState();
    const { session, isStreaming, streamAbortController } = state;
    if (!session || session.id !== sessionId) return;

    // Prevent re-entry: abort existing stream if it is running
    if (isStreaming && streamAbortController) {
        console.log("[useSSEListener] Aborting existing stream before reconnect");
        streamAbortController.abort();
    }

    const abortController = new AbortController();

    useChatStore.setState({
        isStreaming: true,
        isReconnecting: false,
        streamingContent: "",
        streamingToolCalls: [],
        streamingToolResults: [],
        lastHeartbeat: Date.now(),
        streamAbortController: abortController,
    });

    try {
        let assistantContent = "";
        const toolCalls: ToolCall[] = [];
        const toolResults: ToolResult[] = [];

        for await (const event of subscribeToEvents(sessionId, abortController.signal)) {
            const { type, data } = event;

            switch (type) {
                case "connected":
                    break;

                case "thinking":
                case "message": {
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
                        useChatStore.setState({
                            streamingContent: assistantContent,
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
                        useChatStore.setState({
                            streamingContent: assistantContent,
                            streamingToolCalls: [...toolCalls],
                            lastHeartbeat: Date.now(),
                        });
                    }
                    break;

                case "session_updated":
                    if (data && typeof data === "object") {
                        const d = data as Record<string, unknown>;
                        if (d.name && d.session_id) {
                            useChatStore.setState(state => {
                                if (state.session && state.session.id === d.session_id) {
                                    return { session: { ...state.session, name: d.name as string } };
                                }
                                return state;
                            });
                        }
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
                        useChatStore.setState({
                            streamingToolResults: [...toolResults],
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
                        routeSubToolCall(subTool, d, toolCalls, (args) => useWorkflowStore.getState().upsertCall(args));
                        useChatStore.setState({
                            streamingToolCalls: [...toolCalls],
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
                        routeSubToolResult(subResult, d, toolCalls, (args) => useWorkflowStore.getState().upsertCall(args));
                        useChatStore.setState({
                            streamingToolCalls: [...toolCalls],
                            lastHeartbeat: Date.now(),
                        });
                    }
                    break;

                case "executor_start": {
                    const esd = data as Record<string, any>;
                    routeExecutorStart(esd, toolCalls, (args) => useWorkflowStore.getState().upsertCall(args));
                    useChatStore.setState({
                        streamingToolCalls: [...toolCalls],
                        lastHeartbeat: Date.now(),
                    });
                    break;
                }

                case "executor_done": {
                    const edd = data as Record<string, any>;
                    routeExecutorDone(edd, toolCalls, (args) => useWorkflowStore.getState().upsertCall(args));
                    useChatStore.setState({
                        streamingToolCalls: [...toolCalls],
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
                        useChatStore.setState(s => ({
                            messages: [...s.messages, stepMsg],
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
                    break;

                case "dag_complete":
                    try {
                        const { getSessionMessages } = await import("@/lib/api/sessions");
                        const serverMessages = await getSessionMessages(sessionId);
                        const currentSessionForDag = useChatStore.getState().session;
                        if (serverMessages.length > 0 && currentSessionForDag && currentSessionForDag.id === sessionId) {
                            await useChatStore.getState().switchSession(currentSessionForDag);
                            return; // State fully synced with server, exit loop
                        }
                    } catch {
                        // Fallback: just let the loop end and commit manually
                    }
                    break; // Allow loop to end so finalizer block runs

                case "error":
                    useChatStore.setState({
                        isStreaming: false,
                        streamingContent: "",
                        streamingToolCalls: [],
                        streamingToolResults: [],
                        error: typeof data === "string" ? data : "Stream error",
                        streamAbortController: null,
                    });
                    return;
            }
        }

        // Stream finished, finalize state without forcing a history wipe
        useChatStore.setState(state => {
            const nextMessages = [...state.messages];
            if (assistantContent || toolCalls.length > 0) {
                nextMessages.push({
                    id: `assistant-${Date.now()}`,
                    role: "assistant",
                    content: assistantContent,
                    toolCalls: toolCalls.length > 0 ? [...toolCalls] : undefined,
                    toolResults: toolResults.length > 0 ? [...toolResults] : undefined,
                    timestamp: Date.now(),
                });
            }
            return {
                messages: nextMessages,
                isStreaming: false,
                streamingContent: "",
                streamingToolCalls: [],
                streamingToolResults: [],
                streamAbortController: null,
                fsmState: "IDLE", // Reset FSM to idle when stream ends gracefully
            };
        });
    } catch (err) {
        if (err instanceof Error && err.name === "AbortError") {
            useChatStore.setState({
                isStreaming: false,
                isReconnecting: false,
                streamAbortController: null,
            });
        } else {
            console.warn(`[useSSEListener] Reconnect failed (attempt ${attempt}):`, err);

            // Exponential backoff
            const maxRetries = 5;
            if (attempt < maxRetries) {
                const backoffMs = Math.min(1000 * Math.pow(2, attempt), 10000);
                console.log(`[useSSEListener] Retrying in ${backoffMs}ms...`);
                useChatStore.setState({ isReconnecting: true });

                setTimeout(() => {
                    const currentStore = useChatStore.getState();
                    if (currentStore.session?.id === sessionId && !currentStore.isStreaming) {
                        reconnectToSession(sessionId, attempt + 1);
                    } else {
                        useChatStore.setState({ isReconnecting: false });
                    }
                }, backoffMs);
            } else {
                console.error("[useSSEListener] Max reconnect retries reached.");
                useChatStore.setState({
                    isStreaming: false,
                    isReconnecting: false,
                    streamAbortController: null,
                    error: "Connection lost. Please refresh or try again.",
                });
            }
        }
    }
};

export function useSSEListener() {
    useEffect(() => {
        let visibilityTimer: ReturnType<typeof setTimeout> | null = null;

        const handleVisibilityChange = () => {
            if (document.visibilityState === 'visible') {
                if (visibilityTimer) clearTimeout(visibilityTimer);
                visibilityTimer = setTimeout(() => {
                    const state = useChatStore.getState();
                    const currentSession = state.session;
                    if (currentSession && !state.isStreaming && !state.isLoading) {
                        console.log('[useSSEListener] Visibility restored, checking if session needs to be reloaded...');
                        state.loadSession(currentSession.id);
                    }
                }, 300);
            }
        };

        document.addEventListener('visibilitychange', handleVisibilityChange);
        return () => {
            document.removeEventListener('visibilitychange', handleVisibilityChange);
            if (visibilityTimer) clearTimeout(visibilityTimer);
        };
    }, []);

    return {
        reconnectToSession
    };
}
