// =============================================================================
// PH Agent Hub — Demo API Service
// =============================================================================
// Service functions for the anonymous "Try It Now" demo experience.
// =============================================================================

import { api } from "../../../services/api";

export interface DemoConfig {
  guest_token: string;
  session_id: string;
  theme: Record<string, unknown>;
  feature_flags: Record<string, unknown>;
  default_model_id: string | null;
  default_skill_id: string | null;
  default_template_id: string | null;
}

export interface DemoSession {
  id: string;
  tenant_id: string;
  title: string;
  is_temporary: boolean;
}

export interface DemoMessage {
  id: string;
  session_id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface DemoStatus {
  enabled: boolean;
}

/**
 * Check whether demo mode is enabled (public, no auth).
 */
export async function getDemoStatus(): Promise<DemoStatus> {
  const res = await fetch("/api/demo/status");
  if (!res.ok) {
    return { enabled: false };
  }
  return res.json();
}

/**
 * Create a new anonymous demo session.
 * Returns a guest JWT that must be set via setToken() for subsequent calls.
 */
export async function createDemoSession(): Promise<DemoConfig> {
  const res = await api("/demo/session", {
    method: "POST",
  });
  return res as unknown as DemoConfig;
}

/**
 * Get the current demo session info.
 */
export async function getDemoSession(): Promise<DemoSession> {
  const res = await api("/demo/session");
  return res as unknown as DemoSession;
}

/**
 * List messages in the current demo session.
 */
export async function getDemoMessages(): Promise<DemoMessage[]> {
  const res = await api("/demo/session/messages");
  return res as unknown as DemoMessage[];
}
