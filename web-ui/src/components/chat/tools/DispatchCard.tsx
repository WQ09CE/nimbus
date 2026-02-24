import React, { useState } from 'react';
import type { ToolCall, ToolResult } from '@/lib/api';
import { MarkdownRenderer } from '../MarkdownRenderer';
import { ToolDisplay } from './ToolDisplay';

// Visual configuration for each specialist type
interface SpecialistTheme {
    label: string;
    icon: string;
    // Static Tailwind classes for each color context (cannot use dynamic interpolation)
    border: { running: string; failed: string; normal: string };
    bg: { running: string; failed: string; normal: string };
    strip: { running: string; failed: string; normal: string };
    badge: { text: string; bg: string; border: string };
    textMuted: string;
    borderSection: string;
    dots: string;
    toolCount: string;
    processingText: string;
}

const SPECIALIST_THEMES: Record<string, SpecialistTheme> = {
    Dispatch: {
        label: "Executor",
        icon: "\u26A1",
        border: { running: "border-purple-500/30", failed: "border-red-500/30", normal: "border-purple-500/20" },
        bg: { running: "bg-purple-950/20 shadow-[0_0_20px_rgba(168,85,247,0.08)]", failed: "bg-red-950/10", normal: "bg-purple-950/10" },
        strip: { running: "bg-purple-500 animate-pulse", failed: "bg-red-500", normal: "bg-purple-500" },
        badge: { text: "text-purple-300", bg: "bg-purple-500/15", border: "border-purple-500/20" },
        textMuted: "text-purple-400/70",
        borderSection: "border-purple-500/10",
        dots: "bg-purple-400",
        toolCount: "text-purple-400/70",
        processingText: "text-purple-300/60",
    },
    Explore: {
        label: "Explorer",
        icon: "\uD83D\uDD0D",
        border: { running: "border-blue-500/30", failed: "border-red-500/30", normal: "border-blue-500/20" },
        bg: { running: "bg-blue-950/20 shadow-[0_0_20px_rgba(59,130,246,0.08)]", failed: "bg-red-950/10", normal: "bg-blue-950/10" },
        strip: { running: "bg-blue-500 animate-pulse", failed: "bg-red-500", normal: "bg-blue-500" },
        badge: { text: "text-blue-300", bg: "bg-blue-500/15", border: "border-blue-500/20" },
        textMuted: "text-blue-400/70",
        borderSection: "border-blue-500/10",
        dots: "bg-blue-400",
        toolCount: "text-blue-400/70",
        processingText: "text-blue-300/60",
    },
    Implement: {
        label: "Implementer",
        icon: "\uD83D\uDD27",
        border: { running: "border-emerald-500/30", failed: "border-red-500/30", normal: "border-emerald-500/20" },
        bg: { running: "bg-emerald-950/20 shadow-[0_0_20px_rgba(16,185,129,0.08)]", failed: "bg-red-950/10", normal: "bg-emerald-950/10" },
        strip: { running: "bg-emerald-500 animate-pulse", failed: "bg-red-500", normal: "bg-emerald-500" },
        badge: { text: "text-emerald-300", bg: "bg-emerald-500/15", border: "border-emerald-500/20" },
        textMuted: "text-emerald-400/70",
        borderSection: "border-emerald-500/10",
        dots: "bg-emerald-400",
        toolCount: "text-emerald-400/70",
        processingText: "text-emerald-300/60",
    },
    Design: {
        label: "Architect",
        icon: "\uD83D\uDCD0",
        border: { running: "border-orange-500/30", failed: "border-red-500/30", normal: "border-orange-500/20" },
        bg: { running: "bg-orange-950/20 shadow-[0_0_20px_rgba(249,115,22,0.08)]", failed: "bg-red-950/10", normal: "bg-orange-950/10" },
        strip: { running: "bg-orange-500 animate-pulse", failed: "bg-red-500", normal: "bg-orange-500" },
        badge: { text: "text-orange-300", bg: "bg-orange-500/15", border: "border-orange-500/20" },
        textMuted: "text-orange-400/70",
        borderSection: "border-orange-500/10",
        dots: "bg-orange-400",
        toolCount: "text-orange-400/70",
        processingText: "text-orange-300/60",
    },
    Test: {
        label: "Tester",
        icon: "\uD83E\uDDEA",
        border: { running: "border-teal-500/30", failed: "border-red-500/30", normal: "border-teal-500/20" },
        bg: { running: "bg-teal-950/20 shadow-[0_0_20px_rgba(20,184,166,0.08)]", failed: "bg-red-950/10", normal: "bg-teal-950/10" },
        strip: { running: "bg-teal-500 animate-pulse", failed: "bg-red-500", normal: "bg-teal-500" },
        badge: { text: "text-teal-300", bg: "bg-teal-500/15", border: "border-teal-500/20" },
        textMuted: "text-teal-400/70",
        borderSection: "border-teal-500/10",
        dots: "bg-teal-400",
        toolCount: "text-teal-400/70",
        processingText: "text-teal-300/60",
    },
};

