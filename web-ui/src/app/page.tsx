"use client";

import { useEffect, useRef, useState } from "react";
import { useChatStore } from "@/stores";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ChatInput } from "@/components/chat/ChatInput";

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

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [mounted, setMounted] = useState(false);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent]);

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
      <div className="flex-1 overflow-y-auto px-6 py-4">
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

          {/* Real-time activity indicator */}
          {isStreaming && !streamingContent && currentActivity && (
            <div className="flex items-center gap-3 text-gray-400 text-sm py-4 px-4 bg-gray-900/50 rounded-lg border border-gray-800">
              <span className="relative flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-3 w-3 bg-blue-500"></span>
              </span>
              <span>{currentActivity}</span>
              {thinkingIteration !== null && thinkingIteration > 0 && (
                <span className="text-xs text-gray-600">
                  (迭代 {thinkingIteration + 1})
                </span>
              )}
            </div>
          )}

          {/* Streaming message */}
          {isStreaming && streamingContent && (
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
          )}
        </div>

        <div ref={messagesEndRef} />
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
