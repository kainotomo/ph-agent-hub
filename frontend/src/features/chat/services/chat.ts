// =============================================================================
// PH Agent Hub — Chat API Service
// =============================================================================
// All chat API calls: session CRUD, send message, get messages,
// edit/delete/regenerate/feedback, tools, memory, uploads, search.
// =============================================================================

import api from "../../../services/api";

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
  selected_template_id: string | null;
  selected_skill_id: string | null;
  selected_model_id: string | null;
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
