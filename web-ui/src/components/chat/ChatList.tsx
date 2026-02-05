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
        // System messages (e.g. Memory Archived) also go into flow?
        // Or separate? Let's treat as separate for now.
        flushAgentGroup();
        result.push({ type: 'system', message: msg });
      }
    });

    // Add streaming message to pending group if assistant
    if (isStreaming) {
       // Construct streaming message
       const streamingMsg: Message = {
         id: "streaming",
         role: "assistant",
         content: streamingContent,
         toolCalls: streamingToolCalls.length > 0 ? streamingToolCalls : undefined,
         timestamp: Date.now(),
       };
       currentAgentGroup.push(streamingMsg);
    }

    flushAgentGroup();
    
    return result;
  }, [messages, isStreaming, streamingContent, streamingToolCalls]);

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
          // Determine if the last message is a "Result" (Text only, no tools)
          // Exception: If it's the ONLY message and has no tools, it's a direct reply.
          // Exception: If it's streaming, we treat it as process until done? 
          // No, if streaming text, it looks like a result being typed.
          
          const lastHasTools = lastMsg.toolCalls && lastMsg.toolCalls.length > 0;
          const isStreamingMsg = lastMsg.id === "streaming";
          
          let processSteps = msgs;
          let resultMsg = null;

          // Pure text message (no tools) should ALWAYS render as ChatMessage
          // to avoid DOM structure change when streaming ends.
          // This prevents the "jump" effect.
          if (!lastHasTools) {
             // It's a text reply (streaming or not).
             if (msgs.length > 1) {
                resultMsg = lastMsg;
                processSteps = msgs.slice(0, -1);
             } else {
                // Single message, text only.
                resultMsg = lastMsg;
                processSteps = [];
             }
          }

          return (
            <div key={`group-${groupIndex}`}>
              {processSteps.length > 0 && (
                <AgentProcess 
                    steps={processSteps} 
                    isStreaming={isStreaming && !resultMsg} 
                />
              )}
              {resultMsg && (
                <ChatMessage 
                    message={resultMsg} 
                    isStreaming={isStreaming && groupIndex === groups.length - 1} 
                />
              )}
            </div>
          );
        }
        
        return null;
      })}
    </div>
  );
}
