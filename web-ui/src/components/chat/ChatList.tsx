"use client";

import React, { useRef, useEffect, useState } from 'react';
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

  // Auto-scroll to bottom
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom < 300 || isStreaming) {
      requestAnimationFrame(() => {
        el.scrollTo({ top: el.scrollHeight, behavior: isStreaming ? 'auto' : 'smooth' });
      });
      setShowNewMessagesPill(false);
    } else if (messages.length > 0) {
      setShowNewMessagesPill(true);
    }
  }, [messages, isStreaming]);

  // Scroll event listener to track user scrolling
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const handleScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (distFromBottom < 100) {
        setShowNewMessagesPill(false);
      }
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, []);

  // Initial load scroll
  useEffect(() => {
    const el = parentRef.current;
    if (el) {
      setTimeout(() => el.scrollTop = el.scrollHeight, 100);
    }
  }, []);

  return (
    <div
      ref={parentRef}
      className="flex-1 min-h-0 overflow-y-auto custom-scrollbar pt-6 relative"
    >
      <div className="flex flex-col gap-2 max-w-4xl mx-auto px-4 pb-12">
        {messages.map((msg, index) => {
          // If the user injected a message while streaming, it might be right next to another user message
          // Just render them in order.
          return (
            <ChatMessage
              key={msg.id || index}
              message={msg}
              // isStreaming is true ONLY for the last message if the global state is streaming
              isStreaming={isStreaming && index === messages.length - 1 && msg.role === 'assistant'}
            />
          );
        })}
      </div>
      {showNewMessagesPill && (
        <button
          onClick={() => {
            const el = parentRef.current;
            if (el) {
              el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
              setShowNewMessagesPill(false);
            }
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
