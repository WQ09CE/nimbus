import React, { useState } from 'react';
import type { ToolCall, ToolResult } from '@/lib/api';

interface DispatchCardProps {
    tool: {
        id?: string;
        name: string;
        args: Record<string, unknown>;
        result?: unknown;
        error?: string;
        status: "running" | "completed" | "failed";
        duration?: number;
        subCalls?: ToolCall[];
        subResults?: ToolResult[];
    };
}

/** Parse file changes from Dispatch result text */
function parseFileChanges(resultText: string): { icon: string; file: string; type: string }[] {
    const changes: { icon: string; file: string; type: string }[] = [];
    // Match lines like:  ~ hello.txt (modified)  + new.py (created)  - old.py (deleted)
    const regex = /^\s*([~+\-!])\s+(\S+)\s*\((\w+)\)/gm;
    let match;
    while ((match = regex.exec(resultText)) !== null) {
        const [, symbol, file, type] = match;
        const icon = symbol === '+' ? '🟢' : symbol === '-' ? '🔴' : '🟡';
        changes.push({ icon, file, type });
    }
    return changes;
}

/** Extract executor report from Dispatch result text (before "### Files Changed") */
function parseExecutorReport(resultText: string): string {
    const parts = resultText.split('### Files Changed');
    let report = parts[0];
    // Remove the "## Dispatch #N Result" header and "### Executor Report" header
    report = report.replace(/^##\s+Dispatch\s+#\d+\s+Result\s*/m, '');
    report = report.replace(/^###\s+Executor Report\s*/m, '');
    return report.trim();
}

export function DispatchCard({ tool }: DispatchCardProps) {
    const [isExpanded, setIsExpanded] = useState(true);

    const isRunning = tool.status === "running";
    const isFailed = tool.status === "failed";
    const subCalls = tool.subCalls || [];
    const subResults = tool.subResults || [];

    // Parse task description from args
    const task = (tool.args?.task as string) || (tool.args?.context as string) || "Sub-task";
    const taskPreview = task.length > 80 ? task.slice(0, 80) + "..." : task;

    // Parse result
    const resultText = typeof tool.result === 'string' ? tool.result : '';
    const fileChanges = parseFileChanges(resultText);
    const executorReport = parseExecutorReport(resultText);

    // Build sub-call status
    const subCallsWithStatus = subCalls.map((sc) => {
        const matchedResult = subResults.find(sr => sr.id === sc.id);
        return {
            ...sc,
            result: matchedResult?.result,
            error: matchedResult?.error,
            duration: matchedResult?.duration,
            status: matchedResult ? (matchedResult.error ? "failed" : "completed") : "running" as const,
        };
    });

    return (
        <div className={`
      overflow-hidden rounded-xl border transition-all duration-300 relative
      ${isRunning
                ? 'border-purple-500/30 bg-purple-950/20 shadow-[0_0_20px_rgba(168,85,247,0.08)]'
                : isFailed
                    ? 'border-red-500/30 bg-red-950/10'
                    : 'border-purple-500/20 bg-purple-950/10'
            }
    `}>
            {/* Left accent bar */}
            <div className={`absolute left-0 top-0 bottom-0 w-1 ${isRunning ? 'bg-purple-500 animate-pulse' : isFailed ? 'bg-red-500' : 'bg-purple-500'
                }`} />

            {/* Header */}
            <div
                className="px-4 py-3 pl-5 flex items-center justify-between cursor-pointer select-none"
                onClick={() => setIsExpanded(!isExpanded)}
            >
                <div className="flex items-center gap-3 min-w-0">
                    {/* Status indicator */}
                    <div className="flex items-center gap-2">
                        {isRunning ? (
                            <div className="flex gap-0.5">
                                <div className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-bounce" style={{ animationDelay: '0ms' }} />
                                <div className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-bounce" style={{ animationDelay: '150ms' }} />
                                <div className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-bounce" style={{ animationDelay: '300ms' }} />
                            </div>
                        ) : isFailed ? (
                            <span className="text-red-400 text-sm">✗</span>
                        ) : (
                            <span className="text-emerald-400 text-sm">✓</span>
                        )}
                    </div>

                    {/* Agent label */}
                    <span className="text-[10px] uppercase font-bold text-purple-300 bg-purple-500/15 px-2 py-0.5 rounded-full border border-purple-500/20 tracking-wider whitespace-nowrap">
                        ⚡ Executor
                    </span>

                    {/* Task preview */}
                    <span className="text-[12px] text-gray-400 truncate">
                        {taskPreview}
                    </span>
                </div>

                <div className="flex items-center gap-3 shrink-0 ml-2">
                    {/* Sub-tool count */}
                    {subCalls.length > 0 && (
                        <span className="text-[10px] font-mono text-purple-400/70">
                            {subCalls.length} tool{subCalls.length > 1 ? 's' : ''}
                        </span>
                    )}

                    {/* Duration */}
                    {tool.duration && (
                        <span className="text-[10px] font-mono text-gray-600">
                            {(tool.duration / 1000).toFixed(1)}s
                        </span>
                    )}

                    {/* Chevron */}
                    <svg
                        className={`w-3 h-3 text-gray-500 transition-transform duration-200 ${isExpanded ? "rotate-180" : ""}`}
                        fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
                    >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                </div>
            </div>

            {/* Expanded body */}
            {isExpanded && (
                <div className="border-t border-purple-500/10 px-4 pl-5 py-3 space-y-3">

                    {/* Executor tool calls timeline */}
                    {subCallsWithStatus.length > 0 && (
                        <div className="space-y-1.5">
                            {subCallsWithStatus.map((sub, i) => {
                                const isSubRunning = sub.status === "running";
                                const isSubFailed = sub.status === "failed";

                                // Extract summary for file operations
                                let subSummary = "";
                                const pathArg = sub.arguments?.path || sub.arguments?.file_path || sub.arguments?.target_file;
                                const cmdArg = sub.arguments?.command || sub.arguments?.cmd;
                                if (typeof pathArg === 'string') {
                                    const parts = pathArg.split('/');
                                    subSummary = parts.pop() || pathArg;
                                } else if (typeof cmdArg === 'string') {
                                    subSummary = cmdArg.length > 50 ? cmdArg.slice(0, 50) + '...' : cmdArg;
                                }

                                return (
                                    <div key={sub.id || i} className="flex items-center gap-2.5 py-1 px-2 rounded-md hover:bg-white/[0.02] transition-colors">
                                        {/* Status dot */}
                                        <div className={`w-2 h-2 rounded-full shrink-0 ${isSubRunning
                                                ? 'bg-yellow-400 animate-pulse shadow-[0_0_6px_rgba(250,204,21,0.5)]'
                                                : isSubFailed
                                                    ? 'bg-red-400 shadow-[0_0_6px_rgba(248,113,113,0.4)]'
                                                    : 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.4)]'
                                            }`} />

                                        {/* Tool name */}
                                        <span className="text-[12px] font-mono text-purple-200 font-medium">
                                            {sub.name}
                                        </span>

                                        {/* Summary */}
                                        {subSummary && (
                                            <>
                                                <span className="text-gray-700 text-[10px]">/</span>
                                                <span className="text-[11px] font-mono text-gray-500 truncate">
                                                    {subSummary}
                                                </span>
                                            </>
                                        )}

                                        {/* Duration */}
                                        {sub.duration && (
                                            <span className="text-[10px] font-mono text-gray-600 ml-auto shrink-0">
                                                {sub.duration}ms
                                            </span>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    {/* Running indicator when no sub-calls yet */}
                    {isRunning && subCallsWithStatus.length === 0 && (
                        <div className="flex items-center gap-2 py-2 text-[12px] text-purple-300/60">
                            <div className="w-3 h-3 border-2 border-purple-500/30 border-t-purple-400 rounded-full animate-spin" />
                            <span>Executor 正在处理...</span>
                        </div>
                    )}

                    {/* File changes */}
                    {fileChanges.length > 0 && (
                        <div className="pt-1 border-t border-purple-500/10">
                            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5 font-medium">
                                文件变更
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                                {fileChanges.map((fc, i) => (
                                    <span
                                        key={i}
                                        className="inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded-md bg-black/30 border border-white/5 text-gray-300"
                                    >
                                        <span>{fc.icon}</span>
                                        <span>{fc.file}</span>
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Error display */}
                    {tool.error && (
                        <div className="pt-1 border-t border-red-500/10">
                            <div className="text-[11px] text-red-400 bg-red-500/10 px-3 py-2 rounded-md border border-red-500/20 font-mono">
                                {tool.error}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
