"use client";

import { useEffect, useState, useCallback, type RefObject } from "react";
import { useChatStore } from "@/stores";

interface StreamingScrollerProps {
  containerRef: RefObject<HTMLDivElement | null>;
}

export function StreamingScroller({ containerRef }: StreamingScrollerProps) {
  const isStreaming = useChatStore(s => s.isStreaming);
  const streamingContent = useChatStore(s => s.streamingContent);
  const messages = useChatStore(s => s.messages);
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true);
  const [userScrolledUp, setUserScrolledUp] = useState(false);

  const scrollToBottom = useCallback(() => {
    const container = containerRef.current;
    if (container) {
      requestAnimationFrame(() => {
        container.scrollTop = container.scrollHeight;
      });
    }
  }, [containerRef]);

  // Handle user scroll
  const handleScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    const { scrollTop, scrollHeight, clientHeight } = container;
    const isAtBottom = scrollHeight - scrollTop - clientHeight <= 100;

    if (isAtBottom) {
      setUserScrolledUp(false);
      setAutoScrollEnabled(true);
    } else {
      setUserScrolledUp(true);
      setAutoScrollEnabled(false);
    }
  }, [containerRef]);

  // Attach scroll listener
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    container.addEventListener("scroll", handleScroll, { passive: true });
    return () => container.removeEventListener("scroll", handleScroll);
  }, [containerRef, handleScroll]);

  // Auto-scroll when messages change
  useEffect(() => {
    if (autoScrollEnabled) {
      scrollToBottom();
    }
  }, [messages.length, autoScrollEnabled, scrollToBottom]);

  // Auto-scroll during streaming (debounced)
  useEffect(() => {
    if (isStreaming && autoScrollEnabled) {
      const timeoutId = setTimeout(scrollToBottom, 150);
      return () => clearTimeout(timeoutId);
    }
  }, [streamingContent, isStreaming, autoScrollEnabled, scrollToBottom]);

  // Expose scroll-to-bottom button
  if (!userScrolledUp || (!isStreaming && messages.length <= 3)) return null;

  return (
    <div className="absolute bottom-24 right-6 z-10">
      <button
        onClick={() => {
          setAutoScrollEnabled(true);
          setUserScrolledUp(false);
          scrollToBottom();
        }}
        className="bg-blue-600 hover:bg-blue-700 text-white text-xs px-3 py-2 rounded-full shadow-lg transition-all duration-200 flex items-center gap-2 border border-blue-500/50 hover:scale-105 active:scale-95"
      >
        <span>⬇</span>
        <span className="hidden sm:inline">To Bottom</span>
        {isStreaming && (
          <div className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse"></div>
        )}
      </button>
    </div>
  );
}
