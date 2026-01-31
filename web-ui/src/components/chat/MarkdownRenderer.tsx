"use client";

import { useMemo } from "react";

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export function MarkdownRenderer({ content, className = "" }: MarkdownRendererProps) {
  const renderedContent = useMemo(() => {
    // 增强的 markdown 渲染器，支持更多格式
    let html = content;

    // 代码块 (```lang ... ```)，支持更多语言和语法高亮提示
    html = html.replace(/```(\w+)?\n([\s\S]*?)\n?```/g, (match, lang, code) => {
      const language = lang || 'text';
      const codeContent = escapeHtml(code.trim());
      return `<div class="code-block-wrapper"><pre class="code-block" data-lang="${language}"><code class="language-${language}">${codeContent}</code></pre><button class="copy-code-btn" onclick="navigator.clipboard.writeText(decodeURIComponent('${encodeURIComponent(code.trim())}'))" title="Copy code">📋</button></div>`;
    });

    // 行内代码 (`code`)
    html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

    // 粗体 (**text** 或 __text__)
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');

    // 斜体 (*text* 或 _text_)
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/_([^_]+)_/g, '<em>$1</em>');

    // 删除线 (~~text~~)
    html = html.replace(/~~([^~]+)~~/g, '<del class="strikethrough">$1</del>');

    // 标题 (#### ### ## #)，支持更多级别
    html = html.replace(/^#### (.+)$/gm, '<h4 class="markdown-h4">$1</h4>');
    html = html.replace(/^### (.+)$/gm, '<h3 class="markdown-h3">$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2 class="markdown-h2">$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1 class="markdown-h1">$1</h1>');

    // 链接 [text](url)，增强显示
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" class="markdown-link" target="_blank" rel="noopener noreferrer">$1 🔗</a>');

    // 图片 ![alt](url)
    html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<div class="markdown-image-wrapper"><img src="$2" alt="$1" class="markdown-image" loading="lazy" /></div>');

    // 表格支持
    html = html.replace(/\|(.+)\|\n\|[-\s|]+\|\n((?:\|.+\|\n?)*)/g, (match, header, rows) => {
      const headerCells = header.split('|').map((cell: string) => cell.trim()).filter(Boolean);
      const rowsData = rows.trim().split('\n').map((row: string) =>
        row.split('|').map((cell: string) => cell.trim()).filter(Boolean)
      );

      let tableHtml = '<table class="markdown-table"><thead><tr>';
      headerCells.forEach((cell: string) => {
        tableHtml += `<th class="markdown-th">${cell}</th>`;
      });
      tableHtml += '</tr></thead><tbody>';

      rowsData.forEach((row: string[]) => {
        tableHtml += '<tr>';
        row.forEach((cell: string) => {
          tableHtml += `<td class="markdown-td">${cell}</td>`;
        });
        tableHtml += '</tr>';
      });
      tableHtml += '</tbody></table>';

      return tableHtml;
    });

    // 任务列表 (- [ ] unchecked, - [x] checked)
    html = html.replace(/^[-*+] \[ \] (.+)$/gm, '<li class="markdown-task-item"><input type="checkbox" class="markdown-checkbox" disabled> $1</li>');
    html = html.replace(/^[-*+] \[x\] (.+)$/gm, '<li class="markdown-task-item"><input type="checkbox" class="markdown-checkbox" checked disabled> $1</li>');

    // 普通列表项 (- item 或 * item 或 + item)
    html = html.replace(/^[-*+] (.+)$/gm, '<li class="markdown-li">$1</li>');

    // 包装连续的任务列表项
    html = html.replace(/(<li class="markdown-task-item">.*<\/li>\s*)+/g, (match) => {
      return `<ul class="markdown-task-list">${match}</ul>`;
    });

    // 包装连续的列表项
    html = html.replace(/(<li class="markdown-li">.*<\/li>\s*)+/g, (match) => {
      return `<ul class="markdown-ul">${match}</ul>`;
    });

    // 有序列表 (1. item)
    html = html.replace(/^\d+\. (.+)$/gm, '<li class="markdown-oli">$1</li>');
    html = html.replace(/(<li class="markdown-oli">.*<\/li>\s*)+/g, (match) => {
      return `<ol class="markdown-ol">${match}</ol>`;
    });

    // 引用 (> text)，支持嵌套
    html = html.replace(/^> (.+)$/gm, '<blockquote class="markdown-blockquote">$1</blockquote>');

    // 水平分割线 (--- 或 ***)
    html = html.replace(/^(---|___|\*\*\*)$/gm, '<hr class="markdown-hr" />');

    // 高亮文本 (==text==)
    html = html.replace(/==([^=]+)==/g, '<mark class="markdown-highlight">$1</mark>');

    // 键盘按键 (ctrl+c)
    html = html.replace(/\b([A-Za-z0-9]+\+[A-Za-z0-9]+|\b[A-Za-z0-9]+)\b/g, (match) => {
      if (match.includes('+') || ['ctrl', 'alt', 'shift', 'cmd', 'enter', 'escape', 'tab', 'space'].includes(match.toLowerCase())) {
        return `<kbd class="markdown-kbd">${match}</kbd>`;
      }
      return match;
    });

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
      onClick={(e) => {
        // 处理复制按钮点击
        const target = e.target as HTMLElement;
        if (target.classList.contains('copy-code-btn')) {
          e.preventDefault();
          target.style.background = '#10b981';
          target.textContent = '✓';
          setTimeout(() => {
            target.style.background = '';
            target.textContent = '📋';
          }, 1000);
        }
      }}
    />
  );
}

// DataDisplay - 用于展示工具调用结果
interface DataDisplayProps {
  data: unknown;
  maxHeight?: string;
  title?: string;
  className?: string;
}

export function DataDisplay({ data, maxHeight = "300px", title, className = "" }: DataDisplayProps) {
  const renderContent = () => {
    if (data === null || data === undefined) {
      return <span className="text-gray-500 italic">null</span>;
    }
    
    if (typeof data === "string") {
      if (data.includes("\n")) {
        return (
          <pre className="text-sm overflow-auto whitespace-pre-wrap" style={{ maxHeight }}>
            {data}
          </pre>
        );
      }
      return <span className="text-sm">{data}</span>;
    }
    
    if (typeof data === "object") {
      return (
        <pre className="text-sm overflow-auto whitespace-pre-wrap" style={{ maxHeight }}>
          {JSON.stringify(data, null, 2)}
        </pre>
      );
    }
    
    return <span className="text-sm">{String(data)}</span>;
  };

  return (
    <div className={className}>
      {title && <div className="text-xs text-gray-500 mb-1">{title}</div>}
      {renderContent()}
    </div>
  );
}
