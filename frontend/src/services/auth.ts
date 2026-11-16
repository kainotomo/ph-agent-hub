// =============================================================================
// PH Agent Hub — Auth Service
// =============================================================================
// login(), logout(), refreshToken(), getMe() — all call api.ts.
// setToken()/getToken() for in-memory JWT storage.
// =============================================================================

import api, { setToken, getToken } from "./api";

/** Browser-notification preferences stored on the signed-in user. */
export interface NotificationPreferences {
  enabled: boolean;
  on_completion: boolean;
  on_failure: boolean;
  sound: boolean;
}

export const DEFAULT_NOTIFICATION_PREFS: NotificationPreferences = {
  enabled: false,
  on_completion: true,
  on_failure: true,
  sound: false,
};

export interface UserProfile {
  id: string;
  email: string;
  display_name: string;
  role: string;
  tenant_id: string;
  is_active: boolean;
  default_model_id: string | null;
  /** Absent on profiles cached before Issue #619. */
  notification_prefs?: NotificationPreferences;

  created_at: string;
}

export interface LoginParams {
  username: string;
  password: string;
}

export async function login(params: LoginParams): Promise<void> {
  const formData = new URLSearchParams();
  formData.append("username", params.username);
  formData.append("password", params.password);

  const data = await api<{ access_token: string; token_type: string }>(
    "/auth/login",
    {
      method: "POST",
      body: formData,
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      skipAuth: true,
    },
  );

  setToken(data.access_token);
}

export async function logout(): Promise<void> {
  try {
    await api("/auth/logout", { method: "POST" });
  } catch {
    // Silently ignore — we clear the token regardless
  }
  setToken(null);
}

export async function getMe(): Promise<UserProfile> {
  return api<UserProfile>("/users/me");
}

export async function setDefaultModel(modelId: string | null): Promise<void> {
  return api<void>("/users/me/default-model", {
    method: "PUT",
    body: { model_id: modelId },
  });
}

export async function setNotificationPrefs(
  prefs: NotificationPreferences,
): Promise<NotificationPreferences> {
  return api<NotificationPreferences>("/users/me/notifications", {
    method: "PUT",
    body: prefs,
  });
}

export { setToken, getToken };
