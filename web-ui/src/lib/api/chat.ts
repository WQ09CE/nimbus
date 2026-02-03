/**
 * Chat API with SSE Streaming
 * POST /api/v1/sessions/{id}/chat - Send message and stream response
 */

import { apiStream } from "./client";

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
