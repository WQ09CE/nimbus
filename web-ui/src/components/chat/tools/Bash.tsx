import React from 'react';

interface BashProps {
  args: { command: string; [key: string]: any };
  result?: string;
  error?: string;
  status: "running" | "completed" | "failed";
}

export function Bash({ args, result, error, status }: BashProps) {
  // const safeArgs = args || {};
  // const command = safeArgs.command || safeArgs.cmd || safeArgs.code || "unknown";

  return (
    <div className="font-mono text-sm bg-black">
      {/* Terminal Body */}
      <div className="p-3 overflow-x-auto max-h-[200px] overflow-y-auto custom-scrollbar">
        {/* Command echo */}
        <div className="flex gap-2 text-gray-400 mb-2 border-b border-gray-800/50 pb-2">
          <span className="text-green-500">$</span>
          <span>{args.command}</span>
        </div>

        {status === "running" ? (
          <div className="text-gray-500 animate-pulse">Running...</div>
        ) : error ? (
          <div className="text-red-400 whitespace-pre-wrap">{error}</div>
        ) : (
          <div className="text-gray-300 whitespace-pre-wrap leading-relaxed">
            {result || <span className="text-gray-600 italic">(no output)</span>}
          </div>
        )}
        
        {/* Status Line */}
        {status !== "running" && (
          <div className="mt-2 pt-2 border-t border-gray-800/50 text-[10px] text-gray-500 flex justify-end">
            Exit code: {error ? "1" : "0"}
          </div>
        )}
      </div>
    </div>
  );
}
