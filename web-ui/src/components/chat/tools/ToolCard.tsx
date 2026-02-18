import React, { useState } from 'react';
import { ToolDisplay } from './ToolDisplay';
import { DispatchCard } from './DispatchCard';
import type { ToolCall, ToolResult } from '@/lib/api';

interface ToolCardProps {
  tool: {
    id?: string;
    name: string;
    args: any;
    result?: any;
    error?: string;
    status: "running" | "completed" | "failed";
    duration?: number;
    agentType?: "core" | "dispatch";
    subCalls?: ToolCall[];
    subResults?: ToolResult[];
  };
  defaultExpanded?: boolean;
}

export function ToolCard({ tool, defaultExpanded }: ToolCardProps) {
  // Dispatch tools get their own dedicated card
  if (tool.name === 'Dispatch') {
    return <DispatchCard tool={tool} />;
  }

  const [isExpanded, setIsExpanded] = useState(defaultExpanded ?? false);

  // Status Colors & Icons
  const getStatusStyle = () => {
    switch (tool.status) {
      case "running":
        return {
          icon: <div className="w-2 h-2 rounded-full bg-amber-400 animate-pulse ring-[3px] ring-amber-400/20" />,
          border: "border-yellow-500/30",
          bg: "bg-yellow-500/5",
        };
      case "completed":
        return {
          icon: <div className="w-2 h-2 rounded-full bg-emerald-400 ring-[3px] ring-emerald-400/20" />,
          border: "border-emerald-500/30",
          bg: "bg-emerald-500/5",
        };
      case "failed":
        return {
          icon: <div className="w-2 h-2 rounded-full bg-red-400 ring-[3px] ring-red-400/20" />,
          border: "border-red-500/30",
          bg: "bg-red-500/5",
        };
      default:
        return { icon: null, border: "border-gray-800", bg: "bg-gray-900" };
    }
  };

  const style = getStatusStyle();
  const isDispatch = tool.agentType === "dispatch";

  // Summary extraction
  let summary = "";
  if (tool.args) {
    // Try to find a file path argument by common names
    const pathArg = tool.args.path || tool.args.file_path || tool.args.target_file || tool.args.TargetFile || tool.args.AbsolutePath || tool.args.filename || tool.args.file;

    // Try to find a command argument
    const cmdArg = tool.args.command || tool.args.cmd || tool.args.CommandLine || tool.args.command_line;

    if (["Read", "Write", "Edit", "view_file", "replace_file_content", "write_to_file", "edit_file"].some(n => tool.name.toLowerCase().includes(n.toLowerCase()))) {
      if (typeof pathArg === 'string') {
        const parts = pathArg.split('/');
        const fileName = parts.pop() || pathArg;
        const parentDir = parts.pop();
        summary = parentDir ? `${parentDir}/${fileName}` : fileName;

        // Append line range for Read tool with offset/limit
        if (tool.name === "Read") {
          const offset = tool.args.offset as number | undefined;
          const limit = tool.args.limit as number | undefined;
          if (offset && limit) {
            summary += ` :${offset}-${offset + limit}`;
          } else if (offset) {
            summary += ` :${offset}+`;
          } else if (limit) {
            summary += ` :1-${limit}`;
          }
        }
      }
    } else if (["Bash", "RunCommand", "run_command", "execute"].some(n => tool.name.toLowerCase().includes(n.toLowerCase()))) {
      if (typeof cmdArg === 'string') {
        summary = cmdArg;
      }
    } else if (tool.name.toLowerCase().includes("search") && (tool.args.query || tool.args.Query)) {
      summary = (tool.args.query || tool.args.Query) as string;
    }
  }

  return (
    <div data-testid="tool-card" className={`
      group/card overflow-hidden rounded-lg border transition-all duration-200 relative
      ${isExpanded
        ? `bg-[#0d1117] ${style.border}`
        : isDispatch
          ? "bg-purple-900/10 border-purple-500/20 hover:border-purple-500/30"
          : "bg-black/20 border-white/5 hover:border-white/10"
      }
    `}>
      {/* Dispatch Agent Indicator Strip */}
      {isDispatch && (
        <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-purple-500 shadow-[0_0_8px_rgba(168,85,247,0.5)]" />
      )}

      {/* Header */}
      <div
        className={`px-3 py-2.5 flex items-center justify-between cursor-pointer select-none ${isDispatch ? "pl-4" : ""}`}
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-3 min-w-0">
          {/* Status Indicator */}
          <div className="flex items-center justify-center w-4 h-4 shrink-0">
            {style.icon}
          </div>

          {/* Agent Label (only for Dispatch) */}
          {isDispatch && (
            <span className="text-[10px] uppercase font-bold text-purple-400 bg-purple-500/10 px-1.5 py-0.5 rounded border border-purple-500/20 tracking-wider">
              Executor
            </span>
          )}

          {/* Tool Name */}
          <span className={`text-[13px] font-mono font-medium tracking-wide shrink-0 ${isDispatch ? "text-purple-200" : "text-gray-300"}`}>
            {tool.name}
          </span>

          {/* Divider */}
          {summary && <span className="text-gray-700 text-xs">/</span>}

          {/* Summary */}
          {summary && (
            <span className="text-[12px] font-mono text-gray-500 truncate opacity-80 group-hover/card:opacity-100 transition-opacity">
              {summary}
            </span>
          )}
        </div>

        <div className="flex items-center gap-3 shrink-0 ml-2">
          {/* Duration */}
          {tool.duration && (
            <span className="text-[10px] font-mono text-gray-600">
              {tool.duration}ms
            </span>
          )}

          {/* Chevron */}
          <svg
            className={`w-3 h-3 text-gray-600 transition-transform duration-200 ${isExpanded ? "rotate-180" : ""}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>

      {/* Body */}
      {isExpanded && (
        <div className="border-t border-gray-800/50 bg-[#0d1117]/50">
          <ToolDisplay tool={tool} isExpanded={true} />
        </div>
      )}
    </div>
  );
}
