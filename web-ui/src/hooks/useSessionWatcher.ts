"use client";

/**
 * useSessionWatcher
 *
 * Maintains a background SSE connection to the current session's /events endpoint.
 * When a remote client starts a new task (emits `message_start`), this hook:
 *   1. Reloads session messages to pick up the user message sent by the remote client
 *   2. Attaches to the live SSE stream via _attachToRunningSession
 *
 * Includes automatic reconnect with exponential backoff so NAT/proxy timeouts
 * (e.g. OpenWrt connection tracking) don't permanently break the watcher.
 */

import { useEffect, useRef, useCallback } from "react";
import { useChatStore } from "@/stores/chat-store";
import { subscribeToEvents, getSessionMessages } from "@/lib/api";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export function useSessionWatcher() {
    const sessionId = useChatStore(s => s.session?.id);
    const isStreaming = useChatStore(s => s.isStreaming);
    const watcherAbortRef = useRef<AbortController | null>(null);
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const clearReconnectTimer = useCallback(() => {
        if (reconnectTimerRef.current) {
            clearTimeout(reconnectTimerRef.current);
            reconnectTimerRef.current = null;
        }
    }, []);

    const startWatcher = useCallback((sid: string, attempt = 0) => {
        // Abort any previous watcher loop
        watcherAbortRef.current?.abort();
        clearReconnectTimer();

        const controller = new AbortController();
        watcherAbortRef.current = controller;

        (async () => {
            try {
                for await (const event of subscribeToEvents(sid, controller.signal)) {
                    const state = useChatStore.getState();

                    // Session changed — bail out, no reconnect
                    if (state.session?.id !== sid) return;

                    // Reset backoff on any successful event
                    attempt = 0;

                    if (event.type === "message_start" && !state.isStreaming) {
                        // Remote client started a task on this session.
                        // Step 1: reload history to capture the user message they sent.
                        // We cannot use switchSession() here because isSameSession=true
                        // would skip the message reload. Fetch directly and merge instead.
                        try {
                            const serverMessages = await getSessionMessages(sid);
                            // Find user messages not yet in local state
                            const localIds = new Set(useChatStore.getState().messages.map(m => m.id));
                            const newUserMsgs = serverMessages
                                .filter(m => m.role === "user" && !localIds.has(m.id))
                                .map(m => {
                                    const rawContent = m.content || "";
                                    const textContent = Array.isArray(rawContent)
                                        ? (rawContent as any[]).filter(b => b?.type === "text" || typeof b === "string").map(b => typeof b === "string" ? b : b.text || "").join("\n").trim()
                                        : String(rawContent);
                                    const ts = m.created_at ? new Date(m.created_at.replace(" ", "T") + (m.created_at.includes("Z") ? "" : "Z")).getTime() : Date.now();
                                    return {
                                        id: m.id,
                                        role: "user" as const,
                                        content: textContent,
                                        parts: textContent ? [{ type: "text" as const, content: textContent }] : [],
                                        timestamp: ts,
                                    };
                                });
                            if (newUserMsgs.length > 0) {
                                useChatStore.setState(s => ({
                                    messages: [...s.messages, ...newUserMsgs].sort((a, b) => a.timestamp - b.timestamp),
                                }));
                            }
                        } catch { /* non-fatal */ }

                        // Step 2: attach to the running stream
                        const fresh = useChatStore.getState();
                        if (fresh.session?.id === sid && !fresh.isStreaming) {
                            fresh._attachToRunningSession(sid);
                        }

                        // Watcher is superseded by _attachToRunningSession — stop here.
                        // It will be restarted by the isStreaming→false effect when done.
                        controller.abort();
                        return;
                    }
                }

                // Stream ended cleanly (done event / server closed connection).
                // This is normal — reconnect immediately.
                scheduleReconnect(sid, 0);

            } catch (err: any) {
                if (err?.name === "AbortError") return; // intentional abort — don't reconnect

                // Network error / NAT timeout / proxy drop — reconnect with backoff
                scheduleReconnect(sid, attempt);
            }
        })();
    }, [clearReconnectTimer]); // eslint-disable-line react-hooks/exhaustive-deps

    const scheduleReconnect = useCallback((sid: string, attempt: number) => {
        // Don't reconnect if session changed or we started streaming
        const state = useChatStore.getState();
        if (state.session?.id !== sid || state.isStreaming) return;

        const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
        reconnectTimerRef.current = setTimeout(() => {
            // Re-check conditions after delay
            const fresh = useChatStore.getState();
            if (fresh.session?.id === sid && !fresh.isStreaming) {
                startWatcher(sid, attempt + 1);
            }
        }, delay);
    }, [startWatcher]); // eslint-disable-line react-hooks/exhaustive-deps

    // Start watcher when session changes
    useEffect(() => {
        if (!sessionId || isStreaming) return;
        startWatcher(sessionId, 0);
        return () => {
            watcherAbortRef.current?.abort();
            clearReconnectTimer();
        };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sessionId]);

    // Restart watcher after our own streaming ends
    useEffect(() => {
        if (!isStreaming && sessionId) {
            // Small delay to let the done event settle before re-subscribing
            const t = setTimeout(() => startWatcher(sessionId, 0), 600);
            return () => clearTimeout(t);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isStreaming]);
}
