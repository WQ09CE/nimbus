import React, { useState } from 'react';
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
  
  // 判断是否有输出需要显示
  const hasContent = !!safeArgs.content || !!safeArgs.new_text || !!safeArgs.old_text || !!safeArgs.new_string || !!safeArgs.old_string;

  return (
    <div className="font-mono text-sm bg-[#0d1117] rounded-md border border-gray-800 overflow-hidden flex flex-col my-2">
      {/* Header: Title & Status (始终显示，保持最小高度) */}
      <div 
        className="flex items-center justify-between p-2.5 bg-[#161b22] cursor-pointer hover:bg-[#1f242e] transition-colors select-none"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex gap-3 text-gray-300 truncate font-semibold items-center max-w-[70%]">
          <span className="text-purple-400 shrink-0">
            {name === 'Edit' ? '📝' : '📄'}
          </span>
          <span className="truncate" title={`${name} ${filePath}`}>
            {name} <span className="text-gray-400 font-normal">{filePath}</span>
          </span>
        </div>
        
        <div className="flex items-center gap-4 text-xs shrink-0">
          {status === "running" ? (
            <span className="flex items-center gap-1.5 text-blue-400">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500"></span>
              </span>
              Writing
            </span>
          ) : error ? (
            <span className="px-2 py-0.5 rounded font-bold text-red-400 bg-red-500/10 border border-red-500/20">Failed</span>
          ) : (
            <span className="px-2 py-0.5 rounded font-bold text-green-400 bg-green-500/10 border border-green-500/20">Success</span>
          )}
          
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
              <div className="overflow-x-auto max-h-[400px] overflow-y-auto custom-scrollbar pb-2">
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
          ) : name === 'Edit' && (safeArgs.old_text || safeArgs.new_text) ? (
            // Edit: Show Diff with line numbers
            <div className="font-mono text-xs overflow-x-auto">
              <div className="overflow-x-auto max-h-[400px] overflow-y-auto custom-scrollbar">
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
