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

    return (
        <div className="token-footer">
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
