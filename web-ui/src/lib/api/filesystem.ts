/**
 * Filesystem API
 * GET /api/v1/fs/complete - Complete path for workspace selection
 */

import { apiGet } from "./client";

export interface PathCompletion {
  path: string;
  name: string;
  is_dir: boolean;
}

export interface PathCompleteResponse {
  path: string;
  completions: PathCompletion[];
  cwd?: string;
  error?: string;
}

/**
 * Complete a filesystem path
 * @param path Partial path to complete (supports ~)
 * @param limit Maximum number of results
 */
export async function completePath(
  path: string,
  limit: number = 20
): Promise<PathCompleteResponse> {
  const params = new URLSearchParams({ path, limit: limit.toString() });
  return apiGet<PathCompleteResponse>(`/api/v1/fs/complete?${params}`);
}
