import React from 'react';
import { diffLines } from 'diff';

interface FileDiffProps {
  name: string; // Edit or Write
  args: any;
  result?: any;
  error?: string;
  status: "running" | "completed" | "failed";
}

export function FileDiff({ name, args, result, error, status }: FileDiffProps) {
  const safeArgs = args || {};
  const filePath = safeArgs.file_path || safeArgs.path || safeArgs.filename || safeArgs.file || "unknown";
  
  return (
    <div className="font-mono text-sm bg-[#0d1117]">
      <div className="p-0">
        {name === 'Write' ? (
          // Write: Show content being written with line numbers
          <div className="border-l-4 border-green-500/50 bg-green-500/5">
            <div className="text-xs text-green-500 mb-1 font-bold px-3 pt-3">WRITING:</div>
            <div className="overflow-x-auto max-h-[400px] overflow-y-auto custom-scrollbar">
              <table className="w-full text-left border-collapse">
                <tbody>
                  {(args.content || '').split('\n').map((line: string, i: number) => (
                    <tr key={i} className="hover:bg-white/[0.02]">
                      <td className="w-8 px-2 py-0 text-right text-gray-600 select-none bg-green-500/10 border-r border-green-500/20 sticky left-0 text-[10px]">
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
        ) : (
          // Edit: Show Diff with line numbers
          <div className="font-mono text-xs overflow-x-auto">
            <div className="overflow-x-auto max-h-[400px] overflow-y-auto custom-scrollbar">
              <table className="w-full text-left border-collapse">
                <tbody>
                  {(() => {
                    const diffs = diffLines(args.old_string || args.old_text || '', args.new_string || args.new_text || '');
                    const result: React.ReactNode[] = [];
                    let oldLineNum = 1;
                    let newLineNum = 1;
                    
                    diffs.forEach((part, i) => {
                      const lines = part.value.split('\n');
                      // Remove last empty line if it exists
                      if (lines[lines.length - 1] === '') {
                        lines.pop();
                      }
                      
                      lines.forEach((line, j) => {
                        const bgClass = part.added ? 'bg-green-900/20' :
                                       part.removed ? 'bg-red-900/20' : '';
                        const textClass = part.added ? 'text-green-300' :
                                         part.removed ? 'text-red-400' : 'text-gray-400';
                        const prefix = part.added ? '+' : part.removed ? '-' : ' ';
                        
                        // Line numbers logic
                        let oldNum = '';
                        let newNum = '';
                        
                        if (part.removed) {
                          oldNum = oldLineNum.toString();
                          oldLineNum++;
                        } else if (part.added) {
                          newNum = newLineNum.toString();
                          newLineNum++;
                        } else {
                          oldNum = oldLineNum.toString();
                          newNum = newLineNum.toString();
                          oldLineNum++;
                          newLineNum++;
                        }
                        
                        result.push(
                          <tr key={`${i}-${j}`} className={`hover:bg-white/[0.02] ${bgClass}`}>
                            <td className="w-8 px-1 py-0 text-right text-gray-600 select-none bg-[#0d1117] border-r border-gray-800/50 sticky left-0 text-[10px]">
                              {oldNum}
                            </td>
                            <td className="w-8 px-1 py-0 text-right text-gray-600 select-none bg-[#0d1117] border-r border-gray-800/50 sticky left-0 text-[10px]">
                              {newNum}
                            </td>
                            <td className="w-4 px-1 py-0 text-center select-none opacity-50 text-[10px]">
                              {prefix}
                            </td>
                            <td className={`px-3 py-0 whitespace-pre font-code ${textClass}`}>
                              {line || '\n'}
                            </td>
                          </tr>
                        );
                      });
                    });
                    
                    return result;
                  })()}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Execution Result - only show simple success message */}
        {status === "completed" && (
          <div className="p-2 bg-black/20 border-t border-gray-800 text-xs text-green-400 flex items-center gap-2">
            <span>✅</span> {name === 'Edit' ? 'Successfully edited' : 'Successfully written'} {args.file_path || args.path || ''}
          </div>
        )}
        
        {error && (
          <div className="p-3 bg-red-900/10 border-t border-red-900/50 text-red-300 text-xs">
            ❌ {error}
          </div>
        )}
      </div>
    </div>
  );
}
