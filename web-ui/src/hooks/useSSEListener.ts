import { useChatStore } from '../stores/chat-store';
import { subscribeToEvents, ToolCall, ToolResult } from '@/lib/api';
import type { Message } from '@/stores/chat-store';

export const reconnectToSession = async (sessionId: string, attempt: number = 0) => {
    const state = useChatStore.getState();
    const { session, isStreaming, streamAbortController } = state;
    if (!session || session.id !== sessionId) return;

    if (isStreaming && streamAbortController) {
        streamAbortController.abort();
    }

    const abortController = new AbortController();

    const STREAMING_ID = "streaming-assistant";
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

    useChatStore.setState(s => ({
        isStreaming: true,
        messages: [...s.messages, initialAssistantMsg],
        streamAbortController: abortController,
        error: null,
    }));

    try {
        for await (const event of subscribeToEvents(sessionId, abortController.signal)) {
            const { type, data } = event;
            const currentMsgs = useChatStore.getState().messages;
            const targetIdx = currentMsgs.findIndex(m => m.id === STREAMING_ID);
            if (targetIdx === -1) continue;

            const targetMsg = { ...currentMsgs[targetIdx] };
            let updated = false;

            switch (type) {
                case "user_message": {
                    if (data && typeof data === "object") {
                        const content = (data as any)?.content || "";
                        if (content) {
                            const userMsg: Message = {
                                id: `user-remote-${Date.now()}`,
                                role: "user",
                                content,
                                parts: [{ type: "text", content }],
                                timestamp: Date.now(),
                                _rev: 0,
                            };
                            const msgs = [...useChatStore.getState().messages];
                            const streamIdx = msgs.findIndex(m => m.id === STREAMING_ID);
                            if (streamIdx !== -1) {
                                msgs.splice(streamIdx, 0, userMsg);
                            } else {
                                msgs.push(userMsg);
                            }
                            useChatStore.setState({ messages: msgs });
                        }
                    }
                    break;
                }
                case "message": {
                    const chunk = typeof data === "string" ? data : (data as any)?.content || (data as any)?.chunk || "";
                    if (chunk) {
                        targetMsg.content += chunk;
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
                        const tc: ToolCall = {
                            id: d.action_id || d.id || `tc-${Date.now()}`,
                            name: d.tool || d.name || "unknown",
                            arguments: d.args || d.arguments || {},
                        };
                        const tcMap = targetMsg.toolCallsMap || {};
                        targetMsg.toolCallsMap = { ...tcMap, [tc.id as string]: tc };
                        const parts = [...(targetMsg.parts || [])];
                        parts.push({ type: "tool", toolCall: tc });
                        targetMsg.parts = parts;
                        updated = true;
                    }
                    break;
                }
                case "tool_result": {
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
                        const parts = [...(targetMsg.parts || [])];
                        const matchIdx = parts.findIndex(p => p.type === "tool" && p.toolCall?.id === tcId);
                        if (matchIdx !== -1) {
                            const toolPart = parts[matchIdx] as { type: "tool"; toolCall: ToolCall; toolResult?: ToolResult };
                            parts[matchIdx] = { ...toolPart, toolResult: tr };
                        }
                        targetMsg.parts = parts;
                        updated = true;
                    }
                    break;
                }
                case "error":
                    useChatStore.setState({ error: typeof data === "string" ? data : (data as any)?.message || "Stream error" });
                    abortController.abort();
                    break;
                case "done":
                    abortController.abort();
                    break;
            }

            if (updated) {
                targetMsg._rev = (targetMsg._rev || 0) + 1;
                const nextMsgs = [...currentMsgs];
                nextMsgs[targetIdx] = targetMsg;
                useChatStore.setState({ messages: nextMsgs });
            }
        }

        const finalMsgs = [...useChatStore.getState().messages];
        const streamingIdx = finalMsgs.findIndex(m => m.id === STREAMING_ID);
        if (streamingIdx !== -1) {
            finalMsgs[streamingIdx].id = `assistant-${Date.now()}`;
        }
        useChatStore.setState({ messages: finalMsgs, isStreaming: false, streamAbortController: null });

    } catch (err) {
        useChatStore.setState({ isStreaming: false, streamAbortController: null });
    }
};
