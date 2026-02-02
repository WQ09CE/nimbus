"use client";

import { useEffect, useState } from "react";
import { useChatStore } from "@/stores";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatList } from "@/components/chat/ChatList";
import { DebugPanel } from "@/components/debug/DebugPanel";
import { SessionPanel } from "@/components/session/SessionPanel";
import { useAutoScroll, useScrollDetection } from "@/hooks/useAutoScroll";

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

  // Auto-scroll hook
  const { elementRef: messagesEndRef, scrollToBottom } = useAutoScroll({
    enabled: autoScrollEnabled,
    throttleMs: 100, // Smooth throttling during streaming
  });

  // Scroll detection hook
  const { containerRef: messagesContainerRef, handleScroll, scrollToBottom: scrollContainerToBottom } = useScrollDetection({
    threshold: 50,
    onScrollUp: () => {
      setUserScrolledUp(true);
      setAutoScrollEnabled(false);
    },
    onReachBottom: () => {
      setUserScrolledUp(false);
      setAutoScrollEnabled(true);
    },
  });

  // Auto-scroll when new messages arrive or streaming content updates
  useEffect(() => {
    if (autoScrollEnabled) {
      scrollToBottom();
    }
  }, [messages, streamingContent, streamingToolCalls, currentActivity, autoScrollEnabled, scrollToBottom]);

  // Enhanced scroll during streaming with intelligent detection
  useEffect(() => {
    if (isStreaming && autoScrollEnabled) {
      // More frequent updates during streaming for smoother experience
      const timeoutId = setTimeout(() => {
        scrollToBottom();
      }, 50);

      return () => clearTimeout(timeoutId);
    }
  }, [streamingContent, streamingToolCalls, isStreaming, autoScrollEnabled, scrollToBottom]);

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
    <div className="h-screen bg-black text-gray-100 flex flex-col font-mono">
      {/* Header */}
      <header className="flex-shrink-0 border-b border-gray-800 px-6 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xl">☁️</span>
            <h1 className="text-lg font-semibold text-blue-400">Nimbus</h1>
            {session && (
              <button
                onClick={() => setShowSessionPanel(true)}
                className="flex items-center gap-2 text-xs bg-gray-800 hover:bg-gray-700 px-2 py-1 rounded transition-colors"
              >
                <span className="text-gray-400">📁</span>
                <span className="text-gray-300">
                  {session.name || session.id.slice(0, 8)}
                </span>
                {session.workspace_path && (
                  <span className="text-gray-500 max-w-[200px] truncate">
                    ({session.workspace_path.split('/').slice(-2).join('/')})
                  </span>
                )}
                <span className="text-gray-600">▼</span>
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowSessionPanel(true)}
              className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
            >
              Sessions
            </button>
            <button
              onClick={() => createNewSession(true)}
              className="text-xs bg-blue-600 hover:bg-blue-700 px-2 py-1 rounded text-white transition-colors"
            >
              + New
            </button>
          </div>
        </div>
      </header>

      {/* Messages */}
      <div
        ref={messagesContainerRef}
        className="flex-1 overflow-y-auto px-6 py-4"
        onScroll={handleScroll}
      >
        {/* Error */}
        {error && (
          <div className="mb-4 p-3 bg-red-900/20 border border-red-800 rounded text-red-400 text-sm">
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
            <p className="text-gray-500 text-sm">加载中...</p>
          </div>
        )}

        {/* Welcome */}
        {messages.length === 0 && !isStreaming && !isLoading && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="text-4xl mb-4">☁️</div>
            <h2 className="text-xl font-semibold text-blue-400 mb-2">
              Nimbus Agent
            </h2>
            <p className="text-gray-500 text-sm max-w-md">
              Ask me anything. I can read files, search the web, and execute code.
            </p>
          </div>
        )}

        {/* Message list */}
        <ChatList 
            messages={messages}
            isStreaming={isStreaming}
            streamingContent={streamingContent}
            streamingToolCalls={streamingToolCalls}
        />

        <div ref={messagesEndRef} />

        {/* Scroll to bottom button - only show when user scrolled up and there's activity */}
        {userScrolledUp && (isStreaming || messages.length > 3) && (
          <div className="fixed bottom-24 right-6 z-10">
            <button
              onClick={() => {
                setAutoScrollEnabled(true);
                setUserScrolledUp(false);
                scrollContainerToBottom();
                setTimeout(() => scrollToBottom(true), 100);
              }}
              className="bg-blue-600 hover:bg-blue-700 text-white text-xs px-3 py-2 rounded-full shadow-lg transition-all duration-200 flex items-center gap-2 border border-blue-500/50"
            >
              <span>⬇</span>
              <span>滚动到底部</span>
              {isStreaming && (
                <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse"></div>
              )}
            </button>
          </div>
        )}
      </div>

      {/* Working Indicator */}
      {isStreaming && currentActivity && (
        <div className="w-full px-6 pb-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
           <div className="max-w-4xl mx-auto">
             <div className="flex items-center gap-3 text-gray-400 text-xs py-2 px-3 bg-gray-900/80 rounded border border-gray-800/50 backdrop-blur-md shadow-lg border-l-4 border-l-blue-500">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500"></span>
                  </span>
                  <span className="font-mono text-blue-300 font-medium tracking-wide">{currentActivity.toUpperCase()}</span>
                  {thinkingIteration !== null && thinkingIteration > 0 && (
                    <span className="text-gray-500 font-mono ml-auto">
                      ITERATION {thinkingIteration + 1}
                    </span>
                  )}
             </div>
           </div>
        </div>
      )}

      {/* Input */}
      <ChatInput
        onSend={sendMessage}
        onInterrupt={interruptMessage}
        disabled={false} // Allow typing/queueing during streaming
        isStreaming={isStreaming}
        isInterrupting={isInterrupting}
        placeholder={isStreaming ? "输入消息以排队..." : "输入您的消息..."}
      />

      {/* Debug Panel */}
      <DebugPanel />

      {/* Session Panel */}
      <SessionPanel 
        isOpen={showSessionPanel} 
        onClose={() => setShowSessionPanel(false)} 
      />
    </div>
  );
}
