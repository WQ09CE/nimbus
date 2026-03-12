/**
 * TokenFooter — Displays cumulative token usage statistics.
 *
 * Shows input/output/cache tokens and total cost when data is available.
 * Subscribes to chat store's tokenUsage state (fed by SSE usage_update events).
 */
"use client";

import React from "react";
import { useChatStore, type TokenUsageData } from "@/stores/chat-store";

function formatTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return n.toString();
}

function formatCost(cost: number): string {
    if (cost < 0.001) return cost > 0 ? "<$0.001" : "$0.00";
    return `$${cost.toFixed(3)}`;
}

export function TokenFooter() {
    const tokenUsage = useChatStore((s) => s.tokenUsage);

    if (!tokenUsage || tokenUsage.total === 0) return null;

    const ctx = tokenUsage.context_window;

    return (
        <div className="token-footer flex items-center flex-wrap gap-x-2 gap-y-1">
            {ctx && ctx.maximum > 0 && (
                <div 
                    className="flex items-center gap-1.5 px-2 py-0.5 bg-black/20 rounded border border-white/5 mr-2" 
                    title={`Context Window: ${ctx.current} / ${ctx.maximum} tokens`}
                >
                    <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold">CTX</span>
                    <div className="w-16 h-1.5 bg-zinc-800 rounded-full overflow-hidden flex">
                        <div 
                            className={`h-full ${ctx.current / ctx.maximum > 0.85 ? 'bg-red-400' : ctx.current / ctx.maximum > 0.6 ? 'bg-amber-400' : 'bg-emerald-400'}`} 
                            style={{ width: `${Math.min(100, Math.max(0, (ctx.current / ctx.maximum) * 100))}%` }} 
                        />
                    </div>
                    <span className="text-xs font-mono text-zinc-400">
                        {Math.round((ctx.current / ctx.maximum) * 100)}%
                    </span>
                </div>
            )}
            <span className="token-stat" title="Input tokens">
                ↑ {formatTokens(tokenUsage.input)}
            </span>
            <span className="token-stat" title="Output tokens">
                ↓ {formatTokens(tokenUsage.output)}
            </span>
            {tokenUsage.cache_read > 0 && (
                <span className="token-stat token-cache" title="Cache read tokens">
                    ⚡ {formatTokens(tokenUsage.cache_read)}
                </span>
            )}
            {tokenUsage.cost?.total > 0 && (
                <span className="token-stat token-cost" title="Total cost">
                    {formatCost(tokenUsage.cost.total)}
                </span>
            )}
        </div>
    );
}
