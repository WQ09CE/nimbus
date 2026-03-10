import React from 'react';
import { DataDisplay } from '../MarkdownRenderer';
import { useTypewriter } from '@/hooks/useTypewriter';

interface DefaultToolProps {
  name: string;
  args: any;
  result?: any;
  error?: string;
  status: "running" | "completed" | "failed";
}

export function DefaultTool({ name, args, result, error, status }: DefaultToolProps) {
  const isStreaming = status === "running";
  const typedResult = useTypewriter(typeof result === "string" ? result : "", isStreaming, 5);
  const displayResult = typeof result === "string" ? typedResult : result;

  return (
    <div className="space-y-3">
      {/* Args */}
      <DataDisplay
        data={args}
        title="Input"
        className="bg-black/40 p-3 rounded border border-gray-800"
      />

      {/* Result */}
      {result && (
        <DataDisplay
          data={displayResult}
          title="Output"
          maxDepth={3}
          className="bg-black/40 p-3 rounded border border-gray-800 text-green-300/90"
        />
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-900/20 border border-red-800/50 rounded p-3 text-sm font-mono text-red-300">
          <div className="font-bold mb-1 text-red-400">ERROR</div>
          <div className="whitespace-pre-wrap">{error}</div>
        </div>
      )}
    </div>
  );
}
