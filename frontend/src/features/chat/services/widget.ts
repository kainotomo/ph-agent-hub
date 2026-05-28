// =============================================================================
// PH Agent Hub — Widget API Service
// =============================================================================
// Service functions for the embedded (non-demo) chat widget.
// Uses guest JWT for auth against /widget/* endpoints.
// =============================================================================

import { api } from "../../../services/api";

export interface WidgetMessage {
  id: string;
  session_id: string;
  role: string;
  content: string;
  created_at: string;
}

/**
 * List messages in the current widget session.
 */
export async function getWidgetMessages(): Promise<WidgetMessage[]> {
  const res = await api("/widget/session/messages");
  return res as unknown as WidgetMessage[];
}
