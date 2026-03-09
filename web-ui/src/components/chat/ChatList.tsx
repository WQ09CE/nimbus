"use client";

import React, { useRef, useEffect } from 'react';
import type { Message } from "@/stores/chat-store";
import { useChatStore } from "@/stores";
import { ChatMessage } from "./ChatMessage";

interface ChatListProps {
  messages: Message[];
}

export function ChatList({ messages }: ChatListProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const isStreaming = useChatStore(s => s.isStreaming);

  // Auto-scroll to bottom
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;

    // Only scroll if we are already near the bottom, or if we just started streaming
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom < 150 || isStreaming) {
      requestAnimationFrame(() => {
        el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
      });
    }
  }, [messages, isStreaming]);

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
      className="flex-1 min-h-0 overflow-y-auto custom-scrollbar pt-6"
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
    </div>
  );
}
