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
  });

  const virtualItems = virtualizer.getVirtualItems();

  const lastTotalSize = useRef(0);
  const scrollElement = parentRef.current;

  // Track scroll position to update shouldStick and pill visibility
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const handleScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      // Sticky Zone logic: 100px threshold for better reliability
      if (distFromBottom < 100) {
        shouldStick.current = true;
        setShowNewMessagesPill(false);
      } else {
        shouldStick.current = false;
      }
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, []);

  // Use ResizeObserver on the container to detect height changes
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;

    let lastHeight = el.scrollHeight;

    const resizeObserver = new ResizeObserver(() => {
      const newHeight = el.scrollHeight;
      const heightDelta = Math.abs(newHeight - lastHeight);
      
      // Only auto-scroll if:
      // 1. We should stick to bottom
      // 2. The height change is significant (not just virtualizer jitter)
      // 3. Or we're already very close to the bottom
      if (shouldStick.current && heightDelta > 5) {
        el.scrollTo({
          top: newHeight,
          behavior: 'auto' // Use 'auto' instead of 'smooth' to avoid scroll loops during measurement
        });
      }
      lastHeight = newHeight;
    });

    // Observe the scrollable container's content (the virtual list inner element)
    const content = el.firstElementChild;
    if (content) {
      resizeObserver.observe(content);
    }

    return () => resizeObserver.disconnect();
  }, []);

  const scrollToBottom = useCallback(() => {
    if (messages.length > 0) {
      shouldStick.current = true;
      parentRef.current?.scrollTo({
        top: parentRef.current.scrollHeight,
        behavior: 'smooth'
      });
    }
  }, [messages.length]);

  // When new messages arrive, stick to bottom if we are in sticky zone
  useEffect(() => {
    if (messages.length > lastCount.current) {
      if (shouldStick.current) {
        // Smoothly scroll to the new bottom
        requestAnimationFrame(() => {
          parentRef.current?.scrollTo({
            top: parentRef.current.scrollHeight,
            behavior: 'auto' // Use 'auto' to ensure immediate scroll
          });
        });
      } else {
        setShowNewMessagesPill(true);
      }
    }
    lastCount.current = messages.length;
  }, [messages.length]);

  // Initial scroll to bottom on mount
  useEffect(() => {
    if (messages.length > 0) {
      // Step 1: Virtualizer scroll to bottom
      virtualizer.scrollToIndex(messages.length - 1, { align: 'end', behavior: 'auto' });
      shouldStick.current = true;

      // Step 2: Forcibly scroll to absolute bottom after a tick to ensure full message visibility
      requestAnimationFrame(() => {
        if (parentRef.current) {
          parentRef.current.scrollTop = parentRef.current.scrollHeight;
        }
      });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={parentRef}
      className="flex-1 min-h-0 overflow-y-auto custom-scrollbar pt-6 relative"
      style={{ overflowAnchor: 'auto' }}
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
