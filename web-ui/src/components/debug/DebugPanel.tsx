"use client";

import { useState, useEffect, useCallback } from "react";
import { useChatStore } from "@/stores";

interface ContextMessage {
  role: string;
  content?: string;
  tool_calls?: Array<{
    id: string;
    function: {
      name: string;
      arguments: string;
    };
  }>;
  tool_call_id?: string;
  name?: string;
}

interface DebugContext {
  session_id: string;
  total_messages: number;
  total_tokens: number;
  pinned_tokens: number;
  frame_tokens: number;
  messages: ContextMessage[];
}

const API_BASE = typeof window !== "undefined" 
  ? `${window.location.protocol}//${window.location.hostname}:4096`
  : "http://localhost:4096";

export function DebugPanel() {
  const { session, isStreaming } = useChatStore();
  const [isOpen, setIsOpen] = useState(false);
  const [context, setContext] = useState<DebugContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchContext = useCallback(async () => {
    if (!session?.id) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch(`${API_BASE}/debug/sessions/${session.id}/context`);
      if (response.ok) {
        const data = await response.json();
        setContext(data);
      } else if (response.status === 404) {
        setContext(null);
        setError("Session not initialized yet. Send a message first.");
      } else {
        setError(`Error: ${response.status}`);
      }
    } catch (err) {
      setError(`Failed to fetch: ${err}`);
    } finally {
      setLoading(false);
    }
  }, [session?.id]);

  // Auto-refresh when streaming ends
  useEffect(() => {
    if (isOpen && autoRefresh && !isStreaming && session?.id) {
      fetchContext();
    }
  }, [isOpen, autoRefresh, isStreaming, session?.id, fetchContext]);

  // Manual refresh
  const handleRefresh = () => {
    fetchContext();
  };

  const formatContent = (content: string | undefined) => {
    if (!content) return <span className="text-gray-600 italic">null</span>;
    if (content.length > 500) {
      return content.slice(0, 500) + "...";
    }
    return content;
  };

  const formatToolCalls = (toolCalls: ContextMessage["tool_calls"]) => {
    if (!toolCalls || toolCalls.length === 0) return null;
    return (
      <div className="mt-1 pl-2 border-l-2 border-blue-800">
        {toolCalls.map((tc, i) => (
          <div key={i} className="text-xs">
            <span className="text-blue-400">{tc.function.name}</span>
            <span className="text-gray-600">(</span>
            <span className="text-gray-400">
              {tc.function.arguments.length > 100 
                ? tc.function.arguments.slice(0, 100) + "..." 
                : tc.function.arguments}
            </span>
            <span className="text-gray-600">)</span>
          </div>
        ))}
      </div>
    );
  };

  const getRoleColor = (role: string) => {
    switch (role) {
      case "system": return "text-purple-400";
      case "user": return "text-green-400";
      case "assistant": return "text-blue-400";
      case "tool": return "text-yellow-400";
      default: return "text-gray-400";
    }
  };

  if (!isOpen) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="fixed right-4 top-1/2 -translate-y-1/2 bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 p-2 rounded-l-lg border border-r-0 border-gray-700 transition-colors z-50"
        title="Open Debug Panel"
      >
        <span className="text-lg">🔍</span>
      </button>
    );
  }

  return (
    <div className="fixed right-0 top-0 h-full w-[500px] bg-gray-900 border-l border-gray-700 shadow-2xl z-50 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 bg-gray-800">
        <div className="flex items-center gap-2">
          <span className="text-lg">🔍</span>
          <h2 className="text-sm font-semibold text-gray-200">Debug: Context</h2>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-gray-400">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="w-3 h-3"
            />
            Auto
          </label>
          <button
            onClick={handleRefresh}
            disabled={loading}
            className="text-xs bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded text-gray-300"
          >
            {loading ? "..." : "↻"}
          </button>
          <button
            onClick={() => setIsOpen(false)}
            className="text-gray-400 hover:text-gray-200 text-lg"
          >
            ×
          </button>
        </div>
      </div>

      {/* Stats */}
      {context && (
        <div className="px-4 py-2 bg-gray-800/50 border-b border-gray-700 text-xs flex gap-4">
          <span className="text-gray-400">
            Messages: <span className="text-gray-200">{context.total_messages}</span>
          </span>
          <span className="text-gray-400">
            Tokens: <span className="text-gray-200">{context.total_tokens}</span>
          </span>
          <span className="text-gray-400">
            Pinned: <span className="text-purple-400">{context.pinned_tokens}</span>
          </span>
          <span className="text-gray-400">
            Frame: <span className="text-blue-400">{context.frame_tokens}</span>
          </span>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 text-xs font-mono">
        {error && (
          <div className="text-yellow-500 bg-yellow-900/20 p-2 rounded">
            {error}
          </div>
        )}

        {!context && !error && (
          <div className="text-gray-500 text-center py-8">
            {loading ? "Loading..." : "No context yet. Send a message first."}
          </div>
        )}

        {context?.messages.map((msg, i) => (
          <div key={i} className="bg-gray-800/50 rounded p-2 border border-gray-700/50">
            <div className="flex items-center gap-2 mb-1">
              <span className={`font-semibold ${getRoleColor(msg.role)}`}>
                [{msg.role}]
              </span>
              {msg.name && (
                <span className="text-yellow-500 text-xs">
                  ({msg.name})
                </span>
              )}
              {msg.tool_call_id && (
                <span className="text-gray-600 text-xs">
                  id: {msg.tool_call_id.slice(0, 12)}...
                </span>
              )}
            </div>
            <div className="text-gray-300 whitespace-pre-wrap break-words">
              {formatContent(msg.content)}
            </div>
            {formatToolCalls(msg.tool_calls)}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="px-4 py-2 border-t border-gray-700 text-xs text-gray-500">
        Session: {session?.id || "none"}
      </div>
    </div>
  );
}
