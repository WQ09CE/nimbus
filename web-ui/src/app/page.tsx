"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { useChatStore } from "@/stores";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatList } from "@/components/chat/ChatList";
import { ModelSelector } from "@/components/chat/ModelSelector";
import { FileExplorer } from "@/components/chat/FileExplorer";

import { SessionPanel } from "@/components/session/SessionPanel";

export default function Home() {
  const {
    session,
    messages,
    isStreaming,
    streamingContent,
    streamingToolCalls,
    thinkingIteration,
    currentActivity,
    error,
    isInterrupting,
    isLoading,
    createNewSession,
    loadSession,
    sendMessage,
    interruptMessage,
    clearError,
  } = useChatStore();

  const [mounted, setMounted] = useState(false);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true);
  const [showSessionPanel, setShowSessionPanel] = useState(false);
  const [showFilePanel, setShowFilePanel] = useState(false);

  // Simple ref for scroll container
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollRAF = useRef<number | null>(null);

  // Simple scroll to bottom using scrollTop (more stable than scrollIntoView)
  const scrollToBottom = useCallback(() => {
    // Cancel any pending scroll
    if (scrollRAF.current) {
      cancelAnimationFrame(scrollRAF.current);
    }

    // Use RAF to batch scroll updates and avoid layout thrashing
    scrollRAF.current = requestAnimationFrame(() => {
      const container = messagesContainerRef.current;
      if (container) {
        container.scrollTop = container.scrollHeight;
      }
    });
  }, []);

  // Handle user scroll
  const handleScroll = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const { scrollTop, scrollHeight, clientHeight } = container;
    const isAtBottom = scrollHeight - scrollTop - clientHeight <= 100;

    if (isAtBottom) {
      setUserScrolledUp(false);
      setAutoScrollEnabled(true);
    } else {
      setUserScrolledUp(true);
      setAutoScrollEnabled(false);
    }
  }, []);

  // Auto-scroll when messages change (throttled)
  useEffect(() => {
    if (autoScrollEnabled) {
      scrollToBottom();
    }
  }, [messages.length, autoScrollEnabled, scrollToBottom]);

  // Auto-scroll during streaming (debounced to reduce jitter)
  useEffect(() => {
    if (isStreaming && autoScrollEnabled) {
      const timeoutId = setTimeout(() => {
        scrollToBottom();
      }, 150); // Debounce to reduce scroll frequency
      return () => clearTimeout(timeoutId);
    }
  }, [streamingContent, isStreaming, autoScrollEnabled, scrollToBottom]);

  // Initialize session on mount
  useEffect(() => {
    setMounted(true);

    // Try to restore session from localStorage
    const savedSessionId = localStorage.getItem("nimbus_session_id");
    if (savedSessionId && !session) {
      console.log("[Page] Restoring session:", savedSessionId);
      loadSession(savedSessionId);
    } else if (!session) {
      createNewSession();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!mounted) {
    return (
      <div className="h-screen bg-black flex items-center justify-center">
        <div className="text-gray-500 font-mono">Loading...</div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col font-sans overflow-hidden relative">
      {/* Ambient Glow Effects */}
      <div className="fixed inset-0 pointer-events-none z-0">
        <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-blue-500/10 rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-purple-500/10 rounded-full blur-[120px]" />
      </div>

      {/* Header - Glassmorphism */}
      <header className="flex-shrink-0 z-20 px-6 py-4 bg-gray-950/40 backdrop-blur-xl border-b border-white/5 supports-[backdrop-filter]:bg-gray-950/20">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-blue-600 to-cyan-500 flex items-center justify-center shadow-lg shadow-blue-500/20">
              <span className="text-xl">☁️</span>
            </div>
            <div>
              <h1 className="text-lg font-bold bg-gradient-to-r from-blue-100 to-blue-300 bg-clip-text text-transparent">Nimbus</h1>
              <div className="flex items-center gap-2">
                <span className="text-[10px] uppercase tracking-wider text-blue-400 font-semibold px-1.5 py-0.5 rounded bg-blue-500/10 border border-blue-500/20">Beta</span>
                {session && (
                  <button
                    onClick={() => setShowSessionPanel(true)}
                    className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 transition-colors group"
                  >
                    <span className="truncate max-w-[150px]">
                      {session.name || session.id.slice(0, 8)}
                    </span>
                    <span className="opacity-0 group-hover:opacity-100 transition-opacity">▼</span>
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* Model Selector */}
            {session && (
              <ModelSelector
                session={session}
                onChange={() => loadSession(session.id)}
              />
            )}

            <button
              onClick={() => setShowFilePanel(!showFilePanel)}
              className={`p-2 rounded-lg transition-all ${showFilePanel ? 'text-blue-400 bg-blue-500/10' : 'text-gray-400 hover:text-white hover:bg-white/5'}`}
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
              onClick={() => createNewSession(true)}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg shadow-lg shadow-blue-600/20 transition-all hover:scale-[1.02] active:scale-[0.98]"
            >
              <span>New Chat</span>
              <span className="text-blue-200 text-xs bg-blue-700/50 px-1.5 rounded">⌘K</span>
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
            ref={messagesContainerRef}
            className="flex-1 overflow-y-auto px-6 py-4 scroll-smooth custom-scrollbar"
            onScroll={handleScroll}
          >
            {/* Error */}
            {error && (
              <div className="mb-4 p-3 bg-red-900/20 border border-red-800 rounded text-red-400 text-sm animate-in fade-in slide-in-from-top-2">
                <span className="font-semibold">Error:</span> {error}
                <button
                  onClick={clearError}
                  className="ml-3 underline hover:no-underline"
                >
                  Dismiss
                </button>
              </div>
            )}

            {/* Loading messages */}
            {isLoading && (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <div className="text-4xl mb-4 animate-pulse">☁️</div>
                <p className="text-gray-500 text-sm">Loading session...</p>
              </div>
            )}

            {/* Welcome Screen - Only show when no messages and not loading */}
            {messages.length === 0 && !isStreaming && !isLoading && (
              <div className="flex flex-col items-center justify-center h-full text-center animate-in zoom-in-95 duration-500">
                <div className="w-16 h-16 bg-gradient-to-tr from-blue-600 to-cyan-500 rounded-2xl flex items-center justify-center shadow-2xl shadow-blue-500/20 mb-6">
                  <span className="text-3xl">☁️</span>
                </div>
                <h2 className="text-2xl font-bold bg-gradient-to-br from-white to-gray-400 bg-clip-text text-transparent mb-3">
                  Nimbus Agent
                </h2>
                <div className="grid grid-cols-2 gap-3 max-w-lg mt-8">
                  <div className="p-4 bg-white/5 border border-white/5 rounded-xl hover:bg-white/10 transition-colors cursor-default text-left">
                    <div className="text-blue-400 mb-2">📄</div>
                    <h3 className="text-sm font-medium text-gray-200 mb-1">File Operations</h3>
                    <p className="text-xs text-gray-500">Read, write, edit files workspace</p>
                  </div>
                  <div className="p-4 bg-white/5 border border-white/5 rounded-xl hover:bg-white/10 transition-colors cursor-default text-left">
                    <div className="text-purple-400 mb-2">⚡</div>
                    <h3 className="text-sm font-medium text-gray-200 mb-1">Code Execution</h3>
                    <p className="text-xs text-gray-500">Run scripts and commands safely</p>
                  </div>
                  <div className="p-4 bg-white/5 border border-white/5 rounded-xl hover:bg-white/10 transition-colors cursor-default text-left">
                    <div className="text-emerald-400 mb-2">🔍</div>
                    <h3 className="text-sm font-medium text-gray-200 mb-1">Search</h3>
                    <p className="text-xs text-gray-500">Web search and knowledge retrieval</p>
                  </div>
                  <div className="p-4 bg-white/5 border border-white/5 rounded-xl hover:bg-white/10 transition-colors cursor-default text-left">
                    <div className="text-amber-400 mb-2">🧠</div>
                    <h3 className="text-sm font-medium text-gray-200 mb-1">Reasoning</h3>
                    <p className="text-xs text-gray-500">Complex task planning and execution</p>
                  </div>
                </div>
              </div>
            )}

            {/* Message list */}
            <ChatList
              messages={messages}
              isStreaming={isStreaming}
              streamingContent={streamingContent}
              streamingToolCalls={streamingToolCalls}
            />

            <div ref={messagesEndRef} className="h-4" /> {/* Spacer at bottom */}
          </div>

          {/* Working Indicator */}
          {isStreaming && currentActivity && (() => {
            const isExecutor = currentActivity.startsWith('⚡');
            const borderColor = isExecutor ? 'border-l-purple-500' : 'border-l-blue-500';
            const dotColor = isExecutor ? 'bg-purple-400' : 'bg-blue-400';
            const dotBg = isExecutor ? 'bg-purple-500' : 'bg-blue-500';
            const textColor = isExecutor ? 'text-purple-300' : 'text-blue-300';

            return (
              <div className="w-full px-6 pb-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className="max-w-4xl mx-auto">
                  <div className={`flex items-center gap-3 text-gray-400 text-xs py-2 px-3 bg-gray-900/80 rounded border border-gray-800/50 backdrop-blur-md shadow-lg border-l-4 ${borderColor} transition-all duration-300`}>
                    <span className="relative flex h-2 w-2">
                      <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${dotColor} opacity-75`}></span>
                      <span className={`relative inline-flex rounded-full h-2 w-2 ${dotBg}`}></span>
                    </span>
                    <span className={`font-mono ${textColor} font-medium tracking-wide`}>{currentActivity.toUpperCase()}</span>
                    {thinkingIteration !== null && thinkingIteration > 0 && (
                      <span className="text-gray-500 font-mono ml-auto">
                        ITERATION {thinkingIteration + 1}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })()}

          {/* Input Area */}
          <div className="flex-shrink-0 p-6 pt-0 bg-transparent">
            <ChatInput
              onSend={sendMessage}
              onInterrupt={interruptMessage}
              disabled={false}
              isStreaming={isStreaming}
              isInterrupting={isInterrupting}
              placeholder={isStreaming ? "Wait for response..." : "Type a message..."}
            />
          </div>

          {/* Scroll to bottom button */}
          {userScrolledUp && (isStreaming || messages.length > 3) && (
            <div className="absolute bottom-24 right-6 z-10">
              <button
                onClick={() => {
                  setAutoScrollEnabled(true);
                  setUserScrolledUp(false);
                  scrollToBottom();
                }}
                className="bg-blue-600 hover:bg-blue-700 text-white text-xs px-3 py-2 rounded-full shadow-lg transition-all duration-200 flex items-center gap-2 border border-blue-500/50 hover:scale-105 active:scale-95"
              >
                <span>⬇</span>
                <span className="hidden sm:inline">To Bottom</span>
                {isStreaming && (
                  <div className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse"></div>
                )}
              </button>
            </div>
          )}
        </main>

        {/* File Explorer Sidebar */}
        <div
          className={`
            bg-[#1e1e1e] border-l border-[#333] transition-all duration-300 ease-in-out flex flex-col 
            ${showFilePanel ? 'w-80 opacity-100 translate-x-0' : 'w-0 opacity-0 overflow-hidden border-l-0 translate-x-full'}
          `}
        >
          {session && (
            <div className="h-full w-80"> {/* Fixed width container to prevent layout shift during transition */}
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
