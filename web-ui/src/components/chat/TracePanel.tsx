"use client";

/**
 * TracePanel — the session's event log rendered as a structured timeline.
 *
 * Reads /api/v1/sessions/{id}/log (the authoritative jsonl trace) and shows:
 * - stats header (turns / steps / tools / compactions) + invariant health badge
 * - collapsible turn sections with end-reason badges and durations
 * - compaction surface-replace cards (mode, kept count, summary)
 * - seed cards (fork lineage)
 * - graded crash-recovery chips (TOOL_OUTCOME_UNKNOWN amber / TOOL_NOT_STARTED gray)
 * - per-turn "fork from here" (replay via at_seq) + raw jsonl download
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  SessionLogEvent,
  SessionLogResponse,
  forkSession,
  getSessionLog,
  sessionLogDownloadUrl,
} from "@/lib/api/sessions";
import { useChatStore } from "@/stores";

interface TracePanelProps {
  sessionId: string;
}

interface TurnGroup {
  turn: number | null; // null = events before the first turn (e.g. seed)
  startSeq: number;
  endSeq: number | null; // seq of turn/end, null = still open
  reason: string | null;
  synthetic: boolean;
  durationSec: number | null;
  events: SessionLogEvent[];
}

const REASON_STYLE: Record<string, string> = {
  completed: "text-emerald-400 bg-emerald-400/10 border-emerald-400/20",
  aborted: "text-amber-400 bg-amber-400/10 border-amber-400/20",
  error: "text-red-400 bg-red-400/10 border-red-400/20",
  "max-iterations": "text-orange-400 bg-orange-400/10 border-orange-400/20",
  interrupted: "text-violet-400 bg-violet-400/10 border-violet-400/20",
};

function groupTurns(events: SessionLogEvent[]): TurnGroup[] {
  const groups: TurnGroup[] = [];
  let current: TurnGroup | null = null;
  let preamble: TurnGroup | null = null;

  for (const e of events) {
    if (e.type === "turn/start") {
      current = {
        turn: e.data.turn ?? null,
        startSeq: e.seq,
        endSeq: null,
        reason: null,
        synthetic: false,
        durationSec: null,
        events: [],
      };
      groups.push(current);
    } else if (e.type === "turn/end") {
      if (current) {
        current.endSeq = e.seq;
        current.reason = e.data?.reason?.kind ?? null;
        current.synthetic = !!e.data?.synthetic;
        const start = events.find((x) => x.seq === current!.startSeq);
        if (start) current.durationSec = Math.max(0, e.time - start.time);
        current = null;
      }
    } else if (current) {
      current.events.push(e);
    } else {
      // Events outside any turn (seed/applied, stray messages)
      if (!preamble) {
        preamble = {
          turn: null, startSeq: e.seq, endSeq: null, reason: null,
          synthetic: false, durationSec: null, events: [],
        };
        groups.unshift(preamble);
      }
      preamble.events.push(e);
    }
  }
  return groups;
}

function preview(content: unknown, max = 160): string {
  let text = "";
  if (typeof content === "string") text = content;
  else if (Array.isArray(content)) {
    text = content
      .map((b: any) => (typeof b === "string" ? b : b?.text || (b?.type === "image" ? "[image]" : "")))
      .join(" ");
  } else if (content != null) text = JSON.stringify(content);
  text = text.replace(/\s+/g, " ").trim();
  return text.length > max ? text.slice(0, max) + "…" : text;
}

function RecoveryChip({ code }: { code: string }) {
  const unknown = code === "TOOL_OUTCOME_UNKNOWN";
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border ${
        unknown
          ? "text-amber-400 bg-amber-400/10 border-amber-400/30"
          : "text-gray-400 bg-gray-400/10 border-gray-400/30"
      }`}
      title={
        unknown
          ? "崩溃时该调用可能已在执行——先验证外部状态再重试"
          : "崩溃前该调用尚未开始——可以安全重试"
      }
    >
      {unknown ? "⚠ 结果未知" : "○ 未执行"}
    </span>
  );
}

function EventRow({ event }: { event: SessionLogEvent }) {
  const [expanded, setExpanded] = useState(false);
  const t = event.type;
  const msg = event.data?.message;

  if (t === "step/start") {
    return (
      <div className="flex items-center gap-2 text-[10px] text-gray-600 uppercase tracking-wider pt-2">
        <span className="h-px flex-1 bg-nimbus-border" />
        step {event.data.step}
        <span className="h-px flex-1 bg-nimbus-border" />
      </div>
    );
  }
  if (t === "step/end") return null;

  if (t === "seed/applied") {
    const lineage = event.data?.lineage || {};
    return (
      <div className="rounded-lg border border-violet-400/20 bg-violet-400/5 p-2.5 text-xs">
        <div className="flex items-center gap-2 text-violet-300 font-semibold">
          🌱 Seed（fork 起点）
        </div>
        <div className="mt-1 text-gray-400 font-mono text-[11px]">
          parent: {lineage.parent || "?"}
          {lineage.at_seq != null && <span> · at_seq {lineage.at_seq}</span>}
          <span> · {event.data?.messages?.length ?? 0} 条初始消息</span>
        </div>
        {event.data?.summary && (
          <div className="mt-1.5 text-gray-500 whitespace-pre-wrap break-words">
            {preview(event.data.summary, 300)}
          </div>
        )}
      </div>
    );
  }

  if (t === "plan/updated") {
    const plan: string = event.data?.plan || "";
    const items = plan.split("\n").filter((l) => l.startsWith("- ["));
    const done = items.filter((l) => l.startsWith("- [x]") || l.startsWith("- [X]")).length;
    return (
      <div className="rounded-lg border border-emerald-400/20 bg-emerald-400/5 p-2.5 text-xs">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2 text-emerald-300 font-semibold w-full text-left"
        >
          📋 计划更新
          <span className="text-[10px] font-normal text-gray-500">
            {done}/{items.length} 完成
          </span>
          <span className="ml-auto text-gray-600">{expanded ? "▾" : "▸"}</span>
        </button>
        {(expanded || items.length <= 6) && (
          <div className="mt-1.5 space-y-0.5">
            {items.map((l, i) => {
              const isDone = l.startsWith("- [x]") || l.startsWith("- [X]");
              return (
                <div key={i} className={`flex items-start gap-1.5 ${isDone ? "text-gray-600 line-through" : "text-gray-400"}`}>
                  <span className="shrink-0">{isDone ? "☑" : "☐"}</span>
                  <span>{l.replace(/^- \[.\] /, "")}</span>
                </div>
              );
            })}
          </div>
        )}
        {expanded && plan.includes("### Notes") && (
          <div className="mt-2 text-gray-500 whitespace-pre-wrap border-t border-emerald-400/10 pt-2">
            {plan.split("### Notes")[1]?.trim()}
          </div>
        )}
      </div>
    );
  }

  if (t === "compaction/applied") {
    return (
      <div className="rounded-lg border border-sky-400/20 bg-sky-400/5 p-2.5 text-xs">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2 text-sky-300 font-semibold w-full text-left"
        >
          ⚡ 上下文压缩
          <span className="text-[10px] font-normal text-gray-500">
            {event.data.mode} · 保留 {event.data.kept} 条
          </span>
          <span className="ml-auto text-gray-600">{expanded ? "▾" : "▸"}</span>
        </button>
        {expanded && event.data.summary && (
          <div className="mt-2 text-gray-400 whitespace-pre-wrap break-words border-t border-sky-400/10 pt-2 max-h-64 overflow-y-auto">
            {event.data.summary}
          </div>
        )}
      </div>
    );
  }

  if (t === "user/message") {
    return (
      <div className="text-xs text-gray-300">
        <span className="text-sky-400 font-semibold mr-1.5">›</span>
        {preview(msg?.content)}
      </div>
    );
  }

  if (t === "assistant/message") {
    const calls = msg?.tool_calls || [];
    return (
      <div className="text-xs text-gray-400">
        <span className="text-violet-400 font-semibold mr-1.5">‹</span>
        {calls.length > 0 ? (
          <span className="font-mono text-[11px]">
            {calls.map((c: any) => c?.function?.name || c?.name || "tool").join(" · ")}
          </span>
        ) : (
          preview(msg?.content)
        )}
      </div>
    );
  }

  if (t === "tool/result") {
    const code = event.data?.code || msg?.meta?.code;
    const synthetic = event.data?.synthetic || msg?.meta?.synthetic;
    return (
      <div className="text-xs text-gray-500 flex items-start gap-1.5">
        <span className="text-emerald-500/70 font-mono text-[11px] shrink-0">
          ⚙ {msg?.name || "tool"}
        </span>
        {synthetic && code ? (
          <RecoveryChip code={code} />
        ) : (
          <span className="truncate">{preview(msg?.content, 80)}</span>
        )}
      </div>
    );
  }

  return null;
}

export function TracePanel({ sessionId }: TracePanelProps) {
  const [log, setLog] = useState<SessionLogResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const [forking, setForking] = useState<number | null>(null);
  const switchSession = useChatStore((s) => s.switchSession);
  const isStreaming = useChatStore((s) => s.isStreaming);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setLog(await getSessionLog(sessionId));
    } catch (e: any) {
      setError(e?.detail || e?.message || "加载失败");
      setLog(null);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auto-refresh while the agent is streaming (log grows at causal flushes)
  useEffect(() => {
    if (!isStreaming) return;
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [isStreaming, refresh]);

  const turns = useMemo(() => (log ? groupTurns(log.events) : []), [log]);

  const handleFork = async (atSeq: number | undefined, label: string) => {
    setForking(atSeq ?? -1);
    try {
      const forked = await forkSession(sessionId, atSeq);
      // Jump straight into the forked session
      switchSession(forked as any);
    } catch (e) {
      console.error(`Fork ${label} failed:`, e);
    } finally {
      setForking(null);
    }
  };

  const healthy = log && !log.corrupt && log.invariant_violations.length === 0;

  return (
    <div className="h-full flex flex-col" data-testid="trace-panel">
      {/* Header */}
      <div className="flex-shrink-0 px-3 py-2.5 border-b border-nimbus-border">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-gray-200">Trace</h3>
          {log && (
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${
                healthy
                  ? "text-emerald-400 bg-emerald-400/10 border-emerald-400/20"
                  : "text-red-400 bg-red-400/10 border-red-400/20"
              }`}
              title={
                log.corrupt
                  ? `日志损坏: ${log.corrupt}`
                  : log.invariant_violations.join("\n") || "invariants 全部通过"
              }
            >
              {log.corrupt
                ? "corrupt"
                : log.invariant_violations.length === 0
                  ? "✓ consistent"
                  : `${log.invariant_violations.length} violations`}
            </span>
          )}
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={refresh}
              className="p-1.5 text-gray-500 hover:text-gray-200 rounded-md hover:bg-white/5 transition-colors"
              title="刷新"
            >
              <svg className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h5M20 20v-5h-5M4 9a8 8 0 0114-3m2 8a8 8 0 01-14 3" />
              </svg>
            </button>
            <a
              href={sessionLogDownloadUrl(sessionId)}
              download
              className="p-1.5 text-gray-500 hover:text-gray-200 rounded-md hover:bg-white/5 transition-colors"
              title="下载完整轨迹 (jsonl)"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
              </svg>
            </a>
          </div>
        </div>

        {/* Stats row */}
        {log && (
          <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] font-mono">
            {[
              ["turns", log.stats.turns],
              ["steps", log.stats.steps],
              ["tools", log.stats.tool_results],
              ["compact", log.stats.compactions],
              ["events", log.stats.events],
            ].map(([k, v]) => (
              <span key={k} className="px-1.5 py-0.5 rounded bg-nimbus-surface border border-nimbus-border text-gray-400">
                {k} <span className="text-gray-200 font-semibold">{v}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
        {error && (
          <div className="text-xs text-gray-500 text-center py-8">
            {error}
            <div className="mt-1 text-gray-600">（老会话可能没有事件日志）</div>
          </div>
        )}
        {!error && log && turns.length === 0 && (
          <div className="text-xs text-gray-600 text-center py-8">日志为空</div>
        )}

        {turns.map((g, i) => {
          const isCollapsed = collapsed.has(i);
          const reasonStyle = g.reason ? REASON_STYLE[g.reason] || REASON_STYLE.error : "";
          return (
            <div key={i} className="rounded-lg border border-nimbus-border bg-nimbus-surface/50">
              {/* Turn header */}
              <div className="flex items-center gap-2 px-2.5 py-2">
                <button
                  onClick={() => {
                    const next = new Set(collapsed);
                    if (isCollapsed) next.delete(i); else next.add(i);
                    setCollapsed(next);
                  }}
                  className="flex items-center gap-2 flex-1 min-w-0 text-left"
                >
                  <span className="text-gray-600 text-[10px]">{isCollapsed ? "▸" : "▾"}</span>
                  <span className="text-xs font-semibold text-gray-300">
                    {g.turn === null ? "Preamble" : `Turn ${g.turn}`}
                  </span>
                  {g.reason && (
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${reasonStyle}`}>
                      {g.reason}{g.synthetic ? " (修复)" : ""}
                    </span>
                  )}
                  {g.endSeq === null && g.turn !== null && (
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold border text-sky-400 bg-sky-400/10 border-sky-400/20 animate-pulse">
                      running
                    </span>
                  )}
                  {g.durationSec !== null && (
                    <span className="text-[10px] text-gray-600 font-mono">
                      {g.durationSec < 60 ? `${g.durationSec.toFixed(1)}s` : `${(g.durationSec / 60).toFixed(1)}m`}
                    </span>
                  )}
                </button>
                {g.endSeq !== null && (
                  <button
                    onClick={() => handleFork(g.endSeq! + 1, `turn ${g.turn}`)}
                    disabled={forking !== null}
                    className="shrink-0 px-1.5 py-0.5 text-[10px] text-gray-500 hover:text-violet-300 border border-transparent hover:border-violet-400/30 hover:bg-violet-400/10 rounded transition-colors disabled:opacity-50"
                    title={`从 Turn ${g.turn} 结束处分叉新会话（replay at_seq=${g.endSeq! + 1}）`}
                  >
                    {forking === g.endSeq! + 1 ? "forking…" : "⑂ fork"}
                  </button>
                )}
              </div>
              {/* Turn body */}
              {!isCollapsed && (
                <div className="px-2.5 pb-2.5 space-y-1.5 border-t border-nimbus-border/50 pt-2">
                  {g.events.map((e) => (
                    <EventRow key={e.seq} event={e} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
