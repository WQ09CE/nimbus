"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import type { Message } from "@/stores/chat-store";
import type { ToolResult } from "@/lib/api";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { ToolCard } from "./tools/ToolCard";

interface ChatMessageProps {
  message: Message;
  isStreaming?: boolean;
}

interface MergedTool {
  id?: string;
  name: string;
  args: Record<string, unknown>;
  result?: unknown;
  error?: string;
  status: "running" | "completed" | "failed";
  duration?: number;
}

export function ChatMessage({ message, isStreaming }: ChatMessageProps) {
  // const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({}); // Moved to ToolCard
  const [showAllTools, setShowAllTools] = useState(true); // Default expanded
  const previousContentLength = useRef(0);
  const previousToolsLength = useRef(0);

  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  // Ref for the message element (used for potential scroll, but we avoid aggressive scrolling)
  const messageRef = useRef<HTMLDivElement>(null);

  // Reset tracking refs when message changes
  useEffect(() => {
    previousContentLength.current = message.content?.length || 0;
    previousToolsLength.current = message.toolCalls?.length || 0;
  }, [message.id]);

  // NOTE: We intentionally do NOT auto-scroll from ChatMessage component.
  // Auto-scroll should be handled at the container/page level to avoid
  // conflicting scroll behaviors that cause "jumping" effects.
  // The parent component (page.tsx or similar) should handle scroll-to-bottom.

  // Merge tool calls and results
  const tools = useMemo(() => {
    const merged: MergedTool[] = [];
    const calls = message.toolCalls || [];
    const results = message.toolResults || [];

    // Map results by ID
    const resultMap = new Map<string, ToolResult>();
    results.forEach((r) => {
      if (r.id) resultMap.set(r.id, r);
    });

    // Also map by name/index as fallback for missing IDs
    // (Simple fallback strategy: assume order matches if no IDs)

    calls.forEach((call, index) => {
      const result = call.id ? resultMap.get(call.id) : results[index];

      merged.push({
        id: call.id,
        name: call.name,
        args: call.arguments,
        result: result?.result,
        error: result?.error,
        status: result ? (result.error ? "failed" : "completed") : "running",
        duration: result?.duration,
      });
    });

    return merged;
  }, [message.toolCalls, message.toolResults]);

  // Special handling for system messages
  if (isSystem) {
    return (
      <div className="flex justify-center my-2">
        <div className="text-xs text-gray-500 bg-gray-900/30 border border-gray-800/50 rounded-full px-3 py-1 font-mono">
          <span className="text-gray-400">💭</span>
          <span className="ml-1">{message.content}</span>
        </div>
      </div>
    );
  }

  return (
    <div ref={messageRef} className={`flex ${isUser ? "justify-end" : "justify-start"} group`}>
      <div className={`max-w-4xl ${isUser ? "w-auto" : "w-full"}`}>
        {/* Role label */}
        <div className="text-xs text-gray-500 mb-1 font-mono flex items-center gap-2">
          <span className={isUser ? "text-blue-400" : "text-green-400"}>
            {isUser ? "USER" : "ASSISTANT"}
          </span>
          <span className="text-gray-700">
            {new Date(message.timestamp).toLocaleTimeString()}
          </span>
          {isStreaming && (
            <span className="flex h-2 w-2 relative">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500"></span>
            </span>
          )}
        </div>

        {/* Message content */}
        <div
          className={`${
            isUser
              ? "bg-blue-900/20 border-blue-800/50"
              : "bg-gray-900/40 border-gray-800"
          } border rounded-lg overflow-hidden backdrop-blur-sm transition-all duration-200`}
        >
          {/* 1. Content Section (Thinking / Final Answer) */}
          {(message.content || (isStreaming && !tools.length)) && (
            <div className={`px-5 py-4 ${isUser ? "" : "bg-gradient-to-b from-transparent to-black/5"}`}>
              {isUser ? (
                // 用户消息保持简洁显示
                <div className="text-sm leading-relaxed whitespace-pre-wrap text-gray-200 font-sans">
                  {message.content}
                </div>
              ) : (
                // 助手消息使用 Markdown 渲染
                <div className="text-sm leading-relaxed">
                  {message.content ? (
                    <div>
                      <MarkdownRenderer
                        content={message.content}
                        className="text-gray-200"
                      />
                      {/* 流式输入光标 */}
                      {isStreaming && (
                        <span className="inline-block w-1.5 h-5 ml-1 bg-gradient-to-r from-blue-500 to-cyan-400 animate-pulse align-middle rounded-sm" />
                      )}
                    </div>
                  ) : isStreaming && !message.content ? (
                    <div className="flex items-center gap-3 py-2">
                      <div className="flex space-x-1">
                        <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce"></div>
                        <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }}></div>
                        <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
                      </div>
                      <span className="text-gray-500 italic">AI is thinking...</span>
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          )}

          {/* 2. Tools Section */}
          {tools.length > 0 && (
            <div className="bg-black/20 border-t border-gray-800/50">
              <div
                className="px-4 py-2 flex items-center justify-between cursor-pointer hover:bg-white/5 transition-colors"
                onClick={() => setShowAllTools(!showAllTools)}
              >
                <div className="flex items-center gap-2 text-xs font-mono text-gray-500">
                  <span>{showAllTools ? "▼" : "▶"}</span>
                  <span>TOOL CALLS ({tools.length})</span>
                </div>
                {isStreaming ? (
                  <span className="text-[10px] bg-blue-900/30 text-blue-400 px-2 py-0.5 rounded border border-blue-800/50 animate-pulse">
                    EXECUTING
                  </span>
                ) : (
                  <span className="text-[10px] bg-green-900/30 text-green-400 px-2 py-0.5 rounded border border-green-800/50">
                    DONE
                  </span>
                )}
              </div>

              {showAllTools && (
                <div className="divide-y divide-gray-800/50 border-t border-gray-800/50 p-2">
                  {tools.map((tool, i) => (
                    <div key={tool.id || i} className="mb-2 last:mb-0">
                      <ToolCard tool={tool} />
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
