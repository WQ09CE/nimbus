"use client";

import { useState, useRef, useEffect } from "react";

interface ChatInputProps {
  onSend: (message: string) => void;
  onInterrupt?: () => void;
  disabled?: boolean;
  isStreaming?: boolean;
  isInterrupting?: boolean;
  placeholder?: string;
}

export function ChatInput({
  onSend,
  onInterrupt,
  disabled,
  isStreaming,
  isInterrupting,
  placeholder
}: ChatInputProps) {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const wasDisabledRef = useRef(false);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = textareaRef.current.scrollHeight + "px";
    }
  }, [input]);

  // Auto-focus only when transitioning from disabled to enabled
  useEffect(() => {
    if (wasDisabledRef.current && !disabled && textareaRef.current) {
      // Only focus if input is empty (just sent a message)
      if (!input) {
        textareaRef.current.focus();
      }
    }
    wasDisabledRef.current = disabled ?? false;
  }, [disabled, input]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || disabled) return;

    onSend(input.trim());
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <div className="flex-shrink-0 border-t border-gray-800 bg-black">
      <form onSubmit={handleSubmit} className="max-w-4xl mx-auto px-6 py-4">
        <div className="flex items-end gap-3">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder || "Type your message..."}
            disabled={disabled}
            rows={1}
            className={`
              flex-1 bg-gray-900 border border-gray-800 rounded-lg px-4 py-3
              text-sm text-gray-100 placeholder-gray-600
              focus:outline-none focus:border-blue-600 focus:ring-1 focus:ring-blue-600
              resize-none max-h-40 overflow-y-auto
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-colors
            `}
          />
          {isStreaming ? (
            <button
              type="button"
              onClick={onInterrupt}
              disabled={isInterrupting}
              className={`
                px-5 py-3 rounded-lg font-semibold text-sm
                transition-all
                ${
                  isInterrupting
                    ? "bg-gray-600 text-gray-400 cursor-not-allowed"
                    : "bg-red-600 hover:bg-red-700 text-white"
                }
              `}
            >
              {isInterrupting ? "正在停止..." : "停止"}
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim() || disabled}
              className={`
                px-5 py-3 rounded-lg font-semibold text-sm
                transition-all
                ${
                  input.trim() && !disabled
                    ? "bg-blue-600 hover:bg-blue-700 text-white"
                    : "bg-gray-800 text-gray-600 cursor-not-allowed"
                }
              `}
            >
              {disabled ? "..." : "发送"}
            </button>
          )}
        </div>
        <div className="text-xs text-gray-700 mt-2">
          Press Enter to send, Shift+Enter for new line
        </div>
      </form>
    </div>
  );
}
