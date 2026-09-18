// Tokens live in localStorage for the skeleton stage. Hardening (httpOnly
// cookies for the refresh token) is a follow-up — see README.
const ACCESS_KEY = "fcip.access_token";
const REFRESH_KEY = "fcip.refresh_token";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function getAccessToken(): string | null {
  return read(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  return read(REFRESH_KEY);
}

export function setTokens(tokens: { access_token: string; refresh_token: string }): void {
  try {
    localStorage.setItem(ACCESS_KEY, tokens.access_token);
    localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  } catch {
    // storage unavailable (private mode etc.): the session just won't survive a reload
  }
}

export function clearTokens(): void {
  try {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  } catch {
    // nothing to clear
  }
}
