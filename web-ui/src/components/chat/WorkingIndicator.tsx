"use client";

import { useChatStore } from "@/stores";

export function WorkingIndicator() {
  const isStreaming = useChatStore(s => s.isStreaming);
  const currentActivity = useChatStore(s => s.currentActivity);
  const thinkingIteration = useChatStore(s => s.thinkingIteration);

  if (!isStreaming || !currentActivity) return null;

  return (
    <div data-testid="working-indicator" className="w-full px-6 pb-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3 text-nimbus-text-dim text-xs py-2 px-3 bg-nimbus-surface rounded-lg border border-nimbus-border backdrop-blur-xl shadow-lg border-l-4 border-l-sky-400/50 transition-all duration-300">
          <span className="relative flex h-2 w-2">
            <span className="relative inline-flex rounded-full h-2 w-2 bg-sky-400 animate-breathe"></span>
          </span>
          <span className="font-mono text-sky-300 font-medium tracking-wide">{currentActivity.toUpperCase()}</span>
          {thinkingIteration !== null && thinkingIteration > 0 && (
            <span className="text-nimbus-text-dim font-mono ml-auto">
              ITERATION {thinkingIteration + 1}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
