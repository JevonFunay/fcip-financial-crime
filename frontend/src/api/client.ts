import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";

import type { TokenResponse } from "../types";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./tokens";

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
export const SESSION_EXPIRED_EVENT = "fcip:session-expired";

export const apiClient = axios.create({ baseURL: API_BASE_URL });

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean };

apiClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let refreshInFlight: Promise<string> | null = null;

async function refreshAccessToken(): Promise<string> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    throw new Error("No refresh token");
  }
  // Plain axios so this request doesn't loop back through the interceptor below.
  const { data } = await axios.post<TokenResponse>(`${API_BASE_URL}/auth/refresh`, {
    refresh_token: refreshToken,
  });
  setTokens(data);
  return data.access_token;
}

// On 401: rotate the refresh token once (shared across concurrent requests) and
// retry. If that fails too, the session is gone — clear it and tell the app.
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
      if (!refreshInFlight) {
        refreshInFlight = refreshAccessToken().finally(() => {
          refreshInFlight = null;
        });
      }
      const token = await refreshInFlight;
      config.headers.Authorization = `Bearer ${token}`;
      return apiClient(config);
    } catch {
      clearTokens();
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
      throw error;
    }
  },
);
