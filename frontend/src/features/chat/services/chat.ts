// =============================================================================
// PH Agent Hub — Chat API Service
// =============================================================================
// All chat API calls: session CRUD, send message, get messages,
// edit/delete/regenerate/feedback, tools, memory, uploads, search.
// =============================================================================

import api, { getToken } from "../../../services/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TagData {
  id: string;
  name: string;
  color: string | null;
}

export interface SessionData {
  id: string;
  tenant_id: string;
  user_id: string;
  title: string;
  is_temporary: boolean;
  is_pinned: boolean;
  is_pending?: boolean;
  /** True when the session hasn't been persisted yet (lazy creation). */
  selected_skill_id: string | null;
  selected_model_id: string | null;
  selected_template_id?: string | null;
  auto_route_enabled?: boolean;
  auto_select_tools?: boolean;
  thinking_enabled?: boolean | null;
  temperature?: number | null;
  cross_session_retrieval_enabled?: boolean | null;
  tags?: TagData[];
  created_at: string;
  updated_at: string;
}

export interface MessageData {
  id: string;
  session_id: string;
  sender: "user" | "assistant" | "system";
  content: unknown[] | null;
  model_id: string | null;
  model_name?: string | null;
  model_provider?: string | null;
  tool_calls: unknown[] | null;
  tokens_in: number | null;
  tokens_out: number | null;
  is_deleted: boolean;
  summarized?: boolean;
  created_at: string;
  updated_at: string;
}

