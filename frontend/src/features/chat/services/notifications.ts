// =============================================================================
// PH Agent Hub — Notifications API Service
// =============================================================================
// Endpoints for the notification center (bell icon + dropdown, Issue #449).
// =============================================================================

import api from "../../../services/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface NotificationData {
  id: string;
  type: string;
  title: string;
  body: string | null;
  reference_id: string | null;
  reference_type: string | null;
  is_read: boolean;
  created_at: string;
}

export interface NotificationListResponse {
  items: NotificationData[];
  total: number;
}

export interface UnreadCountResponse {
  count: number;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export function listNotifications(
  page: number = 1,
  pageSize: number = 50,
  unreadOnly: boolean = false,
): Promise<NotificationListResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (unreadOnly) {
    params.set("unread_only", "true");
  }
  return api<NotificationListResponse>(
    `/notifications?${params.toString()}`,
  );
}

export function getUnreadCount(): Promise<UnreadCountResponse> {
  return api<UnreadCountResponse>("/notifications/unread-count");
}

export function markNotificationRead(
  notificationId: string,
): Promise<{ status: string }> {
  return api<{ status: string }>(
    `/notifications/${notificationId}/read`,
    { method: "POST" },
  );
}

export function markAllNotificationsRead(): Promise<{
  status: string;
  updated: number;
}> {
  return api<{ status: string; updated: number }>(
    "/notifications/read-all",
    { method: "POST" },
  );
}
