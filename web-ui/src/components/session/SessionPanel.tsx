"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Session,
  listSessions,
  deleteSession,
  deleteSessions,
  interruptSession,
  resumeSession,
  updateSession,
} from "@/lib/api/sessions";
import { useChatStore } from "@/stores";
import { CreateSessionDialog, CreateSessionConfig } from "./CreateSessionDialog";
import { ConfirmDialog } from "./ConfirmDialog";

interface SessionPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

type SessionStatus = "active" | "interrupted" | "completed" | "deleted";

const STATUS_CONFIG: Record<SessionStatus, { label: string; color: string; icon: string }> = {
  active: { label: "Active", color: "text-emerald-400 bg-emerald-400/10 border-emerald-400/20", icon: "●" },
  interrupted: { label: "Paused", color: "text-amber-400 bg-amber-400/10 border-amber-400/20", icon: "⏸" },
  completed: { label: "Done", color: "text-blue-400 bg-blue-400/10 border-blue-400/20", icon: "✓" },
  deleted: { label: "Deleted", color: "text-gray-500 bg-gray-500/10 border-gray-500/20", icon: "🗑" },
};

export function SessionPanel({ isOpen, onClose }: SessionPanelProps) {
  const { session: currentSession, switchSession, createNewSession, isStreaming } = useChatStore();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(false);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Batch selection state
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isSelectMode, setIsSelectMode] = useState(false);

  // Confirm dialog state
  const [confirmDialog, setConfirmDialog] = useState<{ type: "single" | "batch"; id?: string } | null>(null);

  // Search state
  const [searchQuery, setSearchQuery] = useState("");

  // Rename state
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      const list = await listSessions();
      setSessions(list.sort((a, b) => {
        const aTime = a.last_message_at || a.created_at;
        const bTime = b.last_message_at || b.created_at;
        return new Date(bTime).getTime() - new Date(aTime).getTime();
      }));
    } catch (err) {
      console.error("Failed to fetch sessions:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      fetchSessions();
    } else {
      setIsSelectMode(false);
      setSelectedIds(new Set());
    }
  }, [isOpen, fetchSessions]);

  const handleCreateSession = async (config: CreateSessionConfig) => {
    try {
      // Pass the full config to the store's createNewSession
      // Note: We need to update useChatStore to accept this config structure if it doesn't already
      // For now, we fit it into the existing signature
      await createNewSession(true, {
        ...config,
        // Ensure llm_config is passed correctly even if spread above doesn't match perfectly
        llm_config: config.llm_config,
      });
      await fetchSessions();
    } catch (err) {
      console.error("Failed to create session:", err);
    }
  };

  const handleDeleteSession = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setConfirmDialog({ type: "single", id });
  };

  const executeDeleteSession = async (id: string) => {
    setActionLoading(id);
    try {
      await deleteSession(id);
      setSessions(prev => prev.filter(s => s.id !== id));
      if (currentSession?.id === id) switchSession(null);
    } catch (err) {
      console.error("Failed to delete session:", err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return;
    setConfirmDialog({ type: "batch" });
  };

  const executeBatchDelete = async () => {
    setActionLoading("batch");
    try {
      await deleteSessions(Array.from(selectedIds));
      setSessions(prev => prev.filter(s => !selectedIds.has(s.id)));
      if (currentSession && selectedIds.has(currentSession.id)) switchSession(null);
      setSelectedIds(new Set());
      setIsSelectMode(false);
    } catch (err) {
      console.error("Failed to batch delete:", err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleConfirmAction = () => {
    if (!confirmDialog) return;
    if (confirmDialog.type === "single" && confirmDialog.id) {
      executeDeleteSession(confirmDialog.id);
    } else if (confirmDialog.type === "batch") {
      executeBatchDelete();
    }
    setConfirmDialog(null);
  };

  const handleRenameSubmit = async (sessionId: string) => {
    const trimmed = renameValue.trim();
    if (!trimmed) {
      setRenamingId(null);
      return;
    }
    try {
      await updateSession(sessionId, { name: trimmed });
      setSessions(prev => prev.map(s => s.id === sessionId ? { ...s, name: trimmed } : s));
    } catch (err) {
      console.error("Failed to rename session:", err);
    } finally {
      setRenamingId(null);
    }
  };

  const handleResumeSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setActionLoading(id);
    try {
      await resumeSession(id);
      await fetchSessions();
    } catch (err) {
      console.error("Failed to resume session:", err);
    } finally {
      setActionLoading(null);
    }
  };

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const formatRelativeTime = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    const diffHr = Math.floor(diffMs / 3600000);
    const diffDay = Math.floor(diffMs / 86400000);

    if (diffMin < 1) return "刚刚";
    if (diffMin < 60) return `${diffMin}分钟前`;
    if (diffHr < 24) return `${diffHr}小时前`;
    if (diffDay < 7) return `${diffDay}天前`;
    return date.toLocaleDateString("zh-CN", { month: '2-digit', day: '2-digit' });
  };

  const getShortModelName = (modelId: string) => {
    const parts = modelId.split("-");
    if (modelId.includes("gemini")) return `gemini-${parts.find(p => ["flash","pro"].includes(p)) || parts[1]}`;
    if (modelId.includes("claude")) return `${parts[1]}-${parts[2]}`;
    return modelId.length > 12 ? modelId.slice(0, 12) : modelId;
  };

  if (!isOpen) return null;

  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-40" onClick={onClose} />

      {/* Sidebar Panel */}
      <div className="fixed top-0 left-0 bottom-0 w-full md:w-[400px] bg-nimbus-bg/95 backdrop-blur-xl border-r border-nimbus-border z-50 shadow-2xl flex flex-col transform transition-transform duration-300 animate-in slide-in-from-left">

        {/* Header */}
        <div className="px-5 py-4 border-b border-nimbus-border flex items-center justify-between bg-nimbus-bg/80">
          <div>
            <h2 className="text-lg font-semibold text-nimbus-text tracking-tight">Sessions</h2>
            <p className="text-[11px] text-nimbus-text-dim font-mono mt-0.5 uppercase tracking-wider">
              {sessions.length} Conversations
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setIsSelectMode(!isSelectMode)}
              className={`p-2 rounded-lg transition-colors ${isSelectMode ? 'bg-nimbus-surface text-white' : 'text-gray-400 hover:bg-nimbus-surface'}`}
              title="批量管理"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" /></svg>
            </button>
            <button
              onClick={() => setShowCreateDialog(true)}
              className="px-3 py-1.5 bg-sky-500/20 hover:bg-sky-500/30 border border-sky-400/30 shadow-lg shadow-sky-400/10 text-sky-300 text-xs font-medium rounded-lg transition-colors flex items-center gap-1.5"
            >
              <span>+</span> New
            </button>
          </div>
        </div>

        {/* Batch Actions Bar */}
        {isSelectMode && (
          <div className="px-4 py-2 bg-nimbus-surface border-b border-nimbus-border flex justify-between items-center animate-in slide-in-from-top-2">
            <span className="text-xs text-gray-400">Selected: {selectedIds.size}</span>
            <button
              onClick={handleBatchDelete}
              disabled={selectedIds.size === 0}
              className="text-xs text-red-400 hover:text-red-300 disabled:text-gray-600 font-medium px-2 py-1"
            >
              Delete Selected
            </button>
          </div>
        )}

        {/* Search Bar */}
        <div className="px-4 py-2 border-b border-nimbus-border">
          <div className="relative">
            <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-nimbus-text-dim" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search sessions..."
              className="w-full pl-8 pr-8 py-1.5 text-xs bg-nimbus-surface border border-nimbus-border rounded-lg text-nimbus-text placeholder-nimbus-text-dim focus:outline-none focus:border-nimbus-accent/50 transition-colors"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-nimbus-text-dim hover:text-nimbus-text transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            )}
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          {loading ? (
            <div className="flex justify-center py-12">
              <div className="w-6 h-6 border-2 border-nimbus-border border-t-sky-400 rounded-full animate-spin" />
            </div>
          ) : sessions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-gray-500 px-8 text-center">
              <div className="text-4xl mb-3 opacity-20">💬</div>
              <p className="text-sm">No conversations yet.</p>
              <button
                onClick={() => setShowCreateDialog(true)}
                className="mt-4 text-nimbus-accent hover:text-nimbus-accent-soft text-xs font-medium"
              >
                Start a new chat
              </button>
            </div>
          ) : (
            <div className="divide-y divide-nimbus-border">
              {sessions.filter(s => {
                if (!searchQuery) return true;
                const q = searchQuery.toLowerCase();
                return (
                  s.name?.toLowerCase().includes(q) ||
                  s.first_message_preview?.toLowerCase().includes(q)
                );
              }).map(session => {
                const isActive = currentSession?.id === session.id;
                const status = STATUS_CONFIG[session.status as SessionStatus] || STATUS_CONFIG.active;
                const isSelected = selectedIds.has(session.id);
                // Fallback name logic: name -> id prefix -> "Untitled"
                const displayName = session.name?.trim() || `Session ${session.id.slice(0, 8)}`;
                const isStatusActive = session.status === "active";

                return (
                  <div
                    key={session.id}
                    onClick={() => isSelectMode ? toggleSelect(session.id) : (() => { switchSession(session); onClose(); })()}
                    className={`
                      group relative px-4 py-3 cursor-pointer transition-all duration-200
                      ${isActive ? 'bg-sky-400/10 border-l-2 border-l-sky-400' : 'hover:bg-nimbus-surface-hover border-l-2 border-l-transparent'}
                      ${isSelected ? 'bg-nimbus-surface' : ''}
                    `}
                  >
                    {/* Select Checkbox */}
                    {isSelectMode && (
                      <div className="absolute left-2 top-1/2 -translate-y-1/2 z-10">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(session.id)}
                          className="w-4 h-4 rounded border-gray-600 bg-nimbus-bg text-sky-400 focus:ring-offset-0"
                        />
                      </div>
                    )}

                    <div className={isSelectMode ? "pl-6" : ""}>
                      <div className="flex justify-between items-baseline mb-1">
                        {renamingId === session.id ? (
                          <input
                            autoFocus
                            type="text"
                            value={renameValue}
                            onChange={(e) => setRenameValue(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") handleRenameSubmit(session.id);
                              if (e.key === "Escape") setRenamingId(null);
                            }}
                            onBlur={() => handleRenameSubmit(session.id)}
                            onClick={(e) => e.stopPropagation()}
                            className="text-sm font-medium pr-2 leading-snug max-w-[200px] bg-nimbus-surface border border-nimbus-accent/50 rounded px-1.5 py-0.5 text-nimbus-text focus:outline-none"
                          />
                        ) : (
                          <h3 className={`text-sm font-medium pr-2 truncate leading-snug max-w-[200px] ${isActive ? 'text-nimbus-accent' : 'text-gray-200'}`}>
                            {displayName}
                          </h3>
                        )}
                        <span className="text-[10px] text-gray-600 font-mono whitespace-nowrap shrink-0">
                          {formatRelativeTime(session.last_message_at || session.created_at)}
                        </span>
                      </div>

                      {session.first_message_preview && (
                        <p className="text-xs text-gray-500 truncate mt-0.5 leading-snug">
                          {session.first_message_preview}
                        </p>
                      )}

                      <div className="flex items-center gap-2">
                        {/* Simplified Status: Only show pill for non-active, otherwise just dot if needed or strict styling */}
                        {isStatusActive ? (
                          <div className="flex items-center gap-1.5 text-[10px] text-emerald-500/80">
                            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500"></div>
                            Active
                          </div>
                        ) : (
                          <div className={`
                            flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border uppercase tracking-wider
                            ${status.color} scale-90 origin-left
                          `}>
                            {status.label}
                          </div>
                        )}

                        {session.llm_config?.model_id && (
                          <>
                            <span className="text-gray-700 mx-0.5">·</span>
                            <span className="text-[10px] text-gray-500 bg-gray-800 px-1.5 py-0.5 rounded font-mono">
                              {getShortModelName(session.llm_config.model_id)}
                            </span>
                          </>
                        )}

                        {session.workspace_path && (
                          <>
                            <span className="text-gray-700 mx-0.5">•</span>
                            <span className="text-[10px] text-gray-500 truncate max-w-[120px] font-mono" title={session.workspace_path}>
                              {session.workspace_path.startsWith('/') ? '..' : ''}/{session.workspace_path.split('/').pop()}
                            </span>
                          </>
                        )}

                        <span className="text-[10px] text-gray-600 font-mono ml-auto flex items-center gap-1">
                          <svg className="w-3 h-3 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" /></svg>
                          {session.message_count}
                        </span>
                      </div>
                    </div>

                    {/* Hover Actions */}
                    {!isSelectMode && (
                      <div className="absolute right-2 top-1/2 -translate-y-1/2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity bg-nimbus-bg shadow-[-10px_0_20px_#0c1220] pl-2">
                        {session.status === "interrupted" && (
                          <button
                            onClick={(e) => handleResumeSession(session.id, e)}
                            className="p-1.5 text-gray-500 hover:text-emerald-400 transition-colors rounded-md hover:bg-emerald-500/10"
                            title="Resume Session"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                          </button>
                        )}
                        <button
                          onClick={(e) => { e.stopPropagation(); setRenamingId(session.id); setRenameValue(displayName); }}
                          className="p-1.5 text-gray-500 hover:text-sky-400 transition-colors rounded-md hover:bg-sky-500/10"
                          title="Rename Session"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                        </button>
                        <button
                          onClick={(e) => handleDeleteSession(session.id, e)}
                          className="p-1.5 text-gray-500 hover:text-red-400 transition-colors rounded-md hover:bg-red-500/10"
                          title="Delete Session"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-nimbus-border bg-nimbus-bg/80 text-xs text-center text-gray-600">
          Nimbus v0.5.0 • Local Agent Server
        </div>
      </div>

      <CreateSessionDialog
        isOpen={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        onCreate={handleCreateSession}
      />

      <ConfirmDialog
        isOpen={confirmDialog !== null}
        title={confirmDialog?.type === "batch" ? "批量删除" : "删除会话"}
        message={
          confirmDialog?.type === "batch"
            ? `确定要删除选中的 ${selectedIds.size} 个会话吗？此操作不可撤销。`
            : "确定要删除这个会话吗？此操作不可撤销。"
        }
        confirmLabel="删除"
        cancelLabel="取消"
        variant="danger"
        onConfirm={handleConfirmAction}
        onCancel={() => setConfirmDialog(null)}
      />
    </>
  );
}
