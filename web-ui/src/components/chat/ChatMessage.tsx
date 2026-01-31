"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import type { Message } from "@/stores/chat-store";
import type { ToolResult } from "@/lib/api";
import { MarkdownRenderer, DataDisplay } from "./MarkdownRenderer";
import { useAutoScroll } from "@/hooks/useAutoScroll";

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
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({});
  const [showAllTools, setShowAllTools] = useState(true);
  const previousContentLength = useRef(0);
  const previousToolsLength = useRef(0);

  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  // Auto-scroll hook for this message
  const { elementRef: messageRef, scrollToBottom: scrollToMessage } = useAutoScroll({
    enabled: isStreaming && !isUser,
    smooth: true,
    throttleMs: 200, // Less aggressive than page-level scrolling
  });

  // Scroll when content grows during streaming
  useEffect(() => {
    if (isStreaming && !isUser && message.content) {
      const currentLength = message.content.length;
      if (currentLength > previousContentLength.current + 50) { // Only scroll on significant content changes
        previousContentLength.current = currentLength;
        scrollToMessage();
      }
    }
  }, [message.content, isStreaming, isUser, scrollToMessage]);

  // Scroll when new tools are added during streaming
  useEffect(() => {
    if (isStreaming && !isUser && message.toolCalls) {
      const currentToolsLength = message.toolCalls.length;
      if (currentToolsLength > previousToolsLength.current) {
        previousToolsLength.current = currentToolsLength;
        setTimeout(() => scrollToMessage(), 150); // Slight delay for UI to update
      }
    }
  }, [message.toolCalls, isStreaming, isUser, scrollToMessage]);

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

  const toggleTool = (id: string | number) => {
    setExpandedTools(prev => ({ ...prev, [id]: !prev[id] }));
  };

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
          {/* Tools Section - 现在放在最上面 */}
          {tools.length > 0 && (
            <div className="bg-black/20 border-b border-gray-800/50">
              <div
                className="px-4 py-2 flex items-center justify-between cursor-pointer hover:bg-white/5 transition-colors"
                onClick={() => setShowAllTools(!showAllTools)}
              >
                <div className="flex items-center gap-2 text-xs font-mono text-gray-500">
                  <span>{showAllTools ? "▼" : "▶"}</span>
                  <span>TOOL CALLS ({tools.length})</span>
                </div>
                {isStreaming && (
                  <span className="text-[10px] bg-blue-900/30 text-blue-400 px-2 py-0.5 rounded border border-blue-800/50 animate-pulse">
                    EXECUTING
                  </span>
                )}
              </div>

              {showAllTools && (
                <div className="divide-y divide-gray-800/50 border-t border-gray-800/50">
                  {tools.map((tool, i) => {
                    const toolId = tool.id || i;
                    const isExpanded = expandedTools[toolId] ?? true; // Default expanded

                    return (
                      <div key={toolId} className="px-4 py-3 hover:bg-white/[0.02]">
                        {/* Tool Header */}
                        <div
                          className="flex items-center justify-between cursor-pointer group/tool"
                          onClick={() => toggleTool(toolId)}
                        >
                          <div className="flex items-center gap-3">
                            <span className={`text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded ${
                              tool.status === "running" ? "bg-yellow-900/30 text-yellow-500 border border-yellow-800/50" :
                              tool.status === "completed" ? "bg-green-900/30 text-green-500 border border-green-800/50" :
                              "bg-red-900/30 text-red-500 border border-red-800/50"
                            }`}>
                              {tool.status === "running" ? "RUN" :
                               tool.status === "completed" ? "OK" : "ERR"}
                            </span>
                            <span className="text-sm font-mono text-purple-300 font-semibold">
                              {tool.name}
                            </span>
                            {tool.duration && (
                              <span className="text-[10px] text-gray-500 font-mono">
                                {tool.duration}ms
                              </span>
                            )}
                          </div>
                          <span className="text-gray-600 text-xs opacity-0 group-hover/tool:opacity-100 transition-opacity">
                            {isExpanded ? "Collapse" : "Expand"}
                          </span>
                        </div>

                        {/* Tool Details */}
                        {isExpanded && (
                          <div className="mt-3 ml-1 pl-3 border-l-2 border-gray-800 space-y-3">
                            {/* Args */}
                            <DataDisplay
                              data={tool.args}
                              title="Input"
                              className="bg-black/40 p-3 rounded border border-gray-800"
                            />

                            {/* Result */}
                            {tool.status !== "running" && (
                              <div className={`p-3 rounded border ${
                                tool.status === "completed"
                                  ? "bg-black/40 border-gray-800"
                                  : "bg-red-900/10 border-red-900/50"
                              }`}>
                                <DataDisplay
                                  data={tool.error || tool.result}
                                  title={tool.status === "completed" ? "Output" : "Error"}
                                />
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Final Message Content - 现在放在最下面，增强 markdown 显示 */}
          {(message.content || (isStreaming && !tools.length)) && (
            <div className="px-5 py-4 bg-gradient-to-b from-transparent to-black/5">
              {isUser ? (
                // 用户消息保持简洁显示
                <div className="text-sm leading-relaxed whitespace-pre-wrap text-gray-200 font-sans">
                  {message.content}
                </div>
              ) : (
                // 助手消息使用增强的显示
                <div className="text-sm leading-relaxed">
                  {message.content ? (
                    <div>
                      {/* 如果有工具调用，添加结论标题 */}
                      {tools.length > 0 && (
                        <div className="flex items-center gap-2 mb-4 pb-2 border-b border-gray-800/50">
                          <span className="text-green-400">💡</span>
                          <span className="text-xs uppercase text-gray-500 font-bold tracking-wider">
                            Final Response
                          </span>
                        </div>
                      )}

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
        </div>
      </div>
    </div>
  );
}
