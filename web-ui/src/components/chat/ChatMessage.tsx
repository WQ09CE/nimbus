"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import type { Message } from "@/stores/chat-store";
import type { ToolResult } from "@/lib/api";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { ToolCard } from "./tools/ToolCard";
import type { ToolCall } from "@/lib/api";

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
  subCalls?: ToolCall[];
  subResults?: ToolResult[];
}

// Helper for user avatar
function UserAvatar() {
  return (
    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center shrink-0 shadow-lg shadow-blue-500/20">
      <span className="text-xs text-white font-bold">U</span>
    </div>
  );
}

// Helper for AI avatar
function AiAvatar() {
  return (
    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center shrink-0 shadow-lg shadow-cyan-500/20">
      <span className="text-xs text-white">☁️</span>
    </div>
  );
}

export function ChatMessage({ message, isStreaming }: ChatMessageProps) {
  const [showTools, setShowTools] = useState(false);

  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  // Parse tools
  const tools = useMemo(() => {
    const merged: MergedTool[] = [];
    const calls = message.toolCalls || [];
    const results = message.toolResults || [];
    const resultMap = new Map<string, ToolResult>();
    results.forEach((r) => { if (r.id) resultMap.set(r.id, r); });

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
        subCalls: call.subCalls,
        subResults: call.subResults,
      });
    });
    return merged;
  }, [message.toolCalls, message.toolResults]);

  if (isSystem) {
    return (
      <div className="flex justify-center my-6">
        <div className="px-4 py-1.5 rounded-full bg-gray-900/50 border border-gray-800 text-xs text-gray-500 font-medium flex items-center gap-2 backdrop-blur-sm">
          <span>⚡</span>
          <span>{message.content}</span>
        </div>
      </div>
    );
  }

  return (
    <div className={`flex gap-4 mb-6 ${isUser ? "flex-row-reverse" : "flex-row"} group`}>
      {/* Avatar */}
      <div className="mt-1">
        {isUser ? <UserAvatar /> : <AiAvatar />}
      </div>

      {/* Content Container */}
      <div className={`flex flex-col max-w-[85%] ${isUser ? "items-end" : "items-start"}`}>

        {/* Name & Time (Optional, show on hover or always subtle) */}
        <div className={`flex items-center gap-2 mb-1 px-1 text-[10px] text-gray-500 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
          <span className="font-medium opacity-0 group-hover:opacity-100 transition-opacity">
            {isUser ? "You" : "Nimbus"}
          </span>
          <span className="opacity-0 group-hover:opacity-60 transition-opacity">
            {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>

        {/* Bubble */}
        <div
          className={`
            relative px-5 py-3.5 shadow-md
            ${isUser
              ? "bg-blue-600 text-white rounded-2xl rounded-tr-sm"
              : "bg-gray-900/60 backdrop-blur-md border border-white/5 text-gray-100 rounded-2xl rounded-tl-sm"
            }
          `}
        >
          {isUser ? (
            <div className="text-[15px] leading-relaxed whitespace-pre-wrap font-sans selection:bg-white/20">
              {message.content}
            </div>
          ) : (
            <div className="text-[15px] leading-relaxed min-w-[200px]">
              {tools.length > 0 && (
                <div className="mb-3">
                  <button
                    onClick={() => setShowTools(!showTools)}
                    className="flex items-center gap-2 text-xs font-medium text-blue-400 bg-blue-500/10 px-2 py-1 rounded hover:bg-blue-500/20 transition-colors border border-blue-500/10"
                  >
                    <span className="text-[10px]">{showTools ? "▼" : "▶"}</span>
                    <span>Used {tools.length} Tools</span>
                    {isStreaming && tools.some(t => t.status === 'running') && (
                      <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse ml-1" />
                    )}
                  </button>

                  {showTools && (
                    <div className="mt-2 space-y-2 border-l-2 border-gray-800 pl-2">
                      {tools.map((tool, i) => (
                        <ToolCard key={i} tool={tool} />
                      ))}
                    </div>
                  )}
                </div>
              )}

              {message.content ? (
                <MarkdownRenderer content={message.content} className="prose-invert prose-p:leading-relaxed prose-pre:bg-black/30 text-gray-100" />
              ) : (
                isStreaming && <span className="animate-pulse text-gray-500">Thinking...</span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
