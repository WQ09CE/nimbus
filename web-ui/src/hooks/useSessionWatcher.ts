"use client";

/**
 * useSessionWatcher
 *
 * Maintains a background SSE connection to the current session's /events endpoint.
 * When a remote client starts a new task (emits `message_start`), this hook:
 *   1. Reloads session messages to pick up the user message sent by the remote client
 *   2. Attaches to the live SSE stream via _attachToRunningSession
 *
 * This eliminates the need to manually refresh when another device sends a message
 * to the same session.
 */

import { useEffect, useRef, useCallback } from "react";
import { useChatStore } from "@/stores/chat-store";
import { subscribeToEvents } from "@/lib/api";

export function useSessionWatcher() {
    const sessionId = useChatStore(s => s.session?.id);
    const isStreaming = useChatStore(s => s.isStreaming);
    const watcherAbortRef = useRef<AbortController | null>(null);

    const startWatcher = useCallback((sid: string) => {
        // Abort any previous watcher loop
        watcherAbortRef.current?.abort();
        const controller = new AbortController();
        watcherAbortRef.current = controller;

        (async () => {
            try {
                for await (const event of subscribeToEvents(sid, controller.signal)) {
                    const state = useChatStore.getState();

                    // Session changed — bail out
                    if (state.session?.id !== sid) break;

                    if (event.type === "message_start" && !state.isStreaming) {
                        // Remote client started a task on this session.
                        // Step 1: reload history to get the user message they sent
                        try {
                            await state.switchSession(state.session!);
                        } catch { /* non-fatal */ }

                        // Step 2: attach to the running stream
                        // switchSession already calls _attachToRunningSession if running,
                        // but call directly in case of a race
                        const fresh = useChatStore.getState();
                        if (fresh.session?.id === sid && !fresh.isStreaming) {
                            fresh._attachToRunningSession(sid);
                        }

                        // Watcher superseded by _attachToRunningSession — stop
                        controller.abort();
                        break;
                    }
                }
            } catch {
                // AbortError or network disconnect — expected on cleanup
            }
        })();
    }, []);

    // Start watcher when session changes
    useEffect(() => {
        if (!sessionId) return;
        // Only watch when idle (not our own stream)
        if (!isStreaming) {
            startWatcher(sessionId);
        }
        return () => {
            watcherAbortRef.current?.abort();
        };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sessionId]);

    // Restart watcher after our own streaming ends
    useEffect(() => {
        if (!isStreaming && sessionId) {
            // Small delay to let the done event settle before re-subscribing
            const t = setTimeout(() => startWatcher(sessionId), 600);
            return () => clearTimeout(t);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isStreaming]);
}
