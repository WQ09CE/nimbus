"use client";

import { useChatStore } from "@/stores";
import type { Message } from "@/stores/chat-store";

/** Name of the tool currently executing in the streaming message, if any. */
function currentActivity(messages: Message[]): string | null {
  const msg = messages[messages.length - 1];
  if (!msg || msg.role !== "assistant" || !msg.parts) return null;
  for (let i = msg.parts.length - 1; i >= 0; i--) {
    const p = msg.parts[i];
    if (p.type === "tool") {
      const tcId = p.toolCall.id;
      const res = (tcId ? msg.toolResultsMap?.[tcId] : undefined) || p.toolResult;
      if (!res || (res as any)._streaming) {
        return (tcId && msg.toolCallsMap?.[tcId]?.name) || p.toolCall.name;
      }
      return null; // last tool finished — model is back to thinking/writing
    }
    if (p.type === "text" && p.content.trim()) return null; // writing text
  }
  return null;
}

export function WorkingIndicator() {
  const isStreaming = useChatStore(s => s.isStreaming);
  const messages = useChatStore(s => s.messages);

  if (!isStreaming) return null;

  const tool = currentActivity(messages);

  return (
    <div data-testid="working-indicator" className="w-full px-6 pb-2 mb-1 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3 text-nimbus-text-dim text-xs py-2 px-3 bg-nimbus-surface rounded-lg border border-nimbus-border backdrop-blur-xl shadow-lg border-l-4 border-l-sky-400/50 transition-all duration-300">
          <span className="relative flex h-2 w-2">
            <span className="relative inline-flex rounded-full h-2 w-2 bg-sky-400 animate-breathe"></span>
          </span>
          <span className="font-mono text-sky-300 font-medium tracking-wide">
            {tool ? (
              <>Running <span className="text-sky-100">{tool}</span>…</>
            ) : (
              "Thinking…"
            )}
          </span>
        </div>
      </div>
    </div>
  );
}
