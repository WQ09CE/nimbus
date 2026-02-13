"use client";

import { useChatStore } from "@/stores";

export function WorkingIndicator() {
  const isStreaming = useChatStore(s => s.isStreaming);
  const currentActivity = useChatStore(s => s.currentActivity);
  const thinkingIteration = useChatStore(s => s.thinkingIteration);

  if (!isStreaming || !currentActivity) return null;

  const isExecutor = currentActivity.startsWith('⚡');
  const borderColor = isExecutor ? 'border-l-purple-500' : 'border-l-blue-500';
  const dotColor = isExecutor ? 'bg-purple-400' : 'bg-blue-400';
  const dotBg = isExecutor ? 'bg-purple-500' : 'bg-blue-500';
  const textColor = isExecutor ? 'text-purple-300' : 'text-blue-300';

  return (
    <div data-testid="working-indicator" className="w-full px-6 pb-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="max-w-4xl mx-auto">
        <div className={`flex items-center gap-3 text-gray-400 text-xs py-2 px-3 bg-gray-900/80 rounded border border-gray-800/50 backdrop-blur-md shadow-lg border-l-4 ${borderColor} transition-all duration-300`}>
          <span className="relative flex h-2 w-2">
            <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${dotColor} opacity-75`}></span>
            <span className={`relative inline-flex rounded-full h-2 w-2 ${dotBg}`}></span>
          </span>
          <span className={`font-mono ${textColor} font-medium tracking-wide`}>{currentActivity.toUpperCase()}</span>
          {thinkingIteration !== null && thinkingIteration > 0 && (
            <span className="text-gray-500 font-mono ml-auto">
              ITERATION {thinkingIteration + 1}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
