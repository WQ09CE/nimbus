"use client";

import React, { useMemo, useRef, useEffect, memo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import type { Message } from "@/stores/chat-store";
import { useChatStore } from "@/stores";
import { ChatMessage } from "./ChatMessage";
import { AgentProcess } from "./AgentProcess";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface VirtualItem {
  type: 'user' | 'system' | 'agent_group';
  key: string;
  // for user/system
  message?: Message;
  // for agent_group
  processSteps?: Message[];
  resultMsg?: Message | null;
}

interface ChatListProps {
  messages: Message[];
}

// ─────────────────────────────────────────────────────────────────────────────
// StreamingTail — isolated component that subscribes to streaming state.
// Rendered OUTSIDE the virtualizer so historical messages never re-render
// when streaming content changes.
// ─────────────────────────────────────────────────────────────────────────────

const StreamingTail = memo(function StreamingTail() {
  const isStreaming = useChatStore(s => s.isStreaming);
  const streamingContent = useChatStore(s => s.streamingContent);
  const streamingToolCalls = useChatStore(s => s.streamingToolCalls);
  const streamingToolResults = useChatStore(s => s.streamingToolResults);

  if (!isStreaming) return null;

  const streamingMsg: Message = {
    id: "streaming",
    role: "assistant",
    content: streamingContent,
    toolCalls: streamingToolCalls.length > 0 ? streamingToolCalls : undefined,
    toolResults: streamingToolResults.length > 0 ? streamingToolResults : undefined,
    timestamp: Date.now(),
  };

  return (
    <div className="max-w-4xl mx-auto px-4">
      <ChatMessage message={streamingMsg} isStreaming={true} />
    </div>
  );
});

const InjectionTail = memo(function InjectionTail() {
  const isStreaming = useChatStore(s => s.isStreaming);
  const messages = useChatStore(s => s.messages);

  if (!isStreaming) return null;

  const injections = messages.filter(m => m.isInjection && m.role === 'user');
  if (injections.length === 0) return null;

  return (
    <>
      {injections.map(msg => (
        <div key={msg.id} className="max-w-4xl mx-auto px-4 py-3">
          <ChatMessage message={msg} />
        </div>
      ))}
    </>
  );
});

// ─────────────────────────────────────────────────────────────────────────────
// ScrollToBottom — sticky button that appears when user scrolls up
// ─────────────────────────────────────────────────────────────────────────────

const ScrollToBottom = memo(function ScrollToBottom({ containerRef }: { containerRef: React.RefObject<HTMLDivElement | null> }) {
  const isStreaming = useChatStore(s => s.isStreaming);
  const messages = useChatStore(s => s.messages);
  const [show, setShow] = React.useState(false);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handleScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      setShow(distFromBottom > 200);
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, [containerRef]);

  if (!show || (!isStreaming && messages.length <= 3)) return null;

  return (
    <div className="sticky bottom-4 flex justify-center z-10 pointer-events-none">
      <button
        onClick={() => {
          const el = containerRef.current;
          if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
          setShow(false);
        }}
        className="pointer-events-auto bg-blue-600 hover:bg-blue-700 text-white text-xs px-3 py-2 rounded-full shadow-lg transition-all duration-200 flex items-center gap-2 border border-blue-500/50 hover:scale-105 active:scale-95"
      >
        <span>&darr;</span>
        <span className="hidden sm:inline">To Bottom</span>
        {isStreaming && (
          <div className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse" />
        )}
      </button>
    </div>
  );
});

// ─────────────────────────────────────────────────────────────────────────────
// HistoricalRow — memoized row wrapper; each row will not re-render unless
// its own data changes.
// ─────────────────────────────────────────────────────────────────────────────

interface HistoricalRowProps {
  item: VirtualItem;
}

const HistoricalRow = memo(function HistoricalRow({ item }: HistoricalRowProps) {
  if (item.type === 'user' || item.type === 'system') {
    return (
      <div className="max-w-4xl mx-auto px-4">
        <ChatMessage message={item.message!} />
      </div>
    );
  }

  if (item.type === 'agent_group') {
    return (
      <div className="max-w-4xl mx-auto px-4">
        {(item.processSteps?.length ?? 0) > 0 && (
          <AgentProcess steps={item.processSteps!} isStreaming={false} />
        )}
        {item.resultMsg && (
          <ChatMessage message={item.resultMsg} isStreaming={false} />
        )}
      </div>
    );
  }

  return null;
});

// ─────────────────────────────────────────────────────────────────────────────
// ChatList — main list with virtual scrolling
// ─────────────────────────────────────────────────────────────────────────────

