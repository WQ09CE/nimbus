import React, { useMemo } from 'react';
import type { Message } from "@/stores/chat-store";
import { ChatMessage } from "./ChatMessage";
import { AgentProcess } from "./AgentProcess";

interface ChatListProps {
  messages: Message[];
  isStreaming: boolean;
  streamingContent: string;
  streamingToolCalls: any[];
}

export function ChatList({ messages, isStreaming, streamingContent, streamingToolCalls }: ChatListProps) {
  // Historical messages grouping - only depends on messages, NOT streamingContent
  const groups = useMemo(() => {
    const result: any[] = [];
    let currentAgentGroup: Message[] = [];

    const flushAgentGroup = () => {
      if (currentAgentGroup.length > 0) {
        result.push({ type: 'agent_group', messages: [...currentAgentGroup] });
        currentAgentGroup = [];
      }
    };

    messages.forEach((msg) => {
      if (msg.role === 'user') {
        flushAgentGroup();
        result.push({ type: 'user', message: msg });
      } else if (msg.role === 'assistant') {
        currentAgentGroup.push(msg);
      } else if (msg.role === 'system') {
        flushAgentGroup();
        result.push({ type: 'system', message: msg });
      }
    });

    flushAgentGroup();
    
    return result;
  }, [messages]); // ← Only depends on messages!

  // Streaming message rendered separately - won't cause historical messages to re-render
  const streamingElement = useMemo(() => {
    if (!isStreaming) return null;
    const streamingMsg: Message = {
      id: "streaming",
      role: "assistant",
      content: streamingContent,
      toolCalls: streamingToolCalls.length > 0 ? streamingToolCalls : undefined,
      timestamp: Date.now(),
    };
    return <ChatMessage message={streamingMsg} isStreaming={true} />;
  }, [isStreaming, streamingContent, streamingToolCalls]);

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {groups.map((group, groupIndex) => {
        if (group.type === 'user') {
          return <ChatMessage key={group.message.id} message={group.message} />;
        } 
        
        if (group.type === 'system') {
           return <ChatMessage key={group.message.id} message={group.message} />;
        }

        if (group.type === 'agent_group') {
          const msgs = group.messages;
          if (msgs.length === 0) return null;

          const lastMsg = msgs[msgs.length - 1];
          const lastHasTools = lastMsg.toolCalls && lastMsg.toolCalls.length > 0;
          
          let processSteps = msgs;
          let resultMsg = null;

          if (!lastHasTools) {
             if (msgs.length > 1) {
                resultMsg = lastMsg;
                processSteps = msgs.slice(0, -1);
             } else {
                resultMsg = lastMsg;
                processSteps = [];
             }
          }

          return (
            <div key={`group-${groupIndex}`}>
              {processSteps.length > 0 && (
                <AgentProcess 
                    steps={processSteps} 
                    isStreaming={false} 
                />
              )}
              {resultMsg && (
                <ChatMessage 
                    message={resultMsg} 
                    isStreaming={false} 
                />
              )}
            </div>
          );
        }
        
        return null;
      })}
      {/* Streaming message - rendered outside groups to avoid re-rendering history */}
      {streamingElement}
    </div>
  );
}
