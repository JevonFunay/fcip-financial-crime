// The access token lives only in memory (TRD §12.2): nothing auth-related is
// persisted by page scripts. On reload the app calls /auth/refresh, which the
// browser authenticates with the HttpOnly refresh cookie.
let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}
