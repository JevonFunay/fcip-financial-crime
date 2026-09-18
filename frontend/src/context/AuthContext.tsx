import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { setAccessToken } from "../api/accessToken";
import { fetchMe, login as apiLogin, logout as apiLogout } from "../api/auth";
import { SESSION_EXPIRED_EVENT, refreshAccessToken } from "../api/client";
import type { User } from "../types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const queryClient = useQueryClient();

  // On first load nothing is in memory: ask /auth/refresh whether the browser
  // still holds a valid refresh cookie, and only then render protected pages.
  useEffect(() => {
    let cancelled = false;
    refreshAccessToken()
      .then(() => fetchMe())
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch(() => setAccessToken(null))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onExpired = () => {
      setUser(null);
      queryClient.clear();
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [queryClient]);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await apiLogin(email, password);
    setAccessToken(tokens.access_token);
    setUser(await fetchMe());
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      // already revoked or backend unreachable: local logout still proceeds
    }
    setAccessToken(null);
    setUser(null);
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
