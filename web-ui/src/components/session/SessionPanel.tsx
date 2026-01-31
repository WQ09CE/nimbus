"use client";

import { useState, useEffect, useCallback } from "react";
import { Session, listSessions, deleteSession, createSession } from "@/lib/api/sessions";
import { useChatStore } from "@/stores";

interface SessionPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export function SessionPanel({ isOpen, onClose }: SessionPanelProps) {
  const { session: currentSession, switchSession } = useChatStore();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(false);
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [newSessionName, setNewSessionName] = useState("");
  const [newSessionPath, setNewSessionPath] = useState("");
  const [creating, setCreating] = useState(false);

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
    }
  }, [isOpen, fetchSessions]);

  const handleCreateSession = async () => {
    if (!newSessionName.trim()) return;
    
    setCreating(true);
    try {
      const session = await createSession({
        name: newSessionName.trim(),
        workspace_path: newSessionPath.trim() || undefined,
      });
      setSessions(prev => [session, ...prev]);
      setShowNewDialog(false);
      setNewSessionName("");
      setNewSessionPath("");
      // Switch to new session
      switchSession(session);
      onClose();
    } catch (err) {
      console.error("Failed to create session:", err);
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("确定要删除这个 session 吗？")) return;
    
    try {
      await deleteSession(id);
      setSessions(prev => prev.filter(s => s.id !== id));
    } catch (err) {
      console.error("Failed to delete session:", err);
    }
  };

  const handleSelectSession = (session: Session) => {
    switchSession(session);
    onClose();
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const getWorkspaceName = (path?: string) => {
    if (!path) return "默认";
    const parts = path.split("/");
    return parts[parts.length - 1] || path;
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" onClick={onClose}>
      <div 
        className="bg-gray-900 border border-gray-700 rounded-lg w-[600px] max-h-[80vh] flex flex-col shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <h2 className="text-lg font-semibold text-gray-200">Sessions</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowNewDialog(true)}
              className="text-sm bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded text-white"
            >
              + 新建
            </button>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-200 text-xl"
            >
              ×
            </button>
          </div>
        </div>

        {/* New Session Dialog */}
        {showNewDialog && (
          <div className="px-4 py-3 border-b border-gray-700 bg-gray-800/50">
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-400 mb-1">Session 名称</label>
                <input
                  type="text"
                  value={newSessionName}
                  onChange={e => setNewSessionName(e.target.value)}
                  placeholder="例如: nimbus-dev"
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">工作目录 (可选)</label>
                <input
                  type="text"
                  value={newSessionPath}
                  onChange={e => setNewSessionPath(e.target.value)}
                  placeholder="例如: /Users/xxx/projects/my-app"
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => {
                    setShowNewDialog(false);
                    setNewSessionName("");
                    setNewSessionPath("");
                  }}
                  className="text-sm px-3 py-1.5 text-gray-400 hover:text-gray-200"
                >
                  取消
                </button>
                <button
                  onClick={handleCreateSession}
                  disabled={!newSessionName.trim() || creating}
                  className="text-sm bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 px-3 py-1.5 rounded text-white"
                >
                  {creating ? "创建中..." : "创建"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Session List */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="text-center py-8 text-gray-500">Loading...</div>
          ) : sessions.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              暂无 session，点击「新建」创建一个
            </div>
          ) : (
            <div className="divide-y divide-gray-800">
              {sessions.map(session => (
                <div
                  key={session.id}
                  onClick={() => handleSelectSession(session)}
                  className={`px-4 py-3 cursor-pointer hover:bg-gray-800/50 transition-colors ${
                    currentSession?.id === session.id ? "bg-blue-900/20 border-l-2 border-blue-500" : ""
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-gray-200 truncate">
                          {session.name || session.id.slice(0, 12)}
                        </span>
                        {currentSession?.id === session.id && (
                          <span className="text-xs bg-blue-600 px-1.5 py-0.5 rounded text-white">
                            当前
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 mt-1 text-xs text-gray-500">
                        <span>📁 {getWorkspaceName(session.workspace_path)}</span>
                        <span>💬 {session.message_count}</span>
                        <span>{formatDate(session.created_at)}</span>
                      </div>
                    </div>
                    <button
                      onClick={(e) => handleDeleteSession(session.id, e)}
                      className="text-gray-600 hover:text-red-400 p-1 transition-colors"
                      title="删除"
                    >
                      🗑️
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
