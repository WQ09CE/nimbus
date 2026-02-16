/**
 * Session API
 * POST /api/v1/sessions - Create session
 * GET  /api/v1/sessions - List sessions
 * GET  /api/v1/sessions/{id} - Get session detail
 * DELETE /api/v1/sessions/{id} - Delete session
 * POST /api/v1/sessions/{id}/interrupt - Interrupt session
 * POST /api/v1/sessions/{id}/resume - Resume session
 */

import { apiGet, apiPost, apiDelete, apiPatch } from "./client";

export interface Session {
  id: string;
  name?: string;
  created_at: string;
  status: string;
  memory_type: string;
  planner_type: string;
  workspace_path?: string;
  last_message_at?: string;
  message_count: number;
  agent_mode?: string;
  llm_config?: Record<string, string>;
  first_message_preview?: string;
}

export interface SessionCreateRequest {
  name?: string;
  workspace_path?: string;
  memory_type?: string;
  planner_type?: string;
  agent_mode?: string;
}

export interface SessionListResponse {
  items: Session[];  // Server returns 'items', not 'sessions'
  total: number;
  limit: number;
  offset: number;
}

export interface InterruptResponse {
  success: boolean;
  session_id: string;
  interrupted_processes: number;
  checkpoint?: {
    step_index: number;
    iteration: number;
    memory_messages: number;
  };
  error?: string;
}

export interface ResumeResponse {
  success: boolean;
  session_id: string;
  restored_step?: number;
  restored_iteration?: number;
  error?: string;
}

/**
 * Create a new session
 */
export async function createSession(
  req: SessionCreateRequest = {}
): Promise<Session> {
  return apiPost<Session>("/api/v1/sessions", req);
}

/**
 * List all sessions
 */
export async function listSessions(): Promise<Session[]> {
  const resp = await apiGet<SessionListResponse>("/api/v1/sessions");
  return resp.items || [];
}

/**
 * Get session detail
 */
export async function getSession(id: string): Promise<Session> {
  return apiGet<Session>(`/api/v1/sessions/${id}`);
}

/**
 * Delete session
 * @param id Session ID
 * @param hard If true, permanently delete from database
 */
export async function deleteSession(id: string, hard: boolean = true): Promise<void> {
  await apiDelete(`/api/v1/sessions/${id}?hard=${hard}`);
}

/**
 * Interrupt a running session
 * Pauses execution and saves checkpoint to DB
 */
export async function interruptSession(id: string): Promise<InterruptResponse> {
  return apiPost<InterruptResponse>(`/api/v1/sessions/${id}/interrupt`);
}

/**
 * Resume an interrupted session
 * Restores from checkpoint and continues execution
 */
export async function resumeSession(id: string): Promise<ResumeResponse> {
  return apiPost<ResumeResponse>(`/api/v1/sessions/${id}/resume`);
}

/**
 * Message from server
 */
export interface ToolCallArtifact {
  type: "tool_calls";
  tool_calls: Array<{
    id: string;
    type: string;
    function: {
      name: string;
      arguments: string;
    };
  }>;
}

export interface ToolResultArtifact {
  type: "tool_result";
  tool_call_id: string;
  name: string;
}

export interface ServerMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: string;
  artifacts?: (ToolCallArtifact | ToolResultArtifact | unknown)[];
  dag_id?: string;
}

export interface MessageListResponse {
  items: ServerMessage[];
}

/**
 * Get messages for a session
 */
export async function getSessionMessages(
  sessionId: string,
  limit: number = 1000
): Promise<ServerMessage[]> {
  const resp = await apiGet<MessageListResponse>(
    `/api/v1/sessions/${sessionId}/messages?limit=${limit}`
  );
  return resp.items || [];
}

/**
 * Batch delete sessions
 */
export interface FileNode {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileNode[];
  size?: number;
  last_modified?: string;
}

export async function listFiles(sessionId: string, path: string = ""): Promise<FileNode[]> {
  const url = `/api/v1/sessions/${sessionId}/files${path ? `?path=${encodeURIComponent(path)}` : ""}`;
  return apiGet<FileNode[]>(url);
}

export async function deleteSessions(ids: string[]): Promise<void> {
  await Promise.all(ids.map(id => deleteSession(id)));
}

/**
 * Update session configuration
 */
export async function updateSession(id: string, updates: Partial<SessionCreateRequest> & { llm_config?: Record<string, string> }): Promise<Session> {
  return apiPatch<Session>(`/api/v1/sessions/${id}`, updates);
}

export interface Model {
  id: string;
  object?: string;
  created?: number;
  owned_by?: string;
}

/**
 * List available models
 */
export async function listModels(): Promise<Model[]> {
  const resp = await apiGet<{ models: Model[] }>("/api/v1/models");
  return resp.models || [];
}
