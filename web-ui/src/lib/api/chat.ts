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
  type: "image" | "text" | "pdf" | "video";
  /** File name */
  name: string;
  /** File size in bytes */
  size: number;
  /** Content: base64 for images, raw text for text files, "" for url-backed media */
  content: string;
  /** MIME type, e.g. "image/png", "text/plain", "video/mp4" */
  mimeType: string;
  /** Preview URL for images/video (blob URL, client-side only) */
  preview?: string;
  /** Served URL for url-backed media (video) — set after upload */
  url?: string;
  /** Raw File, held client-side until uploaded (video). Not serialized. */
  file?: File;
}

/** Result of POST /sessions/{id}/upload */
export interface UploadResult {
  id: string;
  url: string;
  name: string;
  mime_type: string;
  size: number;
  kind: "image" | "video" | "file";
}

export interface ChatRequest {
  content: string;
  attachments?: Array<{
    type: string;
    content: string;
    name?: string;
    mime_type?: string;
    url?: string;
  }>;
}

/**
 * Upload a media file (image/video) via raw body. Returns a served URL.
 * Streams the File directly — no base64, no multipart.
 */
export async function uploadMedia(sessionId: string, file: File): Promise<UploadResult> {
  const res = await fetch(`/api/v1/sessions/${sessionId}/upload`, {
    method: "POST",
    headers: {
      "Content-Type": file.type || "application/octet-stream",
      "X-Filename": encodeURIComponent(file.name || "upload"),
    },
    body: file,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Upload failed (${res.status}): ${detail.slice(0, 200)}`);
  }
  return (await res.json()) as UploadResult;
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
  _streaming?: boolean; // true while tool_output_chunks are still arriving
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
      url: att.url,
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
      url: att.url,
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
