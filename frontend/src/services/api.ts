// =============================================================================
// PH Agent Hub — API Client
// =============================================================================
// Base fetch wrapper. JWT injected from module-scoped token variable.
// Auto-refreshes on 401 via /auth/refresh. All backend calls go through here.
// =============================================================================

const BASE_URL = import.meta.env.VITE_API_URL || "/api";

let _token: string | null = null;
let _refreshPromise: Promise<string | null> | null = null;

export function setToken(token: string | null): void {
  _token = token;
}

export function getToken(): string | null {
  return _token;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

async function refreshToken(): Promise<string | null> {
  try {
    const res = await fetch(`${BASE_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) {
      return null;
    }
    const data = await res.json();
    _token = data.access_token;
    return _token;
  } catch {
    return null;
  }
}

async function doRefreshOnce(): Promise<string | null> {
  if (_refreshPromise) {
    return _refreshPromise;
  }
  _refreshPromise = refreshToken().finally(() => {
    _refreshPromise = null;
  });
  return _refreshPromise;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface ApiOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  skipAuth?: boolean;
}

export async function api<T = unknown>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  const { skipAuth, body, headers: extraHeaders, ...rest } = options;

  const headers: Record<string, string> = {
    ...(extraHeaders as Record<string, string>),
  };

  if (!(body instanceof FormData) && !(body instanceof URLSearchParams)) {
    headers["Content-Type"] = headers["Content-Type"] || "application/json";
  }

  if (!skipAuth && _token) {
    headers["Authorization"] = `Bearer ${_token}`;
  }

  const fetchOptions: RequestInit = {
    ...rest,
    headers,
    credentials: "include",
  };

  if (body !== undefined && !(body instanceof FormData) && !(body instanceof URLSearchParams)) {
    fetchOptions.body = JSON.stringify(body);
  } else if (body instanceof URLSearchParams) {
    fetchOptions.body = body.toString();
  } else if (body instanceof FormData) {
    fetchOptions.body = body;
  }

  let res = await fetch(`${BASE_URL}${path}`, fetchOptions);

  // Auto-refresh on 401
  if (res.status === 401 && !skipAuth && _token) {
    const newToken = await doRefreshOnce();
    if (newToken) {
      headers["Authorization"] = `Bearer ${newToken}`;
      res = await fetch(`${BASE_URL}${path}`, {
        ...fetchOptions,
        headers,
      });
    }
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const data = await res.json();
    if (!res.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((e: { msg: string }) => e.msg).join("; ")
        : data.detail;
      throw new ApiError(res.status, detail || JSON.stringify(data));
    }
    return data as T;
  }

  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text || res.statusText);
  }

  return (await res.text()) as unknown as T;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

export default api;
