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
    <div className="flex-shrink-0 border-t border-gray-800 bg-black/95 backdrop-blur-sm pb-6 pt-4 px-4">
      <form onSubmit={handleSubmit} className="max-w-4xl mx-auto relative">
        <div 
          className={`
            relative flex items-end gap-2 
            bg-[#1A1A1A] border rounded-2xl overflow-hidden shadow-sm transition-all duration-200
            ${disabled ? "opacity-60 cursor-not-allowed border-gray-800" : "border-gray-700 hover:border-gray-600 focus-within:border-blue-500/50 focus-within:ring-1 focus-within:ring-blue-500/20"}
          `}
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder || "Message Nimbus..."}
            disabled={disabled}
            rows={1}
            className={`
              w-full bg-transparent border-none focus:ring-0 
              text-gray-100 placeholder-gray-500 text-[15px] leading-relaxed
              pl-4 py-3.5 pr-2
              resize-none max-h-[200px] overflow-y-auto
              min-h-[52px]
            `}
            style={{
              scrollbarWidth: 'thin',
              scrollbarColor: '#4B5563 transparent'
            }}
          />
          
          <div className="pb-2 pr-2">
            {isStreaming ? (
              <button
                type="button"
                onClick={onInterrupt}
                disabled={isInterrupting}
                className={`
                  p-2 rounded-xl transition-all duration-200 flex items-center justify-center
                  ${
                    isInterrupting
                      ? "bg-gray-700/50 text-gray-500 cursor-wait"
                      : "bg-gray-800 hover:bg-gray-700 text-red-400 border border-transparent hover:border-red-500/30 group"
                  }
                `}
                title="Stop generation (Esc)"
              >
                {isInterrupting ? (
                  <div className="w-4 h-4 border-2 border-gray-500 border-t-transparent rounded-full animate-spin" />
                ) : (
                  <div className="w-8 h-8 flex items-center justify-center">
                    <div className="w-3 h-3 bg-red-500 rounded-sm group-hover:scale-110 transition-transform" />
                  </div>
                )}
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim() || disabled}
                className={`
                  p-2 rounded-xl transition-all duration-200 flex items-center justify-center
                  ${
                    input.trim() && !disabled
                      ? "bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20"
                      : "bg-gray-800/50 text-gray-600 cursor-not-allowed"
                  }
                `}
              >
                <div className="w-8 h-8 flex items-center justify-center">
                  <svg 
                    viewBox="0 0 24 24" 
                    fill="none" 
                    className={`w-5 h-5 ${input.trim() && !disabled ? "ml-0.5" : ""}`} // Optical alignment
                    stroke="currentColor" 
                    strokeWidth="2.5" 
                    strokeLinecap="round" 
                    strokeLinejoin="round"
                  >
                    <line x1="22" y1="2" x2="11" y2="13"></line>
                    <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                  </svg>
                </div>
              </button>
            )}
          </div>
        </div>

        <div className="flex justify-between items-center mt-2 px-1">
          <div className="text-[11px] text-gray-500 font-medium flex gap-2">
            <span>Enter to send</span>
            <span className="text-gray-600">•</span>
            <span>Shift + Enter for new line</span>
          </div>
          
          {isStreaming && (
            <div className="text-[11px] text-gray-500 flex items-center gap-1.5 animate-pulse">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
              <span>Running • Esc to stop</span>
            </div>
          )}
        </div>
      </form>

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
