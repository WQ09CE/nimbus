"use client";

import { useEffect, useState, useMemo } from "react";
import { useChatStore } from "@/stores";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatList } from "@/components/chat/ChatList";
import { ModelSelector } from "@/components/chat/ModelSelector";
import { FileExplorer } from "@/components/chat/FileExplorer";
import { WorkingIndicator } from "@/components/chat/WorkingIndicator";

import { SessionPanel } from "@/components/session/SessionPanel";

export default function Home() {
  // Fine-grained selectors — only subscribe to what Home actually needs
  const session = useChatStore(s => s.session);
  const messages = useChatStore(s => s.messages);
  const isStreaming = useChatStore(s => s.isStreaming);
  const error = useChatStore(s => s.error);
  const errorInfo = useChatStore(s => s.errorInfo);
  const isLoading = useChatStore(s => s.isLoading);

  // Actions — stable references from Zustand
  const createNewSession = useChatStore(s => s.createNewSession);
  const loadSession = useChatStore(s => s.loadSession);
  const sendMessage = useChatStore(s => s.sendMessage);
  const interruptMessage = useChatStore(s => s.interruptMessage);
  const clearError = useChatStore(s => s.clearError);

  const [mounted, setMounted] = useState(false);
  const [showSessionPanel, setShowSessionPanel] = useState(false);
  const [showFilePanel, setShowFilePanel] = useState(false);

  // Stable placeholder (only changes when isStreaming changes)
  const placeholder = useMemo(
    () => isStreaming ? "Wait for response..." : "Type a message...",
    [isStreaming]
  );

  // Initialize session on mount
  useEffect(() => {
    setMounted(true);
    const savedSessionId = localStorage.getItem("nimbus_session_id");
    if (savedSessionId && !session) {
      loadSession(savedSessionId);
    } else if (!session) {
      createNewSession();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-recover when page becomes visible (iOS app switch, desktop tab switch)
  // Debounced 300ms to avoid rapid Tab-switch triggering multiple reloads
  useEffect(() => {
    let visibilityTimer: ReturnType<typeof setTimeout> | null = null;

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        if (visibilityTimer) clearTimeout(visibilityTimer);
        visibilityTimer = setTimeout(() => {
          const state = useChatStore.getState();
          const currentSession = state.session;
          if (currentSession && !state.isStreaming) {
            console.log("[Page] Visibility restored, reloading session...");
            state.loadSession(currentSession.id);
          }
        }, 300);
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      if (visibilityTimer) clearTimeout(visibilityTimer);
    };
  }, []);

  // Register Cmd+K / Ctrl+K shortcut for New Chat
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        createNewSession(true);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [createNewSession]);

  if (!mounted) {
    return (
      <div className="h-screen bg-nimbus-bg flex items-center justify-center">
        <div className="text-gray-500 font-mono">Loading...</div>
      </div>
    );
  }

  return (
    <div className="h-screen h-[100dvh] flex flex-col font-sans overflow-hidden relative">
      {/* Ambient Glow Effects */}
      <div className="fixed inset-0 pointer-events-none z-0">
        <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-sky-400/[0.06] rounded-full blur-[160px]" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-violet-400/[0.04] rounded-full blur-[160px]" />
      </div>

      {/* Header */}
      <header className="flex-shrink-0 z-20 px-3 md:px-6 py-3 md:py-4 bg-nimbus-bg/80 backdrop-blur-xl border-b border-nimbus-border">
        <div className="md:max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-nimbus-surface backdrop-blur-lg border border-nimbus-border flex items-center justify-center shadow-lg shadow-sky-500/10">
              <span className="text-xl">☁️</span>
            </div>
            <div>
              <h1 className="text-lg font-bold bg-gradient-to-r from-sky-100 to-sky-300 bg-clip-text text-transparent hidden md:inline font-display">Nimbus</h1>
              <div className="flex items-center gap-2">
                <span className="text-[10px] uppercase tracking-wider text-nimbus-accent font-semibold px-1.5 py-0.5 rounded bg-nimbus-surface border border-nimbus-border hidden md:inline">Beta</span>
                {session && (
                  <button
                    data-testid="session-panel-trigger"
                    onClick={() => setShowSessionPanel(true)}
                    className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 transition-colors group"
                  >
                    <span className="truncate max-w-[120px] md:max-w-[200px]">
                      {session.name || session.id.slice(0, 8)}
                    </span>
                    <span className="opacity-0 group-hover:opacity-100 transition-opacity">▼</span>
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-1.5 md:gap-3">
            {session && (
              <ModelSelector
                session={session}
                onChange={() => loadSession(session.id)}
              />
            )}

            <button
              onClick={() => setShowFilePanel(!showFilePanel)}
              className={`p-2 rounded-lg transition-all ${showFilePanel ? 'text-nimbus-accent bg-nimbus-surface' : 'text-gray-400 hover:text-white hover:bg-white/5'}`}
              title="Files"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
              </svg>
            </button>

            <button
              onClick={() => setShowSessionPanel(true)}
              className="p-2 text-gray-400 hover:text-white hover:bg-white/5 rounded-lg transition-all"
              title="History"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </button>
            <button
              data-testid="new-chat-button"
              onClick={() => createNewSession(true)}
              className="flex items-center gap-1.5 md:gap-2 px-2.5 md:px-4 py-1.5 md:py-2 bg-sky-500/20 hover:bg-sky-500/30 border border-sky-400/30 text-sky-300 text-sm font-medium rounded-lg shadow-lg shadow-sky-400/10 transition-all hover:scale-[1.02] active:scale-[0.98]"
              title="New Chat (⌘K)"
            >
              <svg className="w-4 h-4 md:hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              <span className="hidden md:inline">New Chat</span>
              <span className="hidden md:inline text-sky-300/60 text-xs bg-sky-500/10 px-1.5 rounded">⌘K</span>
            </button>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <div className="flex-1 flex overflow-hidden min-h-0 relative">

        {/* Chat Column */}
        <main className="flex-1 flex flex-col min-w-0 relative z-0 bg-transparent">
          {/* Messages */}
          <div
            className="flex-1 flex flex-col overflow-hidden px-3 md:px-6 py-4"
          >
            {/* Error */}
            {error && (
              <div
                data-testid="error-banner"
                className={`mb-4 p-3 rounded text-sm animate-in fade-in slide-in-from-top-2 border ${
                  errorInfo?.code === "llm_rate_limit" || errorInfo?.code === "resource_timeout"
                    ? "bg-amber-900/20 border-amber-700/50 text-amber-300"
                    : errorInfo?.code === "auth_error"
                    ? "bg-orange-900/20 border-orange-700/50 text-orange-300"
                    : "bg-red-900/20 border-red-800/50 text-red-400"
                }`}
              >
                <div className="flex items-start gap-2">
                  <span className="shrink-0 mt-0.5">
                    {errorInfo?.code === "llm_rate_limit" ? "\u23F3" :
                     errorInfo?.code === "resource_timeout" ? "\u23F1\uFE0F" :
                     errorInfo?.code === "auth_error" ? "\uD83D\uDD11" :
                     errorInfo?.code === "llm_ctx_overflow" ? "\uD83D\uDCCF" : "\uD83D\uDD34"}
                  </span>
                  <div className="flex-1 min-w-0">
                    <span className="font-semibold">
                      {errorInfo?.code === "llm_rate_limit" ? "Request Throttled" :
                       errorInfo?.code === "resource_timeout" ? "Request Timeout" :
                       errorInfo?.code === "auth_error" ? "Auth Error" :
                       errorInfo?.code === "llm_ctx_overflow" ? "Context Overflow" :
                       "Error"}
                    </span>
                    <span className="ml-2 opacity-80">{errorInfo?.message ?? error}</span>
                    {errorInfo?.errorId && (
                      <span className="ml-2 text-xs opacity-40">#{errorInfo.errorId}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {errorInfo?.retryable && (
                      <button
                        onClick={() => useChatStore.getState().retryLastMessage()}
                        className="text-sky-400 hover:text-sky-300 underline text-xs"
                      >
                        Retry
                      </button>
                    )}
                    <button
                      onClick={clearError}
                      className="opacity-60 hover:opacity-100 text-xs underline"
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Loading messages */}
            {isLoading && (
              <div className="flex flex-col items-center justify-center flex-1 text-center">
                <div className="text-4xl mb-4 animate-pulse">☁️</div>
                <p className="text-gray-500 text-sm">Loading session...</p>
              </div>
            )}

            {/* Welcome Screen */}
            {messages.length === 0 && !isStreaming && !isLoading && (
              <div data-testid="welcome-screen" className="flex flex-col items-center justify-center flex-1 text-center animate-in zoom-in-95 duration-500">
                <div className="w-16 h-16 bg-nimbus-surface backdrop-blur-lg border border-nimbus-border rounded-2xl flex items-center justify-center shadow-2xl shadow-sky-500/10 mb-6">
                  <span className="text-3xl">☁️</span>
                </div>
                <h2 className="text-2xl font-bold bg-gradient-to-br from-sky-100 to-slate-400 bg-clip-text text-transparent mb-3 font-display">
                  Nimbus Agent
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-lg mt-8">
                  <div className="p-4 bg-nimbus-surface border border-nimbus-border rounded-xl hover:bg-nimbus-surface-hover backdrop-blur-sm transition-colors cursor-default text-left">
                    <div className="text-sky-400 mb-2">📄</div>
                    <h3 className="text-sm font-medium text-gray-200 mb-1">File Operations</h3>
                    <p className="text-xs text-gray-500">Read, write, edit files workspace</p>
                  </div>
                  <div className="p-4 bg-nimbus-surface border border-nimbus-border rounded-xl hover:bg-nimbus-surface-hover backdrop-blur-sm transition-colors cursor-default text-left">
                    <div className="text-violet-400/80 mb-2">⚡</div>
                    <h3 className="text-sm font-medium text-gray-200 mb-1">Code Execution</h3>
                    <p className="text-xs text-gray-500">Run scripts and commands safely</p>
                  </div>
                  <div className="p-4 bg-nimbus-surface border border-nimbus-border rounded-xl hover:bg-nimbus-surface-hover backdrop-blur-sm transition-colors cursor-default text-left">
                    <div className="text-emerald-400/80 mb-2">🔍</div>
                    <h3 className="text-sm font-medium text-gray-200 mb-1">Search</h3>
                    <p className="text-xs text-gray-500">Web search and knowledge retrieval</p>
                  </div>
                  <div className="p-4 bg-nimbus-surface border border-nimbus-border rounded-xl hover:bg-nimbus-surface-hover backdrop-blur-sm transition-colors cursor-default text-left">
                    <div className="text-amber-400/80 mb-2">🧠</div>
                    <h3 className="text-sm font-medium text-gray-200 mb-1">Reasoning</h3>
                    <p className="text-xs text-gray-500">Complex task planning and execution</p>
                  </div>
                </div>
              </div>
            )}

            {/* Message list — ChatList subscribes to streaming internally */}
            <ChatList messages={messages} />

          </div>

          {/* Working Indicator — subscribes to streaming state internally */}
          <WorkingIndicator />

          {/* Input Area */}
          <div className="flex-shrink-0 p-3 md:p-6 pt-0 bg-transparent">
            <ChatInput
              onSend={sendMessage}
              onInterrupt={interruptMessage}
              disabled={false}
              isStreaming={isStreaming}
              isInterrupting={false}
              placeholder={placeholder}
            />
          </div>

        </main>

        {/* Mobile backdrop */}
        {showFilePanel && (
          <div className="fixed inset-0 bg-black/50 z-20 md:hidden" onClick={() => setShowFilePanel(false)} />
        )}

        {/* File Explorer Sidebar */}
        <div
          className={`
            ${showFilePanel
              ? 'fixed inset-y-0 right-0 w-[85%] z-30 md:relative md:inset-auto md:w-80 opacity-100 translate-x-0'
              : 'w-0 opacity-0 overflow-hidden translate-x-full'}
            bg-nimbus-bg/95 backdrop-blur-xl border-l border-nimbus-border transition-all duration-300 ease-in-out flex flex-col
          `}
        >
          {session && (
            <div className="h-full w-full md:w-80">
              <FileExplorer sessionId={session.id} />
            </div>
          )}
        </div>

      </div>

      {/* Session Panel Overlay */}
      <SessionPanel
        isOpen={showSessionPanel}
        onClose={() => setShowSessionPanel(false)}
      />
    </div>
  );
}
