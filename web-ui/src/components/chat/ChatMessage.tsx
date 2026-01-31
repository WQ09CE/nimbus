"use client";

import { useState, useMemo } from "react";
import type { Message } from "@/stores/chat-store";
import type { ToolCall, ToolResult } from "@/lib/api";

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

  const isUser = message.role === "user";

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

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} group`}>
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
          {/* Main Text */}
          {(message.content || (isStreaming && !tools.length)) && (
            <div className="px-5 py-4 text-sm leading-relaxed whitespace-pre-wrap text-gray-200 font-sans">
              {message.content}
              {isStreaming && !message.content && (
                <span className="text-gray-500 italic animate-pulse">Thinking...</span>
              )}
              {isStreaming && message.content && (
                <span className="inline-block w-1.5 h-4 ml-1 bg-blue-500 animate-pulse align-middle" />
              )}
            </div>
          )}

          {/* Tools Section */}
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
                            <div>
                              <div className="text-[10px] uppercase text-gray-600 font-bold mb-1">Input</div>
                              <pre className="text-xs bg-black/40 p-2 rounded border border-gray-800 text-gray-300 overflow-x-auto font-mono">
                                {JSON.stringify(tool.args, null, 2)}
                              </pre>
                            </div>

                            {/* Result */}
                            {tool.status !== "running" && (
                              <div>
                                <div className="text-[10px] uppercase text-gray-600 font-bold mb-1">
                                  {tool.status === "completed" ? "Output" : "Error"}
                                </div>
                                <pre className={`text-xs p-2 rounded border overflow-x-auto font-mono ${
                                  tool.status === "completed"
                                    ? "bg-black/40 border-gray-800 text-gray-400"
                                    : "bg-red-900/10 border-red-900/50 text-red-400"
                                }`}>
                                  {tool.error || (typeof tool.result === "string" ? tool.result : JSON.stringify(tool.result, null, 2))}
                                </pre>
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
        </div>
      </div>
    </div>
  );
}
