// =============================================================================
// PH Agent Hub — Scheduled Tasks API Service
// =============================================================================
// Endpoints for managing scheduled tasks (Issue #297).
// =============================================================================

import api from "../../../services/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ScheduledTaskData {
  id: string;
  tenant_id: string;
  user_id: string;
  goal: string;
  schedule_description: string;
  cron_expression: string;
  timezone: string;
  state: string;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
  last_run_session_id: string | null;
  last_run_error: string | null;
  template_session_id: string | null;
  run_count: number;
  created_at: string;
  updated_at: string;
}

export interface ScheduledTaskListResponse {
  items: ScheduledTaskData[];
  total: number;
}

export interface ScheduledTaskCreate {
  goal: string;
  schedule_description: string;
  cron_expression: string;
  timezone?: string;
  template_session_id?: string | null;
}

export interface ScheduledTaskUpdate {
  goal?: string;
  schedule_description?: string;
  cron_expression?: string;
  timezone?: string;
  state?: string;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export function listScheduledTasks(
  page: number = 1,
  pageSize: number = 50,
  state?: string,
): Promise<ScheduledTaskListResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (state) {
    params.set("state", state);
  }
  return api<ScheduledTaskListResponse>(
    `/scheduled-tasks?${params.toString()}`,
  );
}

export function getScheduledTask(
  taskId: string,
): Promise<ScheduledTaskData> {
  return api<ScheduledTaskData>(`/scheduled-tasks/${taskId}`);
}

export function createScheduledTask(
  data: ScheduledTaskCreate,
): Promise<ScheduledTaskData> {
  return api<ScheduledTaskData>("/scheduled-tasks", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateScheduledTask(
  taskId: string,
  data: ScheduledTaskUpdate,
): Promise<ScheduledTaskData> {
  return api<ScheduledTaskData>(`/scheduled-tasks/${taskId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteScheduledTask(
  taskId: string,
): Promise<void> {
  return api<void>(`/scheduled-tasks/${taskId}`, {
    method: "DELETE",
  });
}

export function pauseScheduledTask(
  taskId: string,
): Promise<ScheduledTaskData> {
  return api<ScheduledTaskData>(`/scheduled-tasks/${taskId}/pause`, {
    method: "POST",
  });
}

export function resumeScheduledTask(
  taskId: string,
): Promise<ScheduledTaskData> {
  return api<ScheduledTaskData>(`/scheduled-tasks/${taskId}/resume`, {
    method: "POST",
  });
}
