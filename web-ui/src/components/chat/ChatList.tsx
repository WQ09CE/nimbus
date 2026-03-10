"use client";

import React, { useRef, useEffect, useState, useCallback } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import type { Message } from "@/stores/chat-store";
import { useChatStore } from "@/stores";
import { ChatMessage } from "./ChatMessage";

interface ChatListProps {
  messages: Message[];
}

export function ChatList({ messages }: ChatListProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const isStreaming = useChatStore(s => s.isStreaming);
  const [showNewMessagesPill, setShowNewMessagesPill] = useState(false);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 200,
    overscan: 5,
    gap: 32, // equivalent to mb-8 (2rem = 32px) between messages
    paddingEnd: 48,
  });

  const virtualItems = virtualizer.getVirtualItems();

  const scrollToBottom = useCallback((smooth = false) => {
    const el = parentRef.current;
    if (!el) return;
    if (smooth) {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    } else {
      el.scrollTop = el.scrollHeight;
    }
  }, []);

  // Auto-scroll when messages change or streaming is active
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom < 300 || isStreaming) {
      scrollToBottom(false);
      setShowNewMessagesPill(false);
      // Second pass: virtualizer may update measurements after first paint
      const t = setTimeout(() => scrollToBottom(false), 80);
      return () => clearTimeout(t);
    } else if (messages.length > 0) {
      setShowNewMessagesPill(true);
    }
  }, [messages, isStreaming, scrollToBottom]);

  // Track user scroll to hide/show new-messages pill
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const handleScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (distFromBottom < 100) setShowNewMessagesPill(false);
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, []);

  // Initial scroll to bottom on mount
  useEffect(() => {
    const t = setTimeout(() => scrollToBottom(false), 100);
    return () => clearTimeout(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={parentRef}
      className="flex-1 min-h-0 overflow-y-auto custom-scrollbar pt-6 relative"
    >
      <div
        style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}
        className="max-w-4xl mx-auto px-4"
      >
        {virtualItems.map(vItem => {
          const msg = messages[vItem.index];
          const isLast = vItem.index === messages.length - 1;
          return (
            <div
              key={msg.id || vItem.index}
              data-index={vItem.index}
              ref={virtualizer.measureElement}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                transform: `translateY(${vItem.start}px)`,
              }}
            >
              <ChatMessage
                message={msg}
                isStreaming={isStreaming && isLast && msg.role === 'assistant'}
              />
            </div>
          );
        })}
      </div>

      {showNewMessagesPill && (
        <button
          onClick={() => {
            scrollToBottom(true);
            setShowNewMessagesPill(false);
          }}
          className="new-messages-pill fixed bottom-28 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-full bg-sky-500/90 hover:bg-sky-400/90 text-white text-sm font-medium shadow-lg shadow-sky-500/20 backdrop-blur-sm transition-colors cursor-pointer flex items-center gap-1.5"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 13.5L12 21m0 0l-7.5-7.5M12 21V3" />
          </svg>
          New messages
        </button>
      )}
    </div>
  );
}
