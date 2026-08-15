/**
 * SSE/Streaming Event Types
 */

export type SSEEventType =
  | "connected"
  | "message_start"
  | "message"
  | "tool_call"
  | "tool_output_chunk"
  | "tool_result"
  | "usage_update"
  | "user_message"
  | "done"
  | "error"
  | "heartbeat";

export interface SSEEvent {
  type: SSEEventType;
  data: unknown;
}

export interface MessageChunk {
  content?: string;
  chunk?: string;
}

export interface ToolCallData {
  action_id?: string;
  id?: string;
  tool?: string;
  name?: string;
  args?: Record<string, unknown>;
  arguments?: Record<string, unknown> | string;
}

export interface ToolOutputChunkData {
  action_id?: string;
  id?: string;
  tool?: string;
  chunk?: string;
  ui_detail?: Record<string, any>;
}

export interface ToolResultData {
  action_id?: string;
  id?: string;
  tool?: string;
  name?: string;
  output?: unknown;
  result?: unknown;
  status?: "SUCCESS" | "ERROR";
  fault?: {
    message: string;
  };
  ui_detail?: Record<string, any>;
}

export interface UsageUpdateData {
  cumulative_usage?: {
    input: number;
    output: number;
    cache_read: number;
    cache_write: number;
    total: number;
    cost: {
      input: number;
      output: number;
      cache_read: number;
      cache_write: number;
      total: number;
    };
  };
  context_window?: {
    current: number;
    maximum: number;
  };
}

export interface UserMessageData {
  content: string;
}
