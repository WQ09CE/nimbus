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
  const shouldStick = useRef(true);
  const lastCount = useRef(messages.length);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 200,
    overscan: 5,
    gap: 32,
    paddingEnd: 48,
    // Removed onChange scrollToIndex — it fights user scroll on every measurement.
    // The totalSize effect below handles auto-stick correctly.
  });

  const virtualItems = virtualizer.getVirtualItems();
  const totalSize = virtualizer.getTotalSize();
  const prevTotalSize = useRef(totalSize);

  useEffect(() => {
    if (shouldStick.current && totalSize !== prevTotalSize.current) {
      prevTotalSize.current = totalSize;
      if (parentRef.current) {
        parentRef.current.scrollTop = parentRef.current.scrollHeight;
      }
    }
  }, [totalSize]);

  const scrollToBottom = useCallback(() => {
    if (messages.length > 0) {
      shouldStick.current = true;
      virtualizer.scrollToIndex(messages.length - 1, { align: 'end' });
      requestAnimationFrame(() => {
        if (parentRef.current) {
          parentRef.current.scrollTop = parentRef.current.scrollHeight;
        }
      });
    }
  }, [messages.length, virtualizer]);

  // When new messages arrive, stick to bottom or show pill
  useEffect(() => {
    if (messages.length > lastCount.current) {
      if (shouldStick.current || isStreaming) {
        virtualizer.scrollToIndex(messages.length - 1, { align: 'end' });
      } else {
        setShowNewMessagesPill(true);
      }
    }
    lastCount.current = messages.length;
  }, [messages.length, isStreaming, virtualizer]);

  // During streaming, keep sticking (only when shouldStick is true)
  useEffect(() => {
    if (isStreaming && shouldStick.current && messages.length > 0) {
      virtualizer.scrollToIndex(messages.length - 1, { align: 'end' });
    }
  }, [messages.length, isStreaming, virtualizer]); // eslint-disable-line react-hooks/exhaustive-deps

  // Detect upward scroll intent — wheel (desktop) + touch (mobile)
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;

    // Desktop: wheel up
    const handleWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) shouldStick.current = false;
    };

    // Mobile: touch swipe detection
    let touchStartY = 0;
    const handleTouchStart = (e: TouchEvent) => {
      touchStartY = e.touches[0].clientY;
    };
    const handleTouchMove = (e: TouchEvent) => {
      const currentY = e.touches[0].clientY;
      // Finger moves down on screen → content scrolls up → user wants to read history
      if (currentY - touchStartY > 10) {
        shouldStick.current = false;
      }
      touchStartY = currentY;
    };

    el.addEventListener('wheel', handleWheel, { passive: true });
    el.addEventListener('touchstart', handleTouchStart, { passive: true });
    el.addEventListener('touchmove', handleTouchMove, { passive: true });
    return () => {
      el.removeEventListener('wheel', handleWheel);
      el.removeEventListener('touchstart', handleTouchStart);
      el.removeEventListener('touchmove', handleTouchMove);
    };
  }, []);

  // Track scroll position to update shouldStick and pill visibility
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const handleScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (distFromBottom < 100) {
        shouldStick.current = true;
        setShowNewMessagesPill(false);
      } else if (distFromBottom > 300) {
        shouldStick.current = false;
      }
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, []);

  // Initial scroll to bottom on mount
  useEffect(() => {
    if (messages.length > 0) {
      const t = setTimeout(() => {
        virtualizer.scrollToIndex(messages.length - 1, { align: 'end' });
      }, 50);
      return () => clearTimeout(t);
    }
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
            scrollToBottom();
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
