// =============================================================================
// PH Agent Hub — AuthProvider
// =============================================================================
// React context; holds user+role+tenantId state; calls auth.ts on mount
// (GET /auth/me); exposes login/logout.
// =============================================================================

import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
} from "react";
import {
  login as authLogin,
  logout as authLogout,
  getMe,
  getToken,
  setToken,
  UserProfile,
} from "../services/auth";
import { api as rawApi } from "../services/api";

interface AuthState {
  user: UserProfile | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  // Try to restore session on mount (skip for widget/demo — they use guest tokens)
  useEffect(() => {
    const path = window.location.pathname;
    const isAnonymous = path.startsWith("/widget") || path.startsWith("/demo");

    if (isAnonymous) {
      setLoading(false);
      return;
    }

    const token = getToken();
    if (token) {
      // Verify existing token
      getMe()
        .then(setUser)
        .catch(() => {
          setToken(null);
          setLoading(false);
        })
        .finally(() => setLoading(false));
    } else {
      // No in-memory token — try refresh cookie
      rawApi<{ access_token: string }>("/auth/refresh", {
        method: "POST",
        skipAuth: true,
      })
        .then((data) => {
          setToken(data.access_token);
          return getMe();
        })
        .then(setUser)
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    await authLogin({ username, password });
    const profile = await getMe();
    setUser(profile);
  }, []);

  const logout = useCallback(async () => {
    await authLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}

export default AuthProvider;
