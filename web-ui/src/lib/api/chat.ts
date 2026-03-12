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
}

export interface ToolResult {
  id?: string;
  name: string;
  result: unknown;
  error?: string;
  duration?: number;
  ui_detail?: Record<string, any>;
  sub_events?: Record<string, any>[]; // Structured progress events from sub-agents
}

/**
 * SSE Event types from Nimbus
 */
export type ChatEventType =
  | "connected"
  | "message_start"
  | "message"
  | "user_message"
  | "tool_call"
  | "tool_output_chunk"
  | "tool_result"
  | "usage_update"
  | "done"
  | "error"
  | "heartbeat";

export interface ChatEvent {
  type: ChatEventType;
  data: unknown;
  id?: string;
}

/**
 * Inject message into running session (supports multimodal attachments)
 */
export async function injectMessage(
  sessionId: string,
  content: string,
  attachments?: ChatAttachment[]
): Promise<void> {
  const endpoint = `/api/v1/sessions/${sessionId}/inject`;
  const request: ChatRequest = { content };

  // Add attachments if present (same format as streamChat)
  if (attachments && attachments.length > 0) {
    request.attachments = attachments.map(att => ({
      type: att.type,
      content: att.content,
      name: att.name,
      mime_type: att.mimeType,
    }));
  }

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
  signal?: AbortSignal,
  lastEventId?: string
): AsyncGenerator<ChatEvent> {
  const endpoint = `/api/v1/sessions/${sessionId}/events`;
  const headers: Record<string, string> = {};
  if (lastEventId) {
    headers["Last-Event-ID"] = lastEventId;
  }
  for await (const event of apiStream(endpoint, undefined, signal, "GET", headers)) {
    yield event as ChatEvent;
  }
}
