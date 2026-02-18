"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
import type { ChatAttachment } from "@/lib/api/chat";

// ============================================================================
// Constants
// ============================================================================

const MAX_IMAGE_SIZE = 10 * 1024 * 1024; // 10MB
const MAX_TEXT_SIZE = 5 * 1024 * 1024;   // 5MB
const MAX_ATTACHMENTS = 5;
const ACCEPTED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];
const ACCEPTED_TEXT_TYPES = ["text/plain", "text/markdown", "text/csv", "text/yaml", "application/json", "application/pdf"];
const ACCEPTED_EXTENSIONS = [".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log", ".pdf", ".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".sh", ".toml"];
const LONG_TEXT_LINE_THRESHOLD = 5;    // Lines before auto-collapsing pasted text
const LONG_TEXT_CHAR_THRESHOLD = 500;  // Characters before auto-collapsing pasted text

// ============================================================================
// Helpers
// ============================================================================

function generateId(): string {
  return `att_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      // Remove data URL prefix: "data:image/png;base64,..."
      const base64 = result.split(",")[1] || result;
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsText(file);
  });
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function isImageType(file: File): boolean {
  return ACCEPTED_IMAGE_TYPES.includes(file.type) || file.type.startsWith("image/");
}

function isTextType(file: File): boolean {
  if (ACCEPTED_TEXT_TYPES.includes(file.type)) return true;
  const ext = `.${file.name.split(".").pop()?.toLowerCase()}`;
  return ACCEPTED_EXTENSIONS.includes(ext);
}

function detectLanguage(text: string): string {
  const firstLine = text.trimStart().split("\n")[0];
  if (firstLine.startsWith("```")) return firstLine.slice(3).trim() || "code";
  if (firstLine.startsWith("#!")) return "script";
  if (/^\s*(import |from |require\(|export )/.test(text)) return "code";
  if (/^\s*(def |class |async |function |const |let |var )/.test(text)) return "code";
  if (/^\s*[{[\(]/.test(text) && /[}\]]\s*$/.test(text.trim())) return "json";
  if (/^\s*<[a-zA-Z]/.test(text)) return "markup";
  return "text";
}

function countLines(text: string): number {
  return text.split("\n").length;
}

// ============================================================================
// Props
// ============================================================================

interface ChatInputProps {
  onSend: (message: string, attachments?: ChatAttachment[]) => void;
  onInterrupt?: () => void;
  disabled?: boolean;
  isStreaming?: boolean;
  isInterrupting?: boolean;
  placeholder?: string;
}

// ============================================================================
// Component
// ============================================================================

export const ChatInput = React.memo(function ChatInput({
  onSend,
  onInterrupt,
  disabled,
  isStreaming,
  isInterrupting,
  placeholder,
}: ChatInputProps) {
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const wasDisabledRef = useRef(false);
  const dragCounterRef = useRef(0);

  const hasContent = input.trim().length > 0 || attachments.length > 0;

  // Auto-clear error after 3 seconds
  useEffect(() => {
    if (error) {
      const timer = setTimeout(() => setError(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [error]);

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const frameId = requestAnimationFrame(() => {
      textarea.style.height = "auto";
      textarea.style.height = textarea.scrollHeight + "px";
    });
    return () => cancelAnimationFrame(frameId);
  }, [input]);

  // Auto-focus when transitioning from disabled to enabled
  useEffect(() => {
    if (wasDisabledRef.current && !disabled && textareaRef.current) {
      if (!input) textareaRef.current.focus();
    }
    wasDisabledRef.current = disabled ?? false;
  }, [disabled, input]);

  // ========================================================================
  // File Processing
  // ========================================================================

  const processFile = useCallback(async (file: File): Promise<ChatAttachment | null> => {
    // Check attachment limit
    if (attachments.length >= MAX_ATTACHMENTS) {
      setError(`最多添加 ${MAX_ATTACHMENTS} 个附件`);
      return null;
    }

    if (isImageType(file)) {
      if (file.size > MAX_IMAGE_SIZE) {
        setError(`图片大小不能超过 ${formatFileSize(MAX_IMAGE_SIZE)}`);
        return null;
      }
      const base64 = await readFileAsBase64(file);
      return {
        id: generateId(),
        type: "image",
        name: file.name || "pasted-image.png",
        size: file.size,
        content: base64,
        mimeType: file.type || "image/png",
        preview: URL.createObjectURL(file),
      };
    } else if (isTextType(file)) {
      if (file.size > MAX_TEXT_SIZE) {
        setError(`文件大小不能超过 ${formatFileSize(MAX_TEXT_SIZE)}`);
        return null;
      }
      if (file.type === "application/pdf") {
        // PDF: send as base64 for backend processing
        const base64 = await readFileAsBase64(file);
        return {
          id: generateId(),
          type: "pdf",
          name: file.name,
          size: file.size,
          content: base64,
          mimeType: "application/pdf",
        };
      }
      const text = await readFileAsText(file);
      return {
        id: generateId(),
        type: "text",
        name: file.name,
        size: file.size,
        content: text,
        mimeType: file.type || "text/plain",
      };
    } else {
      setError(`不支持的文件类型: ${file.type || file.name}`);
      return null;
    }
  }, [attachments.length]);

  const addFiles = useCallback(async (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    for (const file of fileArray) {
      const att = await processFile(file);
      if (att) {
        setAttachments(prev => [...prev, att]);
      }
    }
  }, [processFile]);

  const removeAttachment = useCallback((id: string) => {
    setAttachments(prev => {
      const att = prev.find(a => a.id === id);
      if (att?.preview) URL.revokeObjectURL(att.preview);
      return prev.filter(a => a.id !== id);
    });
  }, []);

  // ========================================================================
  // Event Handlers
  // ========================================================================

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    // Check for images first
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) addFiles([file]);
        return;
      }
    }

    // Check for long text — convert to attachment
    const text = e.clipboardData?.getData("text/plain");
    if (text) {
      const lines = countLines(text);
      const chars = text.length;
      if (lines > LONG_TEXT_LINE_THRESHOLD || chars > LONG_TEXT_CHAR_THRESHOLD) {
        e.preventDefault();
        const lang = detectLanguage(text);
        const label = lang === "text" ? "Pasted text" : `Pasted ${lang}`;
        const ext = lang === "json" ? ".json" : lang === "markup" ? ".html" : lang === "code" || lang === "script" ? ".txt" : ".txt";
        const att: ChatAttachment = {
          id: generateId(),
          type: "text",
          name: `${label}${ext}`,
          size: new Blob([text]).size,
          content: text,
          mimeType: "text/plain",
        };
        setAttachments(prev => [...prev, att]);
        return;
      }
    }
    // For short text, let default behavior handle it
  }, [addFiles]);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (e.dataTransfer?.types.includes("Files")) {
      setIsDragging(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current--;
    if (dragCounterRef.current === 0) {
      setIsDragging(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    dragCounterRef.current = 0;
    if (e.dataTransfer?.files.length) {
      addFiles(e.dataTransfer.files);
    }
  }, [addFiles]);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) {
      addFiles(e.target.files);
    }
    // Reset input so same file can be selected again
    e.target.value = "";
  }, [addFiles]);

  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    if (!hasContent || disabled) return;

    onSend(input.trim(), attachments.length > 0 ? attachments : undefined);
    setInput("");
    setAttachments([]);
  }, [input, attachments, hasContent, disabled, onSend]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    } else if (e.key === "Escape" && isStreaming && onInterrupt) {
      e.preventDefault();
      onInterrupt();
    }
  }, [handleSubmit, isStreaming, onInterrupt]);

  // ========================================================================
  // Render
  // ========================================================================

  return (
    <div className="flex-shrink-0 z-20 pb-4 md:pb-8 px-2 md:px-4 pointer-events-none">
      <div className="max-w-4xl mx-auto pointer-events-auto">
        <form
          onSubmit={handleSubmit}
          className="relative group"
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
        >
          {/* Glow effect */}
          <div className={`absolute -inset-0.5 bg-gradient-to-r from-sky-400/50 to-violet-400/30 rounded-2xl blur opacity-20 group-hover:opacity-40 transition duration-1000 group-focus-within:opacity-50 group-focus-within:duration-200 ${disabled ? "hidden" : ""}`} />

          {/* Drag overlay */}
          {isDragging && (
            <div className="absolute inset-0 z-50 bg-sky-500/10 border-2 border-dashed border-sky-400/40 rounded-2xl flex items-center justify-center backdrop-blur-sm">
              <div className="text-sky-300 text-sm font-medium flex items-center gap-2">
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
                拖放文件到这里
              </div>
            </div>
          )}

          {/* Error toast */}
          {error && (
            <div className="absolute -top-10 left-1/2 -translate-x-1/2 px-3 py-1.5 bg-red-500/90 text-white text-xs rounded-lg shadow-lg animate-in fade-in slide-in-from-bottom-2 z-50 whitespace-nowrap">
              {error}
            </div>
          )}

          <div
            className={`
              relative flex flex-col
              bg-nimbus-surface backdrop-blur-xl border rounded-2xl overflow-hidden shadow-2xl transition-all duration-200
              ${disabled ? "opacity-60 cursor-not-allowed border-gray-800" : "border-nimbus-border hover:border-sky-400/20"}
            `}
          >
            {/* Attachment Preview Bar */}
            {attachments.length > 0 && (
              <div className="flex gap-2 px-3 pt-3 pb-1 overflow-x-auto">
                {attachments.map(att => (
                  <div
                    key={att.id}
                    className="relative group/att flex-shrink-0 rounded-lg border border-white/10 bg-white/5 overflow-hidden"
                  >
                    {att.type === "image" && att.preview ? (
                      <div className="w-16 h-16 relative">
                        <img
                          src={att.preview}
                          alt={att.name}
                          className="w-full h-full object-cover"
                        />
                        <div className="absolute inset-0 bg-black/40 opacity-0 group-hover/att:opacity-100 transition-opacity" />
                      </div>
                    ) : (
                      <div className="max-w-[280px] overflow-hidden">
                        <div className="px-3 py-1.5 flex items-center gap-2 border-b border-white/5">
                          <span className="text-xs">
                            {att.type === "pdf" ? "📄" : "📝"}
                          </span>
                          <span className="text-[11px] text-gray-300 truncate flex-1">{att.name}</span>
                          <span className="text-[10px] text-gray-500 shrink-0">{formatFileSize(att.size)}</span>
                        </div>
                        {att.type === "text" && att.content && (
                          <pre className="px-3 py-1.5 text-[10px] text-gray-500 font-mono leading-tight max-h-[48px] overflow-hidden whitespace-pre">{att.content.slice(0, 200)}</pre>
                        )}
                      </div>
                    )}
                    {/* Remove button */}
                    <button
                      type="button"
                      onClick={() => removeAttachment(att.id)}
                      className="absolute top-0.5 right-0.5 w-5 h-5 p-1 rounded-full bg-black/60 text-gray-300 hover:text-white hover:bg-red-500/80 flex items-center justify-center md:opacity-0 md:group-hover/att:opacity-100 transition-all text-[10px]"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Input Row */}
            <div className="flex items-end gap-1">
              {/* Attach button */}
              <div className="pb-3 pl-2">
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={disabled}
                  className="p-2 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-white/5 transition-colors disabled:opacity-30"
                  title="添加附件 (图片/文件)"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13" />
                  </svg>
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept={[...ACCEPTED_IMAGE_TYPES, ...ACCEPTED_EXTENSIONS.map(e => e)].join(",")}
                  onChange={handleFileSelect}
                  className="hidden"
                />
              </div>

              {/* Textarea */}
              <textarea
                ref={textareaRef}
                data-testid="chat-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                placeholder={placeholder || "Ask follow-up... (paste images with ⌘V)"}
                disabled={disabled}
                rows={1}
                className={`
                  w-full bg-transparent border-none focus:ring-0
                  text-gray-100 placeholder-gray-500 text-[16px] leading-relaxed
                  py-4 pr-2 font-sans
                  resize-none max-h-[120px] md:max-h-[200px] overflow-y-auto
                  min-h-[60px]
                `}
                style={{
                  scrollbarWidth: "thin",
                  scrollbarColor: "#4B5563 transparent",
                }}
              />

              {/* Send / Stop button */}
              <div className="pb-3 pr-3">
                {isStreaming ? (
                  <button
                    type="button"
                    data-testid="stop-button"
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
                    data-testid="send-button"
                    disabled={!hasContent || disabled}
                    className={`
                      p-2 rounded-xl transition-all duration-300 flex items-center justify-center w-10 h-10
                      ${hasContent && !disabled
                        ? "bg-sky-500/80 hover:bg-sky-400/80 text-white shadow-lg shadow-sky-400/20 rotate-0 scale-100"
                        : "bg-nimbus-surface text-gray-600 cursor-not-allowed rotate-90 scale-90 opacity-50"
                      }
                    `}
                  >
                    <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="12" y1="19" x2="12" y2="5" />
                      <polyline points="5 12 12 5 19 12" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Bottom hints */}
          <div className="hidden md:flex justify-between items-center mt-2 px-3 opacity-0 group-focus-within:opacity-100 transition-opacity duration-300">
            <div className="flex gap-4">
              <div className="text-[10px] text-gray-500 font-medium flex gap-1.5 items-center">
                <span className="bg-nimbus-surface px-1 rounded border border-nimbus-border">⏎</span>
                <span>Send</span>
              </div>
              <div className="text-[10px] text-gray-500 font-medium flex gap-1.5 items-center">
                <span className="bg-nimbus-surface px-1 rounded border border-nimbus-border">⇧ ⏎</span>
                <span>Line</span>
              </div>
              <div className="text-[10px] text-gray-500 font-medium flex gap-1.5 items-center">
                <span className="bg-nimbus-surface px-1 rounded border border-nimbus-border">⌘V</span>
                <span>Paste image</span>
              </div>
            </div>

            {isStreaming && (
              <div className="text-[11px] text-nimbus-accent flex items-center gap-1.5 animate-pulse font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-sky-400" />
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
});