export function ChatList({ messages }: ChatListProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const isStreaming = useChatStore(s => s.isStreaming);

  // Compute grouped items from historical messages only.
  // This memo only re-runs when messages array reference changes.
  const items = useMemo<VirtualItem[]>(() => {
    const result: VirtualItem[] = [];
    let currentAgentGroup: Message[] = [];

    // Tools whose completed state must be rendered by ChatMessage (Grid layout),
    // not by AgentProcess (linear list).
    const PARALLEL_TOOLS = new Set(["Dispatch", "Explore", "Implement", "Design", "Test", "ParallelDispatch"]);

    /** Returns true if the message contains any ParallelDispatch tool call */
    const hasParallelTool = (msg: Message): boolean =>
      !!msg.toolCalls?.some(tc => PARALLEL_TOOLS.has(tc.name));

    const flushAgentGroup = () => {
      if (currentAgentGroup.length === 0) return;

      const msgs = [...currentAgentGroup];
      currentAgentGroup = [];

      const lastMsg = msgs[msgs.length - 1];
      const lastHasTools = lastMsg.toolCalls && lastMsg.toolCalls.length > 0;

      // If the last message contains a ParallelDispatch tool, it must be
      // rendered by ChatMessage (which has the Grid layout) regardless of
      // whether it "has tools".  Treat it as resultMsg so it bypasses
      // AgentProcess entirely.
      const lastIsParallel = hasParallelTool(lastMsg);

      let processSteps: Message[];
      let resultMsg: Message | null;

      if (!lastHasTools || lastIsParallel) {
        if (msgs.length > 1) {
          resultMsg = lastMsg;
          processSteps = msgs.slice(0, -1);
        } else {
          resultMsg = lastMsg;
          processSteps = [];
        }
      } else {
        // Check whether any earlier message in the group contains a
        // ParallelDispatch — those should also be extracted as standalone
        // resultMsg rows so they keep their Grid layout.
        const parallelIdx = msgs.findIndex(hasParallelTool);
        if (parallelIdx >= 0) {
          // Everything before the parallel message goes to processSteps,
          // the parallel message itself becomes a dedicated resultMsg row,
          // and everything after it (if any) becomes a second agent_group.
          const before = msgs.slice(0, parallelIdx);
          const parallelMsg = msgs[parallelIdx];
          const after = msgs.slice(parallelIdx + 1);

          if (before.length > 0) {
            result.push({
              type: 'agent_group',
              key: `agent-${msgs[0].id}`,
              processSteps: before,
              resultMsg: null,
            });
          }

          // Parallel message as standalone resultMsg (rendered by ChatMessage)
          result.push({
            type: 'agent_group',
            key: `agent-parallel-${parallelMsg.id}`,
            processSteps: [],
            resultMsg: parallelMsg,
          });

          // Re-queue the remaining messages as a new group
          if (after.length > 0) {
            currentAgentGroup = after;
            flushAgentGroup();
          }
          return;
        }

        processSteps = msgs;
        resultMsg = null;
      }

      result.push({
        type: 'agent_group',
        key: `agent-${msgs[0].id}`,
        processSteps,
        resultMsg,
      });
    };

    messages.forEach((msg) => {
      if (msg.role === 'user') {
        // During streaming, skip injection messages — they'll be rendered
        // in InjectionTail after StreamingTail to preserve visual ordering.
        if (msg.isInjection) return;
        flushAgentGroup();
        result.push({ type: 'user', key: msg.id, message: msg });
      } else if (msg.role === 'assistant') {
        currentAgentGroup.push(msg);
      } else if (msg.role === 'system') {
        flushAgentGroup();
        result.push({ type: 'system', key: msg.id, message: msg });
      }
    });

    flushAgentGroup();
    return result;
  }, [messages, isStreaming]);

  // Virtual scrolling: estimate row height conservatively to avoid
  // unnecessary re-layouts. Each item's measured height will override.
  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (i) => {
      const item = items[i];
      if (!item) return 200;
      if (item.type === 'user') return 120;
      if (item.type === 'system') return 60;
      // agent_group: generous estimate — overshoot is better than undershoot
      // for scroll stability (total height shrinks rather than grows on measure)
      const steps = item.processSteps?.length ?? 0;
      const hasResult = item.resultMsg ? 200 : 0;
      return 200 + steps * 160 + hasResult;
    },
    overscan: 10,
  });

  // Scroll to bottom when messages are first loaded (page refresh / session switch)
  const prevItemsLen = useRef(0);
  useEffect(() => {
    const wasEmpty = prevItemsLen.current === 0;
    prevItemsLen.current = items.length;
    if (wasEmpty && items.length > 0) {
      // 双重 rAF：第一帧触发布局测量，第二帧读取真实 scrollHeight（iOS Safari ResizeObserver 延迟更长）
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const el = parentRef.current;
          if (el) el.scrollTop = el.scrollHeight;
        });
      });
      // setTimeout 兜底：确保 virtual scroller measureElement 回调已完成
      setTimeout(() => {
        const el = parentRef.current;
        if (el) el.scrollTop = el.scrollHeight;
      }, 150);
    }
  }, [items.length]);

  // Scroll to bottom on new messages or start of streaming (only if near bottom)
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom > 200) return;
    requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    });
  }, [messages, isStreaming]);

  return (
    // Outer scroll container — virtualizer attaches here
    <div
      ref={parentRef}
      className="flex-1 min-h-0 overflow-y-auto custom-scrollbar"
    >
      {/* Virtual list inner container */}
      <div
        style={{
          height: `${rowVirtualizer.getTotalSize()}px`,
          width: '100%',
          position: 'relative',
        }}
      >
        {rowVirtualizer.getVirtualItems().map((virtualRow) => (
          <div
            key={virtualRow.key}
            data-index={virtualRow.index}
            ref={rowVirtualizer.measureElement}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              transform: `translateY(${virtualRow.start}px)`,
            }}
          >
            <div className="py-3">
              <HistoricalRow item={items[virtualRow.index]} />
            </div>
          </div>
        ))}
      </div>

      {/* Streaming tail: outside virtual list — never triggers historical re-renders */}
      <div className="py-3">
        <StreamingTail />
      </div>

      {/* Injection messages rendered after streaming tail to preserve visual order */}
      <InjectionTail />

      {/* Bottom spacer for comfortable reading */}
      <div className="h-6" />

      {/* Scroll-to-bottom button */}
      <ScrollToBottom containerRef={parentRef} />
    </div>
  );
}
