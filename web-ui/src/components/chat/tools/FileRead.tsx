import React from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

interface FileReadProps {
  args: { file_path: string; [key: string]: any };
  result?: string;
  error?: string;
  status: "running" | "completed" | "failed";
}

export function FileRead({ args, result, error, status }: FileReadProps) {
  const safeArgs = args || {};
  // Strict path display
  const filePath = safeArgs.file_path || "unknown";
  
  // Format content logic (truncate if too long)
  const content = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
  const lines = content ? content.split('\n') : [];
  const lineCount = lines.length;
  
  // Simple syntax detection based on extension
  const ext = filePath.split('.').pop();

  // Map file extensions to syntax highlighter language names
  const extToLang: Record<string, string> = {
    ts: 'typescript', tsx: 'tsx', js: 'javascript', jsx: 'jsx',
    py: 'python', rs: 'rust', go: 'go', java: 'java',
    cpp: 'cpp', c: 'c', h: 'c', hpp: 'cpp',
    rb: 'ruby', php: 'php', swift: 'swift', kt: 'kotlin',
    css: 'css', scss: 'scss', less: 'less',
    html: 'html', xml: 'xml', svg: 'xml',
    json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml',
    md: 'markdown', sql: 'sql', sh: 'bash', bash: 'bash', zsh: 'bash',
    dockerfile: 'docker', makefile: 'makefile',
  };
  const language = ext ? extToLang[ext.toLowerCase()] : undefined;
  
  return (
    <div className="font-mono text-sm bg-[#0d1117]">
      {/* File Info Bar */}
      {status === "completed" && (
        <div className="bg-[#161b22] px-3 py-1 border-b border-gray-800 flex justify-end items-center text-[10px] text-gray-500 gap-3">
          {safeArgs.offset !== undefined && (
            <span>Offset: {safeArgs.offset}</span>
          )}
          {safeArgs.limit !== undefined && (
            <span>Limit: {safeArgs.limit}</span>
          )}
          {safeArgs.offset !== undefined || safeArgs.limit !== undefined ? (
            <span className="text-gray-700">|</span>
          ) : null}
          <span>{lineCount} lines • {ext?.toUpperCase() || 'TXT'}</span>
        </div>
      )}

      {/* Content Area */}
      <div className="overflow-x-auto max-h-[400px] overflow-y-auto custom-scrollbar">
        {status === "running" ? (
          <div className="p-4 text-gray-500 italic flex items-center gap-2">
            <span className="animate-spin">⟳</span> Reading file...
          </div>
        ) : error ? (
          <div className="p-4 bg-red-900/10 text-red-400 whitespace-pre-wrap border-l-2 border-red-500">
            {error}
          </div>
        ) : content ? (
          language ? (
            <SyntaxHighlighter
              language={language}
              style={vscDarkPlus}
              showLineNumbers
              lineNumberStyle={{ color: '#4b5563', fontSize: '10px', minWidth: '2.5em', paddingRight: '1em', userSelect: 'none' }}
              customStyle={{ margin: 0, padding: '0.5rem 0', background: 'transparent', fontSize: '12px' }}
              wrapLongLines
            >
              {content}
            </SyntaxHighlighter>
          ) : (
            <table className="w-full text-left border-collapse">
              <tbody>
                {lines.map((line, i) => (
                  <tr key={i} className="hover:bg-white/[0.02]">
                    <td className="w-8 px-2 py-0 text-right text-gray-600 select-none bg-[#0d1117] border-r border-gray-800/50 sticky left-0 text-[10px]">
                      {i + 1}
                    </td>
                    <td className="px-3 py-0 whitespace-pre text-gray-300 font-code text-xs">
                      {line || '\n'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : (
          <div className="p-4 text-gray-500 italic">(Empty file)</div>
        )}
      </div>
    </div>
  );
}
