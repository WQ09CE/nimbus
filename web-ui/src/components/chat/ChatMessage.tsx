"use client";

import React from "react";
import type { Message, MessagePart } from "@/stores/chat-store";
import { useTypewriter } from "@/hooks/useTypewriter";
import { MarkdownRenderer } from "./MarkdownRenderer";
import type { ToolCall, ToolResult } from "@/lib/api";

interface ChatMessageProps {
  message: Message;
  isStreaming?: boolean;
}

function UserAvatar() {
  return (
    <div className="w-8 h-8 rounded-full bg-nimbus-surface backdrop-blur-lg border border-sky-400/30 flex items-center justify-center shrink-0 shadow-lg shadow-sky-400/10 mt-1">
      <span className="text-xs text-sky-300 font-bold">U</span>
    </div>
  );
}

function AiAvatar() {
  return (
    <div className="w-8 h-8 rounded-full bg-nimbus-surface backdrop-blur-lg border border-nimbus-border flex items-center justify-center shrink-0 shadow-lg shadow-sky-400/10 mt-1">
      <span className="text-xs">☁️</span>
    </div>
  );
}

function ThoughtBlock({ content }: { content: string }) {
  const displayContent = content.replace(/^`?thought:`?\s*/i, "");
  if (!displayContent) return null;
  return (
    <div className="my-2 pl-3 py-1 border-l-2 border-blue-500/30 bg-blue-500/5 rounded-r-lg flex items-start gap-2 group/thought">
      <span className="text-sm mt-0.5 opacity-70" title="Thinking">🧠</span>
      <div className="text-sm italic text-gray-400 font-sans leading-relaxed">{displayContent}</div>
    </div>
  );
}

function ToolPill({ call, result }: { call: ToolCall; result?: ToolResult }) {
  const isError = result?.error;
  const isRunning = !result;

  return (
    <div className={`text-[12px] font-mono inline-flex flex-col gap-1.5 my-1 px-4 py-2.5 rounded-lg border backdrop-blur-md ${isError ? 'bg-red-500/10 border-red-500/30 text-red-100' : isRunning ? 'bg-blue-500/10 border-blue-500/30 text-blue-100 animate-pulse' : 'bg-green-500/10 border-green-500/30 text-green-100'}`}>
      <div className="flex items-center gap-2 border-b border-white/5 pb-1 mb-0.5">
        <span>{isRunning ? '▶' : isError ? '❌' : '✓'} </span>
        <span className="font-semibold text-nimbus-accent">{call.name}</span>
      </div>
      <div className="opacity-80 break-words max-h-32 overflow-y-auto custom-scrollbar">
        {JSON.stringify(call.arguments, null, 2)}
      </div>
      {result && result.result != null && (
        <div className="opacity-60 text-[10px] truncate pt-1 border-t border-white/5">
          Return: {String(typeof result.result === 'string' ? (result.result as string).replace(/\n/g, ' ') : JSON.stringify(result.result))}
        </div>
      )}
      {result && result.error && (
        <div className="text-red-400 italic text-[10px] break-words pt-1 border-t border-white/5">
          Error: {result.error}
        </div>
      )}
    </div>
  );
}

/** Render a text part with thought block detection */
function TextPart({ content, isStreaming }: { content: string; isStreaming?: boolean }) {
  const displayed = useTypewriter(content, isStreaming === true);
  const clean = displayed.trim();
  if (!clean) return null;

  const lines = clean.split("\n");
  const processed: React.ReactNode[] = [];
  let currentMarkdown: string[] = [];

  const flushMarkdown = () => {
    if (currentMarkdown.length > 0) {
      processed.push(
        <MarkdownRenderer
          key={`md-${processed.length}`}
          content={currentMarkdown.join("\n")}
          isStreaming={isStreaming}
          className="prose-invert prose-p:leading-relaxed prose-pre:bg-[#0d1117]/80 text-[14px] text-gray-100 w-full max-w-none break-words"
        />
      );
      currentMarkdown = [];
    }
  };

  lines.forEach((line, i) => {
    if (/^`?thought:`?/i.test(line.trim())) {
      flushMarkdown();
      processed.push(<ThoughtBlock key={`thought-${i}`} content={line.trim()} />);
    } else {
      currentMarkdown.push(line);
    }
  });
  flushMarkdown();

  return <>{processed}</>;
}

