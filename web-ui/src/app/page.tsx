"use client";

import { useEffect, useState } from "react";
import { useChatStore } from "@/stores";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ChatInput } from "@/components/chat/ChatInput";
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
    createNewSession,
    sendMessage,
    clearError,
  } = useChatStore();

  const [mounted, setMounted] = useState(false);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true);

  // Auto-scroll hook
  const { elementRef: messagesEndRef, scrollToBottom } = useAutoScroll({
    enabled: autoScrollEnabled,
    throttleMs: 100, // Smooth throttling during streaming
  });

  // Scroll detection hook
  const { containerRef: messagesContainerRef, handleScroll, isAtBottom, scrollToBottom: scrollContainerToBottom } = useScrollDetection({
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
    if (!session) {
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
              <span className="text-xs text-gray-600">
                session: {session.id.slice(0, 8)}...
              </span>
            )}
          </div>
          <button
            onClick={createNewSession}
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            New Session
          </button>
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

        {/* Welcome */}
        {messages.length === 0 && !isStreaming && (
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
        <div className="space-y-6 max-w-4xl mx-auto">
          {messages.map((msg) => (
            <ChatMessage key={msg.id} message={msg} />
          ))}

          {/* Streaming message */}
          {isStreaming && (
            <>
              {/* Real-time activity indicator (shown above message) */}
              {currentActivity && (
                <div className="flex items-center gap-3 text-gray-400 text-xs py-2 px-3 mb-2 bg-gray-900/30 rounded border border-gray-800/50">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500"></span>
                  </span>
                  <span>{currentActivity}</span>
                  {thinkingIteration !== null && thinkingIteration > 0 && (
                    <span className="text-gray-600">
                      (第 {thinkingIteration + 1} 轮)
                    </span>
                  )}
                </div>
              )}
              
              <ChatMessage
                message={{
                  id: "streaming",
                  role: "assistant",
                  content: streamingContent,
                  toolCalls: streamingToolCalls.length > 0 ? streamingToolCalls : undefined,
                  timestamp: Date.now(),
                }}
                isStreaming
              />
            </>
          )}
        </div>

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

      {/* Input */}
      <ChatInput
        onSend={sendMessage}
        disabled={isStreaming}
        placeholder="Type your message..."
      />
    </div>
  );
}
