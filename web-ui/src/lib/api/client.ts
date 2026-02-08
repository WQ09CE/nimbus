/**
 * Nimbus API Client
 * 
 * Connects to Nimbus server @ localhost:4096
 * Uses /api/v1/* endpoints with SSE streaming
 */

import { logger } from "../logger";

const getApiBase = () => {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== "undefined") {
    // Browser: use current hostname + port 4096
    return `${window.location.protocol}//${window.location.hostname}:4096`;
  }
  // SSR / Server
  return "http://localhost:4096";
};

const API_BASE = getApiBase();

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

function generateRequestId() {
  return Math.random().toString(36).substring(2, 10);
}

/**
 * Base fetch wrapper with error handling.
 */
export async function apiFetch<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${endpoint}`;
  const reqId = generateRequestId();
  const method = options.method || "GET";

  logger.info(`[API] ${method} ${url} (req_id=${reqId})`);

  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Request-ID": reqId,
      ...options.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({
      detail: "Unknown error",
    }));
    logger.error(`[API] Error ${response.status}: ${error.detail} (req_id=${reqId})`);
    throw new ApiError(response.status, error.detail || "Request failed");
  }

  const text = await response.text();
  logger.debug(`[API] Response ${response.status} (req_id=${reqId})`, text.length > 100 ? text.slice(0, 100) + '...' : text);

  if (!text) return {} as T;

  return JSON.parse(text) as T;
}

/**
 * GET request helper.
 */
export function apiGet<T>(endpoint: string): Promise<T> {
  return apiFetch<T>(endpoint, { method: "GET" });
}

/**
 * POST request helper.
 */
export function apiPost<T>(endpoint: string, data?: unknown): Promise<T> {
  return apiFetch<T>(endpoint, {
    method: "POST",
    body: data ? JSON.stringify(data) : undefined,
  });
}

/**
 * DELETE request helper.
 */
export function apiDelete<T>(endpoint: string): Promise<T> {
  return apiFetch<T>(endpoint, { method: "DELETE" });
}

/**
 * PATCH request helper.
 */
export function apiPatch<T>(endpoint: string, data?: unknown): Promise<T> {
  return apiFetch<T>(endpoint, {
    method: "PATCH",
    body: data ? JSON.stringify(data) : undefined,
  });
}

/**
 * SSE Stream helper for chat.
 * 
 * Nimbus uses event types:
 * - connected, message_start, planning, dag_created
 * - task_start, tool_call, tool_result, task_done, task_failed
 * - permission_request, dag_complete, message, error, heartbeat
 */
export async function* apiStream(
  endpoint: string,
  data?: unknown,
  signal?: AbortSignal,
  method: string = "POST"
): AsyncGenerator<{ type: string; data: unknown }> {
  const url = `${API_BASE}${endpoint}`;
  const reqId = generateRequestId();
  logger.info(`[API] Stream ${method} ${url} (req_id=${reqId})`);

  const fetchOptions: RequestInit = {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Request-ID": reqId,
    },
    signal,
  };

  if (data !== undefined && method !== "GET" && method !== "HEAD") {
    fetchOptions.body = JSON.stringify(data);
  }

  const response = await fetch(url, fetchOptions);

  if (!response.ok) {
    logger.error(`[API] Stream Error ${response.status} (req_id=${reqId})`);
    throw new ApiError(response.status, "Stream request failed");
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("No response body");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        logger.info(`[API] Stream done (req_id=${reqId})`);
        break;
      }

      const chunk = decoder.decode(value, { stream: true });
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      let currentEvent = "message";

      for (const line of lines) {
        if (line.trim() === "") continue;

        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          const dataStr = line.slice(6);
          logger.debug(`[API] Event: ${currentEvent} (req_id=${reqId})`, dataStr.slice(0, 50));
          try {
            const data = JSON.parse(dataStr);
            yield { type: currentEvent, data };
          } catch {
            // Non-JSON data
            yield { type: currentEvent, data: dataStr };
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export { API_BASE };
