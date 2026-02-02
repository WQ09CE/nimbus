"use client";

import { useState, useEffect, useCallback } from "react";
import { 
  Session, 
  listSessions, 
  deleteSession, 
  deleteSessions,
  createSession,
  interruptSession,
  resumeSession,
} from "@/lib/api/sessions";
import { useChatStore } from "@/stores";
import { PathInput } from "./PathInput";

interface SessionPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

type SessionStatus = "active" | "interrupted" | "completed" | "deleted";

const STATUS_CONFIG: Record<SessionStatus, { label: string; color: string; icon: string }> = {
  active: { label: "运行中", color: "bg-green-500", icon: "🟢" },
  interrupted: { label: "已暂停", color: "bg-yellow-500", icon: "⏸️" },
  completed: { label: "已完成", color: "bg-blue-500", icon: "✅" },
  deleted: { label: "已删除", color: "bg-gray-500", icon: "🗑️" },
};

export function SessionPanel({ isOpen, onClose }: SessionPanelProps) {
  const { session: currentSession, switchSession, isStreaming } = useChatStore();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(false);
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [newSessionName, setNewSessionName] = useState("");
  const [newSessionPath, setNewSessionPath] = useState("");
  const [creating, setCreating] = useState(false);
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
      // Reset select mode when panel closes
      setIsSelectMode(false);
      setSelectedIds(new Set());
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
    if (!confirm("确定要删除这个 session 吗？所有对话历史将被清除。")) return;
    
    setActionLoading(id);
    try {
      await deleteSession(id);
      setSessions(prev => prev.filter(s => s.id !== id));
      setSelectedIds(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      // If deleting current session, clear it
      if (currentSession?.id === id) {
        switchSession(null);
      }
    } catch (err) {
      console.error("Failed to delete session:", err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return;
    
    const count = selectedIds.size;
    if (!confirm(`确定要删除选中的 ${count} 个 session 吗？所有对话历史将被清除。`)) return;
    
    setActionLoading("batch");
    try {
      await deleteSessions(Array.from(selectedIds));
      setSessions(prev => prev.filter(s => !selectedIds.has(s.id)));
      
      // If current session was deleted, clear it
      if (currentSession && selectedIds.has(currentSession.id)) {
        switchSession(null);
      }
      
      setSelectedIds(new Set());
      setIsSelectMode(false);
    } catch (err) {
      console.error("Failed to batch delete sessions:", err);
    } finally {
      setActionLoading(null);
    }
  };

  const toggleSelect = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === sessions.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(sessions.map(s => s.id)));
    }
  };

  const handleInterruptSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    
    setActionLoading(id);
    try {
      const result = await interruptSession(id);
      if (result.success) {
        // Update session status locally
        setSessions(prev => prev.map(s => 
          s.id === id ? { ...s, status: "interrupted" } : s
        ));
        alert(`Session 已暂停\n保存在第 ${result.checkpoint?.step_index} 步\n内存消息: ${result.checkpoint?.memory_messages}`);
      } else {
        alert(`暂停失败: ${result.error}`);
      }
    } catch (err) {
      console.error("Failed to interrupt session:", err);
      alert("暂停失败");
    } finally {
      setActionLoading(null);
    }
  };

  const handleResumeSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    
    setActionLoading(id);
    try {
      const result = await resumeSession(id);
      if (result.success) {
        // Update session status locally
        setSessions(prev => prev.map(s => 
          s.id === id ? { ...s, status: "active" } : s
        ));
        alert(`Session 已恢复\n从第 ${result.restored_step} 步继续`);
        // Switch to this session
        const session = sessions.find(s => s.id === id);
        if (session) {
          switchSession({ ...session, status: "active" });
          onClose();
        }
      } else {
        alert(`恢复失败: ${result.error}`);
      }
    } catch (err) {
      console.error("Failed to resume session:", err);
      alert("恢复失败");
    } finally {
      setActionLoading(null);
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
    if (!path || path === ".") return "当前目录";
    if (path === "~") return "Home";
    // Handle ~ paths
    if (path.startsWith("~/")) {
      const parts = path.split("/");
      return parts[parts.length - 1] || path;
    }
    const parts = path.split("/");
    return parts[parts.length - 1] || path;
  };

  const getWorkspaceTooltip = (path?: string) => {
    if (!path) return "使用服务器当前目录";
    if (path === ".") return "使用服务器当前目录";
    return `工作目录: ${path}`;
  };

  const getStatusConfig = (status: string) => {
    return STATUS_CONFIG[status as SessionStatus] || STATUS_CONFIG.active;
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" onClick={onClose}>
      <div 
        className="bg-gray-900 border border-gray-700 rounded-lg w-[700px] max-h-[80vh] flex flex-col shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <div>
            <h2 className="text-lg font-semibold text-gray-200">Sessions</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {isSelectMode 
                ? `已选择 ${selectedIds.size} 个` 
                : "管理你的对话会话"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {isSelectMode ? (
              <>
                <button
                  onClick={toggleSelectAll}
                  className="text-xs text-gray-400 hover:text-gray-200 px-2 py-1.5"
                >
                  {selectedIds.size === sessions.length ? "取消全选" : "全选"}
                </button>
                <button
                  onClick={handleBatchDelete}
                  disabled={selectedIds.size === 0 || actionLoading === "batch"}
                  className="text-sm bg-red-600 hover:bg-red-700 disabled:bg-gray-600 px-3 py-1.5 rounded text-white"
                >
                  {actionLoading === "batch" ? "删除中..." : `删除 (${selectedIds.size})`}
                </button>
                <button
                  onClick={() => {
                    setIsSelectMode(false);
                    setSelectedIds(new Set());
                  }}
                  className="text-sm text-gray-400 hover:text-gray-200 px-2 py-1.5"
                >
                  取消
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => setIsSelectMode(true)}
                  className="text-sm text-gray-400 hover:text-gray-200 px-2 py-1.5"
                  title="批量管理"
                >
                  ☑️
                </button>
                <button
                  onClick={fetchSessions}
                  className="text-sm text-gray-400 hover:text-gray-200 px-2 py-1.5"
                  title="刷新"
                >
                  🔄
                </button>
                <button
                  onClick={() => setShowNewDialog(true)}
                  className="text-sm bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded text-white"
                >
                  + 新建
                </button>
              </>
            )}
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-200 text-xl px-2"
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
                <label className="block text-xs text-gray-400 mb-1">Session 名称 *</label>
                <input
                  type="text"
                  value={newSessionName}
                  onChange={e => setNewSessionName(e.target.value)}
                  placeholder="例如: nimbus-dev"
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
                  autoFocus
                  onKeyDown={e => {
                    if (e.key === "Enter" && newSessionName.trim()) {
                      handleCreateSession();
                    }
                  }}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">工作目录 (可选)</label>
                <PathInput
                  value={newSessionPath}
                  onChange={setNewSessionPath}
                  placeholder="输入路径或按 Tab 补全，例如: ~/projects/"
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
                />
                <div className="flex items-center gap-2 mt-2">
                  <span className="text-xs text-gray-500">快捷选择:</span>
                  <button
                    type="button"
                    onClick={() => setNewSessionPath(".")}
                    className="text-xs bg-gray-700 hover:bg-gray-600 px-2 py-0.5 rounded text-gray-300"
                  >
                    当前目录
                  </button>
                  <button
                    type="button"
                    onClick={() => setNewSessionPath("~/")}
                    className="text-xs bg-gray-700 hover:bg-gray-600 px-2 py-0.5 rounded text-gray-300"
                  >
                    Home
                  </button>
                  <button
                    type="button"
                    onClick={() => setNewSessionPath("~/Desktop/")}
                    className="text-xs bg-gray-700 hover:bg-gray-600 px-2 py-0.5 rounded text-gray-300"
                  >
                    桌面
                  </button>
                </div>
                <p className="text-xs text-gray-500 mt-1">
                  💡 输入路径后按 <kbd className="bg-gray-700 px-1 rounded">Tab</kbd> 或 <kbd className="bg-gray-700 px-1 rounded">↓</kbd> 显示补全
                </p>
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
              <div className="text-4xl mb-2">📭</div>
              暂无 session，点击「新建」创建一个
            </div>
          ) : (
            <div className="divide-y divide-gray-800">
              {sessions.map(session => {
                const statusConfig = getStatusConfig(session.status);
                const isCurrentSession = currentSession?.id === session.id;
                const isActionLoading = actionLoading === session.id;
                
                return (
                  <div
                    key={session.id}
                    onClick={() => isSelectMode ? toggleSelect(session.id, { stopPropagation: () => {} } as React.MouseEvent) : handleSelectSession(session)}
                    className={`px-4 py-3 cursor-pointer hover:bg-gray-800/50 transition-colors ${
                      isCurrentSession ? "bg-blue-900/20 border-l-2 border-blue-500" : ""
                    } ${selectedIds.has(session.id) ? "bg-red-900/20" : ""}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      {/* Checkbox for select mode */}
                      {isSelectMode && (
                        <div 
                          className="flex items-center pt-0.5"
                          onClick={(e) => toggleSelect(session.id, e)}
                        >
                          <input
                            type="checkbox"
                            checked={selectedIds.has(session.id)}
                            onChange={() => {}}
                            className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500 focus:ring-offset-gray-900"
                          />
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        {/* Title Row */}
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium text-gray-200 truncate">
                            {session.name || session.id.slice(0, 12)}
                          </span>
                          {isCurrentSession && (
                            <span className="text-xs bg-blue-600 px-1.5 py-0.5 rounded text-white shrink-0">
                              当前
                            </span>
                          )}
                          {isCurrentSession && isStreaming && (
                            <span className="text-xs bg-green-600 px-1.5 py-0.5 rounded text-white shrink-0 animate-pulse">
                              运行中
                            </span>
                          )}
                          <span 
                            className={`text-xs px-1.5 py-0.5 rounded text-white shrink-0 ${statusConfig.color}`}
                            title={statusConfig.label}
                          >
                            {statusConfig.icon} {statusConfig.label}
                          </span>
                        </div>
                        
                        {/* Info Row */}
                        <div className="flex items-center gap-3 mt-1 text-xs text-gray-500 flex-wrap">
                          <span title={getWorkspaceTooltip(session.workspace_path)}>📁 {getWorkspaceName(session.workspace_path)}</span>
                          <span title="消息数">💬 {session.message_count}</span>
                          <span title="创建时间">🕐 {formatDate(session.created_at)}</span>
                          <span className="text-gray-600 font-mono text-[10px]">{session.id.slice(0, 16)}</span>
                        </div>
                      </div>
                      
                      {/* Action Buttons */}
                      <div className="flex items-center gap-1 shrink-0">
                        {/* Interrupt button - only for current active session */}
                        {isCurrentSession && isStreaming && (
                          <button
                            onClick={(e) => handleInterruptSession(session.id, e)}
                            disabled={isActionLoading}
                            className="text-yellow-500 hover:text-yellow-400 p-1.5 transition-colors disabled:opacity-50"
                            title="暂停执行"
                          >
                            {isActionLoading ? "⏳" : "⏸️"}
                          </button>
                        )}
                        
                        {/* Resume button - for interrupted sessions */}
                        {session.status === "interrupted" && (
                          <button
                            onClick={(e) => handleResumeSession(session.id, e)}
                            disabled={isActionLoading}
                            className="text-green-500 hover:text-green-400 p-1.5 transition-colors disabled:opacity-50"
                            title="恢复执行"
                          >
                            {isActionLoading ? "⏳" : "▶️"}
                          </button>
                        )}
                        
                        {/* Delete button */}
                        <button
                          onClick={(e) => handleDeleteSession(session.id, e)}
                          disabled={isActionLoading}
                          className="text-gray-600 hover:text-red-400 p-1.5 transition-colors disabled:opacity-50"
                          title="删除"
                        >
                          🗑️
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t border-gray-700 bg-gray-800/30">
          <div className="flex items-center justify-between text-xs text-gray-500">
            <span>共 {sessions.length} 个 session</span>
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-green-500"></span> 运行中
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-yellow-500"></span> 已暂停
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
