"use client";

import React, { useState, useRef, useEffect } from 'react';
import { MarkdownRenderer } from '../MarkdownRenderer';
import { MediaView, normalizeMedia } from '../MediaView';
import { LiveTimer } from './LiveTimer';

export interface SpawnAgentCardProps {
    tool: {
        id?: string;
        name: string;
        args: any;
        result?: any;
        error?: string;
        status: "running" | "completed" | "failed";
        duration?: number;
        sub_events?: Record<string, any>[];
        ui_detail?: Record<string, any>;
    };
    defaultState?: "expanded" | "collapsed";
}

const SPECIALIST_THEMES: Record<string, { bg: string, border: string, text: string }> = {
    "Test Engineer": { bg: "bg-emerald-500/10", border: "border-emerald-500/30", text: "text-emerald-400" },
    "System Architect": { bg: "bg-purple-500/10", border: "border-purple-500/30", text: "text-purple-400" },
    "Code Reviewer": { bg: "bg-amber-500/10", border: "border-amber-500/30", text: "text-amber-400" },
    "default": { bg: "bg-blue-500/10", border: "border-blue-500/30", text: "text-blue-400" }
};

const ChevronRight = ({ className }: { className?: string }) => (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}><polyline points="9 18 15 12 9 6"></polyline></svg>
);

const ChevronDown = ({ className }: { className?: string }) => (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}><polyline points="6 9 12 15 18 9"></polyline></svg>
);

const Brain = ({ className }: { className?: string }) => (
    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/><path d="M17.599 6.5a3 3 0 0 0 .399-1.375"/><path d="M6.003 5.125A3 3 0 0 0 6.401 6.5"/><path d="M3.477 10.896a4 4 0 0 1 .585-.396"/><path d="M19.938 10.5a4 4 0 0 1 .585.396"/><path d="M6 18a4 4 0 0 1-1.967-.516"/><path d="M19.967 17.484A4 4 0 0 1 18 18"/></svg>
);

const CheckCircle = ({ className }: { className?: string }) => (
    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
);

const XCircle = ({ className }: { className?: string }) => (
    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
);

function SimpleBadge({ children, variant }: { children: React.ReactNode, variant: "default" | "outline" | "destructive" | "running" }) {
    let classes = "px-2 py-0.5 rounded text-xs font-semibold ";
    if (variant === "destructive") classes += "bg-red-500 text-white";
    else if (variant === "running") classes += "bg-blue-500/20 text-blue-300";
    else if (variant === "outline") classes += "border border-emerald-500/30 text-emerald-400";
    else classes += "bg-zinc-800 text-zinc-300";

    return <span className={classes}>{children}</span>;
}

