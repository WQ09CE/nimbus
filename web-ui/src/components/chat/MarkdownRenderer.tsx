"use client";

import React, { useState } from "react";
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

// 中文字符处理工具函数
function processAsciiDiagramContent(content: string): string {
  const lines = content.split('\n');
  
  // 检测每行是否包含中文字符
  const hasChinese = (line: string) => /[\u4e00-\u9fff]/.test(line);
  
  // 计算字符显示宽度（中文=2，英文=1）
  const getDisplayWidth = (str: string) => {
    let width = 0;
    for (const char of str) {
      width += /[\u4e00-\u9fff]/.test(char) ? 2 : 1;
    }
    return width;
  };
  
  // 处理包含中文的行，尝试保持对齐
  return lines.map(line => {
    if (!hasChinese(line)) return line;
    
    // 对于包含中文的行，我们保持原样，但添加特殊标记用于CSS处理
    return line;
  }).join('\n');
}

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

function CopyButton({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      className={`flex items-center gap-1.5 text-xs transition-colors duration-200 cursor-pointer bg-transparent border-0 p-0 ${
        copied ? "text-green-400" : "text-gray-400 hover:text-white"
      }`}
      onClick={handleCopy}
      title="Copy code"
    >
      <span className="text-sm">{copied ? "✓" : "📋"}</span>
      <span>{copied ? "Copied!" : "Copy"}</span>
    </button>
  );
}

