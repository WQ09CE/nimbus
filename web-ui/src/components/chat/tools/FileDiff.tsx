import React, { useState, useMemo } from 'react';
import { diffLines } from 'diff';

interface FileDiffProps {
  name: string; // Edit or Write
  args: any;
  result?: any;
  error?: string;
  status: "running" | "completed" | "failed";
}

export function FileDiff({ name, args, result, error, status }: FileDiffProps) {
  const [isExpanded, setIsExpanded] = useState(true);
  const safeArgs = args || {};
  const filePath = safeArgs.file_path || safeArgs.path || safeArgs.filename || safeArgs.file || "unknown";

  // File name (last segment) + parent dir for display
  const pathParts = filePath.split('/');
  const fileName = pathParts.pop() || filePath;
  const parentDir = pathParts.slice(-2).join('/'); // last 2 dirs for context

  // Stats for Write: line count + byte size
  const writeContent: string = safeArgs.content || '';
  const writeLineCount = writeContent ? writeContent.split('\n').length : 0;
  const writeByteCount = writeContent ? new TextEncoder().encode(writeContent).length : 0;
  const writeSizeLabel = writeByteCount > 1024
    ? `${(writeByteCount / 1024).toFixed(1)} KB`
    : `${writeByteCount} B`;

  // Stats for Edit: +added / -removed lines
  const editStats = useMemo(() => {
    if (name !== 'Edit') return null;
    const oldText = safeArgs.old_string || safeArgs.old_text || '';
    const newText = safeArgs.new_string || safeArgs.new_text || '';
    if (!oldText && !newText) return null;
    try {
      const diffs = diffLines(oldText, newText);
      let added = 0, removed = 0;
      diffs.forEach(p => {
        const lines = p.value.split('\n').filter((l, i, arr) => !(i === arr.length - 1 && l === '')).length;
        if (p.added) added += lines;
        else if (p.removed) removed += lines;
      });
      return { added, removed };
    } catch { return null; }
  }, [name, safeArgs.old_string, safeArgs.old_text, safeArgs.new_string, safeArgs.new_text]);

  return (
    <div className="font-mono text-sm bg-[#0d1117] rounded-md border border-gray-800 overflow-hidden flex flex-col my-2">
      {/* Header */}
      <div
        className="flex items-center justify-between p-2.5 bg-[#161b22] cursor-pointer hover:bg-[#1f242e] transition-colors select-none"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        {/* Left: icon + file path */}
        <div className="flex gap-2 items-center min-w-0 flex-1">
          <span className="text-purple-400 shrink-0 text-base">
            {name === 'Edit' ? '✏️' : '📄'}
          </span>
          <div className="flex flex-col min-w-0">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-[11px] font-bold text-gray-200 truncate" title={filePath}>
                {fileName}
              </span>
              {parentDir && (
                <span className="text-[10px] text-gray-500 truncate hidden sm:block" title={filePath}>
                  {parentDir}
                </span>
              )}
            </div>
            {/* Full path on second line for context */}
            <span className="text-[10px] text-gray-600 truncate" title={filePath}>
              {filePath}
            </span>
          </div>
        </div>

        {/* Right: stats + status + chevron */}
        <div className="flex items-center gap-3 text-xs shrink-0 ml-2">
          {/* Edit stats: +N -N */}
          {name === 'Edit' && editStats && status !== 'running' && (
            <div className="flex items-center gap-1.5 font-mono">
              <span className="text-emerald-400 text-[11px]">+{editStats.added}</span>
              <span className="text-red-400 text-[11px]">-{editStats.removed}</span>
            </div>
          )}
          {/* Write stats: N lines, X KB */}
          {name === 'Write' && writeContent && status !== 'running' && (
            <div className="flex items-center gap-1.5 font-mono text-[10px] text-gray-500">
              <span>{writeLineCount} lines</span>
              <span className="text-gray-700">·</span>
              <span>{writeSizeLabel}</span>
            </div>
          )}

          {/* Status badge */}
          {status === "running" ? (
            <span className="flex items-center gap-1.5 text-blue-400 text-[11px]">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500" />
              </span>
              {name === 'Write'
                ? writeLineCount > 0 ? `Writing ${writeLineCount} lines` : 'Writing…'
                : 'Editing…'}
            </span>
          ) : error ? (
            <span className="px-2 py-0.5 rounded font-bold text-red-400 bg-red-500/10 border border-red-500/20">Failed</span>
          ) : (
            <span className="px-2 py-0.5 rounded font-bold text-green-400 bg-green-500/10 border border-green-500/20">Done</span>
          )}

          {/* Chevron */}
          <span className="text-gray-500 w-4 text-center">
            {isExpanded ? (
              <svg fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
              </svg>
            ) : (
              <svg fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
              </svg>
            )}
          </span>
        </div>
      </div>

      {/* Body: Diff Content (仅在展开时显示) */}
      {isExpanded && (
        <div className="border-t border-gray-800">
          {name === 'Write' && safeArgs.content ? (
            // Write: Show content being written with line numbers
            <div className="bg-green-500/5">
              <div className="text-xs text-green-500 mb-1 font-bold px-3 pt-3">WRITING CONTENT:</div>
              <div className="overflow-x-auto max-h-[200px] overflow-y-auto custom-scrollbar pb-2">
                <table className="w-full text-left border-collapse">
                  <tbody>
                    {(safeArgs.content || '').split('\n').map((line: string, i: number) => (
                      <tr key={i} className="hover:bg-white/[0.02]">
                        <td className="w-8 px-2 py-0 text-right text-gray-500 select-none bg-black/20 border-r border-gray-800/50 sticky left-0 text-[10px]">
                          {i + 1}
                        </td>
                        <td className="px-3 py-0 whitespace-pre text-gray-300 font-code text-xs">
                          {line || '\n'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : name === 'Edit' && (safeArgs.old_text || safeArgs.new_text || safeArgs.old_string || safeArgs.new_string) ? (
            // Edit: Show Diff with line numbers
            <div className="font-mono text-xs overflow-x-auto">
              <div className="overflow-x-auto max-h-[200px] overflow-y-auto custom-scrollbar">
                <table className="w-full text-left border-collapse">
                  <tbody>
                    {(() => {
                      try {
                        const diffs = diffLines(safeArgs.old_string || safeArgs.old_text || '', safeArgs.new_string || safeArgs.new_text || '');
                        const diffRows: React.ReactNode[] = [];
                        let oldLineNum = 1;
                        let newLineNum = 1;
                        
                        diffs.forEach((part, i) => {
                          const lines = part.value.split('\n');
                          if (lines[lines.length - 1] === '') {
                            lines.pop(); // Remove last empty line split
                          }
                          
                          lines.forEach((line, j) => {
                            const isAdded = part.added;
                            const isRemoved = part.removed;
                            const bgClass = isAdded ? 'bg-green-900/20' : isRemoved ? 'bg-red-900/20' : '';
                            const textClass = isAdded ? 'text-green-300' : isRemoved ? 'text-red-400' : 'text-gray-400';
                            const prefix = isAdded ? '+' : isRemoved ? '-' : ' ';
                            
                            let oldNum = '';
                            let newNum = '';
                            
                            if (isRemoved) {
                              oldNum = oldLineNum.toString();
                              oldLineNum++;
                            } else if (isAdded) {
                              newNum = newLineNum.toString();
                              newLineNum++;
                            } else {
                              oldNum = oldLineNum.toString();
                              newNum = newLineNum.toString();
                              oldLineNum++;
                              newLineNum++;
                            }
                            
                            diffRows.push(
                              <tr key={`${i}-${j}`} className={`hover:bg-white/[0.02] ${bgClass}`}>
                                <td className="w-8 px-1 py-0 text-right text-gray-500 select-none bg-[#0d1117] border-r border-gray-800/50 sticky left-0 text-[10px]">
                                  {oldNum}
                                </td>
                                <td className="w-8 px-1 py-0 text-right text-gray-500 select-none bg-[#0d1117] border-r border-gray-800/50 sticky left-[32px] text-[10px]">
                                  {newNum}
                                </td>
                                <td className={`w-4 px-1 py-0 text-center select-none text-[10px] font-bold ${isAdded ? 'text-green-500' : isRemoved ? 'text-red-500' : 'text-gray-600'}`}>
                                  {prefix}
                                </td>
                                <td className={`px-3 py-0 whitespace-pre font-code ${textClass}`}>
                                  {line || '\n'}
                                </td>
                              </tr>
                            );
                          });
                        });
                        return diffRows;
                      } catch (e) {
                        return (
                          <tr><td className="p-3 text-red-400">Error rendering diff.</td></tr>
                        );
                      }
                    })()}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <div className="p-3 text-gray-500 italic text-xs">No content to display.</div>
          )}
          
          {/* Execution Result Details */}
          {error && (
            <div className="p-3 bg-red-900/10 border-t border-red-900/50 text-red-400 text-xs whitespace-pre-wrap break-words">
              ❌ {error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
