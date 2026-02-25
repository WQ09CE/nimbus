"use client";

import React, { useState, useRef, useEffect } from 'react';
import type { ToolCall, ToolResult } from '@/lib/api';
import { MarkdownRenderer } from '../MarkdownRenderer';

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

export interface SubCallWithStatus {
    id?: string;
    name: string;
    arguments: Record<string, unknown>;
    result?: unknown;
    error?: string;
    duration?: number;
    status: "running" | "completed" | "failed";
}

// ─────────────────────────────────────────────
// Visual configuration for each specialist type
// ─────────────────────────────────────────────

interface SpecialistTheme {
    label: string;
    icon: string;
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
        icon: "⚡",
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
        icon: "🔍",
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
        icon: "🔧",
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
        icon: "📐",
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
        icon: "🧪",
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

const DEFAULT_THEME = SPECIALIST_THEMES.Dispatch;

// ─────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────

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
    /**
     * "collapsed" — header only (default for parallel tasks).
     * "expanded"  — header + tool call list visible immediately.
     */
    defaultState?: "collapsed" | "expanded";
    /**
     * Parallel-task mode: tighter padding, smaller fonts.
     */
    isParallel?: boolean;
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

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

function buildArgsSummary(name: string, args: Record<string, unknown>): string {
    if (name === "Read") {
        const fp = String(args.file_path || "");
        const offset = args.offset ? `:${args.offset}` : "";
        const limit = args.limit ? `-${Number(args.offset || 1) + Number(args.limit)}` : "";
        return `${fp}${offset}${limit}`;
    }
    if (name === "Bash") return String(args.command || "").slice(0, 80);
    if (name === "Write" || name === "Edit") return String(args.file_path || "");
    const first = Object.values(args)[0];
    return first != null ? String(first).slice(0, 60) : "";
}

// ─────────────────────────────────────────────
// SubCallRow — individual tool call row
// ─────────────────────────────────────────────

function SubCallRow({ sub, index, theme }: { sub: SubCallWithStatus; index: number; theme?: SpecialistTheme }) {
    const [isExpanded, setIsExpanded] = useState(false);

    const isRunning = sub.status === "running";
    const isFailed = sub.status === "failed";

    const summary = buildArgsSummary(sub.name, sub.arguments);
    const hasDetails = Object.keys(sub.arguments).length > 0 || sub.result != null || sub.error != null;

    const resultText =
        typeof sub.result === "string"
            ? sub.result
            : sub.result != null
                ? JSON.stringify(sub.result, null, 2)
                : null;

    const dotColor = isRunning
        ? "bg-yellow-400 animate-pulse shadow-[0_0_6px_rgba(250,204,21,0.5)]"
        : isFailed
            ? "bg-red-400 shadow-[0_0_6px_rgba(248,113,113,0.4)]"
            : "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.4)]";

    return (
        <div className="rounded-lg border border-white/[0.06] overflow-hidden bg-white/[0.02] transition-all">
            <button
                type="button"
                className={`w-full flex items-center gap-2 py-2 px-3 text-left transition-colors ${hasDetails ? "hover:bg-white/[0.04] cursor-pointer" : "cursor-default"}`}
                onClick={() => hasDetails && setIsExpanded(v => !v)}
                disabled={!hasDetails}
            >
                {/* Status dot */}
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotColor}`} />
                {/* Index */}
                <span className="text-[10px] font-mono text-gray-600 w-5 text-right shrink-0">
                    {index + 1}.
                </span>
                {/* Tool name */}
                <span className="text-[12px] font-mono text-gray-200 font-semibold shrink-0">
                    {sub.name}
                </span>
                {summary && (
                    <>
                        <span className="text-gray-700 text-[10px] shrink-0">/</span>
                        <span className="text-[11px] font-mono text-gray-500 truncate flex-1">
                            {summary}
                        </span>
                    </>
                )}
                {!summary && <span className="flex-1" />}
                {isRunning ? (
                    <span className="flex gap-0.5 shrink-0">
                        <span className={`w-1 h-1 rounded-full ${theme?.dots ?? "bg-purple-400"} animate-bounce`} style={{ animationDelay: '0ms' }} />
                        <span className={`w-1 h-1 rounded-full ${theme?.dots ?? "bg-purple-400"} animate-bounce`} style={{ animationDelay: '150ms' }} />
                        <span className={`w-1 h-1 rounded-full ${theme?.dots ?? "bg-purple-400"} animate-bounce`} style={{ animationDelay: '300ms' }} />
                    </span>
                ) : sub.duration != null ? (
                    <span className="font-mono text-[10px] text-gray-600 shrink-0">{sub.duration}ms</span>
                ) : null}
                {hasDetails && (
                    <svg
                        className={`w-3 h-3 text-gray-500 transition-transform duration-200 shrink-0 ${isExpanded ? "rotate-180" : ""}`}
                        fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
                    >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                )}
            </button>

            {/* Expanded detail */}
            {isExpanded && hasDetails && (
                <div className="border-t border-white/[0.05] bg-black/30 px-3 py-2.5 space-y-2">
                    {sub.error && (
                        <div className="text-[11px] font-mono text-red-400 bg-red-500/10 rounded px-2.5 py-1.5 border border-red-500/20 whitespace-pre-wrap break-words">
                            {sub.error}
                        </div>
                    )}
                    {Object.keys(sub.arguments).length > 0 && (
                        <details className="group">
                            <summary className="text-[10px] uppercase tracking-wider text-gray-500 cursor-pointer select-none hover:text-gray-400 transition-colors">
                                ⚙ Arguments
                            </summary>
                            <pre className="mt-1 text-[11px] font-mono text-gray-400 overflow-x-auto whitespace-pre-wrap break-all leading-relaxed">
                                {JSON.stringify(sub.arguments, null, 2)}
                            </pre>
                        </details>
                    )}
                    {resultText && (
                        <details open className="group">
                            <summary className="text-[10px] uppercase tracking-wider text-gray-500 cursor-pointer select-none hover:text-gray-400 transition-colors">
                                📤 Result
                            </summary>
                            <pre className="mt-1 text-[11px] font-mono text-gray-400 overflow-x-auto whitespace-pre-wrap break-all leading-relaxed max-h-60 overflow-y-auto">
                                {resultText}
                            </pre>
                        </details>
                    )}
                </div>
            )}
        </div>
    );
}

// ─────────────────────────────────────────────
// DispatchCard — Tri-state: Collapsed / Expanded
//
//  Collapsed  : header only — agent name, status badge, task preview, tool count/time
//  Expanded   : header + tool call list (SubCallRows) + summary report + file changes
//
//  Key behaviors:
//  • Running   → always starts collapsed; header shows live tool count progress
//  • Completed → auto-expands (unless defaultState="collapsed" e.g. parallel mode)
//  • Click header to toggle at any time (including while running)
//  • isParallel → tighter padding/font for stacked parallel-agent grids
// ─────────────────────────────────────────────

export function DispatchCard({ tool, defaultState = "expanded", isParallel = false }: DispatchCardProps) {
    const theme = SPECIALIST_THEMES[tool.name] || DEFAULT_THEME;
    const isRunning = tool.status === "running";
    const isFailed = tool.status === "failed";

    // Bi-state: collapsed | expanded
    // Running always starts collapsed for compact progress view
    const initialExpanded = !isRunning && defaultState === "expanded";
    const [isExpanded, setIsExpanded] = useState(initialExpanded);

    // When running → completed, auto-expand unless parallel/collapsed mode
    const prevRunningRef = useRef(isRunning);
    useEffect(() => {
        if (prevRunningRef.current && !isRunning) {
            if (defaultState !== "collapsed") {
                setIsExpanded(true);
            }
        }
        prevRunningRef.current = isRunning;
    }, [isRunning, defaultState]);

    // ── Sub-call data ──────────────────────────────────
    const toolResult = tool.result as any;
    const recoveredSubCalls = toolResult?.sub_calls || toolResult?.subCalls || [];
    const recoveredSubResults = toolResult?.sub_results || toolResult?.subResults || [];

    const subCalls = (tool.subCalls && tool.subCalls.length > 0) ? tool.subCalls : recoveredSubCalls;
    const subResults = (tool.subResults && tool.subResults.length > 0) ? tool.subResults : recoveredSubResults;

    const subCallsWithStatus: SubCallWithStatus[] = subCalls.map((sc: any, idx: number) => {
        const callId = sc.id;
        const callName = sc.name || sc.tool;
        const callArgs = sc.arguments || sc.args || {};
        const matchedResult = subResults.find((sr: any) => sr.id === callId);
        return {
            id: callId || `${callName}-${idx}`,
            name: callName,
            arguments: callArgs,
            result: matchedResult?.result,
            error: matchedResult?.error,
            duration: matchedResult?.duration,
            status: (matchedResult
                ? (matchedResult.error ? "failed" : "completed")
                : "running") as "running" | "completed" | "failed",
        };
    });

    // ── Derived values ────────────────────────────────
    const task = (tool.args?.task as string) || (tool.args?.prompt as string) || (tool.args?.context as string) || "";
    // Parallel mode: shorter preview; solo mode: up to 80 chars
    const maxTaskLen = isParallel ? 48 : 80;
    const taskPreview = task.length > maxTaskLen ? task.slice(0, maxTaskLen) + "…" : task;

    const resultText = typeof tool.result === 'string' ? tool.result : '';
    const fileChanges = parseFileChanges(resultText);
    const executorReport = parseExecutorReport(resultText);

    const completedCount = subCallsWithStatus.filter(s => s.status === 'completed').length;
    const failedCount = subCallsWithStatus.filter(s => s.status === 'failed').length;
    const totalTools = subCallsWithStatus.length;

    // Live-activity list auto-scrolls to bottom while running
    const subCallsRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (isRunning && isExpanded && subCallsRef.current) {
            subCallsRef.current.scrollTop = subCallsRef.current.scrollHeight;
        }
    }, [isRunning, isExpanded, totalTools]);

    // ── Theme classes ─────────────────────────────────
    const borderClass = isRunning ? theme.border.running : isFailed ? theme.border.failed : theme.border.normal;
    const bgClass = isRunning ? theme.bg.running : isFailed ? theme.bg.failed : theme.bg.normal;
    const stripClass = isRunning ? theme.strip.running : isFailed ? theme.strip.failed : theme.strip.normal;

    // Parallel mode: more compact header padding
    const headerPad = isParallel ? "px-3 py-2 pl-4" : "px-4 py-2.5 pl-5";

    return (
        <div className={`overflow-hidden max-w-full rounded-xl border transition-all duration-300 relative ${borderClass} ${bgClass}`}>
            {/* Left accent strip */}
            <div className={`absolute left-0 top-0 bottom-0 w-1 ${stripClass}`} />

            {/* ── Card Header (always visible) ── */}
            <div
                className={`${headerPad} flex items-center justify-between cursor-pointer hover:bg-white/[0.02] select-none transition-colors`}
                onClick={() => setIsExpanded(v => !v)}
            >
                <div className="flex items-center gap-2.5 min-w-0">
                    {/* Status indicator */}
                    <div className="flex items-center shrink-0">
                        {isRunning ? (
                            <div className="flex gap-0.5">
                                <div className={`w-1.5 h-1.5 rounded-full ${theme.dots} animate-bounce`} style={{ animationDelay: '0ms' }} />
                                <div className={`w-1.5 h-1.5 rounded-full ${theme.dots} animate-bounce`} style={{ animationDelay: '150ms' }} />
                                <div className={`w-1.5 h-1.5 rounded-full ${theme.dots} animate-bounce`} style={{ animationDelay: '300ms' }} />
                            </div>
                        ) : isFailed ? (
                            <span className="text-red-400 text-sm">✗</span>
                        ) : (
                            <span className="text-emerald-400 text-sm">✓</span>
                        )}
                    </div>

                    {/* Role badge */}
                    <span className={`text-[10px] uppercase font-bold ${theme.badge.text} ${theme.badge.bg} px-2 py-0.5 rounded-full border ${theme.badge.border} tracking-wider whitespace-nowrap shrink-0`}>
                        {theme.icon} {theme.label}
                    </span>

                    {/* Task preview */}
                    {taskPreview && (
                        <span className={`${isParallel ? "text-[11px]" : "text-[12px]"} text-gray-400 truncate min-w-0`}>
                            {taskPreview}
                        </span>
                    )}
                </div>

                {/* Right side meta */}
                <div className="flex items-center gap-2.5 shrink-0 ml-2">
                    {/* Tool count / progress */}
                    {isRunning ? (
                        totalTools > 0 ? (
                            <span className={`text-[10px] font-mono ${theme.toolCount}`}>
                                {completedCount}/{totalTools}
                            </span>
                        ) : (
                            <span className={`text-[10px] ${theme.processingText}`}>processing…</span>
                        )
                    ) : (
                        totalTools > 0 && (
                            <span className={`text-[10px] font-mono ${theme.toolCount}`}>
                                {totalTools} call{totalTools !== 1 ? 's' : ''}
                                {failedCount > 0 && <span className="text-red-400 ml-1">({failedCount}✗)</span>}
                            </span>
                        )
                    )}

                    {/* Duration */}
                    {!isRunning && tool.duration != null && (
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

            {/* ── Expanded body ── */}
            {isExpanded && (
                <div className={`border-t ${theme.borderSection}`}>

                    {/* Running + no sub-calls yet: spinner */}
                    {isRunning && totalTools === 0 && (
                        <div className={`flex items-center gap-2 px-5 py-3 text-[12px] ${theme.processingText}`}>
                            <div className="w-3 h-3 border-2 border-white/20 border-t-white/60 rounded-full animate-spin" />
                            <span>{theme.label} initializing…</span>
                        </div>
                    )}

                    {/* Tool calls list — visible while running AND after completion */}
                    {totalTools > 0 && (
                        <div
                            ref={subCallsRef}
                            className={`px-3 py-2 space-y-1 ${isRunning ? "max-h-[260px] overflow-y-auto" : ""}`}
                        >
                            {subCallsWithStatus.map((sub, idx) => (
                                <SubCallRow key={sub.id || idx} sub={sub} index={idx} theme={theme} />
                            ))}
                        </div>
                    )}

                    {/* ── Summary report (Markdown) — only after completion ── */}
                    {!isRunning && executorReport && (
                        <div className={`border-t ${theme.borderSection} px-5 pt-3 pb-2`}>
                            <div className={`text-[10px] uppercase tracking-wider ${theme.textMuted} font-medium mb-2`}>
                                📋 Summary
                            </div>
                            <div className="max-h-[300px] overflow-y-auto rounded-lg">
                                <MarkdownRenderer content={executorReport} />
                            </div>
                        </div>
                    )}

                    {/* ── File changes — only after completion ── */}
                    {!isRunning && fileChanges.length > 0 && (
                        <div className={`border-t ${theme.borderSection} px-5 pb-3 pt-2`}>
                            <div className={`text-[10px] uppercase tracking-wider ${theme.textMuted} font-medium mb-2`}>
                                📁 Files Changed
                            </div>
                            <div className={`rounded-lg border ${theme.borderSection} bg-black/20 divide-y divide-white/[0.04] overflow-hidden`}>
                                {fileChanges.map((change, i) => (
                                    <div key={i} className="flex items-center gap-2 px-3 py-1.5">
                                        <span className="text-[11px]">{change.icon}</span>
                                        <span className="text-[11px] font-mono text-gray-300 truncate flex-1">{change.file}</span>
                                        <span className={`text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded font-medium
                                            ${change.type === 'created' ? 'bg-emerald-500/15 text-emerald-400' :
                                              change.type === 'deleted' ? 'bg-red-500/15 text-red-400' :
                                              'bg-yellow-500/15 text-yellow-400'}`}>
                                            {change.type}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* ── Error ── */}
                    {isFailed && tool.error && (
                        <div className={`border-t ${theme.borderSection} px-5 pb-3 pt-2`}>
                            <div className="text-[11px] font-mono text-red-400 bg-red-500/10 rounded-lg px-3 py-2 border border-red-500/20 whitespace-pre-wrap break-words">
                                {tool.error}
                            </div>
                        </div>
                    )}

                </div>
            )}
        </div>
    );
}