export const ChatMessage = React.memo(function ChatMessage({ message, isStreaming }: ChatMessageProps) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  if (isSystem) {
    return (
      <div className="flex justify-center my-6">
        <div className="px-4 py-1.5 rounded-full bg-nimbus-surface border border-nimbus-border text-xs text-gray-500 font-medium whitespace-pre-wrap text-center max-w-[80%]">{message.content}</div>
      </div>
    );
  }

  const parts = message.parts || [];
  const hasParts = parts.length > 0;

  return (
    <div data-testid={isUser ? "message-user" : "message-assistant"}
      className={`flex gap-4 mb-8 ${isUser ? "flex-row-reverse" : "flex-row"} group`}>

      <div className="shrink-0">
        {isUser ? <UserAvatar /> : <AiAvatar />}
      </div>

      <div className={`flex flex-col min-w-0 w-full max-w-[95%] md:max-w-[85%] ${isUser ? "items-end" : "items-start"}`}>
        <div className={`flex items-center gap-2 mb-1.5 px-1 text-[11px] text-gray-500 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
          <span className="font-medium opacity-0 group-hover:opacity-100 transition-opacity">
            {isUser ? "You" : "Nimbus"}
          </span>
        </div>

        {isUser ? (
          <div className={`
              relative px-4 md:px-5 py-3 md:py-4 shadow-xl
              bg-sky-500/15 border border-sky-400/20 backdrop-blur-md text-nimbus-text rounded-2xl rounded-tr-sm min-w-[30%]
            `}>
            <div className="text-[15px] leading-relaxed whitespace-pre-wrap font-sans selection:bg-white/20">
              {message.isInjection && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 mr-2 mb-1 rounded-full text-[11px] font-medium bg-amber-500/20 text-amber-300 border border-amber-400/30">
                  <span className="not-italic">&#9889;</span> 插入消息
                </span>
              )}
              {message.content}
            </div>
          </div>
        ) : (
          /* Assistant: render parts in chronological order */
          <div className="flex flex-col gap-3 w-full">
            {hasParts ? (
              parts.map((part, idx) => {
                if (part.type === "text") {
                  const isLastPart = idx === parts.length - 1;
                  return (
                    <div key={`part-${idx}`} className="relative px-4 md:px-5 py-3 md:py-4 shadow-xl bg-nimbus-surface backdrop-blur-xl border border-nimbus-border text-gray-100 rounded-2xl rounded-tl-sm w-full">
                      <div className="text-[15px] leading-relaxed w-full">
                        <div className="flex flex-col gap-1 w-full overflow-hidden">
                          <TextPart content={part.content} isStreaming={isStreaming && isLastPart} />
                        </div>
                      </div>
                    </div>
                  );
                } else {
                  return (
                    <div key={`part-${idx}`} className="w-[95%] ml-2">
                      <ToolPill call={part.toolCall} result={part.toolResult} />
                    </div>
                  );
                }
              })
            ) : (
              isStreaming && (
                <div className="relative px-4 md:px-5 py-3 md:py-4 shadow-xl bg-nimbus-surface backdrop-blur-xl border border-nimbus-border text-gray-100 rounded-2xl rounded-tl-sm w-full">
                  <span className="animate-pulse text-gray-400 text-sm">Thinking...</span>
                </div>
              )
            )}
          </div>
        )}
      </div>
    </div>
  );
}, (prevProps, nextProps) => {
  return (
    prevProps.message.id === nextProps.message.id &&
    prevProps.message.content === nextProps.message.content &&
    prevProps.isStreaming === nextProps.isStreaming &&
    prevProps.message.parts === nextProps.message.parts
  );
});