// Default fallback (same as Dispatch)
const DEFAULT_THEME = SPECIALIST_THEMES.Dispatch;

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

function parseFileChanges(resultText: string): { icon: string; file: string; type: string }[] {
    const changes: { icon: string; file: string; type: string }[] = [];
    const regex = /^\s*([~+\-!])\s+(\S+)\s*\((\w+)\)/gm;
    let match;
    while ((match = regex.exec(resultText)) !== null) {
        const [, symbol, file, type] = match;
        const icon = symbol === '+' ? '🟢' : symbol === '-' ? '🔴' : '🟡';
        changes.push({ icon, file, type });
    }
    return changes;
}

function parseExecutorReport(resultText: string): string {
    const parts = resultText.split('### Files Changed');
    let report = parts[0];
    report = report.replace(/^##\s+Dispatch\s+#\d+\s+Result\s*/m, '');
    report = report.replace(/^###\s+Executor Report\s*/m, '');
    report = report.replace(/\n*Use `Read`[\s\S]*$/, '');
    report = report.replace(/\n*Dispatches used:[\s\S]*$/, '');
    return report.trim();
}

function SubToolCallItem({ sub, index }: {
    sub: {
        id?: string;
        name: string;
        arguments: Record<string, unknown>;
        result?: unknown;
        error?: string;
        duration?: number;
        status: "running" | "completed" | "failed";
    };
    index: number;
}) {
    const [isExpanded, setIsExpanded] = useState(false);
    const isSubRunning = sub.status === "running";
    const isSubFailed = sub.status === "failed";

    let subSummary = "";
    const pathArg = sub.arguments?.path || sub.arguments?.file_path || sub.arguments?.target_file;
    const cmdArg = sub.arguments?.command || sub.arguments?.cmd;
    if (typeof pathArg === 'string') {
        const parts = pathArg.split('/');
        subSummary = parts.pop() || pathArg;
        // Append line range for Read tool with offset/limit
        if (sub.name === "Read") {
            const offset = sub.arguments?.offset as number | undefined;
            const limit = sub.arguments?.limit as number | undefined;
            if (offset && limit) subSummary += ` :${offset}-${offset + limit}`;
            else if (offset) subSummary += ` :${offset}+`;
            else if (limit) subSummary += ` :1-${limit}`;
        }
    } else if (typeof cmdArg === 'string') {
        subSummary = cmdArg.length > 60 ? cmdArg.slice(0, 60) + '...' : cmdArg;
    }

    const hasDetails = (sub.arguments && Object.keys(sub.arguments).length > 0) || sub.result != null || sub.error;

    return (
        <div className="rounded-md border border-white/[0.04] overflow-hidden">
            <div
                className={`flex items-center gap-2.5 py-1.5 px-2.5 transition-colors ${hasDetails ? 'cursor-pointer hover:bg-white/[0.03]' : ''}`}
                onClick={() => hasDetails && setIsExpanded(!isExpanded)}
            >
                <div className={`w-2 h-2 rounded-full shrink-0 ${isSubRunning
                    ? 'bg-yellow-400 animate-pulse shadow-[0_0_6px_rgba(250,204,21,0.5)]'
                    : isSubFailed
                        ? 'bg-red-400 shadow-[0_0_6px_rgba(248,113,113,0.4)]'
                        : 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.4)]'
                    }`} />
                <span className="text-[10px] font-mono text-gray-600 w-4 text-right shrink-0">{index + 1}.</span>
                <span className="text-[12px] font-mono text-gray-200 font-medium">{sub.name}</span>
                {subSummary && (
                    <>
                        <span className="text-gray-700 text-[10px]">/</span>
                        <span className="text-[11px] font-mono text-gray-500 truncate">{subSummary}</span>
                    </>
                )}
                <div className="flex-1" />
                {sub.duration != null && (
                    <span className="text-[10px] font-mono text-gray-600 shrink-0">{sub.duration}ms</span>
                )}
                {hasDetails && (
                    <svg className={`w-2.5 h-2.5 text-gray-600 transition-transform duration-200 shrink-0 ${isExpanded ? "rotate-180" : ""}`}
                        fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                )}
            </div>
            {isExpanded && hasDetails && (
                <div className="border-t border-white/[0.04] bg-black/20 px-3 py-2">
                    <ToolDisplay
                        tool={{ id: sub.id, name: sub.name, args: sub.arguments, result: sub.result, error: sub.error, status: sub.status, duration: sub.duration }}
                        isExpanded={true}
                    />
                </div>
            )}
        </div>
    );
}

export function DispatchCard({ tool }: DispatchCardProps) {
    const [isExpanded, setIsExpanded] = useState(true);
    const [isTaskExpanded, setIsTaskExpanded] = useState(false);

    const theme = SPECIALIST_THEMES[tool.name] || DEFAULT_THEME;
    const isRunning = tool.status === "running";
    const isFailed = tool.status === "failed";

    // Recover sub-calls from tool.result if they are missing (e.g. after refresh)
    const toolResult = tool.result as any;
    const recoveredSubCalls = toolResult?.sub_calls || toolResult?.subCalls || [];
    const recoveredSubResults = toolResult?.sub_results || toolResult?.subResults || [];

    const subCalls = (tool.subCalls && tool.subCalls.length > 0) ? tool.subCalls : recoveredSubCalls;
    const subResults = (tool.subResults && tool.subResults.length > 0) ? tool.subResults : recoveredSubResults;

    const task = (tool.args?.task as string) || (tool.args?.prompt as string) || (tool.args?.context as string) || "";
    const taskPreview = task.length > 80 ? task.slice(0, 80) + "..." : task;
    const hasLongTask = task.length > 80;

    const resultText = typeof tool.result === 'string' ? tool.result : '';
    const fileChanges = parseFileChanges(resultText);
    const executorReport = parseExecutorReport(resultText);

    const subCallsWithStatus = subCalls.map((sc: any) => {
        // Handle both camelCase (ToolCall type) and snake_case (recovered from backend result)
        const callId = sc.id;
        const callName = sc.name || sc.tool;
        const callArgs = sc.arguments || sc.args || {};

        const matchedResult = subResults.find((sr: any) => sr.id === callId);
        return {
            id: callId,
            name: callName,
            arguments: callArgs,
            result: matchedResult?.result,
            error: matchedResult?.error,
            duration: matchedResult?.duration,
            status: (matchedResult ? (matchedResult.error ? "failed" : "completed") : "running") as "running" | "completed" | "failed",
        };
    });

    const completedCount = subCallsWithStatus.filter((s: { status: string }) => s.status === 'completed').length;
    const failedCount = subCallsWithStatus.filter((s: { status: string }) => s.status === 'failed').length;

    // Resolve theme classes based on status
    const borderClass = isRunning ? theme.border.running : isFailed ? theme.border.failed : theme.border.normal;
    const bgClass = isRunning ? theme.bg.running : isFailed ? theme.bg.failed : theme.bg.normal;
    const stripClass = isRunning ? theme.strip.running : isFailed ? theme.strip.failed : theme.strip.normal;

    return (
        <div className={`overflow-hidden max-w-full rounded-xl border transition-all duration-300 relative ${borderClass} ${bgClass}`}>
            <div className={`absolute left-0 top-0 bottom-0 w-1 ${stripClass}`} />

            <div className="px-4 py-3 pl-5 flex items-center justify-between cursor-pointer select-none" onClick={() => setIsExpanded(!isExpanded)}>
                <div className="flex items-center gap-3 min-w-0">
                    <div className="flex items-center gap-2">
                        {isRunning ? (
                            <div className="flex gap-0.5">
                                <div className={`w-1.5 h-1.5 rounded-full ${theme.dots} animate-bounce`} style={{ animationDelay: '0ms' }} />
                                <div className={`w-1.5 h-1.5 rounded-full ${theme.dots} animate-bounce`} style={{ animationDelay: '150ms' }} />
                                <div className={`w-1.5 h-1.5 rounded-full ${theme.dots} animate-bounce`} style={{ animationDelay: '300ms' }} />
                            </div>
                        ) : isFailed ? (
                            <span className="text-red-400 text-sm">{'\u2717'}</span>
                        ) : (
                            <span className="text-emerald-400 text-sm">{'\u2713'}</span>
                        )}
                    </div>
                    <span className={`text-[10px] uppercase font-bold ${theme.badge.text} ${theme.badge.bg} px-2 py-0.5 rounded-full border ${theme.badge.border} tracking-wider whitespace-nowrap`}>{theme.icon} {theme.label}</span>
                    <span className="text-[12px] text-gray-400 truncate">{taskPreview}</span>
                </div>
                <div className="flex items-center gap-3 shrink-0 ml-2">
                    {subCalls.length > 0 && (
                        <span className={`text-[10px] font-mono ${theme.toolCount}`}>
                            {isRunning ? `${completedCount}/${subCalls.length} tools` : `${subCalls.length} tool${subCalls.length > 1 ? 's' : ''}`}
                            {failedCount > 0 && <span className="text-red-400 ml-1">({failedCount} failed)</span>}
                        </span>
                    )}
                    {tool.duration && <span className="text-[10px] font-mono text-gray-600">{(tool.duration / 1000).toFixed(1)}s</span>}
                    <svg className={`w-3 h-3 text-gray-500 transition-transform duration-200 ${isExpanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                </div>
            </div>

            {isExpanded && (
                <div className={`border-t ${theme.borderSection} px-4 pl-5 py-3 space-y-3`}>
                    {task && (
                        <div className={`rounded-lg border ${theme.borderSection} bg-black/20 overflow-hidden`}>
                            <div className={`px-3 py-2 flex items-center gap-2 ${hasLongTask ? 'cursor-pointer hover:bg-white/[0.02]' : ''}`} onClick={() => hasLongTask && setIsTaskExpanded(!isTaskExpanded)}>
                                <span className={`text-[10px] uppercase tracking-wider ${theme.textMuted} font-medium shrink-0`}>{'\uD83D\uDCCB'} Task</span>
                                <div className="flex-1" />
                                {hasLongTask && (
                                    <svg className={`w-2.5 h-2.5 text-gray-600 transition-transform duration-200 shrink-0 ${isTaskExpanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                                    </svg>
                                )}
                            </div>
                            <div className={`px-3 pb-2 relative ${!isTaskExpanded && hasLongTask ? 'max-h-[3.5em] overflow-hidden' : ''}`}>
                                <div className="text-[12px] text-gray-300 font-mono whitespace-pre-wrap leading-relaxed">
                                    {isTaskExpanded || !hasLongTask ? task : taskPreview}
                                </div>
                                {!isTaskExpanded && hasLongTask && (
                                    <div className="absolute bottom-0 left-0 right-0 h-6 bg-gradient-to-t from-black/60 to-transparent" />
                                )}
                            </div>
                        </div>
                    )}

                    {subCallsWithStatus.length > 0 && (
                        <div className="space-y-1">
                            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5 font-medium px-1">{'\uD83D\uDD27'} Tool Calls</div>
                            {subCallsWithStatus.map((sub: any, i: number) => (
                                <SubToolCallItem key={sub.id || i} sub={sub} index={i} />
                            ))}
                        </div>
                    )}

                    {isRunning && subCallsWithStatus.length === 0 && (
                        <div className={`flex items-center gap-2 py-2 text-[12px] ${theme.processingText}`}>
                            <div className="w-3 h-3 border-2 border-white/20 border-t-white/60 rounded-full animate-spin" />
                            <span>{theme.label} processing...</span>
                        </div>
                    )}

                    {executorReport && !isRunning && (
                        <div className="rounded-lg border border-emerald-500/10 bg-emerald-950/10 overflow-hidden">
                            <div className="px-3 py-2">
                                <span className="text-[10px] uppercase tracking-wider text-emerald-400/70 font-medium">{'\uD83D\uDCDD'} Summary</span>
                            </div>
                            <div className="px-3 pb-3 text-[12px] text-gray-300">
                                <MarkdownRenderer content={executorReport} className="prose-invert prose-sm prose-p:text-[12px] prose-p:leading-relaxed prose-p:mb-2 prose-table:text-[11px] prose-pre:text-[11px]" />
                            </div>
                        </div>
                    )}

                    {fileChanges.length > 0 && (
                        <div className={`pt-1 border-t ${theme.borderSection}`}>
                            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5 font-medium">{'\uD83D\uDCC1'} Files Changed</div>
                            <div className="flex flex-wrap gap-1.5">
                                {fileChanges.map((fc, i) => (
                                    <span key={i} className="inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded-md bg-black/30 border border-white/5 text-gray-300">
                                        <span>{fc.icon}</span>
                                        <span>{fc.file}</span>
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}

                    {tool.error && (
                        <div className="pt-1 border-t border-red-500/10">
                            <div className="text-[11px] text-red-400 bg-red-500/10 px-3 py-2 rounded-md border border-red-500/20 font-mono">{tool.error}</div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
