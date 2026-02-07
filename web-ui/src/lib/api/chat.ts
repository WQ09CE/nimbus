/**
 * Chat API with SSE Streaming
 * POST /api/v1/sessions/{id}/chat - Send message and stream response
 */

import { apiPost, apiStream } from "./client";

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface ChatRequest {
  content: string;
  attachments?: unknown[];
}

export interface ToolCall {
  id?: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolResult {
  id?: string;
  name: string;
  result: unknown;
  error?: string;
  duration?: number;
}

/**
 * SSE Event types from Nimbus
 */
export type ChatEventType =
  | "connected"
  | "message_start"
  | "planning"
  | "dag_created"
  | "task_start"
  | "step_start"
  | "tool_call"
  | "tool_result"
  | "sub_tool_call"
  | "sub_tool_result"
  | "task_done"
  | "task_failed"
  | "permission_request"
  | "dag_complete"
  | "message"
  | "error"
  | "heartbeat";

export interface ChatEvent {
  type: ChatEventType;
  data: unknown;
}

/**
 * Inject message into running session
 */
export async function injectMessage(
  sessionId: string, 
  content: string
): Promise<void> {
  const endpoint = `/api/v1/sessions/${sessionId}/inject`;
  const request: ChatRequest = { content };
  
  await apiPost(endpoint, request);
}

/**
 * Stream chat response from Nimbus
 */
export async function* streamChat(
  sessionId: string,
  message: string,
  signal?: AbortSignal
): AsyncGenerator<ChatEvent> {
  const endpoint = `/api/v1/sessions/${sessionId}/chat`;
  const request: ChatRequest = { content: message };

  for await (const event of apiStream(endpoint, request, signal)) {
    yield event as ChatEvent;
  }
}