export function SpawnAgentCard({ tool, defaultState = "expanded" }: SpawnAgentCardProps) {
    const role = tool.args?.role || "Sub-Agent";
    const theme = SPECIALIST_THEMES[role] || SPECIALIST_THEMES["default"];
    const isRunning = tool.status === "running";
    const isFailed = tool.status === "failed";
    
    const [isExpanded, setIsExpanded] = useState(defaultState !== "collapsed");
    
    // Auto-expand on completion
    const prevRunningRef = useRef(isRunning);
    useEffect(() => {
        if (prevRunningRef.current && !isRunning && defaultState !== "collapsed") {
            setIsExpanded(true);
        }
        prevRunningRef.current = isRunning;
    }, [isRunning, defaultState]);

    const subEvents = tool.sub_events || [];
    // Timeline step stats (exclude tool_start pairing events) for an at-a-glance
    // progress summary that's visible even when the card is collapsed.
    const steps = subEvents.filter(e => e.type !== 'tool_start');
    const stepCount = steps.length;
    const errorCount = steps.filter(e => e.status === 'ERROR').length;
    const finalMedia = normalizeMedia(tool.ui_detail?.media);

    return (
        <div className={`mt-2 mb-3 rounded-lg border ${theme.border} bg-background/50 overflow-hidden`}>
            {/* Header */}
            <div 
                className={`flex items-center px-4 py-3 cursor-pointer select-none transition-colors hover:bg-white/5 ${theme.bg}`}
                onClick={() => setIsExpanded(!isExpanded)}
            >
                {isExpanded ? (
                    <ChevronDown className={`mr-2 ${theme.text}`} />
                ) : (
                    <ChevronRight className={`mr-2 ${theme.text}`} />
                )}
                
                <div className="flex items-center space-x-2">
                    <span className={`font-semibold ${theme.text}`}>{role}</span>
                    {isRunning && <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse ml-2" />}
                </div>

                <div className="ml-auto flex items-center space-x-3 text-xs">
                    {/* Step-progress summary (visible collapsed) */}
                    {stepCount > 0 && (
                        <span className="text-zinc-500 font-mono">
                            {stepCount} {stepCount === 1 ? "step" : "steps"}
                            {errorCount > 0 && <span className="text-red-400/80"> · {errorCount}✕</span>}
                        </span>
                    )}

                    {isRunning ? (
                        <div className="flex items-center text-blue-400 font-mono">
                            <LiveTimer />
                        </div>
                    ) : (
                        <span className="text-zinc-500 font-mono">
                            {tool.duration ? `${(tool.duration / 1000).toFixed(1)}s` : ""}
                        </span>
                    )}

                    {isFailed ? (
                        <SimpleBadge variant="destructive">Failed</SimpleBadge>
                    ) : isRunning ? (
                        <SimpleBadge variant="running">Running</SimpleBadge>
                    ) : (
                        <SimpleBadge variant="outline">Completed</SimpleBadge>
                    )}
                </div>
            </div>

            {/* Content Body */}
            {isExpanded && (
                <div className="p-4 border-t border-white/5 space-y-4 text-sm text-zinc-300">
                    
                    {/* Goal / Task */}
                    <div className="bg-black/20 rounded p-3 border border-white/5">
                        <div className="text-xs text-zinc-500 mb-1 uppercase tracking-wider font-semibold">Goal</div>
                        <div className="font-medium text-zinc-300">
                            {tool.args?.goal || tool.args?.task || tool.args?.instruction || "No specific goal provided."}
                        </div>
                    </div>

                    {/* Timeline */}
                    {subEvents.length > 0 && (
                        <div>
                            <div className="text-xs text-zinc-500 mb-3 uppercase tracking-wider font-semibold">Execution Timeline</div>
                            <div className="space-y-3 relative before:absolute before:top-2 before:bottom-2 before:left-[11px] before:w-px before:bg-white/10 ml-2 pl-6">
                                {subEvents.filter(evt => evt.type !== 'tool_start').map((evt, idx) => {
                                    const isThinking = evt.type === 'thinking';
                                    const isError = evt.status === 'ERROR';
                                    const hasOutput = evt.output_preview && evt.output_preview.length > 0;
                                    const hasArgs = evt.args_summary || evt.args;
                                    const stepMedia = normalizeMedia(evt.media || evt.ui_detail?.media);
                                    // Find matching tool_start event for args display
                                    const toolStart = subEvents.find(e => e.type === 'tool_start' && e.tool === evt.tool && subEvents.indexOf(e) < subEvents.indexOf(evt));
                                    const argsToShow = toolStart?.args || evt.args;
                                    const argsSummary = toolStart?.args_summary || evt.args_summary || '';
                                    
                                    return (
                                        <details key={idx} className="relative group/step">
                                            <summary className="flex items-start gap-3 cursor-pointer list-none [&::-webkit-details-marker]:hidden select-none">
                                                {/* Adjusted absolute positioning to align with the vertical line at left-[11px] */}
                                                <div className="absolute left-[-21px] top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-background border border-white/10 shadow-sm z-10">
                                                    {isThinking ? (
                                                        <Brain className="text-purple-400 w-3 h-3" />
                                                    ) : isError ? (
                                                        <XCircle className="text-red-400 w-3 h-3" />
                                                    ) : (
                                                        <CheckCircle className="text-emerald-400 w-3 h-3" />
                                                    )}
                                                </div>
                                                <div className="flex-1 min-w-0 bg-black/10 rounded border border-white/5 p-2 text-xs hover:bg-black/20 transition-colors">
                                                    {isThinking ? (
                                                        <div className="text-zinc-400 italic flex items-center gap-2">
                                                            <span className="text-purple-400/80 font-medium">Thoughts:</span>
                                                            <span className="truncate">{evt.thought_preview}...</span>
                                                        </div>
                                                    ) : (
                                                        <div className="flex items-center justify-between">
                                                            <span className="font-mono text-zinc-300 flex items-center gap-2">
                                                                <span className="text-blue-400">[{evt.tool}]</span>
                                                                <span className="text-zinc-500">Step {evt.step}</span>
                                                                {argsSummary && (
                                                                    <span className="text-zinc-600 truncate max-w-[300px]">{argsSummary}</span>
                                                                )}
                                                            </span>
                                                            <div className="flex items-center gap-2">
                                                                <span className={`font-mono ${isError ? "text-red-400" : "text-emerald-400"}`}>
                                                                    {evt.status}
                                                                </span>
                                                                {(hasOutput || argsToShow || stepMedia.length > 0) && (
                                                                    <ChevronRight className="w-3 h-3 text-zinc-600 transition-transform group-open/step:rotate-90" />
                                                                )}
                                                            </div>
                                                        </div>
                                                    )}
                                                </div>
                                            </summary>
                                            {/* Expanded detail */}
                                            {!isThinking && (hasOutput || argsToShow || stepMedia.length > 0) && (
                                                <div className="ml-0 mt-1 space-y-1.5">
                                                    {stepMedia.length > 0 && (
                                                        <div className="bg-black/20 rounded border border-white/5 p-2">
                                                            <div className="text-zinc-600 text-[10px] uppercase tracking-wider mb-1.5 font-semibold">Media</div>
                                                            <MediaView media={stepMedia} />
                                                        </div>
                                                    )}
                                                    {argsToShow && Object.keys(argsToShow).length > 0 && (
                                                        <div className="bg-black/20 rounded border border-white/5 p-2 text-xs">
                                                            <div className="text-zinc-600 text-[10px] uppercase tracking-wider mb-1 font-semibold">Arguments</div>
                                                            <div className="font-mono text-zinc-400 space-y-0.5">
                                                                {Object.entries(argsToShow).map(([k, v]) => (
                                                                    <div key={k} className="flex gap-2">
                                                                        <span className="text-cyan-400/70 shrink-0">{k}:</span>
                                                                        <span className="text-zinc-500 break-all">{String(v).length > 200 ? String(v).slice(0, 200) + '...' : String(v)}</span>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    )}
                                                    {hasOutput && (
                                                        <div className="bg-black/20 rounded border border-white/5 p-2 text-xs">
                                                            <div className="text-zinc-600 text-[10px] uppercase tracking-wider mb-1 font-semibold">Output</div>
                                                            <pre className="font-mono text-zinc-400 whitespace-pre-wrap break-all max-h-[200px] overflow-y-auto">{evt.output_preview}</pre>
                                                        </div>
                                                    )}
                                                </div>
                                            )}
                                        </details>
                                    );
                                })}
                                {isRunning && (
                                    <div className="relative flex items-start gap-3 opacity-50 mt-3">
                                        <div className="absolute left-[-21px] top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-background border border-white/10 shadow-sm z-10">
                                            <div className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                                        </div>
                                        <div className="text-zinc-500 italic text-xs py-1.5">Agent is working...</div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Final Deliverable */}
                    {tool.status === 'completed' && (tool.result || finalMedia.length > 0) && (
                        <div>
                            <div className="text-xs text-zinc-500 mb-2 uppercase tracking-wider font-semibold">Final Deliverable</div>
                            {finalMedia.length > 0 && (
                                <div className="mb-2">
                                    <MediaView media={finalMedia} />
                                </div>
                            )}
                            {tool.result && (
                                <div className="bg-black/30 rounded border border-emerald-500/20 p-3 max-h-[400px] overflow-y-auto">
                                    <MarkdownRenderer content={typeof tool.result === 'string' ? tool.result : JSON.stringify(tool.result, null, 2)} />
                                </div>
                            )}
                        </div>
                    )}

                    {/* Error */}
                    {isFailed && tool.error && (
                        <div className="bg-red-500/10 border border-red-500/30 text-red-400 p-3 rounded font-mono text-sm whitespace-pre-wrap">
                            {tool.error}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
