import React, { memo } from 'react';
import { DefaultTool } from './DefaultTool';
import { FileRead } from './FileRead';
import { FileDiff } from './FileDiff';
import { Bash } from './Bash';

interface ToolDisplayProps {
  tool: {
    id?: string;
    name: string;
    args: any;
    result?: any;
    error?: string;
    status: "running" | "completed" | "failed";
    duration?: number;
    ui_detail?: Record<string, any>;
  };
  isExpanded: boolean;
}

export const ToolDisplay = memo(function ToolDisplay({ tool, isExpanded }: ToolDisplayProps) {
  if (!isExpanded) return null;

  // Dispatch based on tool name
  switch (tool.name) {
    case 'Read':
      return <FileRead {...tool} />;
    case 'Edit':
    case 'Write':
      return <FileDiff {...tool} />;
    case 'Bash':
      return <Bash {...tool} />;
    case 'Grep':
    case 'Glob':
      return <DefaultTool {...tool} />;
    default:
      return <DefaultTool {...tool} />;
  }
});
