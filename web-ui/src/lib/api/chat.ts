/**
 * Chat API with SSE Streaming
 * POST /api/v1/sessions/{id}/chat - Send message and stream response
 */

import { apiPost, apiStream } from "./client";

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

/**
 * Attachment sent with a chat message.
 */
export interface ChatAttachment {
  /** Unique ID (generated client-side) */
  id: string;
  /** Attachment type */
  type: "image" | "text" | "pdf";
  /** File name */
  name: string;
  /** File size in bytes */
  size: number;
  /** Content: base64 for images, raw text for text files */
  content: string;
  /** MIME type, e.g. "image/png", "text/plain" */
  mimeType: string;
  /** Preview URL for images (blob URL) */
  preview?: string;
}

export interface ChatRequest {
  content: string;
  attachments?: Array<{
    type: string;
    content: string;
    name?: string;
    mime_type?: string;
  }>;
}

export interface ToolCall {
  id?: string;
  name: string;
  arguments: Record<string, unknown>;
  agentType?: "core" | "dispatch";
  // Nested executor tool calls (only present on Dispatch tools)
  subCalls?: ToolCall[];
  subResults?: ToolResult[];
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
  | "executor_start"
  | "executor_done"
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
  attachments?: ChatAttachment[],
  signal?: AbortSignal
): AsyncGenerator<ChatEvent> {
  const endpoint = `/api/v1/sessions/${sessionId}/chat`;
  const request: ChatRequest = { content: message };

  // Add attachments if present
  if (attachments && attachments.length > 0) {
    request.attachments = attachments.map(att => ({
      type: att.type,
      content: att.content,
      name: att.name,
      mime_type: att.mimeType,
    }));
  }

  for await (const event of apiStream(endpoint, request, signal)) {
    yield event as ChatEvent;
  }
}

/**
 * Subscribe to SSE events for a running session (reconnection)
 */
export async function* subscribeToEvents(
  sessionId: string,
  signal?: AbortSignal
): AsyncGenerator<ChatEvent> {
  const endpoint = `/api/v1/sessions/${sessionId}/events`;
  for await (const event of apiStream(endpoint, undefined, signal, "GET")) {
    yield event as ChatEvent;
  }
}
