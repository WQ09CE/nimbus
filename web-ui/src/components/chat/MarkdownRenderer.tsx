"use client";

import { useMemo } from "react";

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export function MarkdownRenderer({ content, className = "" }: MarkdownRendererProps) {
  const renderedContent = useMemo(() => {
    // 简单的 markdown 渲染器，支持常见格式
    let html = content;
    
    // 代码块 (```lang ... ```)
    html = html.replace(/```(\w+)?\n([\s\S]*?)\n?```/g, (match, lang, code) => {
      const language = lang || 'text';
      return `<pre class="code-block" data-lang="${language}"><code class="language-${language}">${escapeHtml(code.trim())}</code></pre>`;
    });
    
    // 行内代码 (`code`)
    html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
    
    // 粗体 (**text** 或 __text__)
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    
    // 斜体 (*text* 或 _text_)
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/_([^_]+)_/g, '<em>$1</em>');
    
    // 标题 (# ## ###)
    html = html.replace(/^### (.+)$/gm, '<h3 class="markdown-h3">$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2 class="markdown-h2">$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1 class="markdown-h1">$1</h1>');
    
    // 链接 [text](url)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" class="markdown-link" target="_blank" rel="noopener noreferrer">$1</a>');
    
    // 列表项 (- item 或 * item 或 + item)
    html = html.replace(/^[*+-] (.+)$/gm, '<li class="markdown-li">$1</li>');
    
    // 包装连续的列表项
    html = html.replace(/(<li class="markdown-li">.*<\/li>\s*)+/g, (match) => {
      return `<ul class="markdown-ul">${match}</ul>`;
    });
    
    // 有序列表 (1. item)
    html = html.replace(/^\d+\. (.+)$/gm, '<li class="markdown-oli">$1</li>');
    html = html.replace(/(<li class="markdown-oli">.*<\/li>\s*)+/g, (match) => {
      return `<ol class="markdown-ol">${match}</ol>`;
    });
    
    // 引用 (> text)
    html = html.replace(/^> (.+)$/gm, '<blockquote class="markdown-blockquote">$1</blockquote>');
    
    // 水平分割线 (--- 或 ***)
    html = html.replace(/^(---|___|\*\*\*)$/gm, '<hr class="markdown-hr" />');
    
    // 段落（换行处理）
    html = html.split('\n\n').map(paragraph => {
      // 跳过已经是 HTML 标签的段落
      if (paragraph.trim().startsWith('<')) {
        return paragraph;
      }
      // 普通段落包装
      return paragraph.trim() ? `<p class="markdown-p">${paragraph.trim()}</p>` : '';
    }).filter(Boolean).join('\n');
    
    return html;
  }, [content]);

  function escapeHtml(text: string): string {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  return (
    <div 
      className={`markdown-content ${className}`}
      dangerouslySetInnerHTML={{ __html: renderedContent }}
      style={{
        // CSS 样式作为内联样式，确保样式生效
      }}
    />
  );
}

// 用于显示结构化数据的组件
interface DataDisplayProps {
  data: unknown;
  title?: string;
  className?: string;
}

export function DataDisplay({ data, title, className = "" }: DataDisplayProps) {
  const displayContent = useMemo(() => {
    if (data === null || data === undefined) {
      return <span className="text-gray-500 italic">null</span>;
    }
    
    if (typeof data === 'string') {
      // 检查是否是 JSON 字符串
      try {
        const parsed = JSON.parse(data);
        return <pre className="text-xs text-gray-300 whitespace-pre-wrap overflow-x-auto">{JSON.stringify(parsed, null, 2)}</pre>;
      } catch {
        // 检查是否包含 markdown 格式
        if (data.includes('```') || data.includes('**') || data.includes('##') || data.includes('[') && data.includes('](')) {
          return <MarkdownRenderer content={data} />;
        }
        // 普通文本
        return <div className="text-gray-300 whitespace-pre-wrap">{data}</div>;
      }
    }
    
    if (typeof data === 'number' || typeof data === 'boolean') {
      return <span className="text-blue-400 font-mono">{String(data)}</span>;
    }
    
    if (Array.isArray(data)) {
      if (data.length === 0) {
        return <span className="text-gray-500 italic">[]</span>;
      }
      return (
        <div className="space-y-1">
          {data.map((item, index) => (
            <div key={index} className="flex items-start gap-2">
              <span className="text-gray-500 text-xs font-mono w-6">[{index}]</span>
              <DataDisplay data={item} />
            </div>
          ))}
        </div>
      );
    }
    
    if (typeof data === 'object') {
      const entries = Object.entries(data as Record<string, unknown>);
      if (entries.length === 0) {
        return <span className="text-gray-500 italic">{'{}'}</span>;
      }
      
      // 如果对象很简单，以简洁方式显示
      if (entries.length <= 3 && entries.every(([, value]) => 
        typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
      )) {
        return (
          <div className="inline-flex items-center gap-4 text-sm">
            {entries.map(([key, value]) => (
              <span key={key} className="flex items-center gap-1">
                <span className="text-purple-300 font-mono">{key}:</span>
                <span className="text-gray-300">{String(value)}</span>
              </span>
            ))}
          </div>
        );
      }
      
      // 复杂对象，展开显示
      return (
        <div className="space-y-1">
          {entries.map(([key, value]) => (
            <div key={key} className="flex items-start gap-2">
              <span className="text-purple-300 text-sm font-mono min-w-0 max-w-32 truncate">{key}:</span>
              <div className="flex-1 min-w-0">
                <DataDisplay data={value} />
              </div>
            </div>
          ))}
        </div>
      );
    }
    
    // 回退到 JSON 显示
    return <pre className="text-xs text-gray-300 whitespace-pre-wrap overflow-x-auto">{JSON.stringify(data, null, 2)}</pre>;
  }, [data]);

  return (
    <div className={className}>
      {title && (
        <div className="text-[10px] uppercase text-gray-600 font-bold mb-2">{title}</div>
      )}
      {displayContent}
    </div>
  );
}