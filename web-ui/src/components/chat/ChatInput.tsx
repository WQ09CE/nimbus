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
    } else if (e.key === "Escape" && isStreaming && onInterrupt) {
      e.preventDefault();
      onInterrupt();
    }
  };

  return (
    <div className="flex-shrink-0 z-20 pb-8 px-4 pointer-events-none">
      <div className="max-w-4xl mx-auto pointer-events-auto">
        <form onSubmit={handleSubmit} className="relative group">
          {/* Glow effect behind input */}
          <div className={`absolute -inset-0.5 bg-gradient-to-r from-blue-500 to-purple-600 rounded-2xl blur opacity-20 group-hover:opacity-40 transition duration-1000 group-focus-within:opacity-50 group-focus-within:duration-200 ${disabled ? 'hidden' : ''}`} />

          <div
            className={`
              relative flex items-end gap-2 
              bg-gray-900/80 backdrop-blur-xl border rounded-2xl overflow-hidden shadow-2xl transition-all duration-200
              ${disabled ? "opacity-60 cursor-not-allowed border-gray-800" : "border-white/10 hover:border-white/20"}
              ${input.trim() ? "translate-y-0" : "translate-y-0"}
            `}
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={placeholder || "Ask follow-up..."}
              disabled={disabled}
              rows={1}
              className={`
                w-full bg-transparent border-none focus:ring-0 
                text-gray-100 placeholder-gray-500 text-[16px] leading-relaxed
                pl-5 py-4 pr-2 font-sans
                resize-none max-h-[200px] overflow-y-auto
                min-h-[60px]
              `}
              style={{
                scrollbarWidth: 'thin',
                scrollbarColor: '#4B5563 transparent'
              }}
            />

            <div className="pb-3 pr-3">
              {isStreaming ? (
                <button
                  type="button"
                  onClick={onInterrupt}
                  disabled={isInterrupting}
                  className={`
                    p-2 rounded-xl transition-all duration-200 flex items-center justify-center w-10 h-10
                    ${isInterrupting
                      ? "bg-gray-800 text-gray-500"
                      : "bg-gray-800 text-red-400 hover:bg-red-500/10 hover:text-red-300 border border-white/5 hover:border-red-500/30"
                    }
                  `}
                >
                  {isInterrupting ? (
                    <div className="w-4 h-4 border-2 border-gray-500 border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <div className="w-3 h-3 bg-current rounded-sm" />
                  )}
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim() || disabled}
                  className={`
                    p-2 rounded-xl transition-all duration-300 flex items-center justify-center w-10 h-10
                    ${input.trim() && !disabled
                      ? "bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-500/25 rotate-0 scale-100"
                      : "bg-gray-800/50 text-gray-600 cursor-not-allowed rotate-90 scale-90 opacity-50"
                    }
                  `}
                >
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    className="w-5 h-5" // Optical alignment
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <line x1="12" y1="19" x2="12" y2="5"></line>
                    <polyline points="5 12 12 5 19 12"></polyline>
                  </svg>
                </button>
              )}
            </div>
          </div>

          <div className="flex justify-between items-center mt-2 px-3 opacity-0 group-focus-within:opacity-100 transition-opacity duration-300">
            <div className="flex gap-4">
              <div className="text-[10px] text-gray-500 font-medium flex gap-1.5 items-center">
                <span className="bg-gray-800 px-1 rounded border border-gray-700">⏎</span>
                <span>Send</span>
              </div>
              <div className="text-[10px] text-gray-500 font-medium flex gap-1.5 items-center">
                <span className="bg-gray-800 px-1 rounded border border-gray-700">⇧ ⏎</span>
                <span>Line</span>
              </div>
            </div>

            {isStreaming && (
              <div className="text-[11px] text-blue-400 flex items-center gap-1.5 animate-pulse font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400"></span>
                <span>Generating...</span>
              </div>
            )}
          </div>
        </form>
      </div>

      <style jsx>{`
        textarea::-webkit-scrollbar {
          width: 6px;
        }
        textarea::-webkit-scrollbar-track {
          background: transparent;
        }
        textarea::-webkit-scrollbar-thumb {
          background-color: #374151;
          border-radius: 3px;
          border: 2px solid transparent;
          background-clip: content-box;
        }
        textarea::-webkit-scrollbar-thumb:hover {
          background-color: #4b5563;
        }
      `}</style>
    </div>
  );
}
