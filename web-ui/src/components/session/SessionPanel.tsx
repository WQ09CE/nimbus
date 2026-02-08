"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Session,
  listSessions,
  deleteSession,
  deleteSessions,
  interruptSession,
  resumeSession,
} from "@/lib/api/sessions";
import { useChatStore } from "@/stores";
import { CreateSessionDialog, CreateSessionConfig } from "./CreateSessionDialog";

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

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      const list = await listSessions();
      setSessions(list.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      ));
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

  const handleDeleteSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("确定要删除这个会话吗？")) return;

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

  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return;
    if (!confirm(`确定要删除选中的 ${selectedIds.size} 个会话吗？`)) return;

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

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    return isToday
      ? date.toLocaleTimeString("zh-CN", { hour: '2-digit', minute: '2-digit' })
      : date.toLocaleDateString("zh-CN", { month: '2-digit', day: '2-digit' });
  };

  if (!isOpen) return null;

  return (
    <>
      <div className="fixed inset-0 bg-black/60 z-40 backdrop-blur-sm" onClick={onClose} />

      {/* Sidebar Panel */}
      <div className="fixed top-0 left-0 bottom-0 w-[400px] bg-[#1a1a1a] border-r border-[#333] z-50 shadow-2xl flex flex-col transform transition-transform duration-300 animate-in slide-in-from-left">

        {/* Header */}
        <div className="px-5 py-4 border-b border-[#333] flex items-center justify-between bg-[#1f1f1f]">
          <div>
            <h2 className="text-lg font-semibold text-gray-100 tracking-tight">Sessions</h2>
            <p className="text-[11px] text-gray-500 font-mono mt-0.5 uppercase tracking-wider">
              {sessions.length} Conversations
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setIsSelectMode(!isSelectMode)}
              className={`p-2 rounded-lg transition-colors ${isSelectMode ? 'bg-[#333] text-white' : 'text-gray-400 hover:bg-[#333]'}`}
              title="批量管理"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" /></svg>
            </button>
            <button
              onClick={() => setShowCreateDialog(true)}
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium rounded-lg transition-colors shadow-lg shadow-blue-500/20 flex items-center gap-1.5"
            >
              <span>+</span> New
            </button>
          </div>
        </div>

        {/* Batch Actions Bar */}
        {isSelectMode && (
          <div className="px-4 py-2 bg-[#2a2a2a] border-b border-[#333] flex justify-between items-center animate-in slide-in-from-top-2">
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

        {/* List */}
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          {loading ? (
            <div className="flex justify-center py-12">
              <div className="w-6 h-6 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin" />
            </div>
          ) : sessions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-gray-500 px-8 text-center">
              <div className="text-4xl mb-3 opacity-20">💬</div>
              <p className="text-sm">No conversations yet.</p>
              <button
                onClick={() => setShowCreateDialog(true)}
                className="mt-4 text-blue-400 hover:text-blue-300 text-xs font-medium"
              >
                Start a new chat
              </button>
            </div>
          ) : (
            <div className="divide-y divide-[#252525]">
              {sessions.map(session => {
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
                      ${isActive ? 'bg-blue-500/10 border-l-2 border-l-blue-500' : 'hover:bg-[#252525] border-l-2 border-l-transparent'}
                      ${isSelected ? 'bg-[#2a2a2a]' : ''}
                    `}
                  >
                    {/* Select Checkbox */}
                    {isSelectMode && (
                      <div className="absolute left-2 top-1/2 -translate-y-1/2 z-10">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(session.id)}
                          className="w-4 h-4 rounded border-gray-600 bg-[#1a1a1a] text-blue-500 focus:ring-offset-0"
                        />
                      </div>
                    )}

                    <div className={isSelectMode ? "pl-6" : ""}>
                      <div className="flex justify-between items-baseline mb-1">
                        <h3 className={`text-sm font-medium pr-2 truncate leading-snug max-w-[200px] ${isActive ? 'text-blue-400' : 'text-gray-200'}`}>
                          {displayName}
                        </h3>
                        <span className="text-[10px] text-gray-600 font-mono whitespace-nowrap shrink-0">
                          {formatDate(session.created_at)}
                        </span>
                      </div>

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
                      <div className="absolute right-2 top-1/2 -translate-y-1/2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity bg-[#1a1a1a] shadow-[-10px_0_20px_#1a1a1a] pl-2">
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
        <div className="p-4 border-t border-[#333] bg-[#1f1f1f] text-xs text-center text-gray-600">
          Nimbus v0.5.0 • Local Agent Server
        </div>
      </div>

      <CreateSessionDialog
        isOpen={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        onCreate={handleCreateSession}
      />
    </>
  );
}