export function MarkdownRenderer({ content, className = "" }: MarkdownRendererProps) {
  return (
    <div className={`markdown-content ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          code({ inline, className, children, ...props }: any) {
            const match = /language-(\w+)/.exec(className || '');
            const codeContent = String(children).replace(/\n$/, '');
            
            // Helper: detect if content is an ASCII diagram (增强检测算法)
            const isAsciiDiagram = (content: string): boolean => {
              const lines = content.split('\n').filter(l => l.trim());
              if (lines.length < 3) return false; // 至少需要3行才可能是图表
              
              // 1. Unicode box drawing characters - 最可靠的指标
              const hasBoxDrawing = /[\u2500-\u257F]/.test(content);
              
              // 2. 明确的 ASCII 框字符模式 - 检测完整的框结构
              const hasAsciiBoxes = (() => {
                const topBottomPattern = /^\s*[┌┬┐+][-─═=]*[┌┬┐+]\s*$/m;
                const sidePattern = /^\s*[│┤├|]\s*.*\s*[│┤├|]\s*$/m;
                return topBottomPattern.test(content) && sidePattern.test(content);
              })();
              
              // 3. 流程图箭头和连接符
              const hasFlowElements = /[←→↑↓▲▼◀▶⬆⬇⬅➡]|[──→←│┼├┤┬┴]/.test(content);
              
              // 4. 架构图特征 - 检测明显的组件布局
              const hasArchitecturalStructure = (() => {
                // 检查是否有多个被框包围的组件
                const componentBoxes = content.match(/[┌+][─-]+[┐+][\s\S]*?[└+][─-]+[┘+]/g);
                const hasMultipleComponents = componentBoxes && componentBoxes.length >= 2;
                
                // 检查是否有明显的层次结构
                const hasLayeredStructure = /Layer|Engine|Manager|Gateway|Frontend|Backend|Core/i.test(content);
                
                return hasMultipleComponents || hasLayeredStructure;
              })();
              
              // 5. 关键字检测 - 明确的图表标识
              const hasGraphKeywords = /^[^\w]*(Architecture|Diagram|Workflow|Data Flow|Legend|Framework|System)/mi.test(content);
              
              // 6. 结构化布局检测
              const hasStructuredLayout = (() => {
                // 检查是否有对齐的多列结构
                const indentedLines = lines.filter(l => /^\s{4,}/.test(l));
                const hasConsistentIndentation = indentedLines.length / lines.length > 0.4;
                
                // 检查是否有连接线
                const hasConnectors = lines.some(l => /^[\s│]*[├┤┼]/.test(l));
                
                return hasConsistentIndentation && hasConnectors;
              })();
              
              // 综合判断 - 需要有明确的图表特征
              const diagramScore = [
                hasBoxDrawing,
                hasAsciiBoxes,
                hasFlowElements,
                hasArchitecturalStructure,
                hasGraphKeywords,
                hasStructuredLayout
              ].filter(Boolean).length;
              
              // 至少需要2个明确特征才认定为图表
              return diagramScore >= 2;
            };

            // Block code (with or without language)
            const isBlock = !inline && (match || codeContent.includes('\n'));
            
            if (isBlock) {
              const language = match ? match[1] : 'text';
              const isDiagram = isAsciiDiagram(codeContent);

              // For ASCII diagrams, use enhanced rendering to prevent character breaks
              if (isDiagram) {
                return (
                  <div className="ascii-diagram-container relative rounded-lg overflow-hidden border border-blue-700/50 my-6 shadow-lg group">
                    <div className="flex justify-between items-center px-4 py-2 bg-blue-900/30 border-b border-blue-700/50 select-none">
                      <span className="text-xs font-mono font-medium uppercase tracking-wider text-blue-300">
                        📊 Architecture Diagram
                      </span>
                      <CopyButton code={codeContent} />
                    </div>
                    <div 
                      className="overflow-x-auto overflow-y-hidden px-6 py-6" 
                      style={{
                        minWidth: 'fit-content',
                        background: '#0a0e1a',
                        borderRadius: '0 0 8px 8px'
                      }}
                    >
                      <pre 
                        className="ascii-diagram-enhanced" 
                        style={{
                          // 优化的字体栈，支持中英文等宽显示
                          fontFamily: '"Cascadia Code", "Fira Code", "SF Mono", Menlo, Monaco, "Noto Sans Mono CJK SC", "Source Han Sans CN", "Microsoft YaHei Mono", Consolas, "Liberation Mono", "Courier New", monospace',
                          fontSize: '13px', // 稍微缩小字体以改善对齐
                          lineHeight: '1.2', // 稍微增加行高以改善可读性
                          letterSpacing: '0',
                          wordSpacing: '0',
                          whiteSpace: 'pre',
                          tabSize: 4,
                          fontVariantLigatures: 'none',
                          fontFeatureSettings: '"liga" 0, "calt" 0, "dlig" 0, "hlig" 0',
                          WebkitFontSmoothing: 'antialiased',
                          MozOsxFontSmoothing: 'grayscale',
                          textRendering: 'optimizeSpeed', // 改为 optimizeSpeed 以避免字符变形
                          overflow: 'visible',
                          width: 'fit-content',
                          minWidth: 'fit-content',
                          margin: 0,
                          padding: 0,
                          color: '#38bdf8',
                          background: 'transparent',
                          display: 'inline-block',
                          // 添加字符间距微调，帮助中文对齐
                          fontVariant: 'normal',
                          fontStretch: 'normal',
                          fontStyle: 'normal',
                          fontWeight: 'normal',
                          // 确保统一的字符宽度
                          unicodeBidi: 'normal',
                          direction: 'ltr'
                        }}
                      >
                        {/* 使用预处理函数改善中文对齐 */}
                        {processAsciiDiagramContent(codeContent)}
                      </pre>
                    </div>
                  </div>
                );
              }

              // Regular code block with syntax highlighting
              return (
                <div className="relative rounded-lg overflow-hidden border border-gray-700 bg-[#1e1e1e] my-6 shadow-lg group">
                  <div className="flex justify-between items-center px-4 py-2 bg-[#252526] border-b border-gray-700 select-none">
                    <span className="text-xs font-mono font-medium uppercase tracking-wider text-gray-400">
                      {language}
                    </span>
                    <CopyButton code={codeContent} />
                  </div>
                  <div className="overflow-x-auto">
                    <SyntaxHighlighter
                      style={vscDarkPlus}
                      language={language}
                      PreTag="div"
                      customStyle={{ margin: 0, padding: '1rem', background: 'transparent' }}
                      {...props}
                    >
                      {codeContent}
                    </SyntaxHighlighter>
                  </div>
                </div>
              );
            }
            
            // Inline code
            return (
              <code className="bg-gray-800/50 text-blue-200 px-1.5 py-0.5 rounded text-sm font-mono border border-gray-700/50 break-words" {...props}>
                {children}
              </code>
            );
          },
          h1: ({children}) => <h1 className="text-2xl font-bold text-blue-300 mb-4 mt-6 border-b border-gray-700 pb-2">{children}</h1>,
          h2: ({children}) => <h2 className="text-xl font-bold text-blue-300 mb-3 mt-5 border-b border-gray-800 pb-1">{children}</h2>,
          h3: ({children}) => <h3 className="text-lg font-semibold text-blue-300 mb-2 mt-4">{children}</h3>,
          h4: ({children}) => <h4 className="text-base font-semibold text-blue-300 mb-2 mt-3">{children}</h4>,
          p: ({children}) => <p className="mb-4 text-gray-300 leading-relaxed">{children}</p>,
          ul: ({children}) => <ul className="mb-4 ml-6 space-y-2 list-disc text-gray-300">{children}</ul>,
          ol: ({children}) => <ol className="mb-4 ml-6 space-y-2 list-decimal text-gray-300">{children}</ol>,
          li: ({children}) => <li className="pl-1 text-gray-300">{children}</li>,
          a: ({href, children}) => <a href={href} className="text-blue-400 hover:text-blue-300 underline" target="_blank" rel="noopener noreferrer">{children} 🔗</a>,
          blockquote: ({children}) => <blockquote className="border-l-4 border-gray-600 pl-4 py-1 my-4 text-gray-400 italic bg-gray-800/30 rounded-r">{children}</blockquote>,
          hr: () => <hr className="my-6 border-gray-700" />,
          table: ({children}) => <div className="overflow-x-auto mb-4"><table className="w-full border-collapse bg-gray-900/40 rounded-lg overflow-hidden border border-gray-700">{children}</table></div>,
          thead: ({children}) => <thead className="bg-gray-800/60">{children}</thead>,
          th: ({children}) => <th className="text-gray-300 font-semibold px-4 py-3 text-left border-b border-gray-700">{children}</th>,
          td: ({children}) => <td className="text-gray-300 px-4 py-3 border-b border-gray-800">{children}</td>,
          img: ({src, alt}) => <div className="mb-4 text-center"><img src={src} alt={alt} className="max-w-full h-auto rounded-lg border border-gray-700 shadow-lg inline-block" loading="lazy" /></div>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

// DataDisplay component (legacy/helper)
export function DataDisplay({ data, title, maxDepth = 2, currentDepth = 0, className = "" }: any) {
  const isObject = typeof data === 'object' && data !== null;
  const isArray = Array.isArray(data);
  const isEmpty = isObject && Object.keys(data).length === 0;

  if (data === null || data === undefined) {
    return <span className="text-gray-500 italic">null</span>;
  }

  // 基础类型直接显示
  if (!isObject) {
    let content = String(data);
    let typeClass = "text-gray-300";
    
    if (typeof data === 'boolean') {
      content = data ? 'true' : 'false';
      typeClass = "text-yellow-400";
    } else if (typeof data === 'number') {
      typeClass = "text-blue-400";
    } else if (typeof data === 'string') {
      // 检查是否是 URL
      if (data.startsWith('http')) {
        return <a href={data} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline flex items-center gap-1">🔗 {data}</a>;
      }
      typeClass = "text-green-300/90";
      // 长文本截断
      if (data.length > 300 && currentDepth > 0) {
        return (
          <span className={typeClass}>
            "{data.slice(0, 300)}..." <span className="text-gray-500 text-xs">({data.length} chars)</span>
          </span>
        );
      }
      return <span className={typeClass}>"{data}"</span>;
    }

    return <span className={typeClass}>{content}</span>;
  }

  // 递归深度限制
  if (currentDepth >= maxDepth) {
    return <span className="text-gray-500 italic">{isArray ? `Array(${data.length})` : 'Object {...}'}</span>;
  }

  // 空对象/数组
  if (isEmpty) {
    return <span className="text-gray-500">{(isArray ? '[]' : '{}')}</span>;
  }

  return (
    <div className={`font-mono text-xs ${className}`}>
      {title && <div className="text-gray-500 mb-1 font-semibold">{title}:</div>}
      <div className="pl-2 border-l border-gray-700/50">
        {isArray ? (
          <div className="flex flex-col gap-1">
            {data.map((item: any, index: number) => (
              <div key={index} className="flex gap-2">
                <span className="text-gray-600 select-none">-</span>
                <DataDisplay data={item} maxDepth={maxDepth} currentDepth={currentDepth + 1} />
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {Object.entries(data).map(([key, value]) => (
              <div key={key} className="flex gap-2">
                <span className="text-purple-300/80">{key}:</span>
                <DataDisplay data={value} maxDepth={maxDepth} currentDepth={currentDepth + 1} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
