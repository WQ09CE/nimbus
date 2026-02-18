"use client";

import React, { useState, useMemo, useEffect, useRef } from "react";
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
    <div className="w-8 h-8 rounded-full bg-nimbus-surface backdrop-blur-lg border border-sky-400/30 flex items-center justify-center shrink-0 shadow-lg shadow-sky-400/10">
      <span className="text-xs text-sky-300 font-bold">U</span>
    </div>
  );
}

// Helper for AI avatar
function AiAvatar() {
  return (
    <div className="w-8 h-8 rounded-full bg-nimbus-surface backdrop-blur-lg border border-nimbus-border flex items-center justify-center shrink-0 shadow-lg shadow-sky-400/10">
      <span className="text-xs">☁️</span>
    </div>
  );
}

export const ChatMessage = React.memo(function ChatMessage({ message, isStreaming }: ChatMessageProps) {
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

  // Auto-expand tool list when Dispatch tools are present
  const hasDispatch = tools.some((t) => t.name === "Dispatch");
  useEffect(() => {
    if (hasDispatch) {
      setShowTools(true);
    }
  }, [hasDispatch]);

  // Auto-expand tools during streaming
  useEffect(() => {
    if (isStreaming && tools.length > 0) {
      setShowTools(true);
    }
  }, [isStreaming, tools.length]);

  if (isSystem) {
    return (
      <div className="flex justify-center my-6">
        <div className="px-4 py-1.5 rounded-full bg-nimbus-surface border border-nimbus-border text-xs text-gray-500 font-medium flex items-center gap-2 backdrop-blur-sm">
          <span>⚡</span>
          <span>{message.content}</span>
        </div>
      </div>
    );
  }

  const hasContent = Boolean(message.content);
  const hasTools = tools.length > 0;
  const hasRunningTools = tools.some((t) => t.status === "running");

  const getToolKey = (tool: MergedTool, index: number) => {
    const stableId = tool.id;
    if (stableId) return stableId;

    const serializedArgs = JSON.stringify(tool.args ?? {});
    const durationPart = typeof tool.duration === "number" ? `-d:${tool.duration}` : "";
    const errorPart = tool.error ? "-e" : "";

    return `${tool.name}-${serializedArgs}${durationPart}${errorPart}-${index}`;
  };

  return (
    <div data-testid={isUser ? "message-user" : "message-assistant"} className={`flex gap-4 mb-6 ${isUser ? "flex-row-reverse" : "flex-row"} group`}>
      {/* Avatar */}
      <div className="mt-1">
        {isUser ? <UserAvatar /> : <AiAvatar />}
      </div>

      {/* Content Container */}
      <div className={`flex flex-col max-w-[95%] md:max-w-[85%] ${isUser ? "items-end" : "items-start"}`}>

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
            relative px-3 md:px-5 py-2.5 md:py-3.5 shadow-md
            ${isUser
              ? "bg-sky-500/15 border border-sky-400/20 backdrop-blur-md text-nimbus-text rounded-2xl rounded-tr-sm"
              : "bg-nimbus-surface backdrop-blur-xl border border-nimbus-border text-gray-100 rounded-2xl rounded-tl-sm"
            }
          `}
        >
          {isUser ? (
            <div className="text-[15px] leading-relaxed whitespace-pre-wrap font-sans selection:bg-white/20">
              {message.content}
              {/* Attachments */}
              {message.attachments && message.attachments.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-2">
                  {message.attachments.map(att => {
                    // Build image src: prefer data URL from base64 content, fall back to preview blob URL
                    const imageSrc = att.type === "image" && att.content
                      ? `data:${att.mimeType || "image/png"};base64,${att.content}`
                      : att.preview;

                    return (
                      <div key={att.id} className="rounded-lg overflow-hidden border border-white/20">
                        {att.type === "image" && imageSrc ? (
                          <img
                            src={imageSrc}
                            alt={att.name}
                            className="max-w-[200px] max-h-[150px] object-cover cursor-pointer hover:opacity-80 transition-opacity"
                            onClick={() => {
                              // Open full image in new tab
                              const w = window.open();
                              if (w) {
                                w.document.write(`<img src="${imageSrc}" style="max-width:100%;height:auto;" />`);
                                w.document.title = att.name;
                              }
                            }}
                          />
                        ) : (
                          <div className="flex items-center gap-2 px-3 py-2 bg-white/10">
                            <span className="text-sm">{att.type === "pdf" ? "📄" : "📝"}</span>
                            <span className="text-xs text-blue-100">{att.name}</span>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ) : (
            <div className="text-[15px] leading-relaxed min-w-[200px]">
              {hasContent ? (
                <MarkdownRenderer content={message.content} isStreaming={isStreaming && message.id === "streaming"} className="prose-invert prose-p:leading-relaxed prose-pre:bg-black/30 text-gray-100" />
              ) : (
                <>
                  {isStreaming && !hasTools && <span className="animate-pulse text-gray-500">Thinking...</span>}
                  {isStreaming && hasTools && !hasRunningTools && (
                    <span className="text-xs text-gray-500">Generating response...</span>
                  )}
                </>
              )}

              {hasTools && (
                <div className={hasContent ? "mt-3" : undefined}>
                  <button
                    onClick={() => setShowTools(!showTools)}
                    className="flex items-center gap-2 text-xs font-medium text-nimbus-accent bg-nimbus-surface px-2 py-1 rounded hover:bg-nimbus-surface-hover transition-colors border border-nimbus-border"
                  >
                    <span className="text-[10px]">{showTools ? "▼" : "▶"}</span>
                    <span>Used {tools.length} Tools</span>
                    {isStreaming && hasRunningTools && (
                      <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse ml-1" />
                    )}
                  </button>

                  {showTools && (
                    <div className="mt-2 space-y-2 border-l-2 border-nimbus-border pl-2">
                      {tools.map((tool, i) => (
                        <ToolCard key={getToolKey(tool, i)} tool={tool} />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}, (prevProps, nextProps) => {
  return (
    prevProps.message.id === nextProps.message.id &&
    prevProps.message.content === nextProps.message.content &&
    prevProps.isStreaming === nextProps.isStreaming &&
    prevProps.message.toolCalls === nextProps.message.toolCalls &&
    prevProps.message.toolResults === nextProps.message.toolResults
  );
});
