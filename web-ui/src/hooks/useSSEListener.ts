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
        timestamp: Date.now(),
        toolCalls: [],
        toolResults: []
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
                        const tr: ToolResult = {
                            id: d.action_id || d.id || "",
                            name: d.tool || d.name || "unknown",
                            result: d.output !== undefined ? d.output : d.result,
                            error: d.status === "ERROR" ? (d.fault?.message || "Error") : undefined,
                        };
                        targetMsg.toolResults = [...(targetMsg.toolResults || []), tr];
                        updated = true;
                    }
                    break;
                case "error":
                    useChatStore.setState({ error: typeof data === "string" ? data : (data as any)?.message || "Stream error" });
                    abortController.abort();
                    break;
                case "done":
                    abortController.abort();
                    break;
            }

            if (updated) {
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
