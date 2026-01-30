"use client";

import { useState } from "react";
import type { Message } from "@/stores/chat-store";

interface ChatMessageProps {
  message: Message;
  isStreaming?: boolean;
}

export function ChatMessage({ message, isStreaming }: ChatMessageProps) {
  const [showTools, setShowTools] = useState(false);

  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-3xl ${isUser ? "w-auto" : "w-full"}`}>
        {/* Role label */}
        <div className="text-xs text-gray-600 mb-1 font-semibold">
          {isUser ? "You" : "Assistant"}
          {isStreaming && (
            <span className="ml-2 text-blue-500 animate-pulse">●</span>
          )}
        </div>

        {/* Message content */}
        <div
          className={`${
            isUser
              ? "bg-blue-600/20 border-blue-500/30"
              : "bg-gray-900/50 border-gray-800"
          } border rounded-lg px-4 py-3`}
        >
          <div className="text-sm whitespace-pre-wrap break-words">
            {message.content || (isStreaming && !message.content && (
              <span className="text-gray-600">Thinking...</span>
            ))}
            {isStreaming && message.content && (
              <span className="inline-block w-2 h-4 ml-1 bg-blue-500 animate-pulse" />
            )}
          </div>

          {/* Tool calls */}
          {message.toolCalls && message.toolCalls.length > 0 && (
            <div className="mt-3 pt-3 border-t border-gray-800">
              <button
                onClick={() => setShowTools(!showTools)}
                className="text-xs text-gray-500 hover:text-gray-300 transition-colors flex items-center gap-1"
              >
                <span>{showTools ? "▼" : "▶"}</span>
                <span>
                  {message.toolCalls.length} tool call{message.toolCalls.length > 1 ? "s" : ""}
                </span>
              </button>

              {showTools && (
                <div className="mt-2 space-y-2">
                  {message.toolCalls.map((call, i) => (
                    <div
                      key={i}
                      className="bg-black/50 border border-gray-800 rounded p-2"
                    >
                      <div className="text-xs text-yellow-500 font-semibold mb-1">
                        🔧 {call.name}
                      </div>
                      <pre className="text-xs text-gray-400 overflow-x-auto">
                        {JSON.stringify(call.arguments, null, 2)}
                      </pre>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Tool results */}
          {message.toolResults && message.toolResults.length > 0 && showTools && (
            <div className="mt-2 space-y-2">
              {message.toolResults.map((result, i) => (
                <div
                  key={i}
                  className="bg-black/50 border border-gray-800 rounded p-2"
                >
                  <div className="text-xs text-green-500 font-semibold mb-1">
                    ✓ {result.name} result
                  </div>
                  <pre className="text-xs text-gray-400 overflow-x-auto max-h-40 overflow-y-auto">
                    {typeof result.result === "string"
                      ? result.result
                      : JSON.stringify(result.result, null, 2)}
                  </pre>
                  {result.error && (
                    <div className="text-xs text-red-400 mt-1">
                      Error: {result.error}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Timestamp */}
        <div className="text-xs text-gray-700 mt-1">
          {new Date(message.timestamp).toLocaleTimeString()}
        </div>
      </div>
    </div>
  );
}
