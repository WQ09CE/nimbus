import React, { useState, useRef, useEffect } from 'react';
import { stripAnsiAndCarriageReturns } from '@/lib/stringUtils';
import { useTypewriter } from '@/hooks/useTypewriter';

interface BashProps {
  args: { command: string;[key: string]: any };
  result?: string;
  error?: string;
  status: "running" | "completed" | "failed";
  ui_detail?: Record<string, any>;
}

export function Bash({ args, result, error, status, ui_detail }: BashProps) {
  const [isExpanded, setIsExpanded] = useState(true);
  const outputRef = useRef<HTMLDivElement>(null);

  const exitCode = ui_detail?.exit_code ?? (error ? 1 : 0);
  const isNonZero = exitCode !== 0;
  const hasOutput = !!result || !!error;
  const isStreaming = status === "running";

  const typedResult = useTypewriter(stripAnsiAndCarriageReturns(result || ""), isStreaming, 5);
  const typedError = useTypewriter(stripAnsiAndCarriageReturns(error || ""), isStreaming, 5);

  // 如果处于展开状态且正在运行（流式输出），自动滚动到底部
  useEffect(() => {
    if (status === "running" && isExpanded && outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [result, status, isExpanded]);

  return (
    <div className="font-mono text-sm bg-[#0d1117] rounded-md border border-gray-800 overflow-hidden flex flex-col my-2">
      {/* Header: Command & Status (始终显示，保持最小高度) */}
      <div
        className="flex items-center justify-between p-2.5 bg-[#161b22] cursor-pointer hover:bg-[#1f242e] transition-colors select-none"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex gap-3 text-gray-300 truncate font-semibold items-center max-w-[70%]">
          <span className="text-green-400 shrink-0">$</span>
          <span className="truncate" title={args.command}>{args.command}</span>
        </div>

        <div className="flex items-center gap-4 text-xs shrink-0">
          {status === "running" ? (
            <span className="flex items-center gap-1.5 text-blue-400">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500"></span>
              </span>
              Running
            </span>
          ) : error ? (
            <span className="px-2 py-0.5 rounded font-bold text-red-400 bg-red-500/10 border border-red-500/20">
              Failed
            </span>
          ) : (
            <span className={`px-2 py-0.5 rounded font-bold ${isNonZero ? 'text-amber-400 bg-amber-500/10 border border-amber-500/20' : 'text-green-400 bg-green-500/10 border border-green-500/20'}`}>
              exit {exitCode}
            </span>
          )}

          <span className="text-gray-500 w-4 text-center">
            {isExpanded ? (
              <svg fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
              </svg>
            ) : (
              <svg fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
              </svg>
            )}
          </span>
        </div>
      </div>

      {/* Body: Output & Error (仅在展开时显示) */}
      {isExpanded && (
        <div
          ref={outputRef}
          className="p-3 max-h-[200px] overflow-y-auto custom-scrollbar text-[13px] border-t border-gray-800"
        >
          {!hasOutput && status === "running" && (
            <div className="text-gray-500 italic flex items-center gap-2">
              Waiting for output...
            </div>
          )}
          {!hasOutput && status !== "running" && (
            <div className="text-gray-600 italic">(no output)</div>
          )}
          {error && (
            <div className="text-red-400 whitespace-pre-wrap break-words leading-relaxed">{typedError}</div>
          )}
          {result && !error && (
            <div className="text-gray-300 whitespace-pre-wrap break-words leading-relaxed">{typedResult}</div>
          )}
        </div>
      )}

      {/* Footer: Metadata (仅在展开且结束时显示) */}
      {isExpanded && status !== "running" && ui_detail && (
        <div className="px-3 py-1.5 bg-[#161b22] border-t border-gray-800 text-[11px] text-gray-500 flex justify-between">
          <div className="flex gap-3">
            {ui_detail.total_lines != null && <span>{ui_detail.total_lines} lines</span>}
            {ui_detail.truncated && <span className="text-yellow-500/80">⚠️ Truncated</span>}
            {ui_detail.timed_out && <span className="text-red-400/80">⚠️ Timed out</span>}
          </div>
          <div>
            {ui_detail.duration != null && `${ui_detail.duration.toFixed(2)}s`}
          </div>
        </div>
      )}
    </div>
  );
}
