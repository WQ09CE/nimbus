"use client";
import { useEffect, useRef } from "react";

interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "default";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  isOpen, title, message,
  confirmLabel = "确认", cancelLabel = "取消",
  variant = "default",
  onConfirm, onCancel,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (isOpen) confirmRef.current?.focus();
  }, [isOpen]);

  // ESC to cancel
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [isOpen, onCancel]);

  if (!isOpen) return null;

  const confirmColor = variant === "danger"
    ? "bg-red-500/80 hover:bg-red-500 text-white"
    : "bg-sky-500/80 hover:bg-sky-500 text-white";

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onCancel} />
      <div className="relative bg-nimbus-bg border border-nimbus-border rounded-xl p-6 max-w-sm w-full mx-4 shadow-2xl">
        <h3 className="text-nimbus-text font-medium text-base mb-2">{title}</h3>
        <p className="text-nimbus-text-dim text-sm mb-6">{message}</p>
        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-nimbus-text-dim hover:text-nimbus-text bg-nimbus-surface hover:bg-nimbus-surface-hover rounded-lg transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            className={`px-4 py-2 text-sm rounded-lg transition-colors ${confirmColor}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
