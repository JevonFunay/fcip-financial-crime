import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";

import type { TokenResponse } from "../types";
import { getAccessToken, setAccessToken } from "./accessToken";

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
export const SESSION_EXPIRED_EVENT = "fcip:session-expired";

// withCredentials: the refresh cookie must ride along on cross-origin calls to /auth/*.
export const apiClient = axios.create({ baseURL: API_BASE_URL, withCredentials: true });

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean };

apiClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let refreshInFlight: Promise<string> | null = null;

// Exchanges the HttpOnly refresh cookie for a new access token. Concurrent
// callers share one request, since each call rotates the cookie.
export function refreshAccessToken(): Promise<string> {
  if (!refreshInFlight) {
    // Plain axios so this request doesn't loop back through the interceptor below.
    refreshInFlight = axios
      .post<TokenResponse>(`${API_BASE_URL}/auth/refresh`, null, { withCredentials: true })
      .then(({ data }) => {
        setAccessToken(data.access_token);
        return data.access_token;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

// On 401: refresh once and retry. If that fails too, the session is gone —
// drop the access token and tell the app.
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined;
    const url = config?.url ?? "";
    const isAuthRoute = url.startsWith("/auth/login") || url.startsWith("/auth/refresh");
    if (error.response?.status !== 401 || !config || config._retried || isAuthRoute) {
      throw error;
    }
    config._retried = true;
    try {
      const token = await refreshAccessToken();
      config.headers.Authorization = `Bearer ${token}`;
      return apiClient(config);
    } catch {
      setAccessToken(null);
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
      throw error;
    }
  },
);