export interface ToolData {
  id: string;
  tenant_id: string;
  name: string;
  type: string;
  category: string;
  config: Record<string, unknown> | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface FeedbackData {
  id: string;
  message_id: string;
  user_id: string;
  rating: "up" | "down";
  comment: string | null;
  created_at: string;
}

export interface FileUploadData {
  file_id: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
}

export interface MemoryEntry {
  id: string;
  tenant_id: string;
  user_id: string;
  session_id: string | null;
  key: string;
  value: string;
  source: string;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Session CRUD
// ---------------------------------------------------------------------------

export function createSession(data: {
  title?: string;
  is_temporary?: boolean;
  is_pinned?: boolean;
  selected_template_id?: string;
  selected_skill_id?: string;
  selected_model_id?: string;
  auto_route_enabled?: boolean;
  auto_select_tools?: boolean;
  thinking_enabled?: boolean;
  temperature?: number;
  active_tool_ids?: string[];
}): Promise<SessionData> {
  return api<SessionData>("/chat/session", {
    method: "POST",
    body: data,
  });
}

export function listSessions(): Promise<SessionData[]> {
  return api<SessionData[]>("/chat/sessions");
}

export function getSession(id: string): Promise<SessionData> {
  return api<SessionData>(`/chat/session/${id}`);
}

export function updateSession(
  id: string,
  data: {
    title?: string;
    is_pinned?: boolean;
    selected_template_id?: string | null;
    selected_skill_id?: string | null;
    selected_model_id?: string | null;
    auto_route_enabled?: boolean;
    auto_select_tools?: boolean;
  },
): Promise<SessionData> {
  return api<SessionData>(`/chat/session/${id}`, {
    method: "PUT",
    body: data,
  });
}

export function deleteSession(id: string): Promise<void> {
  return api<void>(`/chat/session/${id}`, { method: "DELETE" });
}

export interface BatchDeleteResult {
  deleted: number;
  skipped: Array<{ id: string; reason: string }>;
  errors: string[];
}

export function deleteSessions(ids: string[]): Promise<BatchDeleteResult> {
  return api<BatchDeleteResult>("/chat/sessions/delete", {
    method: "POST",
    body: { ids },
  });
}

export function finalizeSession(id: string): Promise<SessionData> {
  return api<SessionData>(`/chat/session/${id}/finalize`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Session Context Window (Issue #309)
// ---------------------------------------------------------------------------

export interface SessionContextData {
  tokens_used: number;
  context_length: number | null;
  percentage: number | null;
}

export function getSessionContext(
  sessionId: string,
): Promise<SessionContextData> {
  return api<SessionContextData>(`/chat/session/${sessionId}/context`);
}

// ---------------------------------------------------------------------------
// Summarization (Issue #29)
// ---------------------------------------------------------------------------

export interface SummarizeResponse {
  summary: string;
  summarized_message_count: number;
  tokens_saved: number;
}

export function summarizeSession(
  sessionId: string,
  keepRecentPairs?: number,
): Promise<SummarizeResponse> {
  return api<SummarizeResponse>(`/chat/session/${sessionId}/summarize`, {
    method: "POST",
    body: { keep_recent_pairs: keepRecentPairs ?? 3 },
  });
}

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

export function listMessages(sessionId: string): Promise<MessageData[]> {
  return api<MessageData[]>(`/chat/session/${sessionId}/messages`);
}

export function editMessage(
  sessionId: string,
  messageId: string,
  content: string,
): Promise<{ message_id: string; content: string; model_id: string | null }> {
  return api(`/chat/session/${sessionId}/message/${messageId}`, {
    method: "PUT",
    body: { content },
  });
}

export function deleteMessage(
  sessionId: string,
  messageId: string,
): Promise<void> {
  return api<void>(`/chat/session/${sessionId}/message/${messageId}`, {
    method: "DELETE",
  });
}

export function regenerateMessage(
  sessionId: string,
  messageId: string,
): Promise<{ message_id: string; content: string; model_id: string | null }> {
  return api(`/chat/session/${sessionId}/message/${messageId}/regenerate`, {
    method: "POST",
  });
}

/** Update an assistant message in-place (PATCH — no agent re-run). */
export function updateAssistantMessage(
  sessionId: string,
  messageId: string,
  content: string,
): Promise<MessageData> {
  return api<MessageData>(`/chat/session/${sessionId}/message/${messageId}`, {
    method: "PATCH",
    body: { content },
  });
}

// ---------------------------------------------------------------------------
// Export / Import
// ---------------------------------------------------------------------------

export type ExportFormat = "json" | "txt";

export async function exportSession(
  sessionId: string,
  format: ExportFormat = "json",
): Promise<void> {
  const BASE_URL = import.meta.env.VITE_API_URL || "/api";
  const token = getToken();
  const res = await fetch(
    `${BASE_URL}/chat/session/${sessionId}/export?format=${format}`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    },
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Export failed");
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const contentDisposition = res.headers.get("Content-Disposition") || "";
  const filenameMatch = contentDisposition.match(/filename="?(.+?)"?$/);
  const filename = filenameMatch?.[1] || `conversation.${format}`;
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function importSession(
  file: File,
): Promise<{ session_id: string; message_count: number }> {
  const BASE_URL = import.meta.env.VITE_API_URL || "/api";
  const token = getToken();

  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${BASE_URL}/chat/import`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });

  if (!res.ok) {
    let detail = "Import failed";
    try {
      const err = await res.json();
      detail = err.detail || detail;
    } catch {
      const text = await res.text();
      detail = text || detail;
    }
    throw new Error(detail);
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// Message Feedback
// ---------------------------------------------------------------------------

export function submitFeedback(
  sessionId: string,
  messageId: string,
  rating: "up" | "down",
  comment?: string,
): Promise<FeedbackData> {
  return api<FeedbackData>(
    `/chat/session/${sessionId}/message/${messageId}/feedback`,
    {
      method: "POST",
      body: { rating, comment },
    },
  );
}

// ---------------------------------------------------------------------------
// Cancel Stream
// ---------------------------------------------------------------------------

export function cancelStream(sessionId: string): Promise<void> {
  return api<void>(`/chat/session/${sessionId}/stream`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Session Tools
// ---------------------------------------------------------------------------

export function listSessionTools(sessionId: string): Promise<ToolData[]> {
  return api<ToolData[]>(`/chat/session/${sessionId}/tools`);
}

export function addSessionTool(
  sessionId: string,
  toolId: string,
): Promise<void> {
  return api<void>(`/chat/session/${sessionId}/tools/${toolId}`, {
    method: "POST",
  });
}

export function removeSessionTool(
  sessionId: string,
  toolId: string,
): Promise<void> {
  return api<void>(`/chat/session/${sessionId}/tools/${toolId}`, {
    method: "DELETE",
  });
}

export function setToolAlwaysOn(
  toolId: string,
  alwaysOn: boolean,
): Promise<void> {
  return api<void>(`/chat/session/tools/${toolId}/always-on`, {
    method: "PUT",
    body: { always_on: alwaysOn },
  });
}

export function listAlwaysOnTools(): Promise<string[]> {
  return api<string[]>("/chat/session/tools/always-on");
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

export function searchSessions(query: string): Promise<SessionData[]> {
  return api<SessionData[]>(
    `/chat/sessions/search?q=${encodeURIComponent(query)}`,
  );
}

// ---------------------------------------------------------------------------
// File Uploads
// ---------------------------------------------------------------------------

export function uploadFile(
  sessionId: string,
  file: File,
): Promise<FileUploadData> {
  const formData = new FormData();
  formData.append("file", file);
  return api<FileUploadData>(`/chat/session/${sessionId}/upload`, {
    method: "POST",
    body: formData,
  });
}

export function listUploads(sessionId: string): Promise<FileUploadData[]> {
  return api<FileUploadData[]>(`/chat/session/${sessionId}/uploads`);
}

export function getUploadUrl(
  sessionId: string,
  fileId: string,
): Promise<{ url: string }> {
  return api<{ url: string }>(
    `/chat/session/${sessionId}/upload/${fileId}/url`,
  );
}

export function deleteUpload(
  sessionId: string,
  fileId: string,
): Promise<void> {
  return api<void>(`/chat/session/${sessionId}/upload/${fileId}`, {
    method: "DELETE",
  });
}

export function listMessageUploads(
  sessionId: string,
  messageId: string,
): Promise<FileUploadData[]> {
  return api<FileUploadData[]>(
    `/chat/session/${sessionId}/message/${messageId}/uploads`,
  );
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

export function listMemory(sessionId?: string): Promise<MemoryEntry[]> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  return api<MemoryEntry[]>(`/memory${query}`);
}

export function createMemory(data: {
  key: string;
  value: string;
  session_id?: string;
}): Promise<MemoryEntry> {
  return api<MemoryEntry>("/memory", {
    method: "POST",
    body: data,
  });
}

export function deleteMemory(id: string): Promise<void> {
  return api<void>(`/memory/${id}`, { method: "DELETE" });
}

export function updateMemory(
  id: string,
  data: { key?: string; value?: string },
): Promise<MemoryEntry> {
  return api<MemoryEntry>(`/memory/${id}`, {
    method: "PUT",
    body: data,
  });
}

// ---------------------------------------------------------------------------
// Session Tags
// ---------------------------------------------------------------------------

export function listTenantTags(): Promise<TagData[]> {
  return api<TagData[]>("/chat/session/tags");
}

export function addTagToSession(
  sessionId: string,
  name: string,
): Promise<SessionData> {
  return api<SessionData>(`/chat/session/${sessionId}/tags`, {
    method: "POST",
    body: { name },
  });
}

export function removeTagFromSession(
  sessionId: string,
  tagId: string,
): Promise<void> {
  return api<void>(`/chat/session/${sessionId}/tags/${tagId}`, {
    method: "DELETE",
  });
}

export function listSessionsByTag(tag: string): Promise<SessionData[]> {
  return api<SessionData[]>(
    `/chat/sessions/by-tag?tag=${encodeURIComponent(tag)}`,
  );
}

// ---------------------------------------------------------------------------
// Stream Status & Reconnect — Issue #455
// ---------------------------------------------------------------------------

export interface StreamStatusResponse {
  active: boolean;
  autopilot?: boolean;
  /** True when the autopilot is paused (stream ended but run exists). */
  paused?: boolean;
  /** Current turn number when autopilot is active (1-based). */
  current_turn?: number;
  /** Maximum turns configured for the autopilot run. */
  max_turns?: number;
  /** Backend AutopilotRun state: COMPLETED, FAILED, CANCELLED, PAUSED, etc. */
  run_state?: string | null;
}

/**
 * Check whether a background agent is currently running for *sessionId*.
 * Lightweight Redis check, no DB query.
 */
export function getStreamStatus(
  sessionId: string,
): Promise<StreamStatusResponse> {
  return api<StreamStatusResponse>(
    `/chat/session/${sessionId}/stream-status`,
  );
}

/**
 * Reconnect to an already-running agent stream.
 * Returns the raw Response so the caller can open an EventSource.
 * Throws if the session has no active stream.
 */
export async function reconnectStream(
  sessionId: string,
): Promise<Response> {
  const BASE_URL = import.meta.env.VITE_API_URL || "/api";
  const token = getToken();
  const res = await fetch(
    `${BASE_URL}/chat/session/${sessionId}/stream`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    },
  );
  if (!res.ok) {
    throw new Error(`Reconnect failed with status ${res.status}`);
  }
  return res;
}

/**
 * Pause a running autopilot.
 */
export async function autopilotPause(
  sessionId: string,
): Promise<{ status: string; run_id: string }> {
  return api<{ status: string; run_id: string }>(
    `/chat/session/${sessionId}/autopilot/pause`,
    { method: "POST" },
  );
}

/**
 * Send a steering instruction to a paused autopilot.
 */
export async function autopilotSteer(
  sessionId: string,
  instruction: string,
): Promise<{ status: string; run_id: string }> {
  return api<{ status: string; run_id: string }>(
    `/chat/session/${sessionId}/autopilot/steer`,
    {
      method: "POST",
      body: { instruction },
    },
  );
}
