"use client";

/**
 * useSessionWatcher
 *
 * Maintains a background SSE connection to the current session's /events endpoint.
 * When a remote client starts a new task (emits `message_start`), this hook:
 *   1. Reloads session messages to pick up the user message sent by the remote client
 *   2. Attaches to the live SSE stream via _attachToRunningSession
 *
 * Includes:
 * - Automatic reconnect with exponential backoff (NAT/proxy timeout recovery)
 * - Page visibility change detection (mobile app-switch / tab-switch recovery)
 */

import { useEffect, useRef, useCallback } from "react";
import { useChatStore } from "@/stores/chat-store";
import { subscribeToEvents, getSessionMessages, getSessionStatus } from "@/lib/api";

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
                        try {
                            const serverMessages = await getSessionMessages(sid);
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
                        controller.abort();
                        return;
                    }
                }

                // Stream ended cleanly — reconnect immediately.
                scheduleReconnect(sid, 0);

            } catch (err: any) {
                if (err?.name === "AbortError") return; // intentional abort — don't reconnect

                // Network error / NAT timeout / proxy drop — reconnect with backoff
                scheduleReconnect(sid, attempt);
            }
        })();
    }, [clearReconnectTimer]); // eslint-disable-line react-hooks/exhaustive-deps

    const scheduleReconnect = useCallback((sid: string, attempt: number) => {
        const state = useChatStore.getState();
        if (state.session?.id !== sid || state.isStreaming) return;

        const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
        reconnectTimerRef.current = setTimeout(() => {
            const fresh = useChatStore.getState();
            if (fresh.session?.id === sid && !fresh.isStreaming) {
                startWatcher(sid, attempt + 1);
            }
        }, delay);
    }, [startWatcher]); // eslint-disable-line react-hooks/exhaustive-deps

    // ─── Visibility change recovery (mobile app-switch / tab-switch) ───
    // When the user switches away from the browser on mobile, iOS/Android freeze
    // JS execution and kill TCP connections. The SSE stream silently dies.
    // When they return, we need to:
    //   1. If we were streaming: check backend status and re-attach or reload
    //   2. If we weren't streaming: restart the watcher (it probably died too)
    useEffect(() => {
        const handleVisibilityChange = async () => {
            if (document.visibilityState !== "visible") return;

            const state = useChatStore.getState();
            const sid = state.session?.id;
            if (!sid) return;

            console.info("[SessionWatcher] Page became visible, checking session status...");

            try {
                const status = await getSessionStatus(sid);

                if (status.running) {
                    if (state.isStreaming) {
                        // We think we're streaming but the connection is probably dead.
                        // Abort the dead stream and re-attach.
                        console.info("[SessionWatcher] Session still running, re-attaching to stream...");
                        state.streamAbortController?.abort();
                        // Small delay to let abort handlers clean up
                        await new Promise(r => setTimeout(r, 200));
                        const fresh = useChatStore.getState();
                        if (fresh.session?.id === sid) {
                            // Force isStreaming false so _attachToRunningSession can start cleanly
                            if (fresh.isStreaming) {
                                // Finalize the stale streaming message
                                const finalMsgs = [...fresh.messages];
                                const streamIdx = finalMsgs.findIndex(m => m.id === "streaming-assistant");
                                if (streamIdx !== -1) {
                                    finalMsgs[streamIdx] = { ...finalMsgs[streamIdx], id: `assistant-stale-${Date.now()}` };
                                }
                                useChatStore.setState({
                                    messages: finalMsgs,
                                    isStreaming: false,
                                    streamAbortController: null,
                                });
                            }
                            // Reload session to get any events we missed, then re-attach
                            await useChatStore.getState().switchSession(state.session!);
                        }
                    } else {
                        // Not streaming but agent is running — we missed the start.
                        // switchSession will detect running status and call _attachToRunningSession.
                        console.info("[SessionWatcher] Agent running but not streaming, attaching...");
                        await useChatStore.getState().switchSession(state.session!);
                    }
                } else {
                    // Agent not running
                    if (state.isStreaming) {
                        // We think we're streaming but agent is done — reload to get final results.
                        console.info("[SessionWatcher] Agent finished while away, reloading results...");
                        state.streamAbortController?.abort();
                        await new Promise(r => setTimeout(r, 200));
                        const fresh = useChatStore.getState();
                        if (fresh.session?.id === sid) {
                            if (fresh.isStreaming) {
                                useChatStore.setState({ isStreaming: false, streamAbortController: null });
                            }
                            await useChatStore.getState().switchSession(state.session!);
                        }
                    } else {
                        // Normal idle state — just restart the watcher (it probably died)
                        startWatcher(sid, 0);
                    }
                }
            } catch (err) {
                console.warn("[SessionWatcher] Visibility recovery failed, restarting watcher:", err);
                // Fallback: just restart the watcher
                if (!useChatStore.getState().isStreaming) {
                    startWatcher(sid, 0);
                }
            }
        };

        document.addEventListener("visibilitychange", handleVisibilityChange);
        return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

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
            const t = setTimeout(() => startWatcher(sessionId, 0), 600);
            return () => clearTimeout(t);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isStreaming]);
}
