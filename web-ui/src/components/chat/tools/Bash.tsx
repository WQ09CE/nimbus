import React from 'react';

interface BashProps {
  args: { command: string; [key: string]: any };
  result?: string;
  error?: string;
  status: "running" | "completed" | "failed";
  ui_detail?: Record<string, any>;
}

export function Bash({ args, result, error, status, ui_detail }: BashProps) {
  const exitCode = ui_detail?.exit_code ?? (error ? 1 : 0);
  const isNonZero = exitCode !== 0;

  return (
    <div className="font-mono text-sm bg-black overflow-hidden">
      <div className="p-3 max-h-[300px] overflow-auto custom-scrollbar">
        {/* Command echo */}
        <div className="flex gap-2 text-gray-400 mb-2 border-b border-gray-800/50 pb-2">
          <span className="text-green-500 shrink-0">$</span>
          <span className="break-all">{args.command}</span>
        </div>

        {status === "running" ? (
          <div className="text-gray-500 animate-pulse">Running...</div>
        ) : error ? (
          <div className="text-red-400 whitespace-pre-wrap break-words overflow-hidden">{error}</div>
        ) : (
          <div className="text-gray-300 whitespace-pre-wrap break-words leading-relaxed overflow-hidden">
            {result ? result : <span className="text-gray-600 italic">(no output)</span>}
          </div>
        )}

        {/* Status Line */}
        {status !== "running" && (
          <div className={`mt-2 pt-2 border-t border-gray-800/50 text-[10px] flex justify-between items-center ${isNonZero ? 'text-red-400' : 'text-gray-500'}`}>
            <span>
              {ui_detail?.total_lines != null && `${ui_detail.total_lines} lines`}
              {ui_detail?.truncated && ' (truncated)'}
              {ui_detail?.timed_out && ' timed out'}
            </span>
            <span className={`font-bold ${isNonZero ? 'text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded' : ''}`}>
              exit {exitCode}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
