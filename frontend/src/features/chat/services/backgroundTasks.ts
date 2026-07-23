// =============================================================================
// PH Agent Hub — Background Tasks API Service
// =============================================================================
// Endpoints for listing, viewing, and cancelling background tasks (Issue #449).
// =============================================================================

import api from "../../../services/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface BackgroundTaskData {
  id: string;
  session_id: string;
  goal: string;
  state: string;
  current_turn: number;
  max_turns: number;
  progress_message: string | null;
  result_summary: string | null;
  cumulative_tokens_in: number;
  cumulative_tokens_out: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface BackgroundTaskListResponse {
  items: BackgroundTaskData[];
  total: number;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export function listBackgroundTasks(
  page: number = 1,
  pageSize: number = 20,
  state?: string,
): Promise<BackgroundTaskListResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (state) {
    params.set("state", state);
  }
  return api<BackgroundTaskListResponse>(
    `/background-tasks?${params.toString()}`,
  );
}

export function getBackgroundTask(
  taskId: string,
): Promise<BackgroundTaskData> {
  return api<BackgroundTaskData>(`/background-tasks/${taskId}`);
}

export function cancelBackgroundTask(
  taskId: string,
): Promise<{ message: string; task_id: string }> {
  return api<{ message: string; task_id: string }>(
    `/background-tasks/${taskId}`,
    { method: "DELETE" },
  );
}
