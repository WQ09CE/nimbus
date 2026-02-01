import React, { useState } from 'react';
import type { Message } from "@/stores/chat-store";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { ToolCard } from "./tools/ToolCard";
import type { ToolResult } from "@/lib/api";

interface AgentProcessProps {
  steps: Message[];
  isStreaming?: boolean;
}

export function AgentProcess({ steps, isStreaming }: AgentProcessProps) {
  const [isCollapsed, setIsCollapsed] = useState(false);

  return (
    <div className="my-4 relative group">
      {/* Connector Line */}
      <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-gray-800/50 group-hover:bg-gray-700/50 transition-colors" />

      {/* Header / Collapse Toggle */}
      <div 
        className="flex items-center gap-2 mb-4 cursor-pointer select-none"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <div className="relative z-10 w-8 h-8 rounded-full bg-black border border-gray-800 flex items-center justify-center text-xs text-blue-400">
          ⚡
        </div>
        <div className="text-xs font-mono text-gray-500 flex items-center gap-2">
          <span className="text-blue-400 font-bold">AGENT WORKFLOW</span>
          <span className="bg-gray-900 px-1.5 py-0.5 rounded text-[10px]">
            {steps.length} STEPS
          </span>
          {isStreaming && (
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
          )}
        </div>
      </div>

      {/* Steps Container */}
      {!isCollapsed && (
        <div className="space-y-6 ml-4 pl-6 pb-2">
          {steps.map((step, index) => (
            <div key={step.id || index} className="relative animate-in fade-in slide-in-from-bottom-2 duration-500">
              
              {/* Step Dot */}
              <div className="absolute -left-[29px] top-1 w-3 h-3 rounded-full bg-gray-800 border-2 border-black z-10" />

              {/* 1. Thinking Content */}
              {step.content && (
                <div className="mb-3 text-gray-400 text-sm font-sans leading-relaxed opacity-90 hover:opacity-100 transition-opacity">
                  <MarkdownRenderer content={step.content} />
                </div>
              )}

              {/* 2. Tools */}
              {step.toolCalls && step.toolCalls.length > 0 && (
                <div className="space-y-3">
                  {step.toolCalls.map((toolCall, tIndex) => {
                    // Merge result logic (duplicated from ChatMessage, should extract hook ideally)
                    // For now simple mapping
                    let result: any = undefined;
                    let status: "running" | "completed" | "failed" = "running";
                    let error: string | undefined = undefined;

                    if (step.toolResults) {
                       const res = step.toolResults.find(r => r.id === toolCall.id) || step.toolResults[tIndex];
                       if (res) {
                           result = res.result;
                           error = res.error;
                           status = error ? "failed" : "completed";
                       }
                    }

                    const tool = {
                        ...toolCall,
                        args: toolCall.arguments,
                        result,
                        error,
                        status
                    };

                    return (
                      <div key={toolCall.id || tIndex} className="relative">
                         <ToolCard tool={tool} />
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
