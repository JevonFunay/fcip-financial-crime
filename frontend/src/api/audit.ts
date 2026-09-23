import type { AuditFilters, AuditListResponse } from "../types";
import { API_BASE_URL, apiClient } from "./client";
import { getAccessToken } from "./accessToken";

// Blank fields mean "no filter", so they are dropped rather than sent empty.
function cleaned(filters: AuditFilters): Record<string, string> {
  return Object.fromEntries(
    Object.entries(filters)
      .map(([key, value]) => [key, (value ?? "").trim()])
      .filter(([, value]) => value !== ""),
  );
}

export async function searchAudit(
  filters: AuditFilters,
  page: { limit: number; offset: number },
): Promise<AuditListResponse> {
  const { data } = await apiClient.get<AuditListResponse>("/audit", {
    params: { ...cleaned(filters), ...page },
  });
  return data;
}

/** Downloads the CSV through the same authenticated client, then hands the
 *  browser a blob — an anchor to /audit/export would carry no bearer token. */
export async function exportAudit(filters: AuditFilters): Promise<void> {
  const token = getAccessToken();
  const response = await fetch(
    `${API_BASE_URL}/audit/export?${new URLSearchParams(cleaned(filters)).toString()}`,
    {
      credentials: "include",
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    },
  );
  if (!response.ok) {
    throw new Error(`Export failed (${response.status})`);
  }

  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "audit_trail.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}
