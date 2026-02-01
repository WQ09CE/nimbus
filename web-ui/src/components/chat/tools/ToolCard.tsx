import React, { useState } from 'react';
import { ToolDisplay } from './ToolDisplay';

interface ToolCardProps {
  tool: {
    id?: string;
    name: string;
    args: any;
    result?: any;
    error?: string;
    status: "running" | "completed" | "failed";
    duration?: number;
  };
  defaultExpanded?: boolean;
}

export function ToolCard({ tool, defaultExpanded }: ToolCardProps) {
  // Heuristic: Only Read defaults to collapsed (to save space).
  // Write/Edit/Bash default to expanded (to show changes/actions).
  // Always expand if there is an error.
  const isRead = tool.name === 'Read';
  const isError = tool.status === 'failed' || !!tool.error;
  
  const heuristicExpanded = !isRead || isError;
  
  // Use state for toggle
  const [isExpanded, setIsExpanded] = useState(defaultExpanded ?? heuristicExpanded);

  // Extract summary
  let summary = "";
  if (tool.args) {
    if (tool.name === "Read" || tool.name === "Write" || tool.name === "Edit") {
        // Strict: Only show path if passed in correct argument to avoid misleading user
        // If LLM used wrong arg (e.g. 'filename'), summary should be empty/unknown
        // so user knows something is wrong with the args.
        summary = tool.args.file_path || "";
    } else if (tool.name === "Bash") {
        summary = tool.args.command;
    }
  }

  // Styles based on status
  const statusColor = 
    tool.status === "running" ? "bg-yellow-900/30 text-yellow-500 border-yellow-800/50" :
    tool.status === "completed" ? "bg-green-900/30 text-green-500 border-green-800/50" :
    "bg-red-900/30 text-red-500 border-red-800/50";

  const statusLabel = 
    tool.status === "running" ? "RUN" :
    tool.status === "completed" ? "OK" : "ERR";

  return (
    <div className="border border-gray-800 rounded bg-[#0d1117] overflow-hidden my-2 shadow-sm group/card transition-all duration-200">
      {/* Header */}
      <div 
        className="bg-[#161b22] px-3 py-2 flex justify-between items-center cursor-pointer hover:bg-[#21262d] transition-colors select-none"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-3 overflow-hidden">
          {/* Status Badge */}
          <span className={`text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded border ${statusColor}`}>
            {statusLabel}
          </span>
          
          {/* Tool Name */}
          <span className="text-sm font-mono text-purple-300 font-semibold whitespace-nowrap">
            {tool.name}
          </span>
          
          {/* Summary (File path or command) */}
          {summary && (
            <span className="text-xs text-gray-500 font-mono truncate ml-1 opacity-70 max-w-[200px] sm:max-w-[300px]" title={summary}>
              {summary}
            </span>
          )}
          
          {/* Duration */}
          {tool.duration && (
            <span className="text-[10px] text-gray-600 font-mono whitespace-nowrap ml-auto pl-2">
              {tool.duration}ms
            </span>
          )}
        </div>

        {/* Toggle Icon */}
        <span className="text-gray-500 text-xs ml-2 transform transition-transform duration-200" style={{ transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)' }}>
          ▼
        </span>
      </div>

      {/* Body */}
      {isExpanded && (
        <div className="border-t border-gray-800">
          <ToolDisplay tool={tool} isExpanded={true} />
        </div>
      )}
    </div>
  );
}
