// =============================================================================
// PH Agent Hub — Credential API Service
// =============================================================================
// CRUD for user-connected accounts (email, calendar, tasks).
// =============================================================================

import api from "./api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CredentialData {
  id: string;
  user_id: string;
  tool_id: string;
  label: string;
  provider: string;
  email_address: string | null;
  is_default: boolean;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface CredentialListResponse {
  items: CredentialData[];
  total: number;
}

export interface OAuthUrlResponse {
  url: string;
  state: string;
}

export interface TestConnectionResponse {
  ok: boolean;
  message: string;
  folders?: string[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function qs(params: Record<string, string | undefined>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
    }
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

// ---------------------------------------------------------------------------
// Credential CRUD
// ---------------------------------------------------------------------------

export function listCredentials(tool_id?: string): Promise<CredentialListResponse> {
  return api<CredentialListResponse>(`/credentials${qs({ tool_id })}`);
}

export function deleteCredential(credential_id: string): Promise<void> {
  return api<void>(`/credentials/${credential_id}`, { method: "DELETE" });
}

export function updateCredential(
  credential_id: string,
  data: Record<string, unknown>,
): Promise<CredentialData> {
  return api<CredentialData>(`/credentials/${credential_id}`, {
    method: "PUT",
    body: data,
  });
}

export function testConnection(credential_id: string): Promise<TestConnectionResponse> {
  return api<TestConnectionResponse>(`/credentials/${credential_id}/test`, {
    method: "POST",
  });
}

export function testRawImap(host: string, port: number, username: string, password: string): Promise<TestConnectionResponse> {
  return api<TestConnectionResponse>("/credentials/test-imap", {
    method: "POST",
    body: { host, port, username, password },
  });
}

// ---------------------------------------------------------------------------
// OAuth
// ---------------------------------------------------------------------------

export function getGoogleOAuthUrl(tool_id: string): Promise<OAuthUrlResponse> {
  return api<OAuthUrlResponse>(`/credentials/oauth/google/url${qs({ tool_id })}`);
}

export function getMicrosoftOAuthUrl(tool_id: string): Promise<OAuthUrlResponse> {
  return api<OAuthUrlResponse>(`/credentials/oauth/microsoft/url${qs({ tool_id })}`);
}
