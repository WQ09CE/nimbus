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
  // If no steps, render nothing
  if (!steps || steps.length === 0) return null;

  return (
    <div className="my-2 pl-2">
      <div className="space-y-6">
        {steps.map((step, index) => {
          // Check if this step is essentially just "status update" or "thinking"
          // We can style it subtly.
          const isThinking = !step.toolCalls || step.toolCalls.length === 0;

          return (
            <div key={step.id || index} className="relative animate-in fade-in slide-in-from-bottom-2 duration-500 group">

              {/* Connector Line (only if not last) */}
              {index < steps.length - 1 && (
                <div className="absolute left-[5px] top-4 bottom-[-24px] w-px bg-gray-800/50 group-hover:bg-gray-700/50 transition-colors" />
              )}

              <div className="flex items-start gap-4">
                {/* Status Dot */}
                <div className="relative mt-2 shrink-0">
                  <div className={`w-2.5 h-2.5 rounded-full border-2 border-black z-10 
                        ${isThinking
                      ? "bg-gray-600"
                      : "bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.4)]"
                    }
                     `} />
                </div>

                {/* Content Body */}
                <div className="flex-1 min-w-0 pt-0.5">
                  {/* Content (Thinking or Message) -> Now in Bubble */}
                  {step.content && (
                    <div className={`
                        relative px-4 py-3 shadow-md mb-3
                        bg-gray-900/60 backdrop-blur-md border border-white/5 text-gray-100 rounded-2xl rounded-tl-sm
                      `}>
                      <div className={`leading-relaxed font-sans ${isThinking ? "text-gray-300" : "text-gray-100"}`}>
                        <MarkdownRenderer content={step.content} className="prose-invert prose-p:leading-relaxed prose-pre:bg-black/30" />
                      </div>
                    </div>
                  )}

                  {/* Tools Grid */}
                  {step.toolCalls && step.toolCalls.length > 0 && (
                    <div className="space-y-2">
                      {step.toolCalls.map((toolCall, tIndex) => {
                        // Merge result (logic duplicated, should be hook)
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
                          status,
                          subCalls: toolCall.subCalls,
                          subResults: toolCall.subResults,
                        };

                        return <ToolCard key={toolCall.id || tIndex} tool={tool} />;
                      })}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
